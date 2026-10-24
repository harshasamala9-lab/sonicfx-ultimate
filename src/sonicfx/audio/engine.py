"""EngineAPI — the only engine surface the GUI is allowed to call.

Wraps :class:`~sonicfx.pipewire.daemon.ChainDaemon` with product semantics:

* ``start``  — bring the chain up for a spec and route it to an output;
* ``apply``  — change the processing graph (restart only when it changed);
* ``bypass`` — stop the chain and return the system to its unprocessed path;
* ``status`` — observed state only (never optimistic flags).

Routing is explicit: when an ``output`` is given, auto-connection is disabled in
the generated configuration and :func:`sonicfx.pipewire.wiring.link_playback`
links the chain's playback ports to that device. If wiring fails the daemon is
stopped again — SonicFX never leaves an unrouted chain running.

The measured restart gap is carried in :class:`EngineStatus` so the UI can show
the truth and the Stage 3 acceptance report can quote it.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, replace

from sonicfx.pipewire import wiring
from sonicfx.pipewire.chain import ChainSpec
from sonicfx.pipewire.daemon import ChainDaemon, DaemonError, DaemonStatus


class EngineError(RuntimeError):
    """User-facing engine failure (wraps daemon errors with context)."""


@dataclass(frozen=True)
class EngineStatus:
    """Observed engine state plus the last measured application gap."""

    running: bool
    pid: int | None
    sink_name: str | None
    generation: int
    output: str | int | None = None
    gap_ms: float | None = None
    last_error: str | None = None


class Engine:
    """Owns one processing chain for the lifetime of the application."""

    def __init__(self, daemon: ChainDaemon | None = None) -> None:
        self._daemon = daemon if daemon is not None else ChainDaemon()
        self._spec: ChainSpec | None = None
        self._output: str | int | None = None
        self._gap_ms: float | None = None
        self._last_error: str | None = None

    @property
    def spec(self) -> ChainSpec | None:
        """The spec most recently started or applied (``None`` before start)."""
        return self._spec

    @property
    def output(self) -> str | int | None:
        """The output device most recently routed to."""
        return self._output

    def start(
        self, spec: ChainSpec, *, output: str | int | None = None
    ) -> EngineStatus:
        """Start a chain for ``spec`` and route it to ``output``."""
        prepared = self._prepare(spec, output)
        try:
            status = self._daemon.start(prepared)
            self._wire(prepared, output)
        except (DaemonError, wiring.WiringError) as exc:
            raise self._abort(exc, "could not start the audio engine") from exc
        self._spec = prepared
        self._output = output
        self._gap_ms = None
        self._last_error = None
        return self._status(status)

    def apply(
        self, spec: ChainSpec, *, output: str | int | None = None
    ) -> EngineStatus:
        """Apply ``spec``; no restart happens when graph and route are unchanged."""
        if output is None:
            output = self._output
        prepared = self._prepare(spec, output)
        if (
            self._spec is not None
            and prepared.fingerprint() == self._spec.fingerprint()
            and output == self._output
            and self._daemon.status().running
        ):
            return self.status()
        started = time.perf_counter()
        try:
            status, _daemon_gap = self._daemon.restart(prepared)
            self._wire(prepared, output)
        except (DaemonError, wiring.WiringError) as exc:
            raise self._abort(exc, "could not apply the audio settings") from exc
        self._spec = prepared
        self._output = output
        self._gap_ms = (time.perf_counter() - started) * 1_000.0
        self._last_error = None
        return self._status(status)

    def bypass(self) -> EngineStatus:
        """Stop processing; streams fall back to the hardware path."""
        try:
            self._daemon.stop()
        except DaemonError as exc:
            self._last_error = str(exc)
            raise EngineError(f"could not stop the audio engine: {exc}") from exc
        self._gap_ms = None
        return self.status()

    def set_bypass(self, bypassed: bool) -> EngineStatus:
        """Convenience toggle used by the GUI's bypass control."""
        if bypassed:
            return self.bypass()
        if self._spec is None:
            raise EngineError("no chain has been configured yet")
        return self.start(self._spec, output=self._output)

    def shutdown(self) -> None:
        """Exit-time cleanup; never masks the exit path with an exception."""
        try:
            self._daemon.stop()
        except Exception:  # noqa: BLE001 - exit must be best-effort
            return

    def status(self) -> EngineStatus:
        return self._status(self._daemon.status())

    # -- internals ---------------------------------------------------------
    @staticmethod
    def _prepare(spec: ChainSpec, output: str | int | None) -> ChainSpec:
        """Explicit routing always disables session-manager auto-connection."""
        if output is None:
            return spec
        return replace(spec, autoconnect=False)

    @staticmethod
    def _wire(
        spec: ChainSpec, output: str | int | None
    ) -> tuple[tuple[str, str], ...]:
        if output is None:
            return ()
        pairs = wiring.link_playback(spec.playback_name, output)
        return tuple((pair.source, pair.target) for pair in pairs)

    def _abort(
        self, exc: Exception, context: str
    ) -> EngineError:
        """Stop a partially started chain and turn the failure into EngineError."""
        self._last_error = str(exc)
        try:
            self._daemon.stop()
        except DaemonError:
            pass
        return EngineError(f"{context}: {exc}")

    def _status(self, status: DaemonStatus) -> EngineStatus:
        return EngineStatus(
            running=status.running,
            pid=status.pid,
            sink_name=status.sink_name,
            generation=status.generation,
            output=self._output,
            gap_ms=self._gap_ms,
            last_error=self._last_error or status.last_error,
        )
