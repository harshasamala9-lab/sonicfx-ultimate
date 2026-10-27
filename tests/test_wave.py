"""Analysis and Wave-Studio tests (analysis, worker semantics, widgets)."""
from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from sonicfx.dsp.analysis import AnalysisConfig, AnalysisSnapshot, AudioAnalyzer
from sonicfx.pipewire.tap import TapError
from sonicfx.ui.wave import (
    MODES,
    PALETTES,
    WaveCard,
    WaveSettings,
    WaveStage,
    WaveWorker,
)

FS = 48_000


@pytest.fixture
def screen():
    app = QApplication.instance() or QApplication([])
    return app


@pytest.fixture
def wave_settings_env(monkeypatch, tmp_path):
    """Isolate QSettings writes in tmp_path (same trick as test_gui_smoke)."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return QSettings()


def _spin(seconds: float) -> None:
    app = QApplication.instance() or QApplication([])
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()


def _stereo(mono: np.ndarray, invert_right: bool = False) -> np.ndarray:
    right = -mono if invert_right else mono
    return np.stack([mono, right], axis=1)


# --------------------------------------------------------------------------
# analysis
# --------------------------------------------------------------------------
def test_analysis_config_bounds() -> None:
    with pytest.raises(ValueError):
        AnalysisConfig(fft_size=64)
    with pytest.raises(ValueError):
        AnalysisConfig(bins=0)
    with pytest.raises(ValueError):
        AnalysisConfig(envelope=0)
    with pytest.raises(ValueError):
        AnalysisConfig(attack_s=0.0)
    with pytest.raises(ValueError):
        AnalysisConfig(release_s=10.0)


def _tone(freq: float, seconds: float) -> np.ndarray:
    t = np.arange(int(seconds * FS)) / FS
    return np.sin(2 * np.pi * freq * t)


@pytest.mark.parametrize(
    ("freq", "band_index"),
    [(60.0, 0), (1000.0, 1), (5000.0, 2)],
)
@pytest.mark.parametrize("bins", [32, 64, 96])
def test_band_energy_follows_signal(freq, band_index, bins) -> None:
    analyzer = AudioAnalyzer(
        AnalysisConfig(sample_rate=FS, fft_size=1024, bins=bins, envelope=64)
    )
    snapshot = analyzer.process(_stereo(_tone(freq, 0.3)))
    bands = snapshot.bands
    other = [value for index, value in enumerate(bands) if index != band_index]
    assert bands[band_index] >= max(other)
    assert all(0.0 <= b <= 1.0 for b in bands)


def test_levels_report_amplitude() -> None:
    mono = 0.4 * _tone(800.0, 0.2)
    analyzer = AudioAnalyzer(AnalysisConfig(sample_rate=FS))
    snapshot = analyzer.process(_stereo(mono))
    left, right = snapshot.level
    assert 0.25 < left <= 0.45
    assert 0.25 < right <= 0.45


def test_transient_and_correlation() -> None:
    mono = _tone(300.0, 0.3)
    analyzer = AudioAnalyzer(AnalysisConfig(sample_rate=FS))
    snapshot = analyzer.process(_stereo(mono, invert_right=True))
    assert snapshot.correlation <= -0.9
    snapshot = analyzer.process(_stereo(mono))
    assert snapshot.correlation >= 0.9
    assert 0.0 <= snapshot.transient <= 1.0
    assert snapshot.silent is False


def test_silent_snapshot_is_quiet() -> None:
    analyzer = AudioAnalyzer(AnalysisConfig(sample_rate=FS))
    snapshot = analyzer.process(np.zeros((2048, 2), dtype=np.float64))
    assert snapshot.silent
    assert snapshot.bins.sum() == 0.0
    assert snapshot.waveform.sum() == 0.0


def test_analysis_state_never_grows_unbounded() -> None:
    """Feeding many blocks keeps every snapshot array at its configured size."""
    analyzer = AudioAnalyzer(AnalysisConfig(sample_rate=FS, bins=48, envelope=32))
    noise = (np.random.default_rng(7).standard_normal((512, 2)) * 0.2).astype(
        np.float64
    )
    for _ in range(64):
        snapshot = analyzer.process(noise)
    assert snapshot.bins.shape == (48,)
    assert snapshot.waveform.shape == (32,)
    assert np.isfinite(snapshot.bins).all()


def test_idle_snapshot_is_empty() -> None:
    snapshot = AnalysisSnapshot.idle()
    assert snapshot.silent
    assert snapshot.bins.shape == (64,)
    assert snapshot.waveform.shape == (64,)


# --------------------------------------------------------------------------
# WaveWorker semantics (fake tap factory; never PipeWire)
# --------------------------------------------------------------------------
class ScriptedFrameTap:
    """WaveWorker stand-in: ``read_frames()`` replays a script of blocks."""

    def __init__(self, target: str, script) -> None:
        self.target = target
        self._script = iter(script) if script is not None else None
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def read_frames(self, timeout: float = 0.15) -> np.ndarray | None:
        if self._script is None:
            return np.zeros((512, 2), dtype=np.float64)  # endless live stream
        return next(self._script, None)

    def stop(self) -> None:
        self.stopped = True


def test_wave_worker_emits_snapshots(qtbot) -> None:
    taps: list[ScriptedFrameTap] = []
    target = ["dev.monitor"]

    def factory(new_target: str) -> ScriptedFrameTap:
        tap = ScriptedFrameTap(new_target, None)
        taps.append(tap)
        return tap

    worker = WaveWorker(lambda: target[0], tap_factory=factory)
    records: list[AnalysisSnapshot] = []
    worker.snapshot.connect(records.append)
    worker.start()
    try:
        qtbot.waitUntil(lambda: len(records) >= 2, timeout=5_000)
        assert records[0].bins.shape == (64,)
        assert records[0].waveform.shape == (64,)
        assert taps and taps[0].started and taps[0].target == "dev.monitor"
    finally:
        target.clear()
        worker.stop()
        assert worker.wait(5_000) is True


def test_wave_worker_reports_idle_once_target_is_gone(qtbot) -> None:
    taps: list[ScriptedFrameTap] = []
    target = ["dev.beats"]

    def factory(new_target: str) -> ScriptedFrameTap:
        tap = ScriptedFrameTap(new_target, None)
        taps.append(tap)
        return tap

    worker = WaveWorker(lambda: target[0], tap_factory=factory)
    records: list[AnalysisSnapshot] = []
    worker.snapshot.connect(records.append)
    worker.start()
    try:
        qtbot.waitUntil(lambda: len(records) >= 2, timeout=5_000)
        # Engine off: exactly one final idle frame (seq stays 0), then quiet.
        target.clear()
        qtbot.waitUntil(
            lambda: any(record.seq == 0 and record.silent for record in records),
            timeout=5_000,
        )
        seen = len(records)
        _spin(0.4)
        assert len(records) == seen  # no snapshot spam while idle
        assert taps and taps[-1].stopped is True
    finally:
        worker.stop()
        assert worker.wait(5_000) is True


def test_wave_worker_quiet_without_target(qtbot) -> None:
    taps: list[ScriptedFrameTap] = []
    enabled = [False]

    def factory(new_target: str) -> ScriptedFrameTap:
        tap = ScriptedFrameTap(new_target, None)
        taps.append(tap)
        return tap

    worker = WaveWorker(
        lambda: "dev.monitor" if enabled[0] else "", tap_factory=factory
    )
    records: list[AnalysisSnapshot] = []
    worker.snapshot.connect(records.append)
    worker.start()
    try:
        _spin(0.3)
        assert records == []  # no target: the visualiser stays silent
        assert taps == []

        enabled[0] = True
        qtbot.waitUntil(
            lambda: len(records) >= 1 and bool(taps), timeout=5_000
        )
        assert taps and taps[0].target == "dev.monitor"
    finally:
        enabled[0] = False
        worker.stop()
        assert worker.wait(5_000) is True


def test_wave_worker_restarts_capture_on_refresh(qtbot) -> None:
    taps: list[ScriptedFrameTap] = []
    target = ["dev.monitor"]

    def factory(new_target: str) -> ScriptedFrameTap:
        tap = ScriptedFrameTap(new_target, None)
        taps.append(tap)
        return tap

    worker = WaveWorker(lambda: target[0], tap_factory=factory)
    records: list[AnalysisSnapshot] = []
    worker.snapshot.connect(records.append)
    worker.start()
    try:
        qtbot.waitUntil(lambda: bool(taps and taps[0].started), timeout=4_000)
        target[0] = "other.monitor"  # the user picked a different device
        worker.refresh()
        qtbot.waitUntil(lambda: len(taps) >= 2, timeout=4_000)

        assert taps[0].target == "dev.monitor"
        assert taps[0].stopped is True
        assert taps[1].target == "other.monitor"
    finally:
        target.clear()
        worker.stop()
        assert worker.wait(5_000) is True


def test_wave_worker_reconnects_after_stream_end(qtbot) -> None:
    """A dead capture must reconnect: the thread survives unplugged devices."""
    taps: list[ScriptedFrameTap] = []

    def factory(new_target: str) -> ScriptedFrameTap:
        tap = ScriptedFrameTap(new_target, [np.zeros((256, 2), dtype=np.float64),
                                            None])
        taps.append(tap)
        return tap

    worker = WaveWorker(lambda: "dev.monitor", tap_factory=factory)
    worker.start()
    try:
        qtbot.waitUntil(lambda: len(taps) >= 2, timeout=5_000)
        assert taps[0].stopped is True
        assert taps[1].started is True
        worker.stop()
    finally:
        worker.stop()
        assert worker.wait(5_000) is True


def test_wave_worker_retries_failed_start(qtbot) -> None:
    attempts = {"count": 0}

    def factory(new_target: str) -> ScriptedFrameTap:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise TapError("pw-record could not be started")
        return ScriptedFrameTap(new_target, None)

    worker = WaveWorker(lambda: "dev.monitor", tap_factory=factory)
    records: list[AnalysisSnapshot] = []
    worker.snapshot.connect(records.append)
    worker.start()
    try:
        qtbot.waitUntil(lambda: attempts["count"] >= 2, timeout=5_000)
        assert records and records[0].seq == 0  # failed attempt reports idle
        qtbot.waitUntil(lambda: any(r.seq > 0 for r in records), timeout=5_000)
    finally:
        worker.stop()
        assert worker.wait(5_000) is True


def test_wave_worker_retargets_if_capture_starts_on_stale_device() -> None:
    tap = ScriptedFrameTap("old.monitor", None)
    worker = WaveWorker(lambda: "new.monitor", tap_factory=lambda _target: tap)
    analyzer = AudioAnalyzer(AnalysisConfig(sample_rate=FS))

    assert worker._drain(tap, analyzer, "old.monitor") == "retarget"
    assert tap.started is False


def test_wave_worker_drain_refresh_and_stop_semantics() -> None:
    tap = ScriptedFrameTap("dev.monitor", None)
    target = ["dev.monitor"]
    worker = WaveWorker(lambda: target[0], tap_factory=lambda _target: tap)
    analyzer = AudioAnalyzer(AnalysisConfig(sample_rate=FS))

    worker.refresh()
    assert worker._drain(tap, analyzer, "dev.monitor") == "refresh"
    target[0] = "new.monitor"  # explicit retarget request, no flag needed
    assert worker._drain(tap, analyzer, "dev.monitor") == "retarget"
    assert tap.stopped is False  # the outer run() stops the attempt itself
    worker.stop()
    assert worker._drain(tap, analyzer, "new.monitor") == "stop"


# --------------------------------------------------------------------------
# widget-level smoke tests
# --------------------------------------------------------------------------
def test_wave_settings_clamps_invalid_values() -> None:
    settings = WaveSettings(
        mode=9, fps=120, palette="nope", speed=2.0, particles=-1.0
    ).clamp()
    assert settings.mode == 5
    assert settings.fps == 60
    assert settings.palette == "aurora"
    assert settings.speed == 1.0
    assert settings.particles == 0.0


def test_wave_card_modes_and_labels(screen, wave_settings_env) -> None:
    card = WaveCard(wave_settings_env)
    card.resize(620, 260)
    card.show()
    QApplication.processEvents()
    assert card._stage is not None
    assert len(MODES) == 6
    for index in range(len(MODES)):
        card._mode.setCurrentIndex(index)
        assert card._state.mode == index
        assert card._stage._settings.mode == index
    assert PALETTES
    card.deleteLater()


def test_wave_card_persists_settings(screen, wave_settings_env) -> None:
    card = WaveCard(wave_settings_env)
    card.show()
    QApplication.processEvents()
    card._speed.setValue(88)
    card._mode.setCurrentIndex(3)
    card._palette.setCurrentIndex(2)
    card.save()
    card._settings.sync()

    reloaded = WaveCard(wave_settings_env)
    reloaded.show()
    QApplication.processEvents()
    assert reloaded._state.speed == pytest.approx(0.88)
    assert reloaded._state.mode == 3
    assert reloaded._state.palette == "ember"
    card.deleteLater()
    reloaded.deleteLater()


def test_wave_card_persists_fps_off(screen, wave_settings_env) -> None:
    card = WaveCard(wave_settings_env)
    card.show()
    QApplication.processEvents()
    card._fps.setCurrentIndex(0)  # "Off"
    card.save()
    card._settings.sync()
    assert card._state.fps == 0
    assert not card._stage._timer.isActive()

    reloaded = WaveCard(wave_settings_env)
    reloaded.show()
    QApplication.processEvents()
    assert reloaded._state.fps == 0
    assert not reloaded._stage._timer.isActive()
    card.deleteLater()
    reloaded.deleteLater()


def test_wave_card_reset_restores_defaults(screen, wave_settings_env) -> None:
    card = WaveCard(wave_settings_env)
    card.show()
    QApplication.processEvents()
    card._mode.setCurrentIndex(5)
    card._speed.setValue(5)
    card._reset_settings()  # simulated button press
    QApplication.processEvents()
    assert card._state.mode == 0
    assert card._state.speed == pytest.approx(0.55)
    assert card._mode.currentText() == "Cinematic Wave"
    card.deleteLater()


def test_wave_stage_render_smoke(screen, wave_settings_env) -> None:
    card = WaveCard(wave_settings_env)
    card.resize(620, 260)
    card.show()
    QApplication.processEvents()
    for index in range(len(MODES)):
        card._mode.setCurrentIndex(index)
        QApplication.processEvents()
        image = card.grab()
        assert image.size().width() > 0
        assert not image.isNull()
    card.deleteLater()


def test_stage_fps_off_stops_render_timer(screen) -> None:
    stage = WaveStage()
    stage.resize(400, 200)
    stage.show()
    QApplication.processEvents()

    stage.set_settings(WaveSettings(fps=0))
    assert not stage._timer.isActive()

    stage.set_settings(WaveSettings(fps=30))
    assert stage._timer.isActive()

    stage.stop()
    assert not stage._timer.isActive()
    stage.deleteLater()


def test_stage_reduced_motion_scales_motion(screen) -> None:
    stage = WaveStage()
    stage.set_settings(WaveSettings(speed=1.0, glow=1.0, particles=1.0))
    assert stage._speed() == pytest.approx(1.0)
    assert stage._glow() == pytest.approx(1.0)
    dense = stage._density()

    stage.set_settings(
        WaveSettings(speed=1.0, glow=1.0, particles=1.0, reduced_motion=True)
    )
    assert stage._speed() == pytest.approx(0.35)
    assert stage._glow() == pytest.approx(0.5)
    assert stage._density() < dense
    stage.deleteLater()


def test_intensity_control_reaches_every_mode(screen, wave_settings_env) -> None:
    card = WaveCard(wave_settings_env)
    card.show()
    QApplication.processEvents()
    card._intensity.setValue(10)
    QApplication.processEvents()
    assert card._state.intensity == pytest.approx(0.10)
    assert card._stage._settings.intensity == pytest.approx(0.10)
    low_energy = card._stage._energy()
    card._intensity.setValue(90)
    QApplication.processEvents()
    assert card._stage._energy() > low_energy
    for mode in range(len(MODES)):  # rendering stays clean with strong gain
        card._mode.setCurrentIndex(mode)
        QApplication.processEvents()
        assert card._stage.grab().isNull() is False
    card.deleteLater()


def test_reactive_toggle_reaches_the_stage(screen, wave_settings_env) -> None:
    card = WaveCard(wave_settings_env)
    card.show()
    QApplication.processEvents()
    card._reduced.setChecked(True)
    card._reactive.setChecked(False)
    QApplication.processEvents()
    assert card._stage._settings.reduced_motion is True
    assert card._stage._settings.audio_reactive is False
    card.deleteLater()


def test_wave_card_shutdown_stops_worker_and_timer(
    screen, wave_settings_env
) -> None:
    card = WaveCard(wave_settings_env)
    card.show()
    QApplication.processEvents()
    card.start()
    QApplication.processEvents()
    assert card._stage._timer.isActive()

    worker = WaveWorker(
        lambda: "", tap_factory=lambda t: ScriptedFrameTap(t, None)
    )
    card.bind_worker(worker)
    worker.start()
    card.shutdown()
    assert worker.isFinished()
    assert not card._stage._timer.isActive()
    card.shutdown()  # idempotent close (closeEvent + explicit stop)
    card.deleteLater()
