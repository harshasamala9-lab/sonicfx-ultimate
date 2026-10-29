"""Controller integration tests: threading, debounce, routing safety, metering."""
from __future__ import annotations

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import QObject, QSettings, Signal  # noqa: E402

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


class FakeMeterWorker(QObject):
    """MeterWorker stand-in: same lifecycle surface, never captures."""

    levels = Signal(float, float)
    instances: list[FakeMeterWorker] = []

    def __init__(self, target_provider) -> None:
        super().__init__()
        self.target_provider = target_provider
        FakeMeterWorker.instances.append(self)
        self.start_calls = 0
        self.refresh_calls = 0
        self.stopped = False

    def start(self) -> None:
        self.start_calls += 1

    def refresh(self) -> None:
        self.refresh_calls += 1

    def stop(self) -> None:
        self.stopped = True

    def wait(self, _ms: int = 0) -> bool:
        return True


@pytest.fixture
def controller(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(
        controller_module,
        "list_sinks",
        lambda: [
            SinkInfo(
                "alsa_output.test",
                1,
                "Test Speakers",
                True,
                form_factor="speaker",
                channels=2,
            )
        ],
    )
    monkeypatch.setattr(controller_module, "default_sink_name", lambda: None)
    monkeypatch.setattr(controller_module.autostart, "is_enabled", lambda: False)
    monkeypatch.setattr(controller_module.autostart, "enable", lambda: None)
    monkeypatch.setattr(controller_module.autostart, "disable", lambda: None)
    monkeypatch.setattr(controller_module, "MeterWorker", FakeMeterWorker)
    FakeMeterWorker.instances = []

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


def test_meter_worker_starts_and_tracks_levels(qtbot, controller) -> None:
    ctrl, _engine, _router = controller
    worker = FakeMeterWorker.instances[-1]
    assert worker.start_calls == 1
    assert worker.target_provider() == ""  # engine stopped: meter off

    seen: list[tuple[float, float]] = []
    ctrl.meter_changed.connect(lambda left, right: seen.append((left, right)))
    with qtbot.waitSignal(ctrl.status_changed, timeout=4_000):
        ctrl.start_engine()
    assert worker.refresh_calls >= 1
    assert worker.target_provider() == "alsa_output.test.monitor"

    with qtbot.waitSignal(ctrl.meter_changed, timeout=2_000):
        worker.levels.emit(0.5, 0.25)
    assert seen[-1] == pytest.approx((0.5, 0.25))
    assert ctrl.meter_peak_db[0] == pytest.approx(-6.02, abs=0.02)
    assert ctrl.meter_peak_db[1] == pytest.approx(-12.04, abs=0.02)

    qtbot.waitUntil(lambda: len(seen) >= 2, timeout=2_000)  # decay tick
    assert seen[-1][0] < 0.5


def test_meter_resets_when_engine_stops(qtbot, controller) -> None:
    ctrl, _engine, _router = controller
    worker = FakeMeterWorker.instances[-1]
    with qtbot.waitSignal(ctrl.status_changed, timeout=4_000):
        ctrl.start_engine()
    with qtbot.waitSignal(ctrl.meter_changed, timeout=2_000):
        worker.levels.emit(0.8, 0.7)

    with qtbot.waitSignal(ctrl.status_changed, timeout=4_000):
        ctrl.stop_engine()

    assert ctrl.meter_peak_db == (-60.0, -60.0)

    # A capture result already queued when the engine stopped must not wake a
    # meter that was just reset to silence.
    worker.levels.emit(0.8, 0.7)
    assert ctrl.meter_peak_db == (-60.0, -60.0)


def test_meter_target_switches_with_output(qtbot, controller, monkeypatch) -> None:
    ctrl, _engine, _router = controller
    worker = FakeMeterWorker.instances[-1]
    monkeypatch.setattr(
        controller_module,
        "list_sinks",
        lambda: [
            SinkInfo("alsa_output.test", 1, "Test Speakers", True),
            SinkInfo("bluez.speaker", 2, "JBL Flip 5", False),
        ],
    )
    with qtbot.waitSignal(ctrl.status_changed, timeout=4_000):
        ctrl.start_engine()

    ctrl.set_output("bluez.speaker")

    assert worker.target_provider() == "bluez.speaker.monitor"
    assert worker.refresh_calls >= 2


def test_refresh_rewires_running_engine_after_device_disappears(
    qtbot, controller, monkeypatch
) -> None:
    ctrl, engine, _router = controller
    with qtbot.waitSignal(ctrl.status_changed, timeout=4_000):
        ctrl.start_engine()
    starts = len(engine.apply_calls)

    monkeypatch.setattr(
        controller_module,
        "list_sinks",
        lambda: [SinkInfo("bluez.replacement", 9, "Replacement Speaker", False)],
    )
    with qtbot.waitSignal(ctrl.status_changed, timeout=4_000):
        ctrl.refresh_sinks()
        ctrl.apply_now()

    assert ctrl.output == "bluez.replacement"
    assert len(engine.apply_calls) == starts + 1
    assert engine.apply_calls[-1][1] == "bluez.replacement"


def test_meter_worker_stops_with_controller(controller) -> None:
    ctrl, _engine, _router = controller
    worker = FakeMeterWorker.instances[-1]
    ctrl.shutdown()
    assert worker.stopped is True


def test_cinema_spatial_auto_resolves_speaker_and_builds_spec(
    qtbot, controller
) -> None:
    ctrl, engine, _router = controller
    ctrl.select_preset("cinema_spatial")

    state = ctrl.spatial_state()
    assert state.enabled is True
    assert state.mode == "auto"
    assert state.effective_mode == "speaker"
    assert state.room == "medium"
    assert state.amount == 75

    with qtbot.waitSignal(ctrl.status_changed, timeout=4_000):
        ctrl.start_engine()

    spec = engine.start_calls[-1][0]
    assert spec.spatial is not None
    assert spec.spatial.mode.value == "speaker"
    assert spec.spatial.room.value == "medium"
    assert spec.spatial.amount == pytest.approx(0.75)
    assert spec.spatial.limiter is ctrl._limiter_installed


def test_spatial_controls_emit_and_persist(qtbot, controller) -> None:
    ctrl, _engine, _router = controller
    ctrl.select_preset("cinema_spatial")

    with qtbot.waitSignal(ctrl.spatial_changed, timeout=2_000):
        ctrl.set_spatial_room("large")
    with qtbot.waitSignal(ctrl.spatial_changed, timeout=2_000):
        ctrl.set_spatial_amount(150)
    with qtbot.waitSignal(ctrl.spatial_changed, timeout=2_000):
        ctrl.set_spatial_mode("speaker")

    assert ctrl.spatial_room == "large"
    assert ctrl.spatial_amount == 100
    assert ctrl.spatial_mode == "speaker"
    assert ctrl._settings.value("spatial_room") == "large"
    assert int(ctrl._settings.value("spatial_amount")) == 100
    assert ctrl._settings.value("spatial_mode") == "speaker"


def test_binaural_mode_requires_and_uses_a_sofa(
    qtbot, controller, monkeypatch, tmp_path
) -> None:
    ctrl, engine, _router = controller
    ctrl.select_preset("cinema_spatial")
    sofa = tmp_path / "custom.sofa"
    sofa.write_bytes(b"SOFA")
    monkeypatch.setattr(controller_module, "find_sofa_file", lambda: sofa)

    with qtbot.waitSignal(ctrl.spatial_changed, timeout=2_000):
        ctrl.set_spatial_mode("binaural")
    assert ctrl.spatial_mode == "binaural"
    assert ctrl.spatial_available is True

    with qtbot.waitSignal(ctrl.status_changed, timeout=4_000):
        ctrl.start_engine()
    spec = engine.start_calls[-1][0]
    assert spec.spatial is not None
    assert spec.spatial.mode.value == "binaural"
    assert spec.spatial.sofa_path == str(sofa)


def test_binaural_without_hrtf_is_rejected(qtbot, controller, monkeypatch) -> None:
    ctrl, _engine, _router = controller
    ctrl.select_preset("cinema_spatial")
    monkeypatch.setattr(controller_module, "find_sofa_file", lambda: None)

    with qtbot.waitSignal(ctrl.error, timeout=2_000) as seen:
        ctrl.set_spatial_mode("binaural")

    assert "SOFA" in seen.args[0]
    assert ctrl.spatial_mode == "auto"


def test_auto_mode_follows_output_metadata(qtbot, controller, monkeypatch) -> None:
    ctrl, _engine, _router = controller
    ctrl.select_preset("cinema_spatial")
    assert ctrl.spatial_effective_mode.value == "speaker"

    monkeypatch.setattr(
        controller_module,
        "list_sinks",
        lambda: [
            SinkInfo(
                "alsa_output.headphones",
                3,
                "Built-in Headphones",
                False,
                icon_name="audio-headphones",
                form_factor="headset",
                channels=2,
            )
        ],
    )
    with qtbot.waitSignal(ctrl.spatial_changed, timeout=2_000):
        ctrl.set_output("alsa_output.headphones")

    assert ctrl.output == "alsa_output.headphones"
    assert ctrl.spatial_effective_mode.value == "binaural"


def test_speaker_mode_keeps_sofa_available_for_later_switch(
    qtbot, controller, monkeypatch, tmp_path
) -> None:
    ctrl, engine, _router = controller
    ctrl.select_preset("cinema_spatial")
    sofa = tmp_path / "custom.sofa"
    sofa.write_bytes(b"SOFA")
    monkeypatch.setattr(controller_module, "find_sofa_file", lambda: sofa)

    with qtbot.waitSignal(ctrl.spatial_changed, timeout=2_000):
        ctrl.set_spatial_mode("speaker")
    assert ctrl.spatial_available is True

    with qtbot.waitSignal(ctrl.status_changed, timeout=4_000):
        ctrl.start_engine()

    # Building a speaker graph must not erase the HRTF discovery state; the
    # window would otherwise disable the binaural option after one speaker run.
    assert ctrl.spatial_available is True
    assert ctrl.spatial_state().available is True
    spec = engine.start_calls[-1][0]
    assert spec.spatial is not None
    assert spec.spatial.mode.value == "speaker"


def test_headphones_preset_uses_binaural_on_every_output(
    qtbot, controller, monkeypatch, tmp_path
) -> None:
    ctrl, engine, _router = controller
    # The fixture output is a speaker, which would normally make Auto choose
    # speaker mode.  The Headphones preset must ignore that and pin binaural.
    sofa = tmp_path / "custom.sofa"
    sofa.write_bytes(b"SOFA")
    monkeypatch.setattr(controller_module, "find_sofa_file", lambda: sofa)

    with qtbot.waitSignal(ctrl.spatial_changed, timeout=2_000):
        ctrl.select_preset("headphones")

    state = ctrl.spatial_state()
    assert state.enabled is True
    assert state.fixed_mode is True
    assert state.available is True
    assert state.mode == "binaural"
    assert state.effective_mode == "binaural"
    assert ctrl.spatial_effective_mode.value == "binaural"

    # A manual mode change cannot silently take the Headphones preset off the
    # HRTF path; the window also disables the selector.
    ctrl.set_spatial_mode("speaker")
    assert ctrl.spatial_effective_mode.value == "binaural"

    with qtbot.waitSignal(ctrl.status_changed, timeout=4_000):
        ctrl.start_engine()
    spec = engine.start_calls[-1][0]
    assert spec.spatial is not None
    assert spec.spatial.mode.value == "binaural"
    assert spec.spatial.sofa_path == str(sofa)

    # Cinema Spatial keeps the user's own Auto/manual choice.
    ctrl.select_preset("cinema_spatial")
    assert ctrl.spatial_state().fixed_mode is False
