"""GUI ↔ engine controller.

Owns the only mutable UI state, persists it with :class:`QSettings`, debounces
DSP edits into supervised restarts, and keeps system-wide routing safe:

* routing is only attempted after the chain is running and wired;
* the previous default sink is restored on stop, routing-off, apply failure,
  app exit, and after a crash at next launch;
* engine work runs in a worker thread, so the window never blocks on PipeWire;
* a second background worker reads a passive ``*.monitor`` capture of the
  active output device and drives the live Peak meter (never the audio path).
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
from dataclasses import replace

from PySide6.QtCore import (
    QMetaObject,
    QObject,
    QSettings,
    QThread,
    QTimer,
    Qt,
    Signal,
    Slot,
)

from sonicfx import autostart
from sonicfx.audio.engine import Engine, EngineStatus
from sonicfx.dsp.filters import BiquadBand, FilterKind
from sonicfx.logging_setup import get_logger
from sonicfx.pipewire import tap as tap_module
from sonicfx.pipewire.chain import ChainSpec
from sonicfx.pipewire.routing import DefaultSinkRouter, RoutingError
from sonicfx.pipewire.tap import LevelTap, TapError
from sonicfx.pipewire.wiring import SinkInfo, default_sink_name, list_sinks
from sonicfx.profiles.presets import DEFAULT_PRESET_KEY, get_preset, preset_keys

log = get_logger("ui.controller")

_SHAPED = frozenset({FilterKind.PEAKING, FilterKind.LOW_SHELF, FilterKind.HIGH_SHELF})
_GAIN_LIMIT_DB = 12.0
_DEBOUNCE_MS = 300

# Peak meter: held-peak display semantics (dB domain).
_PEAK_FLOOR_DB = -60.0
_PEAK_DECAY_DB_PER_S = 10.0
_METER_TICK_MS = 100


def _amp_to_db(amplitude: float) -> float:
    return _PEAK_FLOOR_DB if amplitude <= 0.0 else max(
        _PEAK_FLOOR_DB, 20.0 * math.log10(amplitude)
    )


def _db_to_amp(db: float) -> float:
    return 0.0 if db <= _PEAK_FLOOR_DB else 10.0 ** (db / 20.0)


class EngineWorker(QObject):
    """Runs blocking engine calls off the GUI thread (one operation at a time)."""

    status_ready = Signal(object)
    failed = Signal(str, object)

    def __init__(self, engine: Engine) -> None:
        super().__init__()
        self._engine = engine

    @Slot(object, object)
    def start(self, spec: ChainSpec, output: str) -> None:
        try:
            self.status_ready.emit(self._engine.start(spec, output=output))
        except Exception as exc:  # noqa: BLE001 - boundary; report, never crash Qt
            log.exception("engine start failed")
            self.failed.emit(str(exc), self._engine.status())

    @Slot(object, object)
    def apply(self, spec: ChainSpec, output: str) -> None:
        try:
            self.status_ready.emit(self._engine.apply(spec, output=output))
        except Exception as exc:  # noqa: BLE001
            log.exception("engine apply failed")
            self.failed.emit(str(exc), self._engine.status())

    @Slot()
    def bypass(self) -> None:
        try:
            self.status_ready.emit(self._engine.bypass())
        except Exception as exc:  # noqa: BLE001
            log.exception("engine stop failed")
            self.failed.emit(str(exc), self._engine.status())

    @Slot()
    def shutdown(self) -> None:
        self._engine.shutdown()


class MeterWorker(QThread):
    """Background monitor reader for the live Peak meter.

    One attempt owns one :class:`LevelTap` on a ``*.monitor`` node. The loop
    asks a provider for the current target (empty = meter off), captures it,
    emits peaks, and restarts with a fresh tap when the stream ends, when the
    target disappears, or when :meth:`refresh` signals a routing change. The
    GUI thread only ever sees :attr:`levels`, as raw amplitudes.
    """

    levels = Signal(float, float)

    READ_TIMEOUT_S = 0.15
    RETRY_DELAY_S = 0.75
    IDLE_WAIT_S = 0.2
    MIN_LIFETIME_S = 1.0  # shorter means the capture failed to start

    def __init__(
        self,
        target_provider,
        tap_factory=LevelTap,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._target_provider = target_provider
        self._tap_factory = tap_factory
        self._refresh = threading.Event()
        self._stop = threading.Event()

    def refresh(self) -> None:
        """Restart the capture: the chain, output device or state changed."""
        self._refresh.set()

    def stop(self) -> None:
        """Stop the loop; pair with :meth:`wait` from the caller."""
        self._stop.set()
        self._refresh.set()

    def run(self) -> None:
        capturing = False
        while True:
            if self._stop.is_set():
                break
            target = self._target_provider()
            if not target:
                self._refresh.clear()
                if capturing:
                    capturing = False  # one final drain-to-zero, then silence
                    self.levels.emit(0.0, 0.0)
                if self._stop.wait(self.IDLE_WAIT_S):
                    break
                continue
            self._refresh.clear()
            started = time.monotonic()
            try:
                tap = self._tap_factory(target)
                tap.start()
            except TapError as exc:
                log.warning("peak meter target %r unavailable: %s", target, exc)
                self.levels.emit(0.0, 0.0)
                if self._stop.wait(self.RETRY_DELAY_S):
                    break
                continue
            capturing = True
            try:
                outcome = self._drain(tap, target)
            except Exception:  # noqa: BLE001 - the meter must never crash Qt
                log.exception("peak meter drain failed")
                self.levels.emit(0.0, 0.0)
                outcome = "dead"
            lifetime = time.monotonic() - started
            tap.stop()
            if outcome == "stop":
                break
            backoff = 0.0 if lifetime >= self.MIN_LIFETIME_S else self.RETRY_DELAY_S
            if backoff and self._stop.wait(backoff):
                break
        self.levels.emit(0.0, 0.0)

    def _drain(self, tap: LevelTap, target: str) -> str:
        """Read one tap until stop/refresh/target-change/stream end."""
        while True:
            if self._stop.is_set():
                return "stop"
            if self._refresh.is_set():
                self._refresh.clear()
                return "refresh"
            # A refresh can be consumed while the capture is still starting;
            # compare the target itself so a device change is never lost and
            # the meter cannot keep reading the old device's monitor.
            current = self._target_provider()
            if not current or current != target:
                return "retarget"
            levels = tap.read(timeout=self.READ_TIMEOUT_S)
            if levels is None:
                self.levels.emit(0.0, 0.0)  # stream is over: drain the bar
                return "dead"
            self.levels.emit(levels[0], levels[1])


class Controller(QObject):
    """Application controller; the window only reads and calls this surface."""

    start_requested = Signal(object, object)
    apply_requested = Signal(object, object)
    bypass_requested = Signal()

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

    def __init__(
        self,
        engine: Engine | None = None,
        settings: QSettings | None = None,
        router: DefaultSinkRouter | None = None,
        *,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings or QSettings("SonicFX", "SonicFX Ultimate")
        self._engine = engine or Engine()
        self._router = router or DefaultSinkRouter()
        self._recover_stale_route()

        self._preset_key = self._read_str("preset", "songs")
        if self._preset_key not in preset_keys():
            self._preset_key = DEFAULT_PRESET_KEY
        self._bands = self._load_bands(self._preset_key)
        self._output = self._read_str("output_device", "")
        self._use_default = self._read_bool("use_as_default", True)
        self._auto_start = self._read_bool("auto_start_engine", True)
        self._login_autostart = autostart.is_enabled()

        self._running = False
        self._busy = False
        self._pending_apply = False
        self._route_active = False
        self._status: EngineStatus = self._engine.status()
        self._shutdown = False

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(_DEBOUNCE_MS)
        self._debounce.timeout.connect(self._apply_now)

        self._thread = QThread(self)
        self._worker = EngineWorker(self._engine)
        self._worker.moveToThread(self._thread)
        self._worker.status_ready.connect(self._on_status)
        self._worker.failed.connect(self._on_failed)
        self.start_requested.connect(
            self._worker.start, Qt.ConnectionType.QueuedConnection
        )
        self.apply_requested.connect(
            self._worker.apply, Qt.ConnectionType.QueuedConnection
        )
        self.bypass_requested.connect(
            self._worker.bypass, Qt.ConnectionType.QueuedConnection
        )
        self._thread.start()

        # Live peak meter: a separate worker reads a passive *.monitor* copy of
        # the active output device; the held-peak state and smoothing live here.
        self._peak_db = (_PEAK_FLOOR_DB, _PEAK_FLOOR_DB)
        self._meter_worker = MeterWorker(self._meter_target)
        self._meter_worker.levels.connect(self._on_meter_levels)
        self._meter_worker.start()
        self._meter_tick = QTimer(self)
        self._meter_tick.setInterval(_METER_TICK_MS)
        self._meter_tick.timeout.connect(self._decay_and_emit)

    # -- read-only state for the window ------------------------------------
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
        return self._use_default

    @property
    def running(self) -> bool:
        return self._running

    @property
    def busy(self) -> bool:
        return self._busy

    @property
    def status(self) -> EngineStatus:
        return self._status

    @property
    def route_active(self) -> bool:
        return self._route_active

    @property
    def meter_peak_db(self) -> tuple[float, float]:
        """Held peak levels in dBFS (L, R); ``-60`` means digital silence."""
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
        """Emit the initial state once the window has connected its signals."""
        self._sync_login_autostart()
        self.refresh_sinks()
        self.preset_changed.emit(self._preset_key)
        self.bands_changed.emit(self._bands)
        self.custom_changed.emit(self.is_custom())
        self.use_default_changed.emit(self._use_default)
        self.login_autostart_changed.emit(self._login_autostart)
        self.route_changed.emit(self._route_active)
        if self._auto_start:
            QTimer.singleShot(250, self.start_engine)

    def shutdown(self) -> None:
        """Stop the engine, restore routing, and join the worker threads."""
        if self._shutdown:
            return
        self._shutdown = True
        self._debounce.stop()
        self._pending_apply = False
        self._meter_tick.stop()
        self._peak_db = (_PEAK_FLOOR_DB, _PEAK_FLOOR_DB)
        self.meter_changed.emit(0.0, 0.0)
        self._meter_worker.stop()
        if not self._meter_worker.wait(2_000):
            log.warning("meter worker did not finish in time; terminating")
            self._meter_worker.terminate()
            if not self._meter_worker.wait(1_000):
                log.error("meter worker thread refused to exit")
        self._restore_route()
        if self._thread.isRunning():
            QMetaObject.invokeMethod(
                self._worker,
                "shutdown",
                Qt.ConnectionType.BlockingQueuedConnection,
            )
            self._thread.quit()
            if not self._thread.wait(5_000):
                log.warning("engine worker thread did not finish in time")
        # A status that raced with shutdown must not leave SonicFX as default.
        self._restore_route()
        self._settings.sync()

    # -- engine control ----------------------------------------------------
    def start_engine(self) -> None:
        if self._shutdown or self._running or self._busy:
            return
        if not self._output:
            self.refresh_sinks()
        if not self._output:
            self._show_error(
                "No output device is available. Connect speakers or headphones "
                "and press Refresh."
            )
            return
        try:
            spec = self._spec()
        except Exception as exc:  # noqa: BLE001 - invalid profile guard
            self._show_error(f"Could not build the audio chain: {exc}")
            return
        self._set_busy(True)
        self.start_requested.emit(spec, self._output)

    def stop_engine(self) -> None:
        if self._shutdown or self._busy:
            return
        self._debounce.stop()
        self._pending_apply = False
        if not self._running:
            self._restore_route()
            return
        self._restore_route()
        self._set_busy(True)
        self.bypass_requested.emit()

    def set_enabled(self, enabled: bool) -> None:
        if enabled:
            self.start_engine()
        else:
            self.stop_engine()

    def apply_now(self) -> None:
        """Force pending edits to apply immediately (used by tests/UI)."""
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
        log.info("preset selected: %s", key)

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
        names = {sink.name for sink in list_sinks()}
        if name not in names:
            self._show_error(f"Output device {name!r} is no longer available.")
            self.refresh_sinks()
            return
        self._output = name
        self._settings.setValue("output_device", name)
        self.output_changed.emit(name)
        if self._running:
            self._meter_worker.refresh()  # tap the new device's monitor
            self._schedule_apply()

    def refresh_sinks(self) -> None:
        sinks: list[SinkInfo] = list_sinks()
        names = [sink.name for sink in sinks]
        if self._output not in names:
            chosen = next((sink.name for sink in sinks if sink.is_default), "")
            previous = self._output
            self._output = chosen or (names[0] if names else "")
            self._settings.setValue("output_device", self._output)
            if self._running and self._output != previous:
                # The old device vanished: follow it with the meter and rewire
                # the running chain instead of silently pointing at a dead sink.
                self._meter_worker.refresh()
                if self._output:
                    self._schedule_apply()
        self.sinks_changed.emit(sinks)
        self.output_changed.emit(self._output)

    def set_use_default(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._use_default:
            return
        self._use_default = enabled
        self._settings.setValue("use_as_default", enabled)
        self.use_default_changed.emit(enabled)
        if enabled:
            if self._running:
                self._set_busy(True)
                try:
                    self._sync_route()
                finally:
                    self._set_busy(False)
        else:
            self._restore_route()
        log.info("system default routing %s", "enabled" if enabled else "disabled")

    def set_login_autostart(self, enabled: bool) -> None:
        try:
            if enabled:
                autostart.enable()
            else:
                autostart.disable()
        except OSError as exc:
            log.exception("login autostart change failed")
            self._show_error(f"Could not update login autostart: {exc}")
            self.login_autostart_changed.emit(self._login_autostart)
            return
        self._login_autostart = enabled
        self._settings.setValue("login_autostart", enabled)
        self.login_autostart_changed.emit(enabled)

    # -- engine signal handlers -------------------------------------------
    @Slot(object)
    def _on_status(self, status: EngineStatus) -> None:
        if self._shutdown:
            return
        self._status = status
        self._running = status.running
        self._set_busy(False)
        self._meter_worker.refresh()
        if self._running:
            self._meter_tick.start()
        else:
            self._meter_tick.stop()
            self._peak_db = (_PEAK_FLOOR_DB, _PEAK_FLOOR_DB)
            self.meter_changed.emit(0.0, 0.0)
        if status.running:
            if self._use_default:
                self._sync_route(status.sink_name)
            else:
                self._route_active = False
                self.route_changed.emit(False)
        else:
            self._route_active = False
            self.route_changed.emit(False)
        self.state_changed.emit(self._running)
        self.status_changed.emit(status)
        if self._running and self._pending_apply:
            QTimer.singleShot(0, self._apply_now)

    @Slot(str, object)
    def _on_failed(self, message: str, status: EngineStatus) -> None:
        if self._shutdown:
            return
        self._status = status
        self._running = status.running
        self._set_busy(False)
        self._pending_apply = False
        self._meter_worker.refresh()
        if self._running:
            self._meter_tick.start()
        else:
            self._meter_tick.stop()
            self._peak_db = (_PEAK_FLOOR_DB, _PEAK_FLOOR_DB)
            self.meter_changed.emit(0.0, 0.0)
        self._restore_route()
        self.state_changed.emit(self._running)
        self.status_changed.emit(status)
        self._show_error(message)

    # -- peak meter ---------------------------------------------------------
    def _meter_target(self) -> str:
        """Current ``*.monitor`` capture target; empty while metering is off.

        The tap pulls the read-only monitor copy of the *active output device*
        (so Bluetooth and wired devices behave the same); with no device known
        it falls back to the chain's own monitor.
        """
        if self._shutdown or not self._running:
            return ""
        output = self._output or ""
        if output:
            return tap_module.monitor_target(output)
        return tap_module.monitor_target("sonicfx_sink")

    @Slot(float, float)
    def _on_meter_levels(self, left: float, right: float) -> None:
        """Raw peaks from the worker: instant attack on the held values."""
        if self._shutdown or not self._running:
            # A capture result can already be queued when the engine stops;
            # never let it repopulate a meter that was just reset to silence.
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
        """Release the held peaks (standard-ish 10 dB/s) and report them."""
        drop = _PEAK_DECAY_DB_PER_S * (_METER_TICK_MS / 1000.0)
        self._peak_db = (
            max(_PEAK_FLOOR_DB, self._peak_db[0] - drop),
            max(_PEAK_FLOOR_DB, self._peak_db[1] - drop),
        )
        self.meter_changed.emit(
            _db_to_amp(self._peak_db[0]), _db_to_amp(self._peak_db[1])
        )

    # -- applying changes --------------------------------------------------
    def _schedule_apply(self) -> None:
        if not self._running:
            return
        self._pending_apply = True
        self._debounce.start()

    def _apply_now(self) -> None:
        if self._shutdown or not self._pending_apply:
            return
        if self._busy:
            self._debounce.start(150)
            return
        if not self._running:
            self._pending_apply = False
            return
        self._pending_apply = False
        try:
            spec = self._spec()
        except Exception as exc:  # noqa: BLE001
            self._show_error(f"Could not build the audio chain: {exc}")
            return
        self._set_busy(True)
        self.apply_requested.emit(spec, self._output)

    # -- routing -----------------------------------------------------------
    def _sync_route(self, sink_name: str | None = None) -> None:
        sink = sink_name or (self._status.sink_name if self._status else None)
        if not sink:
            sink = "sonicfx_sink"
        try:
            route = self._router.make_default(sink)
        except RoutingError as exc:
            log.error("default routing failed: %s", exc)
            self._route_active = False
            self._use_default = False
            self._settings.setValue("use_as_default", False)
            self.use_default_changed.emit(False)
            self.route_changed.emit(False)
            self._show_error(f"Default output was not changed: {exc}")
            return
        self._route_active = route.active
        self.route_changed.emit(route.active)
        log.info("default output now SonicFX; previous=%s", route.previous_default)

    def _restore_route(self) -> None:
        try:
            restored = self._router.restore()
        except RoutingError:
            log.exception("default route restore failed")
            restored = False
        if (
            (self._route_active or restored)
            and self._output
            and default_sink_name() == "sonicfx_sink"
        ):
            # The recorded device disappeared; never leave the system pointing
            # at a chain that is about to stop.
            try:
                self._router.restore_to(self._output)
            except RoutingError:
                log.exception("fallback default restore failed")
        if self._route_active or restored:
            self._route_active = False
            self.route_changed.emit(False)

    def _recover_stale_route(self) -> None:
        try:
            if self._router.recover_stale(pid=os.getpid()):
                log.info("restored a default output left behind by a previous run")
        except RoutingError:
            log.exception("stale default route recovery failed")

    def _sync_login_autostart(self) -> None:
        want = self._read_bool("login_autostart", True)
        if want != self._login_autostart:
            try:
                if want:
                    autostart.enable()
                else:
                    autostart.disable()
                self._login_autostart = want
            except OSError:
                log.exception("initial login autostart setup failed")
        self._settings.setValue("login_autostart", self._login_autostart)

    # -- helpers -----------------------------------------------------------
    def _spec(self) -> ChainSpec:
        return ChainSpec(
            bands=self._bands,
            sink_description="SonicFX Ultimate",
            autoconnect=False,
        )

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
