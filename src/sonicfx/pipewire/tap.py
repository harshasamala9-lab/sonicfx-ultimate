"""Read-only monitor tap for the live Peak meter (observation only).

``LevelTap`` opens one ``pw-record`` capture on the *monitor ports* of a node
(such as ``bluez_output.XX.monitor``) and reports peaks of the raw float32
stream. Capturing a monitor port is passive: it creates a read-only copy of
what that node already plays and touches nothing else in the audio graph —
no links are moved, no filter nodes are added and no DSP runs in Python.

Live-session lesson baked in as a guard: targeting a sink *without* the
``.monitor`` suffix (``--target sonicfx_sink``) delivered saturated garbage in
testing, so a tap refuses any target that is not a ``*.monitor`` node.
"""
from __future__ import annotations

import os
import select
import subprocess

import numpy as np

__all__ = ["LevelTap", "TapError", "monitor_target", "peak_levels"]

_SAMPLE_BYTES = 4  # float32
_READ_FRAMES = 2048  # one read call (~43 ms of 48 kHz stereo)
_TERMINATE_GRACE_S = 0.5
_HEADROOM = 4.0  # +12 dBFS display headroom for over-0dB clipped monitors


class TapError(RuntimeError):
    """The monitor capture could not be opened or was closed."""


def monitor_target(node_name: str) -> str:
    """Node name of the read-only monitor copy of ``node_name``."""
    return f"{node_name}.monitor"


def peak_levels(chunk: bytes, channels: int = 2) -> tuple[float, float]:
    """Sample peak of one interleaved float32 chunk as ``(left, right)``.

    Bytes that do not form whole frames (partial reads) are ignored. Peaks are
    allowed to overshoot full scale up to :data:`_HEADROOM` — a device running
    above 100 % volume produces a clipped monitor copy, and the meter exposes
    that as a readout above 0 dBFS instead of hiding it.
    """
    frame = channels * _SAMPLE_BYTES
    usable = len(chunk) - (len(chunk) % frame)
    if usable <= 0:
        return (0.0, 0.0)
    samples = np.frombuffer(chunk[:usable], dtype="<f4").reshape(-1, channels)
    left = float(np.abs(samples[:, 0]).max())
    right = float(np.abs(samples[:, 1]).max()) if channels > 1 else left
    return (min(left, _HEADROOM), min(right, _HEADROOM))


class LevelTap:
    """One ``pw-record`` monitor capture; owned by a meter worker attempt."""

    def __init__(
        self,
        target: str,
        *,
        channels: int = 2,
        rate: int = 48_000,
        executable: str = "pw-record",
    ) -> None:
        if not target.endswith(".monitor"):
            raise TapError(
                "level taps must capture a '*.monitor' node; "
                f"{target!r} would report saturated garbage"
            )
        self._target = target
        self._channels = channels
        self._rate = rate
        self._executable = executable
        self._proc: subprocess.Popen[bytes] | None = None

    @staticmethod
    def command(
        target: str,
        *,
        channels: int = 2,
        rate: int = 48_000,
        executable: str = "pw-record",
    ) -> list[str]:
        """Exact capture argv: monitor target, interleaved float32 on stdout."""
        return [
            executable,
            "--target",
            target,
            "--format",
            "f32",
            "--channels",
            str(channels),
            "--rate",
            str(rate),
            "--raw",
            "-",  # payload on stdout only; pw-record never writes files here
        ]

    def start(self) -> None:
        """Open the capture stream (once per tap instance)."""
        if self._proc is not None:
            raise TapError("level tap is already running")
        args = self.command(
            self._target,
            channels=self._channels,
            rate=self._rate,
            executable=self._executable,
        )
        try:
            self._proc = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                cwd="/",
                start_new_session=True,
            )
        except OSError as exc:
            self._proc = None
            raise TapError(f"could not start {self._executable}: {exc}") from exc

    def read(self, timeout: float = 0.15) -> tuple[float, float] | None:
        """Wait up to ``timeout`` for capture data.

        Returns ``(left, right)`` peaks of what arrived; ``(0.0, 0.0)`` on
        silence; ``None`` once the stream is over (then the tap is closed).
        """
        chunk, closed = self._read(timeout)
        if closed:
            return None
        if not chunk:
            return (0.0, 0.0)
        return peak_levels(chunk, self._channels)

    def read_frames(self, timeout: float = 0.15) -> np.ndarray | None:
        """Wait up to ``timeout`` for interleaved float32 frames.

        Returns an interleaved ``(frames, channels)`` float64 array (empty when
        nothing arrived within the timeout), and ``None`` once the capture
        stream is over (then the tap is closed). The buffer is bounded by one
        OS read of at most ``_READ_FRAMES`` frames; a trailing partial frame is
        dropped so the result always has whole frames.
        """
        chunk, closed = self._read(timeout)
        if closed:
            return None
        if not chunk:
            return np.empty((0, self._channels), dtype=np.float64)
        frame_bytes = self._channels * _SAMPLE_BYTES
        usable = (len(chunk) // frame_bytes) * frame_bytes
        samples = np.frombuffer(chunk[:usable], dtype="<f4")
        return samples.reshape(-1, self._channels).astype(np.float64)

    def _read(self, timeout: float) -> tuple[bytes, bool]:
        """One bounded ``os.read``; returns ``(bytes, stream_closed)``."""
        proc = self._proc
        if proc is None or proc.stdout is None:
            raise TapError("level tap is not started")
        try:
            fd = proc.stdout.fileno()
            ready, _, _ = select.select([fd], [], [], timeout)
            if not ready:
                if proc.poll() is not None:
                    self.stop()
                    return b"", True
                return b"", False
            chunk = os.read(fd, _READ_FRAMES * self._channels * _SAMPLE_BYTES)
            if not chunk:
                self.stop()
                return b"", True
            return chunk, False
        except OSError:
            self.stop()
            return b"", True

    def stop(self) -> None:
        """Terminate the capture process and close the pipe; never raises."""
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=_TERMINATE_GRACE_S)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=_TERMINATE_GRACE_S)
        except Exception:  # noqa: BLE001 - a dying tap must not break anything
            pass
        finally:
            for stream in (proc.stdout, proc.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except Exception:  # noqa: BLE001
                        pass
