"""Offscreen GUI smoke tests: real widgets, fake controller (never PipeWire)."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from PySide6.QtCore import QObject, Qt, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from sonicfx.audio.engine import EngineStatus  # noqa: E402
from sonicfx.pipewire.wiring import SinkInfo  # noqa: E402
from sonicfx.profiles.presets import get_preset  # noqa: E402
from sonicfx.ui.main_window import MainWindow  # noqa: E402
from sonicfx.ui.theme import apply_theme  # noqa: E402


class FakeController(QObject):
    status_changed = Signal(object)
    state_changed = Signal(bool)
    busy_changed = Signal(bool)
    error = Signal(str)
    preset_changed = Signal(str)
    bands_changed = Signal(object)
    custom_changed = Signal(bool)
    sinks_changed = Signal(object)
    output_changed = Signal(str)
    route_changed = Signal(bool)
    use_default_changed = Signal(bool)
    login_autostart_changed = Signal(bool)
    meter_changed = Signal(float, float)

    def __init__(self) -> None:
        super().__init__()
        self._preset_key = "songs"
        self._output = "alsa_output.test"
        self._running = True
        self._busy = False
        self._use_default = True
        self.gains: list[tuple[int, float]] = []
        self.enabled_calls: list[bool] = []
        self.shutdown_calls = 0
        self.status = EngineStatus(True, 123, "sonicfx_sink", 3, gap_ms=144.6)

    @property
    def preset_key(self) -> str:
        return self._preset_key

    @property
    def output(self) -> str:
        return self._output

    @property
    def running(self) -> bool:
        return self._running

    @property
    def busy(self) -> bool:
        return self._busy

    @property
    def use_default(self) -> bool:
        return self._use_default

    def bootstrap(self) -> None:
        self.sinks_changed.emit(
            [SinkInfo("alsa_output.test", 7, "Test Speakers", True)]
        )
        self.output_changed.emit(self._output)
        self.preset_changed.emit(self._preset_key)
        self.bands_changed.emit(get_preset(self._preset_key).bands)
        self.custom_changed.emit(False)
        self.use_default_changed.emit(self._use_default)
        self.login_autostart_changed.emit(False)
        self.route_changed.emit(True)
        self.status_changed.emit(self.status)
        self.state_changed.emit(self._running)
        self.meter_changed.emit(0.0, 0.0)  # engine running, digital silence

    def select_preset(self, key: str) -> None:
        self._preset_key = key
        self.preset_changed.emit(key)
        self.bands_changed.emit(get_preset(key).bands)
        self.custom_changed.emit(False)

    def reset_preset(self) -> None:
        self.select_preset(self._preset_key)

    def set_band_gain(self, index: int, gain: float) -> None:
        self.gains.append((index, gain))

    def set_output(self, name: str) -> None:
        self._output = name
        self.output_changed.emit(name)

    def refresh_sinks(self) -> None:
        self.bootstrap()

    def set_enabled(self, enabled: bool) -> None:
        self.enabled_calls.append(enabled)
        self._running = enabled
        self.state_changed.emit(enabled)

    def set_use_default(self, enabled: bool) -> None:
        self._use_default = enabled
        self.use_default_changed.emit(enabled)

    def set_login_autostart(self, enabled: bool) -> None:
        self.login_autostart_changed.emit(enabled)

    def shutdown(self) -> None:
        self.shutdown_calls += 1


def _window(qtbot, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    app = QApplication.instance() or QApplication([])
    apply_theme(app)
    controller = FakeController()
    window = MainWindow(controller)
    qtbot.addWidget(window)
    window.show()
    app.processEvents()
    return controller, window


def test_window_reflects_engine_and_preset(qtbot, monkeypatch, tmp_path) -> None:
    controller, window = _window(qtbot, monkeypatch, tmp_path)

    assert window.windowTitle() == "SonicFX Ultimate"
    assert window._chip.text() == "● Running"
    assert window._power.text() == "Stop engine"
    assert window._stat_gap.value.text() == "145 ms"
    assert window._stat_route.value.text() == "SonicFX"
    assert window._preset_name.text() == "Songs / Music"
    assert len(window._band_rows) == len(get_preset("songs").bands)
    assert window._route_toggle.isChecked() is True


def test_band_slider_calls_controller(qtbot, monkeypatch, tmp_path) -> None:
    controller, window = _window(qtbot, monkeypatch, tmp_path)
    row = window._band_rows[0]
    before = len(controller.gains)

    row.slider.setValue(row.slider.value() + 2)

    assert len(controller.gains) > before
    assert controller.gains[-1][0] == 0
    assert controller.gains[-1][1] == row.slider.value() / 2.0


def test_preset_click_rebuilds_band_rows(qtbot, monkeypatch, tmp_path) -> None:
    controller, window = _window(qtbot, monkeypatch, tmp_path)

    controller.select_preset("movies")
    QApplication.processEvents()

    assert window._preset_name.text() == "Movies / Cinema"
    assert len(window._band_rows) == len(get_preset("movies").bands)
    assert window._preset_cards["movies"].isChecked() is True


def test_power_and_routing_toggles_are_wired(qtbot, monkeypatch, tmp_path) -> None:
    controller, window = _window(qtbot, monkeypatch, tmp_path)

    qtbot.mouseClick(window._power, Qt.MouseButton.LeftButton)
    assert controller.enabled_calls == [False]

    window._route_toggle.setChecked(False)
    assert controller.use_default is False
    assert "normal output" in window._route_hint.text()


def test_preview_screenshot_renders(qtbot, monkeypatch, tmp_path) -> None:
    _controller, window = _window(qtbot, monkeypatch, tmp_path)
    path = tmp_path / "window.png"
    assert window.grab().save(str(path)) is True
    assert path.stat().st_size > 10_000


def test_peak_meter_renders_live_levels(qtbot, monkeypatch, tmp_path) -> None:
    controller, window = _window(qtbot, monkeypatch, tmp_path)

    assert window._stat_peak.value.text() == "-∞ dB"  # silence at boot
    controller.meter_changed.emit(0.5, 0.25)
    QApplication.processEvents()

    assert window._meter._levels == pytest.approx((0.5, 0.25))
    assert window._stat_peak.value.text() == "-6.0 dB"
    assert "right -12.0 dB" in window._stat_peak.value.toolTip()

    # A device running above 100 % volume clips the monitor copy; the bar
    # stops at full scale while the readout keeps the honest over-0dB value.
    controller.meter_changed.emit(1.5, 0.4)
    QApplication.processEvents()

    assert window._meter._levels == pytest.approx((1.0, 0.4))
    assert window._stat_peak.value.text() == "+3.5 dB"


from sonicfx.ui.controller import SpatialUiState  # noqa: E402


class SpatialFakeController(FakeController):
    """Fake controller exposing the Linux Cinema Spatial surface."""

    spatial_changed = Signal(object)
    supports_spatial = True

    def __init__(self) -> None:
        super().__init__()
        self._preset_key = "cinema_spatial"
        self._spatial_mode = "auto"
        self._spatial_room = "medium"
        self._spatial_amount = 75
        self.spatial_calls: list[tuple[str, object]] = []

    def _state(self) -> SpatialUiState:
        effective = (
            "binaural" if self._spatial_mode == "binaural" else "speaker"
        )
        return SpatialUiState(
            enabled=get_preset(self._preset_key).spatial,
            mode=self._spatial_mode,
            effective_mode=effective,
            room=self._spatial_room,
            amount=self._spatial_amount,
            available=True,
            limiter=True,
        )

    def bootstrap(self) -> None:
        super().bootstrap()
        self.spatial_changed.emit(self._state())

    def select_preset(self, key: str) -> None:
        self._preset_key = key
        super().select_preset(key)
        self.spatial_changed.emit(self._state())

    def set_spatial_mode(self, mode: str) -> None:
        self._spatial_mode = mode
        self.spatial_calls.append(("mode", mode))
        self.spatial_changed.emit(self._state())

    def set_spatial_room(self, room: str) -> None:
        self._spatial_room = room
        self.spatial_calls.append(("room", room))
        self.spatial_changed.emit(self._state())

    def set_spatial_amount(self, amount: int) -> None:
        self._spatial_amount = int(amount)
        self.spatial_calls.append(("amount", int(amount)))
        self.spatial_changed.emit(self._state())


def _spatial_window(qtbot, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    app = QApplication.instance() or QApplication([])
    apply_theme(app)
    controller = SpatialFakeController()
    window = MainWindow(controller)
    qtbot.addWidget(window)
    window.show()
    app.processEvents()
    return controller, window


def test_spatial_card_visible_for_cinema_spatial(qtbot, monkeypatch, tmp_path) -> None:
    controller, window = _spatial_window(qtbot, monkeypatch, tmp_path)

    assert window._spatial_card.isVisible() is True
    assert window._spatial_effective.text() == "Stereo speakers · transaural"
    assert window._spatial_mode_combo.currentData() == "auto"
    assert window._spatial_room_combo.currentData() == "medium"
    assert window._spatial_amount.value() == 75
    assert window._spatial_amount_value.text() == "75%"
    assert "Stereo speakers" in window._spatial_hint.text()
    assert controller.spatial_calls == []


def test_spatial_controls_call_controller(qtbot, monkeypatch, tmp_path) -> None:
    controller, window = _spatial_window(qtbot, monkeypatch, tmp_path)

    index = window._spatial_mode_combo.findData("binaural")
    window._spatial_mode_combo.setCurrentIndex(index)
    index = window._spatial_room_combo.findData("large")
    window._spatial_room_combo.setCurrentIndex(index)
    window._spatial_amount.setValue(40)
    QApplication.processEvents()

    assert ("mode", "binaural") in controller.spatial_calls
    assert ("room", "large") in controller.spatial_calls
    assert ("amount", 40) in controller.spatial_calls
    assert window._spatial_amount_value.text() == "40%"
    assert window._spatial_effective.text() == "Headphones · binaural"
    assert "Headphones" in window._spatial_hint.text()


def test_spatial_card_hides_for_tonal_preset(qtbot, monkeypatch, tmp_path) -> None:
    controller, window = _spatial_window(qtbot, monkeypatch, tmp_path)
    assert window._spatial_card.isVisible() is True

    controller.select_preset("movies")
    QApplication.processEvents()

    assert window._spatial_card.isVisible() is False
