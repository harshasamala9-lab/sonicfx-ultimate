"""Loudness, K-weighting and peak metering math (BS.1770-4 / EBU R128 style).

Used ONLY on the low-rate meter tap on the control plane; never in the audio path.
Stage-1 (shelving pre-filter) uses the exact ITU 48 kHz coefficient table — the
same constants as ffmpeg's ebur128 filter and libebur128. For sample rates other
than 48 kHz the tap signal is resampled to 48 kHz first (resample_poly), which
keeps the reference filter exact on every session rate.
"""
from __future__ import annotations

import dataclasses

import numpy as np

#: ITU-R BS.1770-4 stage-1 (high-shelf) digital coefficients, fs = 48 kHz exact.
_SHELF_B = (1.53512485958697, -2.69169618940638, 1.19839281085285)
_SHELF_A = (1.0, -1.69065929318241, 0.73248077419085)
#: Continuous parameters of the RLB high-pass (ITU BS.1770-4).
_RLB_F0 = 38.13547097602508  # Hz
_RLB_Q = 0.5003270373238773

_BLOCK_MS = 400      # EBU Tech 3341 momentary block
_HOP_MS = 100        # 75% overlap
_ABS_GATE = -70.0    # LUFS
_REL_GATE_LU = 10.0  # below ungated mean
_LUFS_OFFSET = -0.691

CHANNEL_GAINS = {"FL": 1.0, "FR": 1.0, "FC": 1.0,
                 "BL": 1.41, "BR": 1.41, "SL": 1.41, "SR": 1.41,
                 "LFE": 0.0}  # LFE excluded from ITU measurements


@dataclasses.dataclass(frozen=True)
class PeakInfo:
    sample_peak_dbfs: float
    true_peak_dbfs: float


def k_weights(fs: float) -> list[tuple[tuple[float, float, float],
                                       tuple[float, float, float]]]:
    """K-weighting cascade for the meter. Returns [(shelf_b, shelf_a), (rlp_b, rlp_a)].

    Upsampled data must arrive at 48 kHz (see :func:`to_48k`).
    """
    from .filters import BiquadBand, FilterKind, design_band

    if abs(fs - 48_000.0) > 1e-6:
        raise ValueError("K-weighting is defined at 48 kHz; resample first (to_48k)")
    rlp = design_band(BiquadBand(FilterKind.HIGH_PASS, _RLB_F0, q=_RLB_Q), fs)
    return [((_SHELF_B[0], _SHELF_B[1], _SHELF_B[2]),
             (_SHELF_A[0], _SHELF_A[1], _SHELF_A[2])),
            ((rlp.b0, rlp.b1, rlp.b2), (1.0, rlp.a1, rlp.a2))]


def to_48k(x: np.ndarray, fs: float) -> np.ndarray:
    """Bandlimited resample to 48 kHz for metering when the tap rate differs."""
    from scipy import signal as sp_signal

    fs_int = int(round(fs))
    if fs_int == 48_000:
        return np.asarray(x, dtype=float)
    g = np.gcd(48_000, fs_int)
    up, down = 48_000 // g, fs_int // g
    return sp_signal.resample_poly(x, up, down)


def k_weight(x: np.ndarray, fs: float) -> np.ndarray:
    """Apply the K-weighting cascade to one channel at 48 kHz."""
    from scipy import signal as sp_signal

    y = np.asarray(x, dtype=float)
    for b, a in k_weights(fs):
        y = sp_signal.lfilter(b, a, y)
    return y


def momentary_lufs(x: np.ndarray, fs: float, channel_gain: float = 1.0) -> float:
    """Momentary loudness (LUFS) of the last 400 ms of ``x`` (single channel)."""
    y = k_weight(x, fs)
    if y.shape[0] < int(0.4 * fs):
        return float("-inf")
    ms = float(np.mean(y[-int(0.4 * fs):] ** 2))
    if ms <= 0:
        return float("-inf")
    return _LUFS_OFFSET + 10.0 * np.log10(ms * channel_gain)


def integrated_lufs(channels: list[np.ndarray] | np.ndarray, fs: float,
                    channel_gains: list[float] | None = None) -> float:
    """Gated integrated loudness (LUFS) of a whole program (EBU Tech 3341).

    ``channels``: list of same-length arrays at 48 kHz.
    ``channel_gains``: ITU channel weights G_ch (L/R/C = 1.0, surround = 1.41,
    LFE = 0.0); defaults to 1.0 per stereo pair.
    """
    import math

    chans = [np.asarray(c, dtype=float) for c in channels]
    n = min(len(c) for c in chans)
    chans = [c[:n] for c in chans]
    gains = channel_gains if channel_gains is not None else [1.0] * len(chans)

    ks = [k_weight(c, fs) for c in chans]
    block = int(_BLOCK_MS * fs / 1000)
    hop = int(_HOP_MS * fs / 1000)
    if n < block:
        return float("-inf")

    starts = list(range(0, n - block + 1, hop))
    z = np.empty(len(starts))
    for i, s in enumerate(starts):
        total = 0.0
        for kc, g_ch in zip(ks, gains, strict=True):
            total += g_ch * float(np.mean(kc[s:s + block] ** 2))
        z[i] = max(total, 1e-24)

    loud_i = _LUFS_OFFSET + 10.0 * np.log10(z)
    keep = loud_i > _ABS_GATE
    if not np.any(keep):
        return float("-inf")
    lk, zk = loud_i[keep], z[keep]
    rel = _LUFS_OFFSET + 10.0 * math.log10(float(np.mean(zk))) - _REL_GATE_LU
    final = lk > rel
    if not np.any(final):
        return float("-inf")
    return _LUFS_OFFSET + 10.0 * math.log10(float(np.mean(zk[final])))


def peaks(x: np.ndarray, fs: float) -> PeakInfo:
    """Sample peak and 4×-oversampled true-peak estimate (documented approximation)."""
    from scipy import signal as sp_signal

    x = np.asarray(x, dtype=float)
    sp_db = 20.0 * np.log10(max(np.max(np.abs(x)), 1e-12))
    x4 = sp_signal.resample_poly(x, 4, 1)
    tp_db = 20.0 * np.log10(max(np.max(np.abs(x4)), 1e-12))
    return PeakInfo(sample_peak_dbfs=sp_db, true_peak_dbfs=tp_db)


def clip_count(x: np.ndarray, fs: float, window_ms: float = 3.0) -> int:
    """Count of distinct clipping occurrences (lenient: allows transient jitter).

    Two samples at ±full-scale separated by less than ``window_ms`` count as one
    event (jitter tolerance), matching how our dashboard must avoid flicker.
    """
    x = np.asarray(x, dtype=float)
    hits = np.abs(x) >= 1.0 - 1e-6
    if not hits.any():
        return 0
    min_gap = int(fs * window_ms / 1000)
    events = 0
    last = -min_gap - 1
    for i in np.flatnonzero(hits):
        if i - last > min_gap:
            events += 1
        last = i
    return events
