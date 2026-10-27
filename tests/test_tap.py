"""Peak tap tests: target guard, argv, peak parsing, pipe-backed read loop."""
from __future__ import annotations

import os
import struct

import pytest

from sonicfx.pipewire import tap as tap_module
from sonicfx.pipewire.tap import LevelTap, TapError, monitor_target, peak_levels


def _stereo(left, right):
    return b"".join(
        struct.pack("<ff", left, right) for left, right in zip(left, right, strict=True)
    )


def test_monitor_target_appends_suffix() -> None:
    assert monitor_target("bluez_output.XX.1") == "bluez_output.XX.1.monitor"
    assert monitor_target("sonicfx_sink") == "sonicfx_sink.monitor"


def test_constructor_refuses_non_monitor_targets() -> None:
    # Learned live: a plain sink name yields saturated garbage from pw-record.
    with pytest.raises(TapError, match="monitor"):
        LevelTap("sonicfx_sink")


def test_command_pins_format_rate_and_stdout() -> None:
    argv = LevelTap.command("dev.monitor")
    assert argv == [
        "pw-record",
        "--target",
        "dev.monitor",
        "--format",
        "f32",
        "--channels",
        "2",
        "--rate",
        "48000",
        "--raw",
        "-",
    ]


def test_peak_levels_finds_stereo_peaks() -> None:
    chunk = _stereo([0.5, -0.9, 0.1], [0.25, 0.8, -0.7])
    assert peak_levels(chunk) == pytest.approx((0.9, 0.8))


def test_peak_levels_keeps_headroom_and_ignores_partial_frame() -> None:
    full = _stereo([2.5, 1.2], [0.3, 0.4])
    left, right = peak_levels(full + b"\x1c")  # 4 trailing bytes are not a frame
    assert left == pytest.approx(2.5)  # honest over-0dBFS peaks (device > 100 %)
    assert right == pytest.approx(0.4)

    hard = peak_levels(_stereo([10.0], [1.0]))  # display headroom clamp (+12 dB)
    assert hard == pytest.approx((4.0, 1.0))

    assert peak_levels(b"") == (0.0, 0.0)
    assert peak_levels(bytes(4)) == (0.0, 0.0)


class _FakePopen:
    """A pipe-backed stand-in: pw-record writes to `wfd`, we read `stdout`."""

    def __init__(self) -> None:
        self.rfd, self.wfd = os.pipe()
        self.stdout = os.fdopen(self.rfd, "rb", buffering=0)
        self.stderr = None
        self.running = True
        self.terminated = False

    def write(self, data: bytes) -> int:
        return os.write(self.wfd, data)

    def poll(self):
        return None if self.running else 0

    def terminate(self) -> None:
        self.running = False
        self.terminated = True

    def wait(self, timeout=None):
        return 0

    def close_writer(self) -> None:
        if self.wfd is not None:
            os.close(self.wfd)
            self.wfd = None


@pytest.fixture
def fake_popen(monkeypatch):
    holder: dict[str, _FakePopen] = {}

    def factory(*args, **kwargs):
        holder["proc"] = _FakePopen()
        return holder["proc"]

    monkeypatch.setattr(tap_module.subprocess, "Popen", factory)
    return holder


def test_read_reports_peaks_silence_and_eof(fake_popen) -> None:
    proc_holder = fake_popen
    tap = LevelTap("dev.monitor")
    tap.start()
    proc = proc_holder["proc"]

    proc.write(_stereo([0.4, 0.9], [0.6, 0.2]))
    assert tap.read(timeout=1.0) == pytest.approx((0.9, 0.6))

    assert tap.read(timeout=0.05) == (0.0, 0.0)  # select timeout = silence

    proc.close_writer()  # pw-record exited: stream over
    assert tap.read(timeout=1.0) is None

    proc.terminate()
    # The tap is closed after EOF: reopening needs a new tap instance.
    with pytest.raises(TapError):
        tap.read(timeout=0.05)


def test_stop_is_idempotent_and_process_is_killed(fake_popen) -> None:
    tap = LevelTap("dev.monitor")
    tap.start()
    proc = fake_popen["proc"]
    tap.stop()
    assert proc.terminated is True
    assert tap._proc is None
    tap.stop()  # second call is a no-op


def test_read_frames_respects_silence_eof_and_bound(fake_popen) -> None:
    """``read_frames`` yields whole ``(frames, channels)`` blocks or ``None``."""
    proc_holder = fake_popen
    tap = LevelTap("dev.monitor")
    tap.start()
    proc = proc_holder["proc"]

    # Nothing written yet: a quiet monitor reports an empty frame block.
    assert tap.read_frames(timeout=0.05).shape == (0, 2)

    frames = _stereo([0.5, -0.25, 0.75], [0.125, -0.625, 0.25])
    proc.write(frames)
    block = tap.read_frames(timeout=1.0)
    assert block.shape == (3, 2)
    assert block[0, 0] == pytest.approx(0.5)
    assert block[1, 1] == pytest.approx(-0.625)
    assert block[2, 0] == pytest.approx(0.75)

    # A trailing partial frame is dropped, never passed on as garbage.
    proc.write(_stereo([0.3], [0.1]) + b"\x42\x00\x00\x00")
    block = tap.read_frames(timeout=1.0)
    assert block.shape == (1, 2)
    assert block[0, 0] == pytest.approx(0.3)
    assert block[0, 1] == pytest.approx(0.1)

    proc.close_writer()  # pw-record exited: stream over
    assert tap.read_frames(timeout=1.0) is None
    with pytest.raises(TapError):
        tap.read_frames(timeout=0.05)


def test_read_frames_rejects_unstarted_tap() -> None:
    tap = LevelTap("dev.monitor")
    with pytest.raises(TapError):
        tap.read_frames(timeout=0.05)
