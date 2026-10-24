"""Supervised lifecycle for the SonicFX child PipeWire daemon (control plane).

The daemon is a *separate* ``pipewire -c <our conf>`` process: its filter-chain
DSP runs in that process's audio thread, while Python only spawns, observes and
stops it.

Safety rules implemented here (docs/implementation-plan.md §5):

* never uses pattern-based kills (``pkill``/``pgrep``); only the PID we started
  is signalled, after verifying ``/proc/<pid>/cmdline`` still references our
  generation config — a recycled PID is never harmed;
* configuration files live in the app state directory, never in system
  PipeWire configuration locations;
* config writes are atomic (temp file + ``os.replace``), so a crash can never
  leave a half-written configuration;
* readiness is proven by finding the virtual sink in the live registry with the
  exact generation marker, not by sleeping and hoping.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from sonicfx.pipewire.chain import ChainSpec, render_chain_conf

_DEFAULT_STATE_HOME = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))


class DaemonError(RuntimeError):
    """The child daemon could not be started, reached or stopped."""


@dataclass(frozen=True)
class DaemonStatus:
    """Observed daemon state (never guessed)."""

    running: bool
    pid: int | None
    sink_name: str | None
    generation: int
    conf_path: Path | None
    last_error: str | None = None


class ChainDaemon:
    """Owns exactly one child daemon and its generation config files."""

    def __init__(
        self,
        state_dir: Path | str | None = None,
        *,
        binary: str = "pipewire",
        ready_timeout: float = 4.0,
        poll_interval: float = 0.1,
    ) -> None:
        self.state_dir = Path(state_dir) if state_dir else _DEFAULT_STATE_HOME / "sonicfx"
        self.chain_dir = self.state_dir / "chains"
        self.log_path = self.state_dir / "logs" / "chain.log"
        self._pid_path = self.state_dir / "chain.pid"
        self.binary = binary
        self.ready_timeout = ready_timeout
        self.poll_interval = poll_interval
        self._proc: subprocess.Popen[bytes] | None = None

    # -- public API ---------------------------------------------------------
    def start(self, spec: ChainSpec) -> DaemonStatus:
        """Start a fresh generation for ``spec`` (stopping any previous one)."""
        if self.status().running:
            self.stop()
        self._ensure_dirs()
        generation = self._latest_generation() + 1
        conf_path = self._write_conf(spec, generation)
        self._spawn(spec, conf_path)
        try:
            self._wait_ready(spec.sink_name, generation)
        except DaemonError:
            self.stop()
            raise
        return self.status()

    def restart(self, spec: ChainSpec) -> tuple[DaemonStatus, float]:
        """Stop, start and measure the total gap in milliseconds."""
        started = time.perf_counter()
        status = self.start(spec)
        gap_ms = (time.perf_counter() - started) * 1_000.0
        return status, gap_ms

    def stop(self, timeout: float = 2.5) -> None:
        """Terminate our daemon if (and only if) it is still ours."""
        pid, conf_path, _sink, _generation = self._read_state()
        if (
            pid is not None
            and conf_path is not None
            and self._process_alive(pid)
            and self._pid_matches_conf(pid, conf_path)
        ):
            self._terminate(pid, timeout=timeout)
            if self._proc is not None and self._proc.pid == pid:
                try:
                    self._proc.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    pass
        self._pid_path.unlink(missing_ok=True)
        self._proc = None

    def status(self) -> DaemonStatus:
        pid, conf_path, sink_name, generation = self._read_state()
        running = pid is not None and self._process_alive(pid)
        if running and conf_path is not None and not self._pid_matches_conf(pid, conf_path):
            # Stale pid file whose PID was recycled by an unrelated process.
            running = False
        return DaemonStatus(
            running=running,
            pid=pid if running else None,
            sink_name=sink_name if running else None,
            generation=generation,
            conf_path=conf_path if running else None,
        )

    def recent_log(self, limit: int = 4_000) -> str:
        """Return the tail of the daemon log (never raises)."""
        try:
            with self.log_path.open("rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                handle.seek(max(0, size - limit))
                data = handle.read()
            return data.decode("utf-8", errors="replace")
        except OSError:
            return ""

    # -- internals ----------------------------------------------------------
    def _ensure_dirs(self) -> None:
        self.chain_dir.mkdir(parents=True, exist_ok=True)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def _write_conf(self, spec: ChainSpec, generation: int) -> Path:
        self.chain_dir.mkdir(parents=True, exist_ok=True)
        conf_path = self.chain_dir / f"chain-{generation:04d}.conf"
        text = render_chain_conf(spec, generation=generation)
        tmp_path = conf_path.with_suffix(".conf.tmp")
        fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, conf_path)
        finally:
            tmp_path.unlink(missing_ok=True)
        self._prune_confs(keep=3)
        return conf_path

    def _prune_confs(self, *, keep: int) -> None:
        generations: list[tuple[int, Path]] = []
        for path in self.chain_dir.glob("chain-*.conf"):
            try:
                generations.append((int(path.stem.split("-", 1)[1]), path))
            except (IndexError, ValueError):
                continue
        for _generation, path in sorted(generations)[:-keep]:
            path.unlink(missing_ok=True)

    def _spawn(self, spec: ChainSpec, conf_path: Path) -> None:
        env = os.environ.copy()
        if "XDG_RUNTIME_DIR" not in env:
            env["XDG_RUNTIME_DIR"] = f"/run/user/{os.getuid()}"
        with self.log_path.open("ab") as log_handle:
            log_handle.write(
                f"\n--- SonicFX chain generation {conf_path.stem} ---\n".encode()
            )
            log_handle.flush()
            try:
                self._proc = subprocess.Popen(
                    [self.binary, "-c", str(conf_path)],
                    stdin=subprocess.DEVNULL,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    close_fds=True,
                    env=env,
                )
            except FileNotFoundError as exc:
                raise DaemonError(
                    f"PipeWire binary not found: {self.binary!r} (is PipeWire installed?)"
                ) from exc
        payload = {
            "pid": self._proc.pid,
            "conf": str(conf_path),
            "sink": spec.sink_name,
            "generation": int(conf_path.stem.split("-", 1)[1]),
        }
        self._pid_path.write_text(json.dumps(payload), encoding="utf-8")
        # Give the process a moment to fail fast on configuration errors.
        time.sleep(min(self.poll_interval, 0.05))
        if self._proc.poll() is not None:
            self._pid_path.unlink(missing_ok=True)
            raise DaemonError(
                "PipeWire child daemon exited immediately:\n" + self.recent_log(2_000)
            )

    def _wait_ready(self, sink_name: str, generation: int) -> None:
        deadline = time.monotonic() + self.ready_timeout
        while time.monotonic() < deadline:
            if self._proc is not None and self._proc.poll() is not None:
                raise DaemonError(
                    "PipeWire child daemon exited during startup:\n"
                    + self.recent_log(2_000)
                )
            if self._sink_present(sink_name, generation):
                return
            time.sleep(self.poll_interval)
        raise DaemonError(
            f"virtual sink {sink_name!r} (generation {generation}) did not appear "
            f"within {self.ready_timeout:.1f}s:\n{self.recent_log(2_000)}"
        )

    def _sink_present(self, sink_name: str, generation: int) -> bool:
        wanted_generation = str(generation)
        for props in self._nodes():
            if props.get("node.name") != sink_name:
                continue
            if props.get("sonicfx.generation") == wanted_generation:
                return True
        return False

    def _nodes(self) -> list[dict[str, str]]:
        """Return node property dicts from the live session registry."""
        try:
            result = subprocess.run(
                ["pw-dump"],
                capture_output=True,
                text=True,
                timeout=5.0,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return []
        if result.returncode != 0:
            return []
        try:
            objects = json.loads(result.stdout)
        except json.JSONDecodeError:
            return []
        nodes: list[dict[str, str]] = []
        for obj in objects:
            if obj.get("type") != "PipeWire:Interface:Node":
                continue
            props = (obj.get("info") or {}).get("props") or {}
            nodes.append({str(k): str(v) for k, v in props.items()})
        return nodes

    def _read_state(self) -> tuple[int | None, Path | None, str | None, int]:
        try:
            raw = json.loads(self._pid_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None, None, None, 0
        try:
            pid = int(raw["pid"])
            conf = Path(str(raw["conf"]))
            generation = int(raw.get("generation", 0))
        except (KeyError, TypeError, ValueError):
            return None, None, None, 0
        sink = str(raw.get("sink") or "") or None
        return pid, conf, sink, generation

    @staticmethod
    def _process_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        # A zombie still accepts signal 0 but must be treated as gone; otherwise
        # stop() would wait out every timeout whenever the child exited.
        try:
            stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        except OSError:
            return False
        fields = stat.rsplit(")", 1)[-1].split()
        return not (fields and fields[0] == "Z")

    @staticmethod
    def _pid_matches_conf(pid: int, conf_path: Path) -> bool:
        try:
            raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        except OSError:
            return False
        args = [part.decode("utf-8", errors="replace") for part in raw.split(b"\0") if part]
        return str(conf_path) in args

    def _terminate(self, pid: int, *, timeout: float) -> None:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self._process_alive(pid):
                return
            time.sleep(0.05)
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and self._process_alive(pid):
            time.sleep(0.05)

    def _latest_generation(self) -> int:
        best = 0
        for path in self.chain_dir.glob("chain-*.conf"):
            try:
                best = max(best, int(path.stem.split("-", 1)[1]))
            except (IndexError, ValueError):
                continue
        _pid, _conf, _sink, generation = self._read_state()
        return max(best, generation)
