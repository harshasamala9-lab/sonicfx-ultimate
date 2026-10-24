"""MeterWorker semantics: emissions, retries, refresh, quiet-when-off."""
from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

import pytest  # noqa: E402

from sonicfx.ui.controller import MeterWorker  # noqa: E402
from sonicfx.pipewire.tap import TapError  # noqa: E402


class ScriptedTap:
    """LevelTap stand-in: ``read()`` replays a fixed script; ``None`` ends it."""

    def __init__(self, target: str, script) -> None:
        self.target = target
        self._script = iter(script) if script is not None else None
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def read(self, timeout: float = 0.15) -> tuple[float, float] | None:
        if self._script is None:
            return (0.3, 0.3)  # endless live stream (still allows refresh)
        return next(self._script, None)

    def stop(self) -> None:
        self.stopped = True


def _spin(seconds: float) -> None:
    app = QApplication.instance() or QApplication([])
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()


def test_worker_emits_scripted_levels_and_retries_after_stream_end(qtbot) -> None:
    taps: list[ScriptedTap] = []
    target = ["dev.monitor"]

    def factory(new_target: str) -> ScriptedTap:
        tap = ScriptedTap(new_target, [(0.25, 0.5), (0.0, 0.0)])
        taps.append(tap)
        return tap

    worker = MeterWorker(lambda: target[0], tap_factory=factory)
    records: list[tuple[float, float]] = []
    worker.levels.connect(lambda left, right: records.append((left, right)))
    worker.start()
    try:
        qtbot.waitUntil(lambda: len(records) >= 3, timeout=4_000)
        assert records[0] == pytest.approx((0.25, 0.5))
        assert records[1] == (0.0, 0.0)
        qtbot.waitUntil(lambda: len(taps) >= 2, timeout=4_000)  # fresh attempt
        assert taps[0].target == "dev.monitor"
        assert taps[0].stopped is True  # the dead tap was terminated
    finally:
        target.clear()
        worker.stop()
        assert worker.wait(5_000) is True


def test_worker_restarts_capture_on_refresh(qtbot) -> None:
    taps: list[ScriptedTap] = []
    target = ["dev.monitor"]

    def factory(new_target: str) -> ScriptedTap:
        tap = ScriptedTap(new_target, None)
        taps.append(tap)
        return tap

    worker = MeterWorker(lambda: target[0], tap_factory=factory)
    records: list[tuple[float, float]] = []
    worker.levels.connect(lambda left, right: records.append((left, right)))
    worker.start()
    try:
        qtbot.waitUntil(lambda: bool(taps and taps[0].started), timeout=3_000)
        target[0] = "other.monitor"  # the user picked a different device
        worker.refresh()
        qtbot.waitUntil(lambda: len(taps) >= 2, timeout=3_000)

        assert taps[0].target == "dev.monitor"
        assert taps[0].stopped is True
        assert taps[1].target == "other.monitor"
    finally:
        target.clear()
        worker.stop()
        assert worker.wait(5_000) is True


def test_worker_recovers_after_failed_start(qtbot) -> None:
    taps: list[ScriptedTap] = []
    attempts = {"count": 0}
    running = [True]

    def factory(new_target: str) -> ScriptedTap:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise TapError("pw-record could not be started")
        tap = ScriptedTap(new_target, [(0.4, 0.4)])
        taps.append(tap)
        return tap

    worker = MeterWorker(
        lambda: "dev.monitor" if running[0] else "", tap_factory=factory
    )
    records: list[tuple[float, float]] = []
    worker.levels.connect(lambda left, right: records.append((left, right)))
    worker.start()
    try:
        # The failed attempt reports silence once, then the loop retries.
        qtbot.waitUntil(lambda: (0.4, 0.4) in records, timeout=4_000)
        assert records[0] == (0.0, 0.0)
    finally:
        running[0] = False
        worker.stop()
        assert worker.wait(5_000) is True


def test_worker_retargets_if_capture_starts_on_stale_device() -> None:
    tap = ScriptedTap("old.monitor", None)
    worker = MeterWorker(lambda: "new.monitor", tap_factory=lambda _target: tap)

    # Simulate the refresh flag being consumed just before the tap starts: the
    # drain still must notice that the requested target no longer matches.
    assert worker._drain(tap, "old.monitor") == "retarget"
    assert tap.started is False


def test_worker_stays_quiet_until_a_target_appears(qtbot) -> None:
    taps: list[ScriptedTap] = []
    enabled = [False]

    def factory(new_target: str) -> ScriptedTap:
        tap = ScriptedTap(new_target, [(0.6, 0.3)])
        taps.append(tap)
        return tap

    worker = MeterWorker(
        lambda: "dev.monitor" if enabled[0] else "", tap_factory=factory
    )
    records: list[tuple[float, float]] = []
    worker.levels.connect(lambda left, right: records.append((left, right)))
    worker.start()
    try:
        _spin(0.3)
        assert records == []  # meter is off: no signals at all

        enabled[0] = True
        qtbot.waitUntil(lambda: (0.6, 0.3) in records, timeout=4_000)
        assert taps and taps[0].target == "dev.monitor"
    finally:
        enabled[0] = False
        worker.stop()
        assert worker.wait(5_000) is True
