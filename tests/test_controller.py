"""Controller integration tests: threading, debounce and routing safety."""
from __future__ import annotations

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import QSettings  # noqa: E402

from sonicfx.audio.engine import EngineError, EngineStatus  # noqa: E402
from sonicfx.pipewire.routing import DefaultRoute, RoutingError  # noqa: E402
from sonicfx.pipewire.wiring import SinkInfo  # noqa: E402
from sonicfx.ui import controller as controller_module  # noqa: E402
from sonicfx.ui.controller import Controller  # noqa: E402


class FakeEngine:
    def __init__(self) -> None:
        self.running = False
        self.generation = 0
        self.output: str | None = None
        self.start_calls: list[tuple[object, str]] = []
        self.apply_calls: list[tuple[object, str]] = []
        self.bypass_calls = 0
        self.shutdown_calls = 0
        self.fail_start = False

    def start(self, spec, *, output=None):
        self.start_calls.append((spec, output))
        if self.fail_start:
            raise EngineError("could not start the audio engine: boom")
        self.running = True
        self.generation += 1
        self.output = output
        return self.status()

    def apply(self, spec, *, output=None):
        self.apply_calls.append((spec, output))
        self.running = True
        self.generation += 1
        self.output = output
        return self.status(gap_ms=123.0)

    def bypass(self):
        self.bypass_calls += 1
        self.running = False
        return self.status()

    def shutdown(self) -> None:
        self.shutdown_calls += 1
        self.running = False

    def status(self, gap_ms: float | None = None) -> EngineStatus:
        return EngineStatus(
            running=self.running,
            pid=1234 if self.running else None,
            sink_name="sonicfx_sink" if self.running else None,
            generation=self.generation,
            output=self.output,
            gap_ms=gap_ms,
        )


class FakeRouter:
    def __init__(self) -> None:
        self.made: list[str] = []
        self.restored = 0
        self.active = False
        self.fail = False

    def recover_stale(self, *, pid=None) -> bool:
        return False

    def make_default(self, sink: str) -> DefaultRoute:
        if self.fail:
            raise RoutingError("wpctl did not switch")
        self.made.append(sink)
        self.active = True
        return DefaultRoute(sink, "alsa.previous", True)

    def restore(self) -> bool:
        if not self.active:
            return False
        self.active = False
        self.restored += 1
        return True

    def restore_to(self, name: str) -> bool:
        self.active = False
        return True

    def is_active(self, sink: str) -> bool:
        return self.active


@pytest.fixture
def controller(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(
        controller_module,
        "list_sinks",
        lambda: [SinkInfo("alsa_output.test", 1, "Test Speakers", True)],
    )
    monkeypatch.setattr(controller_module, "default_sink_name", lambda: None)
    monkeypatch.setattr(controller_module.autostart, "is_enabled", lambda: False)
    monkeypatch.setattr(controller_module.autostart, "enable", lambda: None)
    monkeypatch.setattr(controller_module.autostart, "disable", lambda: None)

    settings = QSettings(
        str(tmp_path / "settings.ini"), QSettings.Format.IniFormat
    )
    settings.setValue("auto_start_engine", False)
    settings.setValue("use_as_default", True)
    settings.setValue("login_autostart", False)
    settings.setValue("output_device", "alsa_output.test")

    fake_engine = FakeEngine()
    fake_router = FakeRouter()
    ctrl = Controller(fake_engine, settings, fake_router)
    ctrl.bootstrap()
    yield ctrl, fake_engine, fake_router
    ctrl.shutdown()


def test_start_applies_chain_and_default_route(qtbot, controller) -> None:
    ctrl, engine, router = controller

    with qtbot.waitSignal(ctrl.status_changed, timeout=4_000) as seen:
        ctrl.start_engine()

    assert seen.args[0].running is True
    assert engine.start_calls and engine.start_calls[0][1] == "alsa_output.test"
    assert router.made == ["sonicfx_sink"]
    assert ctrl.running is True
    assert ctrl.route_active is True
    assert ctrl.status.gap_ms is None


def test_band_edit_debounces_into_one_apply(qtbot, controller) -> None:
    ctrl, engine, _router = controller
    with qtbot.waitSignal(ctrl.status_changed, timeout=4_000):
        ctrl.start_engine()

    ctrl.set_band_gain(0, 4.5)
    ctrl.set_band_gain(0, 5.0)
    assert engine.apply_calls == []

    with qtbot.waitSignal(ctrl.status_changed, timeout=4_000):
        ctrl.apply_now()

    assert len(engine.apply_calls) == 1
    assert ctrl.bands[0].gain_db == 5.0
    assert ctrl.status.gap_ms == 123.0


def test_stop_restores_default_and_stops_engine(qtbot, controller) -> None:
    ctrl, engine, router = controller
    with qtbot.waitSignal(ctrl.status_changed, timeout=4_000):
        ctrl.start_engine()

    with qtbot.waitSignal(ctrl.status_changed, timeout=4_000):
        ctrl.stop_engine()

    assert router.restored == 1
    assert router.active is False
    assert engine.bypass_calls == 1
    assert ctrl.running is False


def test_start_failure_is_reported_and_engine_stopped(qtbot, controller) -> None:
    ctrl, engine, _router = controller
    engine.fail_start = True

    with qtbot.waitSignal(ctrl.error, timeout=4_000) as seen:
        ctrl.start_engine()

    assert "boom" in seen.args[0]
    assert ctrl.running is False


def test_route_failure_disables_preference(qtbot, controller) -> None:
    ctrl, _engine, router = controller
    router.fail = True

    with qtbot.waitSignal(ctrl.error, timeout=4_000):
        ctrl.start_engine()
    qtbot.waitUntil(lambda: ctrl.use_default is False, timeout=2_000)

    assert ctrl.running is True  # audio still runs, just not system-default
    assert ctrl.route_active is False


def test_band_gains_persist_as_json(qtbot, controller, tmp_path) -> None:
    ctrl, _engine, _router = controller
    ctrl.set_band_gain(1, -2.5)
    ctrl._settings.sync()

    raw = ctrl._settings.value("band_gains")
    values = json.loads(raw)
    assert values[1] == -2.5
    assert ctrl.is_custom() is True
