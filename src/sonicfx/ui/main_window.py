"""Main window for SonicFX Ultimate (presentation only)."""
from __future__ import annotations

import sys

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from sonicfx.dsp.filters import BiquadBand
from sonicfx.logging_setup import get_logger
from sonicfx.pipewire.tap import monitor_target
from sonicfx.profiles.presets import PRESETS, get_preset
from sonicfx.ui.theme import repolish
from sonicfx.ui.wave import WaveCard, WaveWorker
from sonicfx.ui.widgets import (
    BandRow,
    CurveView,
    LevelMeter,
    LogoMark,
    PresetCard,
    Stat,
    StatusChip,
    amplitude_db,
    format_level_db,
)

log = get_logger("ui.window")


class MainWindow(QMainWindow):
    """The complete one-window UI; all behaviour lives in the controller."""

    def __init__(self, controller, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._c = controller
        self._settings = QSettings("SonicFX", "SonicFX Ultimate")
        self._updating = False
        self._running = bool(getattr(controller, "running", False))
        self._busy = bool(getattr(controller, "busy", False))
        self._route_active = False
        self._use_default = bool(getattr(controller, "use_default", True))
        self._supports_routing = bool(getattr(controller, "supports_routing", True))
        self._supports_spatial = bool(getattr(controller, "supports_spatial", False))
        self._spatial_state = None
        self._login_autostart = False
        self._preset_key = str(getattr(controller, "preset_key", "flat"))
        self._error_text = ""
        self._status = None
        self._sink_desc: dict[str, str] = {}
        self._band_rows: list[BandRow] = []
        self._preset_cards: dict[str, PresetCard] = {}

        self.setWindowTitle("SonicFX Ultimate")
        self.setMinimumSize(1000, 700)
        self.resize(1180, 730)
        self._build()
        self._connect()
        self._restore_geometry()
        self._c.bootstrap()

    # -- construction ------------------------------------------------------
    def _build(self) -> None:
        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(24, 18, 24, 14)
        outer.setSpacing(13)

        outer.addLayout(self._build_header())

        self._error_banner = QLabel()
        self._error_banner.setObjectName("ErrorBanner")
        self._error_banner.setWordWrap(True)
        self._error_banner.setVisible(False)
        outer.addWidget(self._error_banner)

        body = QHBoxLayout()
        body.setSpacing(16)
        body.addWidget(self._build_left_column())
        body.addLayout(self._build_right_column(), 1)
        outer.addLayout(body, 1)

        self._wave_worker = WaveWorker(self._wave_target)
        self._wave_card.bind_worker(self._wave_worker)

        footer = QHBoxLayout()
        footer.setSpacing(12)
        note = QLabel(
            str(
                getattr(
                    self._c,
                    "footer_note",
                    "All DSP runs inside PipeWire's real-time thread · stop the "
                    "engine to bypass it completely",
                )
            )
        )
        note.setObjectName("Faint")
        footer.addWidget(note)
        footer.addStretch(1)
        version = QLabel("v1.0 · free & local")
        version.setObjectName("Faint")
        footer.addWidget(version)
        outer.addLayout(footer)

        logo = LogoMark(64)
        self.setWindowIcon(QIcon(logo.grab()))
        self._refresh_status_ui()
        self._refresh_route_hint()

    def _build_header(self) -> QHBoxLayout:
        header = QHBoxLayout()
        header.setSpacing(13)

        header.addWidget(LogoMark(42))

        titles = QVBoxLayout()
        titles.setSpacing(1)
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title = QLabel("SonicFX")
        title.setObjectName("AppTitle")
        title_row.addWidget(title)
        tag = QLabel("ULTIMATE")
        tag.setObjectName("BrandTag")
        title_row.addWidget(tag)
        title_row.addStretch(1)
        titles.addLayout(title_row)
        subtitle = QLabel(
            str(
                getattr(
                    self._c,
                    "subtitle",
                    "Cinema-style clarity for everything you play — free, local, "
                    "real-time.",
                )
            )
        )
        subtitle.setObjectName("AppSubtitle")
        titles.addWidget(subtitle)
        header.addLayout(titles)
        header.addStretch(1)

        self._chip = StatusChip()
        self._chip.setAccessibleName("Engine status")
        header.addWidget(self._chip, 0, Qt.AlignmentFlag.AlignVCenter)

        self._power = QPushButton("Start engine")
        self._power.setObjectName("Primary")
        self._power.setCursor(Qt.CursorShape.PointingHandCursor)
        self._power.setMinimumWidth(132)
        self._power.setAccessibleName("Start or stop the audio engine")
        header.addWidget(self._power, 0, Qt.AlignmentFlag.AlignVCenter)
        return header

    def _build_left_column(self) -> QWidget:
        left = QWidget()
        left.setFixedWidth(350)
        column = QVBoxLayout(left)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(14)

        presets_card = QFrame()
        presets_card.setObjectName("Card")
        presets = QVBoxLayout(presets_card)
        presets.setContentsMargins(16, 14, 16, 14)
        presets.setSpacing(8)
        heading = QLabel("Enhancement")
        heading.setObjectName("CardTitle")
        presets.addWidget(heading)
        hint = QLabel("Pick a starting point, then fine-tune below.")
        hint.setObjectName("Muted")
        presets.addWidget(hint)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        container = QWidget()
        self._presets_layout = QVBoxLayout(container)
        self._presets_layout.setContentsMargins(0, 4, 4, 2)
        self._presets_layout.setSpacing(6)
        scroll.setWidget(container)
        presets.addWidget(scroll, 1)

        self._preset_group = QButtonGroup(self)
        self._preset_group.setExclusive(True)
        for preset in PRESETS:
            card = PresetCard(preset)
            card.clicked.connect(
                lambda _checked=False, key=preset.key: self._preset_clicked(key)
            )
            self._preset_cards[preset.key] = card
            self._preset_group.addButton(card)
            self._presets_layout.addWidget(card)
        self._presets_layout.addStretch(1)
        column.addWidget(presets_card, 1)

        routing_card = QFrame()
        routing_card.setObjectName("Card")
        routing = QVBoxLayout(routing_card)
        routing.setContentsMargins(16, 13, 16, 13)
        routing.setSpacing(7)
        route_title = QLabel(
            str(getattr(self._c, "routing_title", "System routing"))
        )
        route_title.setObjectName("CardTitle")
        routing.addWidget(route_title)

        self._route_toggle = QCheckBox("Use SonicFX for all system audio")
        self._route_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self._route_toggle.setAccessibleName(
            "Use SonicFX as the system default output"
        )
        self._route_toggle.setVisible(self._supports_routing)
        routing.addWidget(self._route_toggle)

        self._route_hint = QLabel(
            str(
                getattr(
                    self._c,
                    "routing_hint",
                    "Becomes the default output when the engine starts.",
                )
            )
        )
        self._route_hint.setObjectName("Muted")
        self._route_hint.setWordWrap(True)
        routing.addWidget(self._route_hint)

        self._apo_button = None
        if not self._supports_routing and hasattr(self._c, "open_configurator"):
            self._apo_button = QPushButton("Open Equalizer APO")
            self._apo_button.setObjectName("Ghost")
            self._apo_button.setCursor(Qt.CursorShape.PointingHandCursor)
            self._apo_button.setToolTip(
                "Open Equalizer APO's Device Selector and enable your outputs"
            )
            routing.addWidget(self._apo_button)

        self._login_toggle = QCheckBox("Launch SonicFX at login")
        self._login_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self._login_toggle.setAccessibleName("Launch SonicFX automatically at login")
        routing.addWidget(self._login_toggle)

        self._login_hint = QLabel()
        self._login_hint.setObjectName("Faint")
        self._login_hint.setWordWrap(True)
        routing.addWidget(self._login_hint)
        column.addWidget(routing_card)

        return left

    def _build_spatial_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("Card")
        card.setVisible(False)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 10, 18, 10)
        layout.setSpacing(5)

        header = QHBoxLayout()
        header.setSpacing(10)
        title = QLabel("Cinema Spatial")
        title.setObjectName("CardTitle")
        header.addWidget(title)
        self._spatial_effective = QLabel("Virtual room")
        self._spatial_effective.setObjectName("Muted")
        header.addWidget(self._spatial_effective)
        header.addStretch(1)
        self._spatial_available = QLabel()
        self._spatial_available.setObjectName("Faint")
        header.addWidget(self._spatial_available)
        layout.addLayout(header)

        controls = QHBoxLayout()
        controls.setSpacing(8)
        mode_label = QLabel("Mode")
        mode_label.setObjectName("StatCaption")
        controls.addWidget(mode_label)
        self._spatial_mode_combo = QComboBox()
        self._spatial_mode_combo.addItem("Auto (from device)", "auto")
        self._spatial_mode_combo.addItem("Headphones · binaural", "binaural")
        self._spatial_mode_combo.addItem("Stereo speakers · transaural", "speaker")
        self._spatial_mode_combo.setMinimumWidth(190)
        self._spatial_mode_combo.setAccessibleName("Cinema Spatial output mode")
        controls.addWidget(self._spatial_mode_combo)

        room_label = QLabel("Room")
        room_label.setObjectName("StatCaption")
        controls.addWidget(room_label)
        self._spatial_room_combo = QComboBox()
        self._spatial_room_combo.addItem("Small room", "small")
        self._spatial_room_combo.addItem("Medium room", "medium")
        self._spatial_room_combo.addItem("Large cinema", "large")
        self._spatial_room_combo.setMinimumWidth(125)
        self._spatial_room_combo.setAccessibleName("Cinema Spatial room size")
        controls.addWidget(self._spatial_room_combo)

        amount_label = QLabel("Immersion")
        amount_label.setObjectName("StatCaption")
        controls.addWidget(amount_label)
        self._spatial_amount = QSlider(Qt.Orientation.Horizontal)
        self._spatial_amount.setRange(0, 100)
        self._spatial_amount.setValue(75)
        self._spatial_amount.setMinimumWidth(130)
        self._spatial_amount.setAccessibleName("Cinema Spatial immersion amount")
        controls.addWidget(self._spatial_amount, 1)
        self._spatial_amount_value = QLabel("75%")
        self._spatial_amount_value.setObjectName("Muted")
        self._spatial_amount_value.setMinimumWidth(38)
        controls.addWidget(self._spatial_amount_value)
        layout.addLayout(controls)

        self._spatial_hint = QLabel(
            "Headphones: measured HRTF over a virtual 7.1.4 ring. Speakers: "
            "transaural width and room acoustics. This is virtualisation, not "
            "object audio."
        )
        self._spatial_hint.setObjectName("Faint")
        self._spatial_hint.setWordWrap(True)
        layout.addWidget(self._spatial_hint)
        return card

    def _build_right_column(self) -> QVBoxLayout:
        right = QVBoxLayout()
        right.setSpacing(14)

        output_card = QFrame()
        output_card.setObjectName("Card")
        output = QHBoxLayout(output_card)
        output.setContentsMargins(18, 13, 18, 13)
        output.setSpacing(14)
        output_text = QVBoxLayout()
        output_text.setSpacing(1)
        output_title = QLabel(str(getattr(self._c, "device_label", "Playback device")))
        output_title.setObjectName("CardTitle")
        output_text.addWidget(output_title)
        output_hint = QLabel(
            str(
                getattr(
                    self._c,
                    "device_hint",
                    "SonicFX sends processed audio to this device.",
                )
            )
        )
        output_hint.setObjectName("Muted")
        output_text.addWidget(output_hint)
        output.addLayout(output_text)
        output.addStretch(1)

        self._output_combo = QComboBox()
        self._output_combo.setMinimumWidth(310)
        self._output_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self._output_combo.setAccessibleName("Playback output device")
        output.addWidget(self._output_combo)
        self._refresh_button = QPushButton("Refresh")
        self._refresh_button.setObjectName("Ghost")
        self._refresh_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._refresh_button.setToolTip("Rescan available hardware outputs")
        output.addWidget(self._refresh_button)
        right.addWidget(output_card)

        self._spatial_card = self._build_spatial_card()
        right.addWidget(self._spatial_card)

        self._wave_card = WaveCard(self._settings)
        right.addWidget(self._wave_card)

        tune_card = QFrame()
        tune_card.setObjectName("Card")
        tune = QVBoxLayout(tune_card)
        tune.setContentsMargins(18, 13, 18, 12)
        tune.setSpacing(8)
        tune_header = QHBoxLayout()
        tune_header.setSpacing(10)
        tune_titles = QVBoxLayout()
        tune_titles.setSpacing(1)
        tune_title = QLabel("Fine tune")
        tune_title.setObjectName("CardTitle")
        tune_titles.addWidget(tune_title)
        self._preset_name = QLabel("Flat")
        self._preset_name.setObjectName("PresetName")
        tune_titles.addWidget(self._preset_name)
        tune_header.addLayout(tune_titles)
        tune_header.addStretch(1)
        self._reset_button = QPushButton("Reset preset")
        self._reset_button.setObjectName("Ghost")
        self._reset_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._reset_button.setToolTip("Restore this preset's original gains")
        tune_header.addWidget(self._reset_button)
        tune.addLayout(tune_header)

        divider = QFrame()
        divider.setObjectName("Divider")
        tune.addWidget(divider)

        self._curve = CurveView()
        tune.addWidget(self._curve)

        band_scroll = QScrollArea()
        band_scroll.setWidgetResizable(True)
        band_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        band_container = QWidget()
        self._bands_layout = QVBoxLayout(band_container)
        self._bands_layout.setContentsMargins(0, 2, 6, 2)
        self._bands_layout.setSpacing(4)
        self._bands_layout.addStretch(1)
        band_scroll.setWidget(band_container)
        tune.addWidget(band_scroll, 1)
        right.addWidget(tune_card, 1)

        engine_card = QFrame()
        engine_card.setObjectName("Card")
        engine = QVBoxLayout(engine_card)
        engine.setContentsMargins(18, 11, 18, 11)
        engine.setSpacing(7)
        stats = QHBoxLayout()
        stats.setSpacing(24)
        self._stat_state = Stat("Engine state", "Stopped")
        self._stat_gap = Stat(
            str(getattr(self._c, "stat_gap_caption", "Restart gap")), "—"
        )
        self._stat_route = Stat(
            str(getattr(self._c, "stat_route_caption", "System default")), "Off"
        )
        self._stat_output = Stat(
            str(getattr(self._c, "stat_output_caption", "Chain output")), "—"
        )
        self._stat_peak = Stat("Peak", "—")
        for stat in (
            self._stat_state,
            self._stat_gap,
            self._stat_route,
            self._stat_output,
            self._stat_peak,
        ):
            stats.addWidget(stat, 1)
        engine.addLayout(stats)

        meter_row = QHBoxLayout()
        meter_row.setSpacing(12)
        meter_caption = QLabel("Level")
        meter_caption.setObjectName("StatCaption")
        meter_row.addWidget(meter_caption)
        self._meter = LevelMeter()
        meter_row.addWidget(self._meter, 1)
        engine.addLayout(meter_row)
        right.addWidget(engine_card)
        return right

    def _connect(self) -> None:
        self._power.clicked.connect(self._power_clicked)
        self._reset_button.clicked.connect(self._c.reset_preset)
        self._refresh_button.clicked.connect(self._c.refresh_sinks)
        self._output_combo.currentIndexChanged.connect(self._combo_changed)
        self._route_toggle.toggled.connect(self._route_toggled)
        self._login_toggle.toggled.connect(self._login_toggled)
        if self._apo_button is not None:
            self._apo_button.clicked.connect(self._c.open_configurator)

        self._c.status_changed.connect(self._on_status)
        self._c.state_changed.connect(self._on_state)
        self._c.busy_changed.connect(self._on_busy)
        self._c.error.connect(self._on_error)
        self._c.preset_changed.connect(self._on_preset)
        self._c.bands_changed.connect(self._on_bands)
        self._c.custom_changed.connect(self._on_custom)
        self._c.sinks_changed.connect(self._on_sinks)
        self._c.output_changed.connect(self._on_output)
        self._c.route_changed.connect(self._on_route)
        self._c.use_default_changed.connect(self._on_use_default)
        self._c.login_autostart_changed.connect(self._on_login_autostart)
        self._c.meter_changed.connect(self._on_meter)
        if hasattr(self._c, "spatial_changed"):
            self._c.spatial_changed.connect(self._on_spatial)
        if self._supports_spatial:
            self._spatial_mode_combo.currentIndexChanged.connect(
                self._spatial_mode_changed
            )
            self._spatial_room_combo.currentIndexChanged.connect(
                self._spatial_room_changed
            )
            self._spatial_amount.valueChanged.connect(self._spatial_amount_changed)

        self._wave_worker.start()
        self._wave_card.start()

    # -- header / state ----------------------------------------------------
    def _wave_target(self) -> str:
        """Passive monitor target for the Wave Studio analyser (read-only).

        Empty means "no capture": engine off, no output chosen, or a platform
        without PipeWire ``*.monitor`` nodes. On Windows the Wave Studio card
        still renders and animates, but there is no audio-analysis feed there
        yet (that would be WASAPI loopback work, see limitations doc).
        """
        if sys.platform != "linux":
            return ""
        running = bool(getattr(self._c, "running", False))
        output = str(getattr(self._c, "output", "") or "")
        if not running or not output:
            return ""
        return monitor_target(output)

    def _refresh_wave_worker(self) -> None:
        if hasattr(self, "_wave_worker"):
            self._wave_worker.refresh()

    def _power_clicked(self) -> None:
        self._c.set_enabled(not self._running)

    def _on_busy(self, busy: bool) -> None:
        self._busy = busy
        self._refresh_status_ui()

    def _on_state(self, running: bool) -> None:
        self._running = running
        self._refresh_status_ui()
        self._refresh_wave_worker()

    def _on_status(self, status) -> None:
        self._status = status
        self._running = bool(status.running)
        if status.running:
            self._error_text = ""
        elif getattr(status, "last_error", None):
            self._error_text = str(status.last_error)
        self._refresh_status_ui()
        self._refresh_error_banner()

    def _on_error(self, message: str) -> None:
        self._error_text = " ".join(str(message).split())[:280]
        self._error_banner.setToolTip(str(message))
        self._refresh_error_banner()
        self._refresh_status_ui()

    def _refresh_error_banner(self) -> None:
        self._error_banner.setText(f"⚠  {self._error_text}" if self._error_text else "")
        self._error_banner.setVisible(bool(self._error_text))

    def _refresh_status_ui(self) -> None:
        if self._busy:
            self._chip.set_state("Applying…", "busy")
        elif self._running:
            self._chip.set_state("● Running", "running")
        elif self._error_text:
            self._chip.set_state("Attention", "error")
        else:
            self._chip.set_state("● Stopped", "stopped")

        self._power.setEnabled(not self._busy)
        self._power.setText("Stop engine" if self._running else "Start engine")
        self._power.setObjectName("Ghost" if self._running else "Primary")
        repolish(self._power)

        status = self._status
        self._stat_state.set_value("Running" if self._running else "Stopped")
        gap = getattr(status, "gap_ms", None) if status is not None else None
        self._stat_gap.set_value(f"{gap:.0f} ms" if gap is not None else "—")
        if self._supports_routing:
            self._stat_route.set_value("SonicFX" if self._route_active else "Off")
        else:
            self._stat_route.set_value("APO active" if self._running else "Off")
        device = self._c.output
        description = self._sink_desc.get(device, device)
        self._stat_output.set_value(self._elide(description or "—", 24))
        self._stat_output.value.setToolTip(description)
        self._refresh_route_hint()

    @staticmethod
    def _elide(text: str, limit: int) -> str:
        return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"

    # -- peak meter --------------------------------------------------------
    def _on_meter(self, left: float, right: float) -> None:
        self._meter.set_levels(left, right)
        peak = amplitude_db(max(left, right))
        self._stat_peak.set_value(format_level_db(peak))
        self._stat_peak.value.setToolTip(
            f"Left {format_level_db(amplitude_db(left))}, "
            f"right {format_level_db(amplitude_db(right))}"
        )

    # -- presets / bands ---------------------------------------------------
    def _preset_clicked(self, key: str) -> None:
        if self._updating or key == self._preset_key:
            return
        self._c.select_preset(key)

    def _sync_preset(self, key: str) -> None:
        self._preset_key = key
        card = self._preset_cards.get(key)
        if card is not None:
            self._updating = True
            card.setChecked(True)
            self._updating = False
        label = get_preset(key).label if key in self._preset_cards else key
        self._preset_name.setText(label)

    def _on_preset(self, key: str) -> None:
        self._sync_preset(key)

    def _on_custom(self, custom: bool) -> None:
        label = get_preset(self._preset_key).label
        self._preset_name.setText(f"{label} · custom" if custom else label)

    def _on_bands(self, bands: tuple[BiquadBand, ...]) -> None:
        self._curve.set_bands(bands)
        if self._compatible_rows(bands):
            for row, band in zip(self._band_rows, bands, strict=True):
                row.update_band(band)
            return
        self._rebuild_bands(bands)

    def _compatible_rows(self, bands: tuple[BiquadBand, ...]) -> bool:
        if len(self._band_rows) != len(bands):
            return False
        return all(
            row.band.kind == band.kind and row.band.freq == band.freq
            for row, band in zip(self._band_rows, bands, strict=False)
        )

    def _rebuild_bands(self, bands: tuple[BiquadBand, ...]) -> None:
        while self._bands_layout.count():
            item = self._bands_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._band_rows = []
        if not bands:
            empty = QLabel(
                "No equalisation bands. The chain passes a neutral curve — "
                "choose a preset to add enhancement."
            )
            empty.setObjectName("Muted")
            empty.setWordWrap(True)
            self._bands_layout.addWidget(empty)
        for index, band in enumerate(bands):
            row = BandRow(index, band)
            row.gain_changed.connect(self._c.set_band_gain)
            self._bands_layout.addWidget(row)
            self._band_rows.append(row)
        self._bands_layout.addStretch(1)

    # -- Cinema Spatial ----------------------------------------------------
    def _on_spatial(self, state) -> None:
        self._spatial_state = state
        if not self._supports_spatial:
            self._spatial_card.setVisible(False)
            return
        self._spatial_card.setVisible(bool(state.enabled))
        self._updating = True
        try:
            index = self._spatial_mode_combo.findData(state.mode)
            if index >= 0:
                self._spatial_mode_combo.setCurrentIndex(index)
            index = self._spatial_room_combo.findData(state.room)
            if index >= 0:
                self._spatial_room_combo.setCurrentIndex(index)
            self._spatial_amount.setValue(int(state.amount))
            self._spatial_amount_value.setText(f"{int(state.amount)}%")
        finally:
            self._updating = False
        self._spatial_effective.setText(state.effective_label)
        self._spatial_available.setText(
            "" if state.available else "HRTF file not found"
        )
        if state.effective_mode == "binaural":
            if state.available:
                hint = (
                    "Headphones: measured SOFA HRTF, virtual 7.1.4 ring and a "
                    "synthetic room. Keep the volume moderate; HRTF peaks can "
                    "be intense."
                )
            else:
                hint = (
                    "Binaural mode needs a SOFA HRTF file. Install libmysofa1 "
                    "or place a .sofa file in ~/.local/share/sonicfx/hrtf/. "
                    "Speaker mode needs no HRTF."
                )
        else:
            hint = (
                "Stereo speakers: transaural width, lateral early reflections "
                "and a synthetic room. Best in the near-field sweet spot; true "
                "rear/height needs headphones or a multichannel receiver."
            )
        if not state.limiter:
            hint += (
                " The SWH look-ahead limiter was not found, so fixed headroom is "
                "being used; install swh-plugins for peak-safe limiting."
            )
        self._spatial_hint.setText(hint)

    def _spatial_mode_changed(self, _index: int) -> None:
        if self._updating:
            return
        mode = self._spatial_mode_combo.currentData()
        if isinstance(mode, str):
            self._c.set_spatial_mode(mode)

    def _spatial_room_changed(self, _index: int) -> None:
        if self._updating:
            return
        room = self._spatial_room_combo.currentData()
        if isinstance(room, str):
            self._c.set_spatial_room(room)

    def _spatial_amount_changed(self, value: int) -> None:
        self._spatial_amount_value.setText(f"{int(value)}%")
        if self._updating:
            return
        self._c.set_spatial_amount(int(value))

    # -- routing toggles ---------------------------------------------------
    def _route_toggled(self, checked: bool) -> None:
        if self._updating:
            return
        self._route_toggle.setEnabled(False)
        self._c.set_use_default(checked)
        self._route_toggle.setEnabled(True)

    def _on_use_default(self, enabled: bool) -> None:
        self._updating = True
        self._use_default = enabled
        self._route_toggle.setChecked(enabled)
        self._updating = False
        self._refresh_route_hint()

    def _on_route(self, active: bool) -> None:
        self._route_active = active
        self._stat_route.set_value("SonicFX" if active else "Off")
        self._refresh_route_hint()

    def _refresh_route_hint(self) -> None:
        if not self._supports_routing:
            self._route_hint.setText(
                str(getattr(self._c, "routing_hint", "Equalizer APO backend."))
            )
            return
        if not self._use_default:
            text = "Your apps keep their normal output device."
        elif self._route_active:
            text = (
                "SonicFX is your default output. Your previous device is saved "
                "and restored when you stop."
            )
        else:
            text = (
                "Becomes the default output when the engine starts. Your previous "
                "device is restored when you stop."
            )
        self._route_hint.setText(text)
        self._route_toggle.setToolTip(
            "Sets SonicFX as the system default output while the engine runs. "
            "The previous default is restored on stop, exit or crash recovery."
        )

    def _login_toggled(self, checked: bool) -> None:
        if self._updating:
            return
        self._c.set_login_autostart(checked)

    def _on_login_autostart(self, enabled: bool) -> None:
        self._updating = True
        self._login_autostart = enabled
        self._login_toggle.setChecked(enabled)
        self._updating = False
        if self._supports_routing:
            hint = (
                "Starts automatically and re-applies your routing."
                if enabled
                else "Launch SonicFX manually when you want processing."
            )
        else:
            hint = (
                "Starts automatically and re-enables Equalizer APO processing."
                if enabled
                else "Launch SonicFX manually when you want processing."
            )
        self._login_hint.setText(hint)

    # -- output device -----------------------------------------------------
    def _on_sinks(self, sinks) -> None:
        self._sink_desc = {sink.name: sink.description for sink in sinks}
        self._updating = True
        self._output_combo.clear()
        for sink in sinks:
            self._output_combo.addItem(sink.description, sink.name)
            index = self._output_combo.count() - 1
            self._output_combo.setItemData(
                index, sink.name, Qt.ItemDataRole.ToolTipRole
            )
        self._output_combo.setEnabled(bool(sinks))
        if not sinks:
            self._output_combo.addItem("No output device found", "")
        current = self._c.output
        index = self._output_combo.findData(current)
        if index >= 0:
            self._output_combo.setCurrentIndex(index)
        self._updating = False
        self._refresh_status_ui()

    def _on_output(self, name: str) -> None:
        self._updating = True
        index = self._output_combo.findData(name)
        if index >= 0:
            self._output_combo.setCurrentIndex(index)
        self._updating = False
        self._refresh_status_ui()
        self._refresh_wave_worker()

    def _combo_changed(self, _index: int) -> None:
        if self._updating:
            return
        name = self._output_combo.currentData()
        if isinstance(name, str) and name:
            self._c.set_output(name)

    # -- geometry / exit ---------------------------------------------------
    def _restore_geometry(self) -> None:
        saved = self._settings.value("window_geometry")
        if saved is not None and self.restoreGeometry(saved):
            return
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        frame = self.frameGeometry()
        frame.moveCenter(screen.availableGeometry().center())
        self.move(frame.topLeft())

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        self._settings.setValue("window_geometry", self.saveGeometry())
        self._settings.sync()
        try:
            self._wave_card.shutdown()
        except Exception:  # noqa: BLE001 - closing must always succeed
            log.exception("wave worker shutdown failed")
        try:
            self._c.shutdown()
        except Exception:  # noqa: BLE001 - closing must always succeed
            log.exception("controller shutdown failed")
        super().closeEvent(event)
