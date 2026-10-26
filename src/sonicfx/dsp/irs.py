"""Impulse-response synthesis for convolver stages (offline design only).

Feeds freely-licensed convolver nodes in the PipeWire filter-chain:
* synthetic program reverb IRs (deterministic, seeded),
* discrete early-reflection tap lists (for builtin `delay` stages),
* Schroeder-style RT60 estimation used by tests to verify decay accuracy.

Everything here runs at design time; the audio path consumes the finished IR.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np


def _one_pole_state_filter(x: np.ndarray, a: float, y0: float) -> tuple[np.ndarray, float]:
    """Streaming one-pole y[i] = y[i−1] + a·(x[i]−y[i−1]) via lfilter with state."""
    from scipy import signal as sp_signal

    b = [a, 0.0]
    den = [1.0, -(1.0 - a)]
    state = sp_signal.lfiltic(b, den, [y0])
    y, state = sp_signal.lfilter(b, den, x, zi=state)
    return y, float(state[-1] / (1.0 - (1.0 - a)))


def reverb_ir(fs: float, rt60_s: float, size_norm: float = 0.5,
              damping_norm: float = 0.4, predelay_ms: float = 15.0,
              spread_norm: float = 0.6, seed: int = 7) -> np.ndarray:
    """Synthetic exponential-decay reverb IR with time-progressive damping.

    Args mirror the Cinema screen controls (normalised 0..1).
    Returns the finished IR (peak-normalised to ≤ 1.0; wet gain controlled downstream).
    """
    if fs <= 0:
        raise ValueError("fs must be positive")
    if not (0.03 <= rt60_s <= 8.0):
        raise ValueError("rt60_s must be within 0.03–8 s")
    if not (0.0 <= size_norm <= 1.0 and 0.0 <= damping_norm <= 1.0 and
            0.0 <= spread_norm <= 1.0):
        raise ValueError("size/damping/spread must be in [0,1]")

    rng = np.random.default_rng(seed)
    tail_s = rt60_s * 1.15
    n_total = int(fs * (predelay_ms / 1000.0 + tail_s)) + 1
    ir = np.zeros(n_total)
    window = max(4, min(int(fs * (0.005 + 0.14 * size_norm)), n_total - 1))

    # Diffuse tail: decaying noise with one-pole damping whose cutoff darkens over time.
    tail_start = min(window, int(predelay_ms / 1000.0 * fs))
    n_tail = n_total - tail_start
    if n_tail > 0:
        noise = rng.standard_normal(n_tail)
        base_a = 1.0 - math.exp(-2.0 * math.pi *
                                (fs / 2.0 * (0.35 + 0.55 * (1.0 - damping_norm))) / fs)
        chunk = 4096
        y = 0.0
        for start in range(0, n_tail, chunk):
            stop = min(start + chunk, n_tail)
            frac = (start + stop) / 2.0 / max(n_tail, 1)
            a_here = base_a * (1.0 - 0.6 * frac * damping_norm)  # darker (smaller a) over time
            a_here = min(0.99999, max(1e-6, a_here))
            filtered, y = _one_pole_state_filter(noise[start:stop], a_here, y)
            noise[start:stop] = filtered
        decay = np.exp(-6.9078 * np.arange(n_tail) / max(fs * rt60_s, 1.0))
        # click-free start: 10 ms raised-cosine fade-in at the tail head
        ramp = int(0.010 * fs)
        if ramp > 0 and n_tail > ramp:
            decay[:ramp] *= 0.5 * (1.0 - np.cos(np.pi * np.arange(ramp) / ramp))
        ir[tail_start:] = noise * decay

    # Early reflections added on top (window overlaps the tail head by design).
    n_refl = 6 + int(18 * size_norm)
    refl_window = max(4, min(window, n_total - 1))
    taps = np.sort(rng.integers(1, refl_window, size=n_refl))
    taps = np.unique(taps[taps > tail_start]) if tail_start else np.unique(taps)
    weights = (-6.0 - 1.5 * np.arange(len(taps)))          # dB
    weights += rng.uniform(-1.0, 1.0, size=len(taps))
    for idx, w in zip(taps, weights, strict=True):
        ir[idx] += 10.0 ** (w / 20.0)

    peak = np.max(np.abs(ir))
    if peak > 0:
        ir = ir / peak
    # Predelay silence enforced last:
    if predelay_ms > 0:
        ir[: int(predelay_ms / 1000.0 * fs)] = 0.0
    return ir


def early_reflection_taps(fs: float, size_norm: float = 0.5, seed: int = 7
                          ) -> list[tuple[float, float]]:
    """(delay_ms, gain) list for builtin delay-node early-reflection builders."""
    rng = np.random.default_rng(seed + 1)
    n = 5 + int(12 * size_norm)
    delays = np.sort(rng.uniform(3.0, 30 + 90 * size_norm, size=n))
    gains = 10.0 ** ((-4.0 - 2.2 * np.arange(n)) / 20.0) * rng.uniform(0.8, 1.2, n)
    return [(float(d), float(min(g, 1.0)))
            for d, g in zip(delays, gains, strict=True)]


def stereo_room_ir(
    fs: float,
    rt60_s: float,
    size_norm: float = 0.5,
    damping_norm: float = 0.4,
    predelay_ms: float = 16.0,
    wet_gain: float = 0.26,
    spread_norm: float = 0.6,
    seed: int = 17,
) -> np.ndarray:
    """Stereo room response with an explicit direct path.

    The first sample of each channel is the direct sound (gain 1), so a
    convolver can replace the dry path without a parallel latency-sensitive
    mix.  Two seeded tails are split into a common mid and a side component;
    ``spread_norm`` scales only the side, preserving the centre image while
    adding width.  Asymmetric early-reflection taps supply lateral cues.
    Returns ``(samples, 2)`` float64, peak-normalised to at most 1.0.
    """
    if fs <= 0:
        raise ValueError("fs must be positive")
    if not (0.0 <= wet_gain <= 1.0):
        raise ValueError("wet_gain must be in [0,1]")
    if not (0.0 <= spread_norm <= 1.0):
        raise ValueError("spread_norm must be in [0,1]")
    # Generate two decorrelated tails, then keep a common mid component and
    # scale only the side component.  A fully independent L/R tail is too
    # destructive to the direct image (a mono source would arrive from the
    # whole room); this keeps the room wide without collapsing the centre.
    wet_l = reverb_ir(
        fs,
        rt60_s,
        size_norm=size_norm,
        damping_norm=damping_norm,
        predelay_ms=predelay_ms,
        spread_norm=spread_norm,
        seed=seed,
    )
    wet_r = reverb_ir(
        fs,
        rt60_s,
        size_norm=size_norm,
        damping_norm=damping_norm,
        predelay_ms=predelay_ms,
        spread_norm=spread_norm,
        seed=seed + 1,
    )
    wet_mid = 0.5 * (wet_l + wet_r)
    wet_side = 0.5 * (wet_l - wet_r)
    wet_pair = (
        wet_mid + spread_norm * wet_side,
        wet_mid - spread_norm * wet_side,
    )
    # Explicit lateral early reflections make the synthetic room read as a
    # cinema rather than a generic reverb tail.  Times/gains differ slightly
    # between ears (asymmetric room), which also widens the diffuse field.
    early_taps = (
        (
            (7.0, 0.22), (11.0, -0.16), (16.0, 0.15),
            (23.0, -0.11), (31.0, 0.09), (41.0, 0.06),
        ),
        (
            (8.0, 0.20), (12.0, -0.15), (18.0, 0.14),
            (26.0, -0.10), (35.0, 0.08), (46.0, 0.055),
        ),
    )
    channels: list[np.ndarray] = []
    for offset, wet in enumerate(wet_pair):
        ir = np.zeros(wet.size, dtype=np.float64)
        ir[0] = 1.0
        ir += wet * wet_gain
        for delay_ms, gain in early_taps[offset]:
            index = int(delay_ms * 1e-3 * fs)
            if 0 < index < ir.size:
                ir[index] += gain
        peak = float(np.max(np.abs(ir)))
        if peak > 1.0:
            ir /= peak
        channels.append(ir)
    return np.stack(channels, axis=1)


def write_room_ir(
    path: Path | str,
    fs: int,
    *,
    rt60_s: float,
    size_norm: float = 0.5,
    damping_norm: float = 0.4,
    predelay_ms: float = 16.0,
    wet_gain: float = 0.26,
    spread_norm: float = 0.6,
    seed: int = 17,
) -> Path:
    """Write a stereo room response as 16-bit PCM WAV for PipeWire's convolver."""
    from scipy.io import wavfile

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    ir = stereo_room_ir(
        float(fs),
        rt60_s,
        size_norm=size_norm,
        damping_norm=damping_norm,
        predelay_ms=predelay_ms,
        wet_gain=wet_gain,
        spread_norm=spread_norm,
        seed=seed,
    )
    wavfile.write(
        destination,
        int(fs),
        np.asarray(np.clip(ir, -1.0, 1.0) * 32767.0, dtype="<i2"),
    )
    return destination


def estimate_rt60(ir: np.ndarray, fs: float) -> float:
    """Straightforward Schroeder RT60: regression −5…−25 dB, extrapolate to −60 dB."""
    ir = np.asarray(ir, dtype=float)
    energy = ir.astype(np.float64) ** 2
    if energy.size < 64 or float(energy.max()) <= 1e-24:
        return 0.0
    back = np.cumsum(energy[::-1])[::-1]
    db = 10.0 * np.log10(np.maximum(back / back[0], 1e-24))
    idx0 = np.argmax(db < -5.0).item()
    if idx0 == 0:  # no point falls below −5 dB (IR too short / noise floor high)
        return 0.0
    idx1 = np.argmax(db <= -25.0).item()
    if idx1 <= idx0 or idx1 - idx0 < 8:
        return 0.0
    t = np.arange(idx0, idx1) / fs
    seg = db[idx0:idx1]
    slope = float(np.polyfit(t, seg, 1)[0])
    if slope >= -1e-9:
        return 0.0
    # Time to fall 60 dB at this slope:
    return abs(60.0 / slope)
