"""Opt-in default-sink switching: make the system play *through* SonicFX.

When enabled, the SonicFX virtual sink becomes the default output. Apps follow
the default into the chain; the chain's playback side is explicitly wired to
the real hardware sink chosen in the GUI (see :mod:`sonicfx.pipewire.wiring`),
so audio is processed and then reaches the speakers/headphones.

Safety rules:

* the switch only happens after the chain is running and wired — never to a
  dead sink;
* the previous default device is persisted *before* switching and restored on
  disable, bypass, engine stop, app exit and at next launch after a crash;
* only nodes whose name starts with ``sonicfx`` can be made default here;
* SonicFX is never selected as its own playback target.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from sonicfx.pipewire.wiring import default_sink_name

_DEFAULT_STATE_HOME = Path(
    os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")
)


class RoutingError(RuntimeError):
    """The default output could not be switched or restored."""


@dataclass(frozen=True)
class DefaultRoute:
    """Result of a successful default-output switch."""

    sonicfx_sink: str
    previous_default: str | None
    active: bool


def _run(args: list[str], *, timeout: float = 5.0) -> str:
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout, check=False
        )
    except FileNotFoundError as exc:
        raise RoutingError(f"{args[0]} is not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise RoutingError(f"{args[0]} timed out") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RoutingError(f"{args[0]} failed: {detail}")
    return result.stdout


def _nodes() -> list[dict]:
    try:
        objects = json.loads(_run(["pw-dump"]))
    except (RoutingError, json.JSONDecodeError):
        return []
    return objects if isinstance(objects, list) else []


def _sink_global_id(name: str) -> int | None:
    for obj in _nodes():
        if obj.get("type") != "PipeWire:Interface:Node":
            continue
        props = (obj.get("info") or {}).get("props") or {}
        if props.get("node.name") != name:
            continue
        if props.get("media.class") != "Audio/Sink":
            continue
        try:
            return int(obj.get("id", 0))
        except (TypeError, ValueError):
            return None
    return None


def _process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return False
    fields = stat.rsplit(")", 1)[-1].split()
    return not (fields and fields[0] == "Z")


class DefaultSinkRouter:
    """Switch the system default sink to SonicFX and restore it safely."""

    def __init__(self, state_dir: Path | str | None = None) -> None:
        self.state_dir = (
            Path(state_dir) if state_dir is not None else _DEFAULT_STATE_HOME / "sonicfx"
        )
        self.state_path = self.state_dir / "routing.json"

    # -- state --------------------------------------------------------------
    def read_state(self) -> dict[str, object] | None:
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return raw if isinstance(raw, dict) else None

    def previous_default(self) -> str | None:
        state = self.read_state() or {}
        previous = state.get("previous_default")
        return previous if isinstance(previous, str) and previous else None

    def _write_state(self, sonicfx_sink: str, previous: str | None) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "sonicfx_sink": sonicfx_sink,
            "previous_default": previous,
            "pid": os.getpid(),
        }
        tmp = self.state_path.with_suffix(".json.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, self.state_path)

    # -- switching ----------------------------------------------------------
    def is_active(self, sonicfx_sink: str) -> bool:
        return (
            default_sink_name() == sonicfx_sink
            and _sink_global_id(sonicfx_sink) is not None
        )

    def make_default(self, sonicfx_sink: str) -> DefaultRoute:
        """Switch the default output to ``sonicfx_sink`` (state-safe)."""
        if not sonicfx_sink.startswith("sonicfx"):
            raise RoutingError(
                "only SonicFX's own virtual sink can become the default output"
            )
        sink_id = _sink_global_id(sonicfx_sink)
        if sink_id is None:
            raise RoutingError(f"SonicFX sink {sonicfx_sink!r} is not running")

        state = self.read_state() or {}
        stored_previous = state.get("previous_default")
        if isinstance(stored_previous, str) and stored_previous:
            # Keep the original device across repeated restarts (preset edits).
            previous: str | None = stored_previous
        else:
            current = default_sink_name()
            previous = None if current == sonicfx_sink else current
        self._write_state(sonicfx_sink, previous)

        _run(["wpctl", "set-default", str(sink_id)])
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if default_sink_name() == sonicfx_sink:
                return DefaultRoute(sonicfx_sink, previous, True)
            time.sleep(0.05)

        # Roll back rather than leave the system pointing at a dead route.
        if previous:
            self._restore_named(previous)
        self.state_path.unlink(missing_ok=True)
        raise RoutingError(
            "WirePlumber did not switch the default output to SonicFX"
        )

    def restore(self) -> bool:
        """Restore the saved previous default output; returns True if changed."""
        state = self.read_state()
        if not state:
            return False
        previous = state.get("previous_default")
        self.state_path.unlink(missing_ok=True)
        if not isinstance(previous, str) or not previous:
            return False
        return self._restore_named(previous)

    def restore_to(self, name: str) -> bool:
        """Point the default output back at a concrete hardware sink."""
        if name.startswith("sonicfx"):
            raise RoutingError("refusing to restore the default to SonicFX")
        return self._restore_named(name)

    def recover_stale(self, *, pid: int | None = None) -> bool:
        """Restore a default left behind by a crashed instance.

        Skipped when the recorded owner process is still alive and is not the
        caller (another SonicFX instance may be legitimately running).
        """
        state = self.read_state()
        if not state:
            return False
        stored_pid = state.get("pid")
        if (
            pid is not None
            and isinstance(stored_pid, int)
            and stored_pid != pid
            and _process_alive(stored_pid)
        ):
            return False
        return self.restore()

    # -- helpers ------------------------------------------------------------
    def _restore_named(self, name: str) -> bool:
        sink_id = _sink_global_id(name)
        if sink_id is None:
            return False
        try:
            _run(["wpctl", "set-default", str(sink_id)])
        except RoutingError:
            return False
        return True
