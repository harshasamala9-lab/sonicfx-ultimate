"""Dark, high-contrast theme for SonicFX Ultimate (Fusion + one stylesheet)."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication, QWidget

_CHECK_ICON = (Path(__file__).resolve().parent / "assets" / "check.svg").as_posix()

COLORS = {
    "bg": "#0e1118",
    "bg_alt": "#121620",
    "card": "#171b26",
    "card_hover": "#1c2130",
    "border": "#252b3a",
    "border_strong": "#323b52",
    "text": "#e8ecf6",
    "muted": "#8b93a7",
    "faint": "#6b7488",
    "accent": "#5b7cfa",
    "accent_hi": "#7b96ff",
    "accent_soft": "#1a2340",
    "violet": "#9a6bff",
    "success": "#64e2a8",
    "warning": "#ffcc66",
    "danger": "#ff7b8a",
}

_QSS = """
QWidget {
    color: #e8ecf6;
    font-family: "Ubuntu", "Inter", "Noto Sans", "DejaVu Sans", sans-serif;
    font-size: 13px;
}
QMainWindow, QWidget#Root {
    background-color: #0e1118;
}
QToolTip {
    background-color: #1d2331;
    color: #e8ecf6;
    border: 1px solid #303a52;
    border-radius: 6px;
    padding: 6px 8px;
}

QFrame#Card {
    background-color: #171b26;
    border: 1px solid #252b3a;
    border-radius: 16px;
}
QFrame#Divider {
    background-color: #252b3a;
    border: none;
    max-height: 1px;
    min-height: 1px;
}

QLabel {
    background: transparent;
}
QLabel#AppTitle {
    font-size: 27px;
    font-weight: 700;
    color: #f5f7ff;
}
QLabel#BrandTag {
    background-color: #212a44;
    color: #9db4ff;
    border: 1px solid #34406b;
    border-radius: 7px;
    padding: 2px 7px;
    font-size: 9px;
    font-weight: 700;
}
QLabel#AppSubtitle {
    color: #8b93a7;
    font-size: 12px;
}
QLabel#SectionTitle {
    font-size: 11px;
    font-weight: 700;
    color: #aab4c9;
}
QLabel#CardTitle {
    font-size: 16px;
    font-weight: 600;
    color: #eef2fc;
}
QLabel#Muted {
    color: #8b93a7;
    font-size: 11px;
}
QLabel#Faint {
    color: #6b7488;
    font-size: 10px;
}
QLabel#PresetName {
    color: #9db4ff;
    font-size: 11px;
    font-weight: 600;
}
QLabel#BandName {
    color: #d7deed;
    font-size: 12px;
    font-weight: 500;
}
QLabel#BandValue {
    color: #cdd7ea;
    font-size: 12px;
    font-weight: 600;
}
QLabel#StatValue {
    font-size: 17px;
    font-weight: 600;
    color: #eef2fc;
}
QLabel#StatCaption {
    color: #7d879d;
    font-size: 10px;
    font-weight: 600;
}
QLabel#ErrorBanner {
    background-color: rgba(255, 123, 138, 0.10);
    border: 1px solid rgba(255, 123, 138, 0.35);
    border-radius: 9px;
    color: #ffb3bd;
    padding: 7px 10px;
    font-size: 11px;
}
QLabel#StatusChip {
    border-radius: 12px;
    padding: 5px 11px;
    font-size: 11px;
    font-weight: 600;
}
QLabel#StatusChip[state="running"] {
    background-color: rgba(100, 226, 168, 0.13);
    color: #64e2a8;
}
QLabel#StatusChip[state="busy"] {
    background-color: rgba(255, 204, 102, 0.13);
    color: #ffcc66;
}
QLabel#StatusChip[state="stopped"] {
    background-color: rgba(139, 147, 167, 0.13);
    color: #9aa4ba;
}
QLabel#StatusChip[state="error"] {
    background-color: rgba(255, 123, 138, 0.13);
    color: #ff8f9b;
}

QPushButton {
    background-color: transparent;
    border: 1px solid #252b3a;
    border-radius: 9px;
    padding: 8px 14px;
    color: #d7deed;
}
QPushButton:hover {
    background-color: #1d2331;
    border-color: #323b52;
}
QPushButton:pressed {
    background-color: #161b27;
}
QPushButton:disabled {
    color: #5f687c;
    border-color: #202533;
    background-color: transparent;
}
QPushButton#Primary {
    background-color: #5b7cfa;
    border: none;
    color: #ffffff;
    padding: 10px 20px;
    font-weight: 600;
    border-radius: 10px;
}
QPushButton#Primary:hover {
    background-color: #6b8bff;
}
QPushButton#Primary:pressed {
    background-color: #4e6ce0;
}
QPushButton#Primary:disabled {
    background-color: #29314a;
    color: #707a90;
}
QPushButton#Ghost {
    background-color: transparent;
    border: 1px solid #2a3143;
    color: #c7cede;
    padding: 7px 12px;
    border-radius: 8px;
}
QPushButton#Ghost:hover {
    border-color: #4a5674;
    background-color: #1d2331;
}
QPushButton#PresetCard {
    text-align: left;
    background-color: #141822;
    border: 1px solid #222836;
    border-radius: 12px;
    padding: 0;
    min-height: 52px;
}
QPushButton#PresetCard:hover {
    background-color: #1a1f2c;
    border-color: #303a52;
}
QPushButton#PresetCard:checked {
    background-color: #1a2340;
    border: 1px solid #5b7cfa;
}
QLabel#PresetTitle {
    color: #dfe5f2;
    font-size: 13px;
    font-weight: 600;
}
QLabel#PresetTitle[active="true"] {
    color: #ffffff;
}
QLabel#PresetDesc {
    color: #8892a8;
    font-size: 10px;
}

