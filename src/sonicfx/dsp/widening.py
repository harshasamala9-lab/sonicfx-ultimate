"""Stereo widening / mid-side math and mono-compatibility checks (offline).

The real-time widening runs in PipeWire (SWH matrixSpatialiser or builtin
matrix ops); these helpers power the UI measurements and the acceptance tests
(promised in the build prompt: "stereo-widening tests measure phase and mono
compatibility").
"""
from __future__ import annotations

import numpy as np


def to_mid_side(left: np.ndarray, right: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Lossless mid/side transform (M = (L+R)/2, S = (L−R)/2)."""
    ln = np.asarray(left, float)
    rn = np.asarray(right, float)
    return (ln + rn) / 2.0, (ln - rn) / 2.0


def from_mid_side(mid: np.ndarray, side: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Inverse transform (L = M+S, R = M−S)."""
    m = np.asarray(mid, float)
    s = np.asarray(side, float)
    return m + s, m - s


def apply_width(left: np.ndarray, right: np.ndarray, width: float
                ) -> tuple[np.ndarray, np.ndarray]:
    """Scale the side channel by ``width`` (1.0 = unchanged, 0 = mono).

    Positive width widens; values > ~3 collapse mono-compatibility (caller's UI
    should bound that, the maths itself stays honest).
    """
    if width < 0:
        raise ValueError("width must be ≥ 0 (negative widths swap phase)")
    m, s = to_mid_side(left, right)
    return from_mid_side(m, s * float(width))


def mono_sum(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    """Plain mono fold-down (L+R), i.e. what a mono device would play."""
    return np.asarray(left, float) + np.asarray(right, float)


def mono_energy_ratio(left: np.ndarray, right: np.ndarray, width: float) -> float:
    """Mono-fold energy of ``apply_width`` relative to the un-widened fold.

    Analytically the fold is 2·M regardless of the side-channel scale (S cancels
    in L+R), so a correct implementation yields exactly 1.0 for every width.
    This function exists to *prove* that invariant numerically.
    """
    mx = mono_sum(*apply_width(left, right, width))
    mx0 = mono_sum(left, right)
    e0 = float(np.mean(mx0 ** 2)) + 1e-30
    return float(np.mean(mx ** 2)) / e0


def channel_correlation_pair(left: np.ndarray, right: np.ndarray) -> float:
    """Pearson correlation of L/R in [−1, 1] over the common length.

    1 = mono-coherent, 0 = decorrelated, −1 = out-of-phase.
    """
    ln = np.asarray(left, dtype=float)
    rn = np.asarray(right, dtype=float)
    n = min(ln.size, rn.size)
    ln, rn = ln[:n].copy(), rn[:n].copy()
    ln -= ln.mean()
    rn -= rn.mean()
    denom = float(np.linalg.norm(ln) * np.linalg.norm(rn))
    if denom <= 1e-30:
        return 0.0
    return float(np.dot(ln, rn) / denom)


def channel_correlation(left: np.ndarray, right: np.ndarray) -> float:
    """Alias of :func:`channel_correlation_pair`."""
    return channel_correlation_pair(left, right)


def mono_compat_report(left: np.ndarray, right: np.ndarray, width: float = 1.0
                       ) -> dict[str, float]:
    """Full mono-compatibility snapshot used by the Cinema/Headphone screens."""
    ln2, rn2 = apply_width(left, right, width)
    m, s = to_mid_side(ln2, rn2)
    s_energy = float(np.mean(s ** 2)) + 1e-30
    m_energy = float(np.mean(m ** 2)) + 1e-30
    return {
        "correlation": channel_correlation_pair(ln2, rn2),
        "side_mid_ratio": s_energy / m_energy,
        "fold_peak_dbfs": 20.0 * float(np.log10(max(np.max(np.abs(mono_sum(ln2, rn2))),
                                                    1e-12))),
        "mid_peak_lin": float(np.max(np.abs(m))),
    }


def interaural_phase(left: np.ndarray, right: np.ndarray, fs: float,
                     band: tuple[float, float]) -> float:
    """Average inter-channel phase difference (deg) within ``band`` (analytic signal)."""
    from scipy import signal as sp_signal

    ln = np.asarray(left, float)
    rn = np.asarray(right, float)
    n = min(ln.size, rn.size)
    if n < 16:
        raise ValueError("signal too short for a phase estimate")
    phi_l = np.angle(sp_signal.hilbert(ln[:n]))
    phi_r = np.angle(sp_signal.hilbert(rn[:n]))
    diff = np.unwrap(phi_r - phi_l)
    f = np.linspace(0, fs / 2, diff.size)
    mask = (f >= band[0]) & (f <= band[1])
    if not mask.any():
        raise ValueError(f"band {band} falls outside the resolvable range")
    return float(np.degrees(np.mean(diff[mask])))
