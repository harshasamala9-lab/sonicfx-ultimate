"""Reusable widgets for the SonicFX GUI (no PipeWire access here)."""
from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from sonicfx.dsp.filters import BiquadBand, FilterKind, response_db
from sonicfx.profiles.presets import Preset
from sonicfx.ui.theme import COLORS, repolish

_SHAPED = frozenset({FilterKind.PEAKING, FilterKind.LOW_SHELF, FilterKind.HIGH_SHELF})

_KIND_LABELS = {
    FilterKind.PEAKING: "Peak",
    FilterKind.LOW_SHELF: "Low shelf",
    FilterKind.HIGH_SHELF: "High shelf",
    FilterKind.LOW_PASS: "Low-pass",
    FilterKind.HIGH_PASS: "High-pass",
    FilterKind.BAND_PASS: "Band-pass",
    FilterKind.NOTCH: "Notch",
    FilterKind.ALL_PASS: "All-pass",
}


def format_freq(freq: float) -> str:
    if freq >= 10_000:
        return f"{freq / 1_000:.1f} kHz".replace(".0 kHz", " kHz")
    if freq >= 1_000:
        return f"{freq / 1_000:.2f} kHz".replace(".00 kHz", " kHz")
    return f"{freq:.0f} Hz"


LEVEL_FLOOR_DB = -60.0  # meter scale bottom; must match the controller's floor


def amplitude_db(amplitude: float) -> float:
    """Level in dBFS of a 0..1 amplitude (floored at the meter bottom)."""
    if amplitude <= 0.0:
        return LEVEL_FLOOR_DB
    return max(LEVEL_FLOOR_DB, 20.0 * math.log10(amplitude))


def format_level_db(db: float) -> str:
    """UI text for a dBFS level; anything at the floor reads digital silence."""
    if db <= -59.95:
        return "-∞ dB"
    return f"{db:+.1f} dB"