QComboBox {
    background-color: #10141d;
    border: 1px solid #2a3143;
    border-radius: 9px;
    padding: 8px 12px;
    min-height: 18px;
    color: #e8ecf6;
}
QComboBox:hover {
    border-color: #3b4762;
}
QComboBox:focus {
    border-color: #5b7cfa;
}
QComboBox::drop-down {
    border: none;
    width: 26px;
}
QComboBox QAbstractItemView {
    background-color: #171b26;
    border: 1px solid #2a3143;
    border-radius: 8px;
    padding: 4px;
    outline: none;
    selection-background-color: #2a3a63;
    selection-color: #ffffff;
}

QCheckBox {
    spacing: 9px;
    color: #dfe5f2;
    font-size: 12px;
}
QCheckBox:disabled {
    color: #707a90;
}
QCheckBox::indicator {
    width: 17px;
    height: 17px;
    border-radius: 5px;
    border: 1px solid #30384c;
    background-color: #10141d;
}
QCheckBox::indicator:hover {
    border-color: #4a5674;
}
QCheckBox::indicator:checked {
    image: url("@CHECK_ICON@");
    background-color: #5b7cfa;
    border: 1px solid #5b7cfa;
}
QCheckBox::indicator:checked:hover {
    image: url("@CHECK_ICON@");
    background-color: #6b8bff;
    border-color: #6b8bff;
}

QSlider::groove:horizontal {
    height: 4px;
    background-color: #2a3143;
    border-radius: 2px;
}
QSlider::sub-page:horizontal {
    background-color: #5b7cfa;
    border-radius: 2px;
}
QSlider::add-page:horizontal {
    background-color: #2a3143;
    border-radius: 2px;
}
QSlider::handle:horizontal {
    width: 12px;
    height: 12px;
    margin: -5px 0;
    border-radius: 7px;
    background-color: #dfe6ff;
    border: 2px solid #5b7cfa;
}
QSlider::handle:horizontal:hover {
    background-color: #ffffff;
}
QSlider::handle:horizontal:pressed {
    background-color: #ffffff;
    border-color: #7b96ff;
}
QSlider::groove:horizontal:disabled {
    background-color: #222836;
}
QSlider::sub-page:horizontal:disabled {
    background-color: #2b3247;
}

QScrollArea {
    border: none;
    background: transparent;
}
QScrollArea > QWidget > QWidget {
    background: transparent;
}
QScrollBar:vertical {
    background: transparent;
    width: 8px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background-color: #2c3448;
    border-radius: 4px;
    min-height: 28px;
}
QScrollBar::handle:vertical:hover {
    background-color: #3a4560;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
    background: none;
}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: none;
}
QScrollBar:horizontal {
    height: 8px;
    background: transparent;
}
QScrollBar::handle:horizontal {
    background-color: #2c3448;
    border-radius: 4px;
    min-width: 28px;
}
""".replace("@CHECK_ICON@", _CHECK_ICON)


def apply_theme(app: QApplication) -> None:
    """Apply Fusion, a dark palette and the application stylesheet once."""
    app.setStyle("Fusion")

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(COLORS["bg"]))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(COLORS["text"]))
    palette.setColor(QPalette.ColorRole.Base, QColor(COLORS["bg_alt"]))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(COLORS["card"]))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(COLORS["card_hover"]))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(COLORS["text"]))
    palette.setColor(QPalette.ColorRole.Text, QColor(COLORS["text"]))
    palette.setColor(QPalette.ColorRole.Button, QColor(COLORS["card"]))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(COLORS["text"]))
    palette.setColor(QPalette.ColorRole.BrightText, QColor(COLORS["danger"]))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(COLORS["accent"]))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    palette.setColor(
        QPalette.ColorGroup.Disabled,
        QPalette.ColorRole.Text,
        QColor(COLORS["faint"]),
    )
    palette.setColor(
        QPalette.ColorGroup.Disabled,
        QPalette.ColorRole.ButtonText,
        QColor(COLORS["faint"]),
    )
    app.setPalette(palette)
    app.setFont(QFont("Ubuntu", 10))
    app.setStyleSheet(_QSS)


def repolish(widget: QWidget) -> None:
    """Re-evaluate dynamic-property selectors after changing a property."""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()
