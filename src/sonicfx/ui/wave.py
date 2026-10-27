"""Wave Studio: Qt visualizer for the SonicFX Ultimate GUI.

This module owns the three lightweight pieces of the Wave Studio feature:
a passive analysis worker, the six-mode rendering surface and the controls
card. It never runs audio processing on the PipeWire real-time thread and
never claims to be Dolby or object-audio decoding.
"""
from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, replace

import numpy as np
from PySide6.QtCore import (
    QPointF,
    QRectF,
    QSettings,
    Qt,
    QThread,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from sonicfx.dsp.analysis import AnalysisConfig, AnalysisSnapshot, AudioAnalyzer
from sonicfx.logging_setup import get_logger
from sonicfx.pipewire.tap import LevelTap, TapError

log = get_logger("ui.wave")


# --------------------------------------------------------------------------
# settings / snapshot
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class WaveSettings:
    """User preferences for the Wave Studio card."""

    mode: int = 0  # 0..5, one entry per visualisation
    intensity: float = 0.75
    speed: float = 0.55
    amplitude: float = 0.72
    particles: float = 0.55
    glow: float = 0.45
    palette: str = "aurora"
    audio_reactive: bool = True
    fps: int = 30
    reduced_motion: bool = False

    def clamp(self) -> WaveSettings:
        def bounds(value: float, low: float = 0.0, high: float = 1.0) -> float:
            return max(low, min(high, float(value)))

        palette = (
            str(self.palette) if str(self.palette) in PALETTES else "aurora"
        )
        return replace(
            self,
            mode=int(max(0, min(5, int(self.mode)))),
            intensity=bounds(self.intensity),
            speed=bounds(self.speed),
            amplitude=bounds(self.amplitude),
            particles=bounds(self.particles),
            glow=bounds(self.glow),
            palette=palette,
            audio_reactive=bool(self.audio_reactive),
            fps=int(max(0, min(60, int(self.fps)))),
            reduced_motion=bool(self.reduced_motion),
        )


MODES: tuple[str, ...] = (
    "Cinematic Wave",
    "Particle Reactor",
    "3D Cinema Space",
    "Spectrum Nebula",
    "Spectrogram Flow",
    "Light Wave",
)

PALETTES: tuple[str, ...] = ("aurora", "ice", "ember", "emerald", "spectrum")

_PALETTE_COLORS: dict[str, tuple[str, str]] = {
    "aurora": ("#7ea8ff", "#ffd08a"),
    "ice": ("#7fdcff", "#c7e9ff"),
    "ember": ("#ff9a6b", "#ffd08a"),
    "emerald": ("#68e2a1", "#6bfff0"),
    "spectrum": ("#a48cff", "#ff9d6b"),
}


# --------------------------------------------------------------------------
# worker
# --------------------------------------------------------------------------
class WaveWorker(QThread):
    """Passive monitor analysis worker.

    One attempt owns one ``LevelTap`` on a ``*.monitor`` node and feeds the
    captured chunks to ``AudioAnalyzer``. Snapshots are emitted as
    ``Signal(object)``, so the GUI never reads audio in its paint thread. A
    dead capture (device unplugged, engine restart) never kills the thread:
    the loop reconnects until :meth:`stop` is requested.
    """

    snapshot = Signal(object)

    READ_TIMEOUT_S = 0.06
    RETRY_DELAY_S = 0.75
    IDLE_WAIT_S = 0.25
    MIN_LIFETIME_S = 1.0  # shorter means the capture failed to start

    def __init__(
        self,
        target_provider,
        *,
        sample_rate: int = 48_000,
        tap_factory=LevelTap,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._target_provider = target_provider
        self._sample_rate = int(sample_rate)
        self._tap_factory = tap_factory
        self._refresh = threading.Event()
        self._stop = threading.Event()

    # -- control ------------------------------------------------------------
    def refresh(self) -> None:
        """Restart the capture: the device or routing changed."""
        self._refresh.set()

    def stop(self) -> None:
        """Stop the loop; pair with :meth:`wait` from the caller."""
        self._stop.set()
        self._refresh.set()

    # -- loop ---------------------------------------------------------------
    def run(self) -> None:
        capturing = False
        while True:
            if self._stop.is_set():
                break
            target = self._target_provider()
            if not target:
                self._refresh.clear()
                if capturing:
                    capturing = False  # engine off: one final idle frame
                    self.snapshot.emit(AnalysisSnapshot.idle())
                if self._stop.wait(self.IDLE_WAIT_S):
                    break
                continue
            self._refresh.clear()
            started = time.monotonic()
            try:
                tap = self._tap_factory(target)
                tap.start()
            except TapError as exc:
                log.warning("wave target %r unavailable: %s", target, exc)
                self.snapshot.emit(AnalysisSnapshot.idle())
                if self._stop.wait(self.RETRY_DELAY_S):
                    break
                continue
            capturing = True
            analyzer = AudioAnalyzer(AnalysisConfig(sample_rate=self._sample_rate))
            try:
                outcome = self._drain(tap, analyzer, target)
            except Exception:  # noqa: BLE001 - the visualiser never crashes Qt
                log.exception("wave drain failed")
                self.snapshot.emit(AnalysisSnapshot.idle())
                outcome = "dead"
            lifetime = time.monotonic() - started
            tap.stop()
            if outcome == "stop":
                break
            backoff = 0.0 if lifetime >= self.MIN_LIFETIME_S else self.RETRY_DELAY_S
            if backoff and self._stop.wait(backoff):
                break
        self.snapshot.emit(AnalysisSnapshot.idle())

    def _drain(
        self, tap: LevelTap, analyzer: AudioAnalyzer, target: str
    ) -> str:
        """Read one tap until stop/refresh/target-change/stream end."""
        while True:
            if self._stop.is_set():
                return "stop"
            if self._refresh.is_set():
                self._refresh.clear()
                return "refresh"
            # A refresh can be consumed while the capture is still starting;
            # compare the target itself so a device change is never lost and
            # the visualiser cannot keep reading the old device's monitor.
            current = self._target_provider()
            if not current or current != target:
                return "retarget"
            frames = tap.read_frames(timeout=self.READ_TIMEOUT_S)
            if frames is None:
                return "dead"  # pw-record exited: reconnect below
            if frames.size == 0:
                continue  # silent read; keep the same analyzer state
            self.snapshot.emit(analyzer.process(frames))


# --------------------------------------------------------------------------
# rendering surface
# --------------------------------------------------------------------------
class WaveStage(QWidget):
    """A custom widget painting the six visualisation modes."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAccessibleName("Wave studio canvas")
        self.setMinimumHeight(180)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self._snapshot: AnalysisSnapshot | None = None
        self._settings = WaveSettings()
        self._fps = 30
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.CoarseTimer)
        self._timer.timeout.connect(self._tick)
        self._particles: list[tuple[float, float]] = []
        self._columns: list[np.ndarray] = []
        self._phase = 0.0

    # -- public -------------------------------------------------------------
    def set_snapshot(self, snapshot: object) -> None:
        if isinstance(snapshot, AnalysisSnapshot):
            self._snapshot = snapshot
            self._remember_column(snapshot)
            self.update()

    def set_settings(self, settings: WaveSettings) -> None:
        self._settings = settings.clamp()
        self._fps = self._settings.fps
        self._apply_fps()

    def set_visible(self, visible: bool) -> None:  # noqa: N802 - Qt API
        super().setVisible(visible)
        self._apply_fps()

    def start(self) -> None:
        if self._fps > 0 and self.isVisible():
            self._timer.start(1000 // max(1, self._fps))
        self.update()

    def stop(self) -> None:
        self._timer.stop()

    # -- internals ----------------------------------------------------------
    def _remember_column(self, snapshot: AnalysisSnapshot) -> None:
        column = snapshot.bins.astype(np.float32, copy=True)
        if not self._columns or self._columns[-1].shape != column.shape:
            self._columns = [column]
        else:
            self._columns.append(column)
            if len(self._columns) > 160:
                del self._columns[: len(self._columns) - 160]

    def _apply_fps(self) -> None:
        if self._fps > 0 and self.isVisible():
            self._timer.start(1000 // max(1, self._fps))
        else:
            self._timer.stop()

    def _tick(self) -> None:
        self._phase += 0.06
        self.update()

    def paintEvent(  # noqa: N802 - Qt API
        self, _event
    ) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        try:
            if self._settings.mode == 0:
                self._paint_cinematic_wave(painter)
            elif self._settings.mode == 1:
                self._paint_particle_reactor(painter)
            elif self._settings.mode == 2:
                self._paint_space(painter)
            elif self._settings.mode == 3:
                self._paint_spectrum(painter)
            elif self._settings.mode == 4:
                self._paint_flow(painter)
            else:
                self._paint_light(painter)
        finally:
            painter.end()

    # -- helpers ------------------------------------------------------------
    def _gradient(self, settings: WaveSettings) -> QLinearGradient:
        colors = _PALETTE_COLORS.get(settings.palette, _PALETTE_COLORS["aurora"])
        gradient = QLinearGradient(0.0, 0.0, self.width(), self.height())
        gradient.setColorAt(0.0, QColor(colors[0]))
        gradient.setColorAt(1.0, QColor(colors[1]))
        return gradient

    def _bins(self) -> np.ndarray:
        return (
            self._snapshot.bins
            if self._snapshot is not None
            else np.zeros(64, dtype=np.float32)
        )

    def _waveform(self) -> np.ndarray:
        return (
            self._snapshot.waveform
            if self._snapshot is not None
            else np.zeros(64, dtype=np.float32)
        )

    def _silent(self) -> bool:
        return self._snapshot.silent if self._snapshot is not None else True

    def _speed(self) -> float:
        if self._settings.reduced_motion:
            return 0.35 * self._settings.speed
        return self._settings.speed

    def _amplitude(self) -> float:
        return self._settings.amplitude

    def _glow(self) -> float:
        if self._settings.reduced_motion:
            return 0.5 * self._settings.glow
        return self._settings.glow

    def _energy(self) -> float:
        """Visual gain from the Intensity control (0.5 neutral .. 1.5 strong)."""
        if self._settings.reduced_motion:
            return 0.5 + 0.6 * self._settings.intensity
        return 0.5 + self._settings.intensity

    def _density(self) -> int:
        base = int(24 + 140 * self._settings.particles)
        return max(6, base // (2 if self._settings.reduced_motion else 1))

    def _smoothing(self) -> float:
        return self._settings.glow

    # -- paint implementations ----------------------------------------------
    def _paint_cinematic_wave(self, painter: QPainter) -> None:
        width, height = float(self.width()), float(self.height())
        bins = self._bins()
        n = len(bins)
        for layer, offset in enumerate((0.0, 0.18, 0.36, 0.54)):
            path = QPainterPath()
            path.moveTo(0.0, height * 0.62)
            for index in range(n):
                x = (index / max(1, n - 1)) * width
                sine = math.sin(
                    self._phase * (0.6 + 0.18 * layer) + offset + index * 0.05
                )
                amp = self._amplitude() * self._energy() * float(bins[index])
                y = height * 0.62 + sine * height * 0.08 + amp * height * 0.18
                path.lineTo(x, y)
            pen = QPen(QColor(126, 168, 255, 60 + 30 * layer))
            pen.setWidthF(1.6)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)

        reflection = QPainterPath()
        wave = self._waveform()
        density = len(wave)
        for index in range(density):
            x = (index / max(1, density - 1)) * width
            y = height * 0.72 + float(wave[index]) * height * 0.2
            reflection.lineTo(x, y)
        pen = QPen(QColor(255, 208, 138, 46))
        pen.setWidthF(1.4)
        painter.setPen(pen)
        painter.drawPath(reflection)

        painter.setPen(QColor("#6b7488"))
        painter.drawText(
            QRectF(12.0, 4.0, max(40.0, width - 24.0), 16.0),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            "Cinematic Wave",
        )
        if self._silent():
            painter.drawText(
                QRectF(12.0, height - 22.0, max(40.0, width - 24.0), 18.0),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                "silent — idle motion",
            )

    def _paint_particle_reactor(self, painter: QPainter) -> None:
        width, height = float(self.width()), float(self.height())
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#0d1117"))
        painter.drawRoundedRect(QRectF(0.0, 0.0, width, height), 12.0, 12.0)
        self._update_particles(width, height)
        for x, y in self._particles:
            radius = 2.0 + 2.6 * self._glow()
            gradient = QRadialGradient(x, y, radius * 3.0)
            gradient.setColorAt(0.0, QColor(255, 208, 138, 190))
            gradient.setColorAt(1.0, QColor(126, 168, 255, 0))
            painter.setBrush(gradient)
            painter.drawEllipse(QPointF(x, y), radius, radius)
        painter.setPen(QColor("#6b7488"))
        painter.drawText(
            QRectF(12.0, 4.0, max(40.0, width - 24.0), 16.0),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            "Particle Reactor",
        )

    def _update_particles(self, width: float, height: float) -> None:
        count = self._density()
        reactive = self._settings.audio_reactive
        bass = float(self._bins()[: max(1, len(self._bins()) // 3)].mean())
        push = (0.4 + 1.6 * (bass if reactive else 0.5)) * self._energy()
        if len(self._particles) != count:
            self._particles = [
                (
                    width * 0.5 + math.cos(index) * width * 0.18,
                    height * 0.5 + math.sin(index) * height * 0.22,
                )
                for index in range(count)
            ]
        for index, (x, y) in enumerate(self._particles):
            angle = math.atan2(y - height * 0.5, x - width * 0.5)
            radius = min(0.48, push * (0.12 + 0.32 * self._settings.particles))
            self._particles[index] = (
                width * 0.5 + math.cos(angle) * width * radius,
                height * 0.5 + math.sin(angle) * height * radius,
            )

    def _paint_space(self, painter: QPainter) -> None:
        width, height = float(self.width()), float(self.height())
        layers = 9
        for layer in range(layers):
            depth = layer / (layers - 1) if layers > 1 else 0.0
            scale = 1.0 - 0.55 * depth
            alpha = max(10, int(160 * (1.0 - 0.7 * depth)))
            pen = QPen(QColor(126, 168, 255, alpha))
            pen.setWidthF(1.4)
            painter.setPen(pen)
            path = QPainterPath()
            bins = self._bins()
            n = len(bins)
            for index in range(n):
                x = width * 0.5 + (index / max(1, n - 1) - 0.5) * width * scale
                value = float(bins[index]) * self._energy()
                base = height * 0.72 + depth * height * 0.12
                y = base - value * height * 0.24 * scale
                target = QPointF(x, y)
                if index == 0:
                    path.moveTo(target)
                else:
                    path.lineTo(target)
            painter.drawPath(path)
        painter.setPen(QColor("#6b7488"))
        painter.drawText(
            QRectF(12.0, 4.0, max(40.0, width - 24.0), 16.0),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            "3D Cinema Space",
        )
        painter.drawText(
            QRectF(12.0, height - 22.0, max(40.0, width - 24.0), 18.0),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            "perspective visual — not a spatial measurement",
        )

    def _paint_spectrum(self, painter: QPainter) -> None:
        width, height = float(self.width()), float(self.height())
        bins = self._bins()
        n = len(bins)
        bar_w = max(2.0, width / max(1, n) - 2.0)
        painter.setPen(Qt.PenStyle.NoPen)
        baseline = height - 24.0
        for index, value in enumerate(bins):
            x = 12.0 + index * (bar_w + 2.0)
            bar_h = max(
                2.0,
                min(1.0, float(value) * self._energy())
                * height
                * 0.62
                * self._amplitude(),
            )
            rect = QRectF(x, baseline - bar_h, bar_w, bar_h)
            painter.setBrush(self._gradient(self._settings))
            painter.drawRect(rect)
            # Peak-hold indicator.
            hold = min(baseline - 4.0, baseline - bar_h - 2.0)
            painter.setBrush(QColor(255, 208, 138, 150))
            painter.drawRect(QRectF(x, hold, bar_w, 1.6))
        painter.setPen(QColor("#6b7488"))
        painter.drawText(
            QRectF(12.0, 4.0, max(40.0, width - 24.0), 16.0),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            "Spectrum Nebula",
        )
        painter.drawText(
            QRectF(12.0, height - 22.0, max(40.0, width - 24.0), 18.0),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            "log-frequency spectrum",
        )

    def _paint_flow(self, painter: QPainter) -> None:
        width, height = float(self.width()), float(self.height())
        cols = len(self._columns)
        if cols <= 1:
            painter.setPen(QColor("#6b7488"))
            painter.drawText(
                QRectF(12.0, 4.0, max(40.0, width - 24.0), 16.0),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                "Spectrogram Flow",
            )
            return
        column_w = width / max(1, cols)
        for index, column in enumerate(reversed(self._columns)):
            x = index * column_w
            rows = len(column)
            for row, value in enumerate(column):
                y = height - 26.0 - (row / max(1, rows - 1)) * (height - 34.0)
                color = QColor.fromHsvF(
                    self._palette_hue(float(value)),
                    0.65,
                    max(0.15, 0.25 + 0.6 * min(1.0, float(value) * self._energy())),
                )
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(color)
                painter.drawRect(
                    QRectF(x, y - column_w * 0.5, column_w, max(1.0, column_w))
                )
        painter.setPen(QColor("#6b7488"))
        painter.drawText(
            QRectF(12.0, 4.0, max(40.0, width - 24.0), 16.0),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            "Spectrogram Flow",
        )

    def _paint_light(self, painter: QPainter) -> None:
        width, height = float(self.width()), float(self.height())
        bins = self._bins()
        n = len(bins)
        for index in range(n):
            x = (index / max(1, n - 1)) * width
            color = QColor.fromHsvF(
                self._palette_hue(index / max(1, n - 1)), 0.72, 0.9
            )
            path = QPainterPath()
            path.moveTo(0.0, height * 0.55)
            for step in range(64):
                sine = math.sin(self._phase * (0.5 + 0.08 * (step % 7)) + index * 0.05)
                amp = min(1.0, float(bins[index]) * self._energy())
                y2 = height * 0.55 + sine * height * 0.22 + amp * height * 0.2
                path.lineTo(x, y2)
            pen = QPen(color, 1.5, Qt.PenStyle.NoPen)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)
        painter.setPen(QColor("#6b7488"))
        painter.drawText(
            QRectF(12.0, 4.0, max(40.0, width - 24.0), 16.0),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            "Light Wave",
        )
        painter.drawText(
            QRectF(12.0, height - 22.0, max(40.0, width - 24.0), 18.0),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            "wavelength-inspired visuals — decorative",
        )

    def _palette_hue(self, fraction: float) -> float:
        palette = self._settings.palette
        if palette == "ice":
            return 0.58 + 0.05 * fraction
        if palette == "ember":
            return 0.05 + 0.03 * fraction
        if palette == "emerald":
            return 0.42 + 0.12 * fraction
        if palette == "spectrum":
            return 0.78 - 0.62 * fraction
        return 0.62 - 0.08 * fraction


# --------------------------------------------------------------------------
# card
# --------------------------------------------------------------------------
class WaveCard(QFrame):
    """The complete Wave Studio card: visual, controls and settings."""

    def __init__(self, settings: QSettings | None = None) -> None:
        super().__init__()
        self.setObjectName("Card")
        self._settings = settings
        self._state = WaveSettings().clamp()
        self._worker: WaveWorker | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel("Wave Studio")
        title.setObjectName("CardTitle")
        header.addWidget(title)
        hint = QLabel(
            "Audio-reactive visualisations (decorative, not a measurement)"
        )
        hint.setObjectName("Muted")
        header.addWidget(hint)
        header.addStretch(1)
        self._reset = QPushButton("Reset")
        self._reset.setObjectName("Ghost")
        header.addWidget(self._reset)
        layout.addLayout(header)

        body = QHBoxLayout()
        self._stage = WaveStage()
        body.addWidget(self._stage, 1)
        layout.addLayout(body)

        self._build_controls(layout)
        self._load()
        self._reset.clicked.connect(self._reset_settings)
        self._apply()

    # -- controls -----------------------------------------------------------
    def _build_controls(self, layout: QVBoxLayout) -> None:
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)
        self._mode = QComboBox()
        for label in MODES:
            self._mode.addItem(label)
        self._mode.setAccessibleName("Wave Studio visualisation mode")
        grid.addWidget(QLabel("Mode"), 0, 0)
        grid.addWidget(self._mode, 0, 1)
        self._speed = QSlider(Qt.Orientation.Horizontal)
        self._speed.setRange(0, 100)
        self._speed.setAccessibleName("Animation speed")
        grid.addWidget(QLabel("Speed"), 1, 0)
        grid.addWidget(self._speed, 1, 1)
        self._amplitude = QSlider(Qt.Orientation.Horizontal)
        self._amplitude.setRange(0, 100)
        self._amplitude.setAccessibleName("Wave amplitude")
        grid.addWidget(QLabel("Amplitude"), 0, 2)
        grid.addWidget(self._amplitude, 0, 3)
        self._particles = QSlider(Qt.Orientation.Horizontal)
        self._particles.setRange(0, 100)
        self._particles.setAccessibleName("Particle density")
        grid.addWidget(QLabel("Particles"), 1, 2)
        grid.addWidget(self._particles, 1, 3)
        self._glow = QSlider(Qt.Orientation.Horizontal)
        self._glow.setRange(0, 100)
        self._glow.setAccessibleName("Glow amount")
        grid.addWidget(QLabel("Glow"), 0, 4)
        grid.addWidget(self._glow, 0, 5)
        self._palette = QComboBox()
        for palette in PALETTES:
            self._palette.addItem(str(palette).title())
        self._palette.setAccessibleName("Colour palette")
        grid.addWidget(QLabel("Palette"), 1, 4)
        grid.addWidget(self._palette, 1, 5)
        self._fps = QComboBox()
        for value, label in ((0, "Off"), (15, "15 FPS"), (30, "30 FPS"), (60, "60 FPS")):
            self._fps.addItem(label, value)
        self._fps.setAccessibleName("Target frame rate")
        grid.addWidget(QLabel("Limit"), 0, 6)
        grid.addWidget(self._fps, 0, 7)
        self._reactive = QCheckBox("Audio reactive")
        self._reactive.setAccessibleName("Audio reactivity")
        grid.addWidget(self._reactive, 1, 6, 1, 2)
        self._reduced = QCheckBox("Reduced motion")
        self._reduced.setAccessibleName("Reduced motion")
        grid.addWidget(self._reduced, 2, 0, 1, 3)
        self._intensity = QSlider(Qt.Orientation.Horizontal)
        self._intensity.setRange(0, 100)
        self._intensity.setAccessibleName("Animation intensity")
        grid.addWidget(QLabel("Intensity"), 2, 3)
        grid.addWidget(self._intensity, 2, 4, 1, 4)
        layout.addLayout(grid)

        self._mode.currentIndexChanged.connect(self._on_mode)
        self._speed.valueChanged.connect(self._on_speed)
        self._amplitude.valueChanged.connect(self._on_amplitude)
        self._particles.valueChanged.connect(self._on_particles)
        self._glow.valueChanged.connect(self._on_glow)
        self._palette.currentIndexChanged.connect(self._on_palette)
        self._fps.currentIndexChanged.connect(self._on_fps)
        self._reactive.toggled.connect(self._on_reactive)
        self._reduced.toggled.connect(self._on_reduced)
        self._intensity.valueChanged.connect(self._on_intensity)

    def _on_intensity(self, value: int) -> None:
        self._state = replace(self._state, intensity=value / 100.0)
        self._stage.set_settings(self._state)
        self.save()

    def _on_mode(self, index: int) -> None:
        self._state = replace(self._state, mode=int(index))
        self._stage.set_settings(self._state)
        self.save()

    def _on_speed(self, value: int) -> None:
        self._state = replace(self._state, speed=value / 100.0)
        self._stage.set_settings(self._state)
        self.save()

    def _on_amplitude(self, value: int) -> None:
        self._state = replace(self._state, amplitude=value / 100.0)
        self._stage.set_settings(self._state)
        self.save()

    def _on_particles(self, value: int) -> None:
        self._state = replace(self._state, particles=value / 100.0)
        self._stage.set_settings(self._state)
        self.save()

    def _on_glow(self, value: int) -> None:
        self._state = replace(self._state, glow=value / 100.0)
        self._stage.set_settings(self._state)
        self.save()

    def _on_palette(self, index: int) -> None:
        if 0 <= index < len(PALETTES):
            self._state = replace(self._state, palette=PALETTES[int(index)])
            self._stage.set_settings(self._state)
            self.save()

    def _on_fps(self, index: int) -> None:
        fps = self._fps.itemData(index)
        if fps is not None:
            self._state = replace(self._state, fps=int(fps))
            self._stage.set_settings(self._state)
            self.save()

    def _on_reactive(self, checked: bool) -> None:
        self._state = replace(self._state, audio_reactive=bool(checked))
        self._stage.set_settings(self._state)
        self.save()

    def _on_reduced(self, checked: bool) -> None:
        self._state = replace(self._state, reduced_motion=bool(checked))
        self._stage.set_settings(self._state)
        self.save()

    # -- persistence --------------------------------------------------------
    def _load(self) -> None:
        if self._settings is None:
            return
        try:
            state = WaveSettings(
                mode=int(self._settings.value("wave_mode", 0)),
                intensity=float(self._settings.value("wave_intensity", 0.75)),
                speed=float(self._settings.value("wave_speed", 0.55)),
                amplitude=float(self._settings.value("wave_amplitude", 0.72)),
                particles=float(self._settings.value("wave_particles", 0.55)),
                glow=float(self._settings.value("wave_glow", 0.45)),
                palette=str(self._settings.value("wave_palette", "aurora")),
                audio_reactive=bool(self._settings.value("wave_audio_reactive", True)),
                fps=int(self._settings.value("wave_fps", 30)),
                reduced_motion=bool(self._settings.value("wave_reduced_motion", False)),
            )
        except (TypeError, ValueError, AttributeError):
            state = WaveSettings()
        self._state = state.clamp()

    def _apply(self) -> None:
        self._mode.setCurrentIndex(self._state.mode)
        self._speed.setValue(int(round(self._state.speed * 100.0)))
        self._intensity.setValue(int(round(self._state.intensity * 100.0)))
        self._amplitude.setValue(int(round(self._state.amplitude * 100.0)))
        self._particles.setValue(int(round(self._state.particles * 100.0)))
        self._glow.setValue(int(round(self._state.glow * 100.0)))
        index = self._palette.findText(str(self._state.palette).title())
        if index >= 0:
            self._palette.setCurrentIndex(index)
        fps_index = self._fps.findData(int(self._state.fps))
        if fps_index >= 0:
            self._fps.setCurrentIndex(fps_index)
        self._reactive.setChecked(bool(self._state.audio_reactive))
        self._reduced.setChecked(bool(self._state.reduced_motion))
        self._stage.set_settings(self._state)

    def save(self) -> None:
        """Persist the current settings."""
        if self._settings is None:
            return
        state = self._state
        self._settings.setValue("wave_mode", int(state.mode))
        self._settings.setValue("wave_intensity", float(state.intensity))
        self._settings.setValue("wave_speed", float(state.speed))
        self._settings.setValue("wave_amplitude", float(state.amplitude))
        self._settings.setValue("wave_particles", float(state.particles))
        self._settings.setValue("wave_glow", float(state.glow))
        self._settings.setValue("wave_palette", str(state.palette))
        self._settings.setValue("wave_audio_reactive", bool(state.audio_reactive))
        self._settings.setValue("wave_fps", int(state.fps))
        self._settings.setValue("wave_reduced_motion", bool(state.reduced_motion))
        self._settings.sync()

    def _reset_settings(self) -> None:
        self._state = WaveSettings().clamp()
        self._apply()
        self.save()

    # -- worker binding ----------------------------------------------------
    def bind_worker(self, worker: WaveWorker | None) -> None:
        """Attach the analysis worker; the caller owns its lifecycle."""
        self._worker = worker
        if worker is not None:
            worker.snapshot.connect(self._on_snapshot)

    def start(self) -> None:
        self._stage.start()

    def stop(self) -> None:
        self._stage.stop()

    def shutdown(self) -> None:
        self.stop()
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.stop()
            worker.wait(3000)
            worker.deleteLater()

    def _on_snapshot(self, snapshot: object) -> None:
        self._stage.set_snapshot(snapshot)
