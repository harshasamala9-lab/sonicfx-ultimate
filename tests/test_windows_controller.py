"""Windows controller/window tests using fakes, so no Windows API is needed."""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, QSettings, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from sonicfx.ui.main_window import MainWindow  # noqa: E402
from sonicfx.ui.theme import apply_theme  # noqa: E402
from sonicfx.windows.apo import EqualizerApo  # noqa: E402
from sonicfx.windows.controller import WindowsController  # noqa: E402
from sonicfx.windows.meter import WindowsOutput  # noqa: E402


class FakeMeter(QObject):
    levels = Signal(float, float)
    unavailable = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.started = False
        self.stopped = False
        self.device: str | None = None

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def set_device(self, device: str | None) -> None:
        self.device = device


class FakeAutostart:
    enabled = False
    calls: list[bool] = []

    @classmethod
    def is_enabled(cls) -> bool:
        return cls.enabled

    @classmethod
    def enable(cls) -> None:
        cls.enabled = True
        cls.calls.append(True)

    @classmethod
    def disable(cls) -> None:
        cls.enabled = False
        cls.calls.append(False)


def _controller(tmp_path: Path, monkeypatch) -> tuple[WindowsController, FakeMeter, EqualizerApo]:
    config_dir = tmp_path / "apo"
    config_dir.mkdir()
    (config_dir / "config.txt").write_text("Preamp: -2 dB\n", encoding="utf-8")
    apo = EqualizerApo(config_dir=config_dir)
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    settings.setValue("auto_start_engine", False)
    settings.setValue("login_autostart", False)
    meter = FakeMeter()
    FakeAutostart.enabled = False
    FakeAutostart.calls = []
    monkeypatch.setattr("sonicfx.windows.controller.autostart", FakeAutostart)
    controller = WindowsController(
        apo=apo,
        settings=settings,
        meter_factory=lambda: meter,
        speaker_provider=lambda: [
            WindowsOutput("speaker-1", "Test Speakers", True),
            WindowsOutput("speaker-2", "Bluetooth Headphones", False),
        ],
    )
    return controller, meter, apo


def test_windows_controller_applies_and_disables_include(tmp_path, qtbot, monkeypatch) -> None:
    controller, meter, apo = _controller(tmp_path, monkeypatch)
    assert qtbot is not None  # pytest-qt creates the Qt application
    controller.bootstrap()

    assert meter.started is True
    assert controller.preset_key == "best_all_in_one"
    assert controller.output == "speaker-1"

    controller.start_engine()
    assert controller.running is True
    assert apo.include_active() is True
    assert meter.device == "speaker-1"

    controller.select_preset("cinema_immersion")
    assert controller.preset_key == "cinema_immersion"
    controller.apply_now()
    text = (apo.config_dir / "sonicfx.txt").read_text(encoding="utf-8")
    assert "Filter 1: ON HPQ" in text
    assert "Preamp: -4.5 dB" in text

    controller.stop_engine()
    assert controller.running is False
    assert apo.include_active() is False
    assert "Preamp: -2 dB" in apo.config_file.read_text(encoding="utf-8")

    controller.shutdown()
    assert meter.stopped is True


def test_windows_controller_adopts_stale_include(tmp_path, qtbot, monkeypatch) -> None:
    controller, meter, apo = _controller(tmp_path, monkeypatch)
    assert qtbot is not None
    apo.apply(controller.bands)
    assert apo.include_active() is True

    controller.bootstrap()

    assert controller.running is True
    assert controller.status.running is True
    assert meter.device == "speaker-1"
    controller.stop_engine()
    assert apo.include_active() is False
    controller.shutdown()


def test_windows_controller_missing_apo_is_user_facing(tmp_path, qtbot, monkeypatch) -> None:
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    settings.setValue("auto_start_engine", False)
    settings.setValue("login_autostart", False)
    meter = FakeMeter()
    FakeAutostart.enabled = False
    monkeypatch.setattr("sonicfx.windows.controller.autostart", FakeAutostart)
    controller = WindowsController(
        apo=EqualizerApo(config_dir=tmp_path / "missing"),
        settings=settings,
        meter_factory=lambda: meter,
        speaker_provider=lambda: [],
    )
    errors: list[str] = []
    controller.error.connect(errors.append)

    controller.bootstrap()
    controller.start_engine()

    assert errors
    assert "Equalizer APO" in errors[0]
    assert "not found" in errors[0]
    assert controller.running is False
    controller.shutdown()


def test_main_window_uses_windows_labels_and_hides_routing(
    tmp_path, qtbot, monkeypatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    app = QApplication.instance() or QApplication([])
    apply_theme(app)
    controller, _meter, _apo = _controller(tmp_path, monkeypatch)
    window = MainWindow(controller)
    qtbot.addWidget(window)
    window.show()
    app.processEvents()

    assert window._supports_routing is False
    assert window._route_toggle.isHidden() is True
    assert window._apo_button is not None
    assert window._apo_button.text() == "Open Equalizer APO"
    assert window._stat_output.caption.text() == "METER OUTPUT"
    assert window._stat_route.caption.text() == "BACKEND"
    assert "Equalizer APO" in window._route_hint.text()

    controller.start_engine()
    app.processEvents()
    assert window._stat_route.value.text() == "APO active"
    assert window._power.text() == "Stop engine"

    controller.stop_engine()
    app.processEvents()
    assert window._stat_route.value.text() == "Off"
    assert window._power.text() == "Start engine"
    controller.shutdown()
