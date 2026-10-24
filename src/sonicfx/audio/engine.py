"""EngineAPI — the only engine surface the GUI is allowed to call.

Wraps :class:`~sonicfx.pipewire.daemon.ChainDaemon` with product semantics:

* ``start``  — bring the chain up for a spec;
* ``apply``  — change the processing graph (restart only when it changed);
* ``bypass`` — stop the chain and return the system to its unprocessed path;
* ``status`` — observed state only (never optimistic flags).

The measured restart gap is carried in :class:`EngineStatus` so the UI can show
the truth and the Stage 3 acceptance report can quote it.
"""
from __future__ import annotations

from dataclasses import dataclass

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
    gap_ms: float | None = None
    last_error: str | None = None


class Engine:
    """Owns one processing chain for the lifetime of the application."""

    def __init__(self, daemon: ChainDaemon | None = None) -> None:
        self._daemon = daemon if daemon is not None else ChainDaemon()
        self._spec: ChainSpec | None = None
        self._gap_ms: float | None = None
        self._last_error: str | None = None

    @property
    def spec(self) -> ChainSpec | None:
        """The spec most recently started or applied (``None`` before start)."""
        return self._spec

    def start(self, spec: ChainSpec) -> EngineStatus:
        """Start a chain for ``spec`` (restarting any running generation)."""
        try:
            status = self._daemon.start(spec)
        except DaemonError as exc:
            self._last_error = str(exc)
            raise EngineError(f"could not start the audio engine: {exc}") from exc
        self._spec = spec
        self._gap_ms = None
        self._last_error = None
        return self._status(status)

    def apply(self, spec: ChainSpec) -> EngineStatus:
        """Apply ``spec``; no restart happens when the graph is unchanged."""
        if (
            self._spec is not None
            and spec.fingerprint() == self._spec.fingerprint()
            and self._daemon.status().running
        ):
            return self.status()
        try:
            status, gap_ms = self._daemon.restart(spec)
        except DaemonError as exc:
            self._last_error = str(exc)
            raise EngineError(f"could not apply the audio settings: {exc}") from exc
        self._spec = spec
        self._gap_ms = gap_ms
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
        return self.start(self._spec)

    def shutdown(self) -> None:
        """Exit-time cleanup; never masks the exit path with an exception."""
        try:
            self._daemon.stop()
        except Exception:  # noqa: BLE001 - exit must be best-effort
            return

    def status(self) -> EngineStatus:
        return self._status(self._daemon.status())

    def _status(self, status: DaemonStatus) -> EngineStatus:
        return EngineStatus(
            running=status.running,
            pid=status.pid,
            sink_name=status.sink_name,
            generation=status.generation,
            gap_ms=self._gap_ms,
            last_error=self._last_error or status.last_error,
        )
