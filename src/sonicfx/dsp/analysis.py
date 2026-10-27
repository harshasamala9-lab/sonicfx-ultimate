"""Audio-reactive analysis for the Wave Studio visualizer (observation only).

This module is *not* part of the real-time DSP chain. It consumes short,
read-only monitor chunks produced by :mod:`sonicfx.pipewire.tap` and returns a
small bounded snapshot (log-spaced spectrum, RMS bands, waveform envelope and
transient strength) that the Qt GUI can paint. It never touches the PipeWire
audio thread and never claims to be a measurement standard.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np

__all__ = ["AnalysisConfig", "AnalysisSnapshot", "AudioAnalyzer"]

#: Defaults-bass / mid / treble energy come from the log-spaced spectrum, so
#: keep the log ranges explicit here for readability.
_BASS_SHARE = 0.30
_MID_SHARE = 0.62


@dataclass(frozen=True)
class AnalysisConfig:
    """Analysis tuning. Buffer shapes are fixed at construction."""

    fft_size: int = 1024
    sample_rate: int = 48_000
    #: Number of log-spaced magnitude bins produced per FFT update.
    bins: int = 64
    #: Number of waveform envelope points.
    envelope: int = 64
    attack_s: float = 0.04
    release_s: float = 0.22

    def __post_init__(self) -> None:
        if not (128 <= self.fft_size <= 8192):
            raise ValueError("fft_size must be between 128 and 8192")
        if self.sample_rate <= 0:
            raise ValueError("sample_rate must be positive")
        if not (8 <= self.bins <= 256):
            raise ValueError("bins must be between 8 and 256")
        if not (8 <= self.envelope <= 256):
            raise ValueError("envelope must be between 8 and 256")
        if not (0.01 <= self.attack_s <= 1.0):
            raise ValueError("attack_s out of range")
        if not (0.05 <= self.release_s <= 2.0):
            raise ValueError("release_s out of range")


@dataclass(frozen=True)
class AnalysisSnapshot:
    """One bounded analysis frame for the GUI.

    All arrays are already normalised to ``0..1``. A snapshot is immutable, so
    the GUI can keep a reference safely.
    """

    seq: int
    rate: int
    bins: np.ndarray  # (bins,) log-spaced magnitudes
    waveform: np.ndarray  # (envelope,) 0..1 envelope
    bands: tuple[float, float, float]  # bass / mid / treble RMS 0..1
    transient: float  # normalised attack strength 0..1
    correlation: float  # stereo correlation −1..1 (0 for mono)
    level: tuple[float, float]  # L/R sample peaks 0..1
    silent: bool
    clipped: bool

    @staticmethod
    def idle() -> AnalysisSnapshot:
        zeros = np.zeros(64, dtype=np.float32)
        return AnalysisSnapshot(
            seq=0,
            rate=48_000,
            bins=zeros.copy(),
            waveform=zeros.copy(),
            bands=(0.0, 0.0, 0.0),
            transient=0.0,
            correlation=0.0,
            level=(0.0, 0.0),
            silent=True,
            clipped=False,
        )


class AudioAnalyzer:
    """Bounded, stateful audio analysis.

    ``process`` accepts one interleaved ``(frames, channels)`` float32 block
    from a monitor capture and returns a snapshot. Repeated calls keep a
    rolling buffer and smoothed spectrum, never growing beyond the configured
    sizes.
    """

    _LOG_MIN = 30.0

    def __init__(self, config: AnalysisConfig | None = None) -> None:
        cfg = config or AnalysisConfig()
        self.cfg = cfg
        self._n = int(cfg.fft_size)
        self._rate = float(cfg.sample_rate)
        self.bins = int(cfg.bins)
        self.envelope = int(cfg.envelope)

        self._window = np.hanning(self._n)
        self._norm = 2.0 / self._n
        nyquist = self._rate / 2.0
        self._centre = np.geomspace(
            max(self._LOG_MIN, 30.0), max(40.0, nyquist - 1.0), num=self.bins
        )
        self._bin_lo = np.clip(
            self._centre * 2.0 ** (-1.0 / 24.0), self._LOG_MIN, nyquist
        )
        self._bin_hi = np.clip(
            self._centre * 2.0 ** (1.0 / 24.0), self._LOG_MIN, nyquist
        )
        self._freqs = np.fft.rfftfreq(self._n, d=1.0 / self._rate)

        self._buffer = np.zeros(self._n, dtype=np.float32)
        self._seq = 0
        self._last_dt = time.monotonic()
        self._spec = np.zeros(self.bins, dtype=np.float32)
        self._waveform = np.zeros(self.envelope, dtype=np.float32)
        self._bands: tuple[float, float, float] = (0.0, 0.0, 0.0)
        self._transient = 0.0
        self._correlation = 0.0
        self._level: tuple[float, float] = (0.0, 0.0)
        self._silent = True
        self._clipped = False
        self._smoothed = np.zeros(self.bins, dtype=np.float32)
        self._last_rms = 0.0

    # -- public --------------------------------------------------------------
    def reset(self) -> None:
        """Drop accumulated state (used after device changes)."""
        self._buffer.fill(0.0)
        self._spec.fill(0.0)
        self._smoothed.fill(0.0)
        self._waveform.fill(0.0)
        self._bands = (0.0, 0.0, 0.0)
        self._transient = 0.0
        self._correlation = 0.0
        self._level = (0.0, 0.0)
        self._silent = True
        self._clipped = False
        self._seq = 0
        self._last_rms = 0.0

    def process(self, samples: np.ndarray) -> AnalysisSnapshot:
        """Analyze one interleaved ``(frames, channels)`` float block."""
        if samples.ndim != 2 or samples.shape[0] == 0:
            self.reset()
            return self._snapshot()
        if samples.shape[1] < 1:
            self.reset()
            return self._snapshot()
        block = samples.astype(np.float32, copy=False)
        mono = block[:, 0]
        self._push(mono)
        self._levels(block)
        if block.shape[1] > 1:
            self._correlation = _safe_corr(block[:, 0], block[:, 1])
        else:
            self._correlation = 0.0
        spec = self._spectrum(mono)
        self._spec = spec
        smoothed = _smooth(spec, self._smoothed, self.cfg.attack_s, self.cfg.release_s)
        self._smoothed = smoothed
        self._bands = self._band_energies(smoothed)
        self._transient = self._transient_strength(mono)
        self._waveform = self._waveform_envelope(mono)
        self._seq += 1
        self._last_dt = time.monotonic()
        return self._snapshot()

    # -- internals -----------------------------------------------------------
    def _push(self, mono: np.ndarray) -> None:
        n = self._n
        if len(mono) >= n:
            self._buffer[:] = mono[-n:]
        else:
            self._buffer = np.concatenate(
                (self._buffer[len(mono):], mono.astype(np.float32, copy=False))
            )

    def _levels(self, block: np.ndarray) -> None:
        left = float(np.max(np.abs(block[:, 0]))) if len(block) else 0.0
        right = (
            float(np.max(np.abs(block[:, 1])))
            if block.shape[1] > 1 and len(block)
            else 0.0
        )
        self._level = (min(1.0, max(0.0, left)), min(1.0, max(0.0, right)))
        self._clipped = self._level[0] >= 0.999 or self._level[1] >= 0.999

    def _spectrum(self, mono: np.ndarray) -> np.ndarray:
        windowed = self._buffer * self._window
        spec = np.abs(np.fft.rfft(windowed)) * self._norm
        result = np.zeros(self.bins, dtype=np.float32)
        for index in range(self.bins):
            mask = (self._freqs >= self._bin_lo[index]) & (
                self._freqs < self._bin_hi[index]
            )
            if np.any(mask):
                result[index] = float(np.mean(spec[mask]))
        # A rolling window keeps some lag; late energy is normalised to 0..1.
        result = np.divide(
            result, max(1e-4, float(result.max())),
            out=result, where=result > 1e-6,
        )
        return result

    def _band_energies(
        self, smoothed: np.ndarray
    ) -> tuple[float, float, float]:
        bass = float(np.mean(smoothed[: int(self.bins * _BASS_SHARE)]))
        mid = float(np.mean(smoothed[int(self.bins * _BASS_SHARE): int(self.bins * _MID_SHARE)]))
        treble = float(np.mean(smoothed[int(self.bins * _MID_SHARE):]))
        return (
            min(1.0, bass * 1.35),
            min(1.0, mid * 1.35),
            min(1.0, treble * 1.35),
        )

    def _transient_strength(self, mono: np.ndarray) -> float:
        rms = float(np.sqrt(np.mean(np.square(mono)))) if len(mono) else 0.0
        now = time.monotonic()
        dt = max(0.005, now - self._last_dt)
        rise = max(0.0, rms - self._last_rms)
        strength = min(1.0, (rise / dt) / 4.0)
        self._last_rms = 0.85 * self._last_rms + 0.15 * rms
        return strength

    def _waveform_envelope(self, mono: np.ndarray) -> np.ndarray:
        n = self.envelope
        if len(mono) <= n:
            out = np.zeros(n, dtype=np.float32)
            out[: len(mono)] = np.abs(mono)
            return np.clip(out, 0.0, 1.0)
        step = len(mono) / n
        idx = np.arange(n, dtype=np.int32)
        head = (idx * step).astype(np.int32)
        tail = np.minimum(head + 1, len(mono) - 1)
        lo = np.abs(mono[head])
        hi = np.abs(mono[tail])
        frac = (idx * step) - head
        envelope = lo + (hi - lo) * frac
        return np.clip(envelope, 0.0, 1.0).astype(np.float32)

    def _snapshot(self) -> AnalysisSnapshot:
        silent = (
            np.max(self._smoothed) < 0.01
            and self._level[0] < 0.01
            and self._level[1] < 0.01
        )
        return AnalysisSnapshot(
            seq=self._seq,
            rate=int(self._rate),
            bins=self._smoothed.copy(),
            waveform=self._waveform.copy(),
            bands=self._bands,
            transient=self._transient,
            correlation=self._correlation,
            level=self._level,
            silent=bool(silent),
            clipped=bool(self._clipped),
        )


def _smooth(
    current: np.ndarray, previous: np.ndarray, attack_s: float, release_s: float
) -> np.ndarray:
    """Attack/release smoothing with a bounded per-frame blend."""
    a = min(1.0, max(0.08, attack_s))
    r = min(1.0, max(0.08, release_s))
    rising = current > previous
    factor = np.where(rising, a, r)
    return previous + factor * (current - previous)


def _safe_corr(left: np.ndarray, right: np.ndarray) -> float:
    """Pearson correlation bounded to [-1, 1]; returns 0 for flat signals."""
    a = left - float(np.mean(left))
    b = right - float(np.mean(right))
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom <= 1e-9:
        return 0.0
    return max(-1.0, min(1.0, float(np.dot(a, b) / denom)))
