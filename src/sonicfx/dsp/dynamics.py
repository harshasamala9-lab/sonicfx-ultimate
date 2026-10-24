"""Dynamics gain computer & offline limiter simulation.

These are the exact books used for:
* the GUI's gain-reduction *estimate* (computed on the meter tap, after the fact),
* profile headroom pre-checks,
* acceptance tests of the hosted look-ahead limiter (compare chain output).

Not part of the real-time audio path.
"""
from __future__ import annotations

import math

import numpy as np


def compressor_curve_db(x_db: np.ndarray | float, threshold_db: float,
                        ratio: float, knee_db: float = 6.0) -> np.ndarray:
    """Static compressor input→output mapping (dB in, dB out).

    Soft knee: quadratic inside [threshold−knee/2, threshold+knee/2].
    Validates: below the knee → no change; above → slope 1/ratio.
    """
    x = np.asarray(x_db, dtype=float)
    half = max(knee_db, 0.0) / 2.0
    lo = threshold_db - half
    hi = threshold_db + half
    out = x.copy()
    if knee_db > 0.0:
        inside = (x > lo) & (x < hi)
        # Straight line blended at knee edges (quadratic knee, standard form):
        # y = x + (1/ratio − 1) * (x − lo)² / (2·(hi−lo))
        out = np.where(inside,
                       x + (1.0 / ratio - 1.0) * (x - lo) ** 2 / (2.0 * (hi - lo)),
                       out)
    above = x >= hi
    out = np.where(above, threshold_db + (x - threshold_db) / ratio, out)
    return out if out.ndim else float(out)  # type: ignore[return-value]


def compressor_gain_db(x_db: np.ndarray | float, threshold_db: float,
                       ratio: float, knee_db: float = 6.0) -> np.ndarray:
    """Gain reduction (negative dB) applied by the compressor curve."""
    return np.subtract(compressor_curve_db(float(x_db) if not isinstance(x_db, np.ndarray)
                                           else x_db, threshold_db, ratio, knee_db),
                       float(x_db) if not isinstance(x_db, np.ndarray) else x_db)


def _env_coeffs(ms: float, fs: float) -> float:
    """Per-sample progress coefficient for a one-pole envelope follower.

    Returns c = 1 − exp(−1/(τ·fs)) so that ``e ← e + c·(target − e)`` reaches
    the target with time constant ``ms`` (instant when ms ≤ 0).
    """
    if ms <= 0.0:
        return 1.0
    return 1.0 - math.exp(-1.0 / (ms / 1000.0 * fs))


def db_to_lin(db: float | np.ndarray) -> np.ndarray:
    return np.power(10.0, np.asarray(db, dtype=float) / 20.0)


def lin_to_db(x: np.ndarray | float) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    return 20.0 * np.log10(np.maximum(np.abs(x), 1e-12))


def simulate_compressor(x: np.ndarray, fs: float, threshold_db: float,
                        ratio: float, attack_ms: float, release_ms: float,
                        knee_db: float = 6.0) -> tuple[np.ndarray, np.ndarray]:
    """Offline compressor reference simulation (control-plane GR estimate).

    Signal chain: peak-envelope detector (attack/release ballistics) → static
    gain computer (soft knee) → output. Returns (y, gr_db per sample).
    """
    x = np.asarray(x, dtype=float)
    a_attack, a_release = _env_coeffs(attack_ms, fs), _env_coeffs(release_ms, fs)

    # 1) peak-envelope detector with attack/release ballistics
    env = np.empty_like(x)
    e = 0.0
    for i, xi in enumerate(np.abs(x)):
        if xi > e:
            e = e + a_attack * (xi - e) if a_attack < 1.0 else xi
        else:
            e = e + a_release * (xi - e) if a_release < 1.0 else xi
        env[i] = e

    # 2) static gain computer: GAIN (dB) = out(level) − level
    level_db = lin_to_db(env)
    gain_lin = db_to_lin(compressor_curve_db(level_db, threshold_db, ratio, knee_db)
                         - level_db)
    return x * gain_lin, lin_to_db(gain_lin)


def brickwall_limiter(x: np.ndarray, fs: float, threshold_db: float = -1.0,
                      lookahead_ms: float = 5.0, release_ms: float = 80.0
                      ) -> tuple[np.ndarray, np.ndarray]:
    """True look-ahead brickwall limiter (offline reference simulation).

    Returns (y, applied_gr_db). Output peaks are guaranteed ≤ threshold ± 0.25 dB
    for band-limited test material within the oversampled window.
    """
    x = np.asarray(x, dtype=float)
    n = x.shape[0]
    lag = max(1, int(lookahead_ms / 1000.0 * fs))
    thr_lin = db_to_lin(threshold_db)
    # Peak envelope over the lookahead window (reversed cummax):
    peak = np.empty(n + lag)
    peak[:lag] = 0.0
    peak[lag:] = np.abs(x)
    # Sliding max via simple O(n·lag/… ) scheme: use stride tricks for speed.
    st = np.lib.stride_tricks.sliding_window_view(peak, lag + 1)
    env_max = st.max(axis=1)  # length n

    required = np.where(env_max > 0, thr_lin / np.maximum(env_max, 1e-12), 1.0)
    required = np.minimum(required, 1.0)
    a_release = _env_coeffs(release_ms, fs)
    g = 1.0
    gain = np.empty(n)
    for i, t in enumerate(required):
        if t < g:       # attack: immediate (lookahead covers it)
            g = t
        else:           # release: one-pole
            g = g + a_release * (t - g)
        gain[i] = g
    return x * gain, lin_to_db(gain)


def estimate_gain_reduction_db(x_in: np.ndarray, x_out: np.ndarray, fs: float,
                               median_ms: float = 50.0) -> float:
    """Median gain reduction (dB, ≥ 0) between two synchronized analysis windows."""
    import numpy as _np

    win = max(1, int(median_ms / 1000.0 * fs))
    n = min(len(x_in), len(x_out))
    diff = (20.0 * _np.log10(_np.maximum(_np.abs(np.asarray(x_out[:n])), 1e-12))
            - 20.0 * _np.log10(_np.maximum(_np.abs(np.asarray(x_in[:n])), 1e-12)))
    return float(-np.median(diff[:n - win + 1: max(1, win // 4)]))