class LogoMark(QWidget):
    """Small painted logo: a gradient tile with a waveform."""

    def __init__(self, size: int = 42, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAccessibleName("SonicFX logo")

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
        gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
        gradient.setColorAt(0.0, QColor(COLORS["accent"]))
        gradient.setColorAt(1.0, QColor(COLORS["violet"]))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(gradient)
        painter.drawRoundedRect(rect, 12.0, 12.0)

        pen = QPen(QColor(255, 255, 255, 238))
        pen.setWidthF(max(2.0, self.width() / 15.0))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        heights = (0.24, 0.46, 0.64, 0.40, 0.20)
        step = self.width() / (len(heights) + 1)
        center_y = self.height() / 2.0
        for index, height in enumerate(heights):
            x = step * (index + 1)
            half = self.height() * height / 2.0
            painter.drawLine(QPointF(x, center_y - half), QPointF(x, center_y + half))
        painter.end()


class StatusChip(QLabel):
    """Observed engine state as a coloured pill (never optimistic)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("StatusChip")
        self.set_state("Stopped", "stopped")

    def set_state(self, text: str, state: str) -> None:
        self.setText(text)
        self.setProperty("state", state)
        repolish(self)


class PresetCard(QPushButton):
    """Large checkable preset card with a title and one-line description."""

    def __init__(self, preset: Preset, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.preset = preset
        self.setObjectName("PresetCard")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(preset.description)
        self.setMinimumHeight(54)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(13, 7, 13, 7)
        layout.setSpacing(1)
        self.title = QLabel(preset.label)
        self.title.setObjectName("PresetTitle")
        summary = preset.description
        if len(summary) > 50:
            summary = summary[:49].rstrip() + "…"
        self.description = QLabel(summary)
        self.description.setObjectName("PresetDesc")
        self.description.setWordWrap(False)
        self.description.setTextFormat(Qt.TextFormat.PlainText)
        for label in (self.title, self.description):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(self.title)
        layout.addWidget(self.description)
        self.toggled.connect(self._update_active)

    def _update_active(self, checked: bool) -> None:
        self.title.setProperty("active", "true" if checked else "false")
        repolish(self.title)

    def setChecked(self, checked: bool) -> None:  # noqa: N802 - Qt API
        super().setChecked(checked)
        self._update_active(checked)


class BandRow(QWidget):
    """One editable EQ band: label, frequency and a half-dB gain slider."""

    gain_changed = Signal(int, float)

    @property
    def band(self) -> BiquadBand:
        return self._band

    def __init__(
        self, index: int, band: BiquadBand, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._index = index
        self._band = band
        self._shaped = band.kind in _SHAPED
        self.setMinimumHeight(34)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self.name = QLabel(self._label_text(band))
        self.name.setObjectName("BandName")
        self.name.setMinimumWidth(136)
        self.name.setToolTip(
            f"{_KIND_LABELS[band.kind]} · Q {band.q:g} · "
            f"{format_freq(band.freq)}"
        )
        layout.addWidget(self.name)

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(-24, 24)  # half-dB steps, ±12 dB
        self.slider.setSingleStep(1)
        self.slider.setPageStep(2)
        self.slider.setEnabled(self._shaped)
        self.slider.setToolTip("Gain (±12 dB, 0.5 dB steps)")
        if self._shaped:
            self.slider.setValue(int(round(band.gain_db * 2.0)))
        layout.addWidget(self.slider, 1)

        self.value = QLabel(self._gain_text(band))
        self.value.setObjectName("BandValue")
        self.value.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.value.setMinimumWidth(58)
        layout.addWidget(self.value)

        self.slider.valueChanged.connect(self._on_slider)

    @staticmethod
    def _label_text(band: BiquadBand) -> str:
        return f"{_KIND_LABELS[band.kind]} · {format_freq(band.freq)}"

    def _gain_text(self, band: BiquadBand) -> str:
        if not self._shaped:
            return "fixed"
        return f"{band.gain_db:+.1f} dB"

    def _on_slider(self, value: int) -> None:
        gain = value / 2.0
        self.value.setText(f"{gain:+.1f} dB")
        self.gain_changed.emit(self._index, gain)

    def update_band(self, band: BiquadBand) -> None:
        """Sync from the controller without re-entering the signal chain."""
        self._band = band
        self._shaped = band.kind in _SHAPED
        self.name.setText(self._label_text(band))
        self.slider.blockSignals(True)
        self.slider.setEnabled(self._shaped)
        if self._shaped:
            self.slider.setValue(int(round(band.gain_db * 2.0)))
        self.slider.blockSignals(False)
        self.value.setText(self._gain_text(band))


class CurveView(QWidget):
    """The designed cascade response (design math only — never the audio path).

    Values come from the same RBJ formulas and band list that are rendered into
    the PipeWire chain, so the curve is a truthful picture of the design.
    """

    FREQ_MIN = 20.0
    FREQ_MAX = 20_000.0
    DB_MIN = -15.0
    DB_MAX = 15.0

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._bands: tuple[BiquadBand, ...] = ()
        self._freqs = np.logspace(
            np.log10(self.FREQ_MIN), np.log10(self.FREQ_MAX), 260
        )
        self.setMinimumHeight(108)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.setAccessibleName("Designed frequency response curve")

    def set_bands(self, bands: tuple[BiquadBand, ...]) -> None:
        self._bands = tuple(bands)
        self.update()

    def _curve_db(self) -> np.ndarray:
        if not self._bands:
            return np.zeros_like(self._freqs)
        db, _phase = response_db(list(self._bands), 48_000.0, self._freqs)
        return np.clip(db, self.DB_MIN, self.DB_MAX)

    def _x(self, freq: float, plot: QRectF) -> float:
        span = np.log10(self.FREQ_MAX / self.FREQ_MIN)
        return plot.left() + (
            np.log10(freq / self.FREQ_MIN) / span
        ) * plot.width()

    def _y(self, db: float, plot: QRectF) -> float:
        span = self.DB_MAX - self.DB_MIN
        return plot.top() + ((self.DB_MAX - db) / span) * plot.height()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        width, height = self.width(), self.height()
        if width < 60 or height < 60:
            painter.end()
            return

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#10141d"))
        painter.drawRoundedRect(QRectF(0, 0, width, height), 10.0, 10.0)

        plot = QRectF(32.0, 20.0, width - 42.0, height - 37.0)
        font = painter.font()
        font.setPointSizeF(7.2)
        painter.setFont(font)

        grid_pen = QPen(QColor("#222a3a"))
        grid_pen.setWidthF(1.0)
        zero_pen = QPen(QColor("#3a4560"))
        zero_pen.setWidthF(1.0)
        zero_pen.setStyle(Qt.PenStyle.DashLine)
        for db in (-12.0, -6.0, 0.0, 6.0, 12.0):
            y = self._y(db, plot)
            painter.setPen(zero_pen if db == 0.0 else grid_pen)
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            if db in (-12.0, 0.0, 12.0):
                painter.setPen(QColor("#687186"))
                painter.drawText(
                    QRectF(4.0, y - 7.0, plot.left() - 9.0, 14.0),
                    int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                    f"{db:+.0f}" if db else "0",
                )

        for freq, label in ((100.0, "100"), (1_000.0, "1k"), (10_000.0, "10k")):
            x = self._x(freq, plot)
            painter.setPen(grid_pen)
            painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            painter.setPen(QColor("#687186"))
            painter.drawText(
                QRectF(x - 18.0, plot.bottom() + 2.0, 36.0, 13.0),
                int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop),
                label,
            )

        curve = self._curve_db()
        points = [
            QPointF(self._x(float(freq), plot), self._y(float(db), plot))
            for freq, db in zip(self._freqs, curve, strict=True)
        ]
        line = QPainterPath(points[0])
        for point in points[1:]:
            line.lineTo(point)
        area = QPainterPath(line)
        area.lineTo(plot.right(), plot.bottom())
        area.lineTo(plot.left(), plot.bottom())
        area.closeSubpath()
        gradient = QLinearGradient(0.0, plot.top(), 0.0, plot.bottom())
        gradient.setColorAt(0.0, QColor(91, 124, 250, 66))
        gradient.setColorAt(1.0, QColor(91, 124, 250, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.fillPath(area, gradient)

        curve_pen = QPen(QColor("#7b96ff"))
        curve_pen.setWidthF(2.0)
        curve_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        curve_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(curve_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(line)

        painter.setPen(QColor("#687186"))
        painter.drawText(
            QRectF(9.0, 3.0, width - 60.0, 13.0),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            "DESIGNED RESPONSE",
        )
        painter.drawText(
            QRectF(width - 60.0, 3.0, 51.0, 13.0),
            int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
            "48 kHz",
        )
        painter.end()


class LevelMeter(QWidget):
    """Two stacked peak bars for the processed output (display only).

    Values are peak amplitudes in 0..1 fed by the controller from a passive
    monitor capture. The scale is dBFS from −60 dB to 0 dB: green up to the
    −12 dB guide, yellow then red at the top, plus a bright cap at the held
    level. The widget does no math on audio — it only renders what it gets.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._levels = (0.0, 0.0)
        self.setFixedHeight(32)
        self.setMinimumWidth(140)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAccessibleName("Output peak meter")
        self.setToolTip(
            "Live peak of the processed output (left bar above right bar), "
            "-60…0 dBFS; a full red bar means the device output clips "
            "(e.g. system volume above 100 %)"
        )

    def set_levels(self, left: float, right: float) -> None:
        self._levels = (max(0.0, min(1.0, float(left))), max(0.0, min(1.0, float(right))))
        self.update()

    def _fraction(self, amplitude: float) -> float:
        if amplitude <= 0.0:
            return 0.0
        db = max(LEVEL_FLOOR_DB, 20.0 * math.log10(amplitude))
        return max(0.0, min(1.0, (db - LEVEL_FLOOR_DB) / -LEVEL_FLOOR_DB))

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        width, height = float(self.width()), float(self.height())
        pad, gap = 6.0, 4.0
        bar_w, bar_h = width - pad * 2.0, (height - pad * 2.0 - gap) / 2.0

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#0e1116"))
        painter.drawRoundedRect(QRectF(0.0, 0.0, width, height), 7.0, 7.0)

        gradient = QLinearGradient(pad, 0.0, pad + bar_w, 0.0)
        gradient.setColorAt(0.0, QColor(COLORS["success"]))
        gradient.setColorAt(0.80, QColor(COLORS["success"]))
        gradient.setColorAt(0.90, QColor(COLORS["warning"]))
        gradient.setColorAt(1.0, QColor(COLORS["danger"]))

        # -12 dB guide across both bars.
        guide_x = pad + bar_w * 0.8
        painter.setBrush(QColor(255, 255, 255, 26))
        painter.drawRect(QRectF(guide_x - 0.5, pad - 3.0, 1.0, height - 2.0 * pad + 6.0))

        for row, amplitude in enumerate(self._levels):
            y = pad + row * (bar_h + gap)
            frac = self._fraction(amplitude)
            fill_w = bar_w * frac
            painter.setBrush(QColor("#171b27"))
            painter.drawRoundedRect(QRectF(pad, y, bar_w, bar_h), 3.0, 3.0)
            if fill_w > 0.5:
                painter.setBrush(gradient)
                painter.drawRoundedRect(
                    QRectF(pad, y, max(fill_w, bar_h), bar_h), 3.0, 3.0
                )
                painter.setBrush(QColor(238, 242, 246, 215))
                painter.drawRect(QRectF(pad + fill_w - 2.0, y, 2.0, bar_h))
        painter.end()


class Stat(QWidget):
    """Compact value + caption used in the engine card."""

    def __init__(
        self, caption: str, value: str = "—", parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(1)
        self.value = QLabel(value)
        self.value.setObjectName("StatValue")
        self.caption = QLabel(caption.upper())
        self.caption.setObjectName("StatCaption")
        layout.addWidget(self.value)
        layout.addWidget(self.caption)

    def set_value(self, value: str) -> None:
        self.value.setText(value)
