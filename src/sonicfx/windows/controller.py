"""Windows controller: presets → Equalizer APO, plus WASAPI loopback metering.

The public surface intentionally mirrors :class:`sonicfx.ui.controller.Controller`
so the existing Qt window can be reused.  The Windows backend does not perform
default-device routing: Equalizer APO applies to the render devices selected in
its own Device Selector, which is safer and matches how Windows audio works.
"""
from __future__ import annotations

import json
import math
import os
import sys
from dataclasses import dataclass, replace

from PySide6.QtCore import QObject, QSettings, QThread, QTimer, Signal, Slot

from sonicfx.dsp.filters import BiquadBand, FilterKind
from sonicfx.logging_setup import get_logger
from sonicfx.profiles.presets import DEFAULT_PRESET_KEY, get_preset, preset_keys
from sonicfx.windows import autostart
from sonicfx.windows.apo import ApoError, EqualizerApo
from sonicfx.windows.meter import WindowsMeterWorker, available_speakers

log = get_logger("ui.windows_controller")

_SHAPED = frozenset({FilterKind.PEAKING, FilterKind.LOW_SHELF, FilterKind.HIGH_SHELF})
_GAIN_LIMIT_DB = 12.0
_DEBOUNCE_MS = 300
_PEAK_FLOOR_DB = -60.0
_PEAK_DECAY_DB_PER_S = 10.0
_METER_TICK_MS = 100


def _amp_to_db(amplitude: float) -> float:
    return _PEAK_FLOOR_DB if amplitude <= 0.0 else max(
        _PEAK_FLOOR_DB, 20.0 * math.log10(amplitude)
    )


def _db_to_amp(db: float) -> float:
    return 0.0 if db <= _PEAK_FLOOR_DB else 10.0 ** (db / 20.0)


@dataclass(frozen=True)
class WindowsEngineStatus:
    """Observed Windows backend state."""

    running: bool
    pid: int | None = None
    sink_name: str | None = None
    generation: int = 0
    output: str | None = None
    gap_ms: float | None = None
    last_error: str | None = None
    backend: str = "Equalizer APO"


