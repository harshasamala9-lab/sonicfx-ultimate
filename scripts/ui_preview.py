#!/usr/bin/env python3
"""Render a deterministic offscreen preview of the SonicFX window.

Usage::

    .venv/bin/python scripts/ui_preview.py [output.png]

This runs against a fake controller: it never touches PipeWire, never changes
the default sink and never plays audio. The PNG is used for documentation and
visual review only.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

# Must be set before PySide6 creates a platform integration.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Isolate window-geometry QSettings from the user's real configuration.
_PREVIEW_HOME = Path("/tmp/opencode/sonicfx-preview-home")
shutil.rmtree(_PREVIEW_HOME, ignore_errors=True)
os.environ["XDG_CONFIG_HOME"] = str(_PREVIEW_HOME)

from PySide6.QtCore import QObject, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from sonicfx.audio.engine import EngineStatus  # noqa: E402
from sonicfx.pipewire.wiring import SinkInfo  # noqa: E402
from sonicfx.profiles.presets import get_preset  # noqa: E402
from sonicfx.ui.main_window import MainWindow  # noqa: E402
from sonicfx.ui.theme import apply_theme  # noqa: E402


class PreviewController(QObject):
    """Drop-in controller stand-in with realistic sample values."""

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
        self._preset_key = "best_all_in_one"
        self._output = "bluez_output.30_C0_1B_D2_9B_F2.1"
        self._running = True
        self._busy = False
        self._status = EngineStatus(
            running=True,
            pid=48213,
            sink_name="sonicfx_sink",
            generation=4,
            output=self._output,
            gap_ms=144.6,
        )

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
        return True

    def bootstrap(self) -> None:
        sinks = [
            SinkInfo(
                "alsa_output.pci-0000_00_1f.3.analog-stereo",
                57,
                "Built-in Audio Analog Stereo",
            ),
            SinkInfo(
                "bluez_output.30_C0_1B_D2_9B_F2.1",
                503,
                "JBL Flip 5",
            ),
        ]
        self.sinks_changed.emit(sinks)
        self.output_changed.emit(self._output)
        self.preset_changed.emit(self._preset_key)
        self.bands_changed.emit(get_preset(self._preset_key).bands)
        self.custom_changed.emit(False)
        self.use_default_changed.emit(True)
        self.login_autostart_changed.emit(True)
        self.route_changed.emit(True)
        self.status_changed.emit(self._status)
        self.state_changed.emit(self._running)
        # Sample "music playing" peaks so the meter is visible in the preview.
        self.meter_changed.emit(0.55, 0.42)

    def select_preset(self, key: str) -> None:
        self._preset_key = key
        self.preset_changed.emit(key)
        self.bands_changed.emit(get_preset(key).bands)
        self.custom_changed.emit(False)

    def reset_preset(self) -> None:
        self.select_preset(self._preset_key)

    def set_band_gain(self, index: int, gain: float) -> None:
        pass

    def set_output(self, name: str) -> None:
        self._output = name
        self.output_changed.emit(name)

    def refresh_sinks(self) -> None:
        self.bootstrap()

    def set_enabled(self, enabled: bool) -> None:
        self._running = enabled
        self.state_changed.emit(enabled)
        if not enabled:
            self.status_changed.emit(
                EngineStatus(False, None, None, self._status.generation)
            )

    def set_use_default(self, enabled: bool) -> None:
        self.use_default_changed.emit(enabled)
        self.route_changed.emit(enabled)

    def set_login_autostart(self, enabled: bool) -> None:
        self.login_autostart_changed.emit(enabled)

    def shutdown(self) -> None:
        pass


def main() -> int:
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("docs/ui-preview.png")
    app = QApplication(["sonicfx-preview"])
    app.setApplicationName("SonicFX Ultimate")
    apply_theme(app)

    window = MainWindow(PreviewController())
    window.resize(1160, 680)
    window.show()
    app.processEvents()
    app.processEvents()

    output.parent.mkdir(parents=True, exist_ok=True)
    if not window.grab().save(str(output)):
        print(f"could not write {output}", file=sys.stderr)
        return 1
    print(output)
    window.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