class WindowsController(QObject):
    """Controller for the Equalizer APO-backed Windows build."""

    status_changed = Signal(object)
    error = Signal(str)
    state_changed = Signal(bool)
    busy_changed = Signal(bool)
    meter_changed = Signal(float, float)
    preset_changed = Signal(str)
    bands_changed = Signal(object)
    custom_changed = Signal(bool)
    sinks_changed = Signal(object)
    output_changed = Signal(str)
    route_changed = Signal(bool)
    use_default_changed = Signal(bool)
    login_autostart_changed = Signal(bool)

    backend_name = "equalizer_apo"
    supports_routing = False
    subtitle = "Cinema-style clarity for Windows — native Equalizer APO processing."
    footer_note = (
        "DSP runs inside Equalizer APO (native C++) · stop the engine to bypass "
        "it completely"
    )
    routing_title = "Windows audio backend"
    routing_hint = (
        "Equalizer APO applies to the outputs you enable in its Device Selector. "
        "Open it once after installation."
    )
    device_label = "Meter output"
    device_hint = (
        "Choose the output whose live peak you want to watch. APO processing is "
        "configured per device in Equalizer APO."
    )
    stat_gap_caption = "Apply time"
    stat_route_caption = "Backend"
    stat_output_caption = "Meter output"

    def __init__(
        self,
        apo: EqualizerApo | None = None,
        settings: QSettings | None = None,
        *,
        meter_factory=None,
        speaker_provider=available_speakers,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings or QSettings("SonicFX", "SonicFX Ultimate")
        self._apo = apo or EqualizerApo()
        self._speaker_provider = speaker_provider
        self._preset_key = self._read_str("preset", "best_all_in_one")
        if self._preset_key not in preset_keys():
            self._preset_key = DEFAULT_PRESET_KEY
        self._bands = self._load_bands(self._preset_key)
        self._output = self._read_str("output_device", "")
        self._auto_start = self._read_bool("auto_start_engine", True)
        self._login_autostart = autostart.is_enabled()
        self._running = False
        self._busy = False
        self._shutdown = False
        self._pending_apply = False
        self._generation = 0
        self._last_error: str | None = None
        self._status = WindowsEngineStatus(running=False)
        self._peak_db = (_PEAK_FLOOR_DB, _PEAK_FLOOR_DB)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(_DEBOUNCE_MS)
        self._debounce.timeout.connect(self._apply_now)

        self._meter_tick = QTimer(self)
        self._meter_tick.setInterval(_METER_TICK_MS)
        self._meter_tick.timeout.connect(self._decay_and_emit)

        factory = meter_factory or WindowsMeterWorker
        self._meter_worker = factory()
        self._meter_worker.levels.connect(self._on_meter_levels)
        unavailable = getattr(self._meter_worker, "unavailable", None)
        if unavailable is not None:
            unavailable.connect(self._on_meter_unavailable)
        set_device = getattr(self._meter_worker, "set_device", None)
        if set_device is not None:
            set_device(self._output or None)
        self._meter_worker.start()

    # -- read-only state ---------------------------------------------------
    @property
    def preset_key(self) -> str:
        return self._preset_key

    @property
    def bands(self) -> tuple[BiquadBand, ...]:
        return self._bands

    @property
    def output(self) -> str:
        return self._output

    @property
    def use_default(self) -> bool:
        return False

    @property
    def running(self) -> bool:
        return self._running

    @property
    def busy(self) -> bool:
        return self._busy

    @property
    def status(self) -> WindowsEngineStatus:
        return self._status

    @property
    def route_active(self) -> bool:
        return False

    @property
    def meter_peak_db(self) -> tuple[float, float]:
        return self._peak_db

    def login_autostart_enabled(self) -> bool:
        return self._login_autostart

    def is_custom(self) -> bool:
        base = get_preset(self._preset_key).bands
        if len(base) != len(self._bands):
            return True
        return any(
            abs(a.gain_db - b.gain_db) > 1e-6
            for a, b in zip(base, self._bands, strict=False)
            if a.kind in _SHAPED
        )

    # -- lifecycle ---------------------------------------------------------
    def bootstrap(self) -> None:
        self._sync_login_autostart()
        self.refresh_sinks()
        self.preset_changed.emit(self._preset_key)
        self.bands_changed.emit(self._bands)
        self.custom_changed.emit(self.is_custom())
        self.use_default_changed.emit(False)
        self.login_autostart_changed.emit(self._login_autostart)
        self.route_changed.emit(False)
        status = self._apo.status()
        if not status.installed:
            self._last_error = status.detail
            self.error.emit(status.detail)
        elif status.include_active:
            # A previous run may have crashed before removing its include.
            # Adopt that active Equalizer APO state instead of showing a
            # misleading "Stopped" UI while audio is still being processed.
            self._running = True
            self._pending_apply = False
            self._meter_tick.start()
            set_device = getattr(self._meter_worker, "set_device", None)
            if set_device is not None:
                set_device(self._output or None)
            log.info("Adopted an active Equalizer APO include from a prior run")
        self._emit_status()
        if self._running:
            self.state_changed.emit(True)
        if self._auto_start and status.installed and not self._running:
            QTimer.singleShot(250, self.start_engine)

    def shutdown(self) -> None:
        if self._shutdown:
            return
        self._shutdown = True
        self._debounce.stop()
        self._pending_apply = False
        self._meter_tick.stop()
        self._peak_db = (_PEAK_FLOOR_DB, _PEAK_FLOOR_DB)
        self.meter_changed.emit(0.0, 0.0)
        stop = getattr(self._meter_worker, "stop", None)
        if stop is not None:
            stop()
        if isinstance(self._meter_worker, QThread):
            if not self._meter_worker.wait(2_000):
                log.warning("Windows meter worker did not finish in time")
                self._meter_worker.requestInterruption()
                self._meter_worker.terminate()
                self._meter_worker.wait(500)
        if self._running:
            try:
                self._apo.disable()
            except ApoError:
                log.exception("Equalizer APO disable failed during shutdown")
        self._running = False
        self._settings.sync()

    # -- engine control ----------------------------------------------------
    def start_engine(self) -> None:
        if self._shutdown or self._running or self._busy:
            return
        status = self._apo.status()
        if not status.installed:
            self._show_error(status.detail)
            return
        self._set_busy(True)
        try:
            self._apo.apply(self._bands)
        except ApoError as exc:
            self._set_busy(False)
            self._last_error = str(exc)
            self._show_error(str(exc))
            self._emit_status()
            return
        except OSError as exc:
            self._set_busy(False)
            self._last_error = str(exc)
            self._show_error(
                f"Windows denied access to Equalizer APO's config folder: {exc}. "
                "Run the installer as administrator so it can grant write access."
            )
            self._emit_status()
            return
        self._busy = False
        self._running = True
        self._pending_apply = False
        self._last_error = None
        self._generation += 1
        set_device = getattr(self._meter_worker, "set_device", None)
        if set_device is not None:
            set_device(self._output or None)
        self._meter_tick.start()
        self._status = WindowsEngineStatus(
            running=True,
            sink_name="equalizer_apo",
            generation=self._generation,
            output=self._output,
            gap_ms=0.0,
        )
        self.state_changed.emit(True)
        self.status_changed.emit(self._status)
        self.busy_changed.emit(False)
        log.info("Equalizer APO backend enabled with preset %s", self._preset_key)

    def stop_engine(self) -> None:
        if self._shutdown or self._busy:
            return
        self._debounce.stop()
        self._pending_apply = False
        if not self._running:
            return
        try:
            self._apo.disable()
        except (ApoError, OSError) as exc:
            self._show_error(f"Could not disable Equalizer APO: {exc}")
            return
        self._running = False
        self._generation += 1
        self._meter_tick.stop()
        set_device = getattr(self._meter_worker, "set_device", None)
        if set_device is not None:
            set_device(None)
        self._peak_db = (_PEAK_FLOOR_DB, _PEAK_FLOOR_DB)
        self.meter_changed.emit(0.0, 0.0)
        self._status = WindowsEngineStatus(
            running=False,
            sink_name="equalizer_apo",
            generation=self._generation,
            output=self._output,
        )
        self.state_changed.emit(False)
        self.status_changed.emit(self._status)
        log.info("Equalizer APO backend disabled")

    def set_enabled(self, enabled: bool) -> None:
        if enabled:
            self.start_engine()
        else:
            self.stop_engine()

    def apply_now(self) -> None:
        self._debounce.stop()
        self._apply_now()

    # -- user edits --------------------------------------------------------
    def select_preset(self, key: str) -> None:
        try:
            preset = get_preset(key)
        except KeyError as exc:
            self._show_error(str(exc))
            return
        self._preset_key = key
        self._bands = preset.bands
        self._settings.setValue("preset", key)
        self._persist_bands()
        self.preset_changed.emit(key)
        self.bands_changed.emit(self._bands)
        self.custom_changed.emit(False)
        if self._running:
            self._schedule_apply()

    def reset_preset(self) -> None:
        self.select_preset(self._preset_key)

    def set_band_gain(self, index: int, gain_db: float) -> None:
        if not 0 <= index < len(self._bands):
            return
        band = self._bands[index]
        if band.kind not in _SHAPED:
            return
        gain = max(-_GAIN_LIMIT_DB, min(_GAIN_LIMIT_DB, float(gain_db)))
        if abs(band.gain_db - gain) < 1e-9:
            return
        self._bands = (
            self._bands[:index]
            + (replace(band, gain_db=gain),)
            + self._bands[index + 1 :]
        )
        self._persist_bands()
        self.bands_changed.emit(self._bands)
        self.custom_changed.emit(self.is_custom())
        if self._running:
            self._schedule_apply()

    def set_output(self, name: str) -> None:
        if not name or name == self._output:
            return
        self._output = name
        self._settings.setValue("output_device", name)
        self.output_changed.emit(name)
        set_device = getattr(self._meter_worker, "set_device", None)
        if set_device is not None:
            set_device(name)
        if self._running:
            self._peak_db = (_PEAK_FLOOR_DB, _PEAK_FLOOR_DB)

    def refresh_sinks(self) -> None:
        sinks = list(self._speaker_provider())
        names = [sink.name for sink in sinks]
        if self._output not in names:
            chosen = next((sink.name for sink in sinks if sink.is_default), "")
            self._output = chosen or (names[0] if names else "")
            self._settings.setValue("output_device", self._output)
            set_device = getattr(self._meter_worker, "set_device", None)
            if set_device is not None:
                set_device(self._output or None)
        self.sinks_changed.emit(sinks)
        self.output_changed.emit(self._output)

    def set_use_default(self, enabled: bool) -> None:
        # Windows deliberately has no default-sink switch. Equalizer APO is
        # installed per endpoint in its Device Selector.
        self.use_default_changed.emit(False)
        self.route_changed.emit(False)

    def set_login_autostart(self, enabled: bool) -> None:
        try:
            if enabled:
                autostart.enable()
            else:
                autostart.disable()
        except OSError as exc:
            self._show_error(f"Could not update login autostart: {exc}")
            self.login_autostart_changed.emit(self._login_autostart)
            return
        self._login_autostart = enabled
        self._settings.setValue("login_autostart", enabled)
        self.login_autostart_changed.emit(enabled)

    def open_configurator(self) -> None:
        status = self._apo.status()
        if status.configurator is None:
            self._show_error(
                "Equalizer APO's Device Selector was not found. Reinstall "
                "Equalizer APO, then reopen SonicFX."
            )
            return
        if sys.platform != "win32":
            self._show_error("Equalizer APO's Device Selector only runs on Windows.")
            return
        try:
            os.startfile(str(status.configurator))  # type: ignore[attr-defined]
        except OSError as exc:
            self._show_error(f"Could not open Equalizer APO: {exc}")

    # -- internal ----------------------------------------------------------
    def _emit_status(self) -> None:
        self._status = WindowsEngineStatus(
            running=self._running,
            sink_name="equalizer_apo",
            generation=self._generation,
            output=self._output,
            gap_ms=0.0 if self._running else None,
            last_error=self._last_error,
        )
        self.status_changed.emit(self._status)

    def _schedule_apply(self) -> None:
        if not self._running:
            return
        self._pending_apply = True
        self._debounce.start()

    def _apply_now(self) -> None:
        if self._shutdown or not self._pending_apply or not self._running:
            return
        self._pending_apply = False
        try:
            self._apo.apply(self._bands)
        except (ApoError, OSError) as exc:
            self._last_error = str(exc)
            self._show_error(f"Could not update Equalizer APO: {exc}")
            self._emit_status()
            return
        self._generation += 1
        self._last_error = None
        self._emit_status()

    @Slot(float, float)
    def _on_meter_levels(self, left: float, right: float) -> None:
        if self._shutdown or not self._running:
            return
        self._peak_db = (
            max(self._peak_db[0], _amp_to_db(left)),
            max(self._peak_db[1], _amp_to_db(right)),
        )
        self.meter_changed.emit(
            _db_to_amp(self._peak_db[0]), _db_to_amp(self._peak_db[1])
        )

    @Slot()
    def _decay_and_emit(self) -> None:
        drop = _PEAK_DECAY_DB_PER_S * (_METER_TICK_MS / 1000.0)
        self._peak_db = (
            max(_PEAK_FLOOR_DB, self._peak_db[0] - drop),
            max(_PEAK_FLOOR_DB, self._peak_db[1] - drop),
        )
        self.meter_changed.emit(
            _db_to_amp(self._peak_db[0]), _db_to_amp(self._peak_db[1])
        )

    @Slot(str)
    def _on_meter_unavailable(self, message: str) -> None:
        # Metering is a monitor path, not the DSP. Never turn a missing optional
        # loopback device into a fake level or a blocked engine.
        log.warning("%s", message)

    def _sync_login_autostart(self) -> None:
        if sys.platform != "win32":
            # Allows the controller and window to be tested on non-Windows
            # hosts without writing into a real Startup folder.
            self._login_autostart = False
            return
        want = self._read_bool("login_autostart", True)
        if want != self._login_autostart:
            try:
                if want:
                    autostart.enable()
                else:
                    autostart.disable()
                self._login_autostart = want
            except OSError:
                log.exception("initial Windows login autostart setup failed")
                self._login_autostart = False
        self._settings.setValue("login_autostart", self._login_autostart)

    def _set_busy(self, busy: bool) -> None:
        if self._busy == busy:
            return
        self._busy = busy
        self.busy_changed.emit(busy)

    def _show_error(self, message: str) -> None:
        log.error("%s", message)
        self.error.emit(message)

    def _persist_bands(self) -> None:
        self._settings.setValue(
            "band_gains", json.dumps([round(b.gain_db, 2) for b in self._bands])
        )
        self._settings.sync()

    def _load_bands(self, key: str) -> tuple[BiquadBand, ...]:
        preset = get_preset(key)
        raw = self._settings.value("band_gains")
        if not isinstance(raw, str):
            return preset.bands
        try:
            gains = json.loads(raw)
        except json.JSONDecodeError:
            return preset.bands
        if not isinstance(gains, list) or len(gains) != len(preset.bands):
            return preset.bands
        loaded: list[BiquadBand] = []
        try:
            for band, gain in zip(preset.bands, gains, strict=True):
                value = float(gain)
                if band.kind in _SHAPED:
                    if not -_GAIN_LIMIT_DB <= value <= _GAIN_LIMIT_DB:
                        return preset.bands
                    loaded.append(replace(band, gain_db=value))
                else:
                    loaded.append(band)
        except (TypeError, ValueError):
            return preset.bands
        return tuple(loaded)

    def _read_bool(self, key: str, default: bool) -> bool:
        value = self._settings.value(key, default)
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    def _read_str(self, key: str, default: str) -> str:
        value = self._settings.value(key, default)
        return str(value) if value is not None else default
