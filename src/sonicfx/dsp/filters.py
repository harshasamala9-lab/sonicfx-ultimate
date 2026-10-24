"""Biquad band design (RBJ audio-EQ-cookbook) + response math.

Design values are exported as logical controls ("Freq/Q/Gain") directly to
PipeWire's builtin biquad operators (``bq_peaking`` etc. share the same
RBJ formulation), so the displayed curve == applied chain response.

Coordinate with :mod:`scipy` for lowpass/highpass cross-validation in tests.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

import numpy as np


class FilterKind(StrEnum):
    """Biquad band kinds supported by PipeWire builtin ``bq_*`` operators."""

    PEAKING = "peaking"
    LOW_SHELF = "lowshelf"
    HIGH_SHELF = "highshelf"
    LOW_PASS = "lowpass"
    HIGH_PASS = "highpass"
    BAND_PASS = "bandpass"      # constant 0 dB peak-gain member
    NOTCH = "notch"
    ALL_PASS = "allpass"

    @property
    def pipewire_label(self) -> str:
        return "bq_" + self.value


#: Frequency grid for curve rendering (log-spaced 20 Hz … 22 kHz at ~96 pts/octave).
_FREQ_GRID = np.logspace(np.log10(20.0), np.log10(22000.0), 449)


@dataclass(frozen=True)
class BiquadBand:
    """One equaliser band. Frozen so profiles share instances safely."""

    kind: FilterKind
    freq: float            # Hz, corner/centre
    gain_db: float = 0.0   # peaking/shelf only; LPF/HPF/NOTCH ignore
    q: float = 0.71        # design Q (shelf maps to RBJ S via _shelf_slope())
    enabled: bool = True

    def __post_init__(self) -> None:
        if not (20.0 <= self.freq <= 20_500.0):
            raise ValueError(f"band frequency {self.freq} Hz outside 20–20.5 kHz")
        if self.q <= 0.0 or self.q > 18.0:
            raise ValueError(f"band Q {self.q} outside (0, 18]")
        if self.kind in (FilterKind.PEAKING, FilterKind.LOW_SHELF, FilterKind.HIGH_SHELF):
            if not (-24.0 <= self.gain_db <= 24.0):
                raise ValueError(f"band gain {self.gain_db} dB outside ±24 dB")

    # -- export to PipeWire filter-chain control values --------------------
    def control_params(self) -> dict[str, float]:
        """Logical control values understood by builtin bq_* operators."""
        shaped = (self.kind in (FilterKind.PEAKING, FilterKind.LOW_SHELF,
                                FilterKind.HIGH_SHELF))
        return {
            "Freq": float(self.freq),
            "Q": float(self.q),
            "Gain": float(self.gain_db if shaped else 0.0),
        }


@dataclass(frozen=True)
class Coefficients:
    """Normalised biquad (a0 == 1)."""

    b0: float
    b1: float
    b2: float
    a1: float
    a2: float

    def as_tuple(self) -> tuple[float, float, float, float, float]:
        return (self.b0, self.b1, self.b2, self.a1, self.a2)


def _wrap_angle(w0: float) -> float:
    """Digital frequency for design (clamped below Nyquist)."""
    return min(max(w0, 1e-6), math.pi - 1e-6)


def _shelf_slope(q: float) -> float:
    """Map UI Q (0…18) to RBJ shelf slope S ∈ (0, 1]. S=1 → steepest shelf."""
    return min(1.0, max(0.05, q / 2.0))


def design_band(band: BiquadBand, fs: float) -> Coefficients:
    """RBJ cookbook bilinear design of one band at sample rate ``fs``.

    Raises ValueError for fs<=0. All formulas (W3C audio-eq-cookbook, RBJ)
    are the same family PipeWire's builtin bq_* operators implement.
    """
    if fs <= 0:
        raise ValueError("fs must be positive")
    w0 = _wrap_angle(2.0 * math.pi * band.freq / fs)
    c = math.cos(w0)
    s = math.sin(w0)

    kind = band.kind
    if kind is FilterKind.PEAKING:
        A = 10.0 ** (band.gain_db / 40.0)
        alpha = s / (2.0 * band.q)
        b0 = 1.0 + alpha * A
        b1 = -2.0 * c
        b2 = 1.0 - alpha * A
        a0 = 1.0 + alpha / A
        a1 = -2.0 * c
        a2 = 1.0 - alpha / A
    elif kind in (FilterKind.LOW_SHELF, FilterKind.HIGH_SHELF):
        A = 10.0 ** (band.gain_db / 40.0)
        S = _shelf_slope(band.q)
        alpha = s / 2.0 * math.sqrt((A + 1.0 / A) * (1.0 / S - 1.0) + 2.0)
        two_sqA_a = 2.0 * math.sqrt(A) * alpha
        if kind is FilterKind.LOW_SHELF:
            b0 = A * ((A + 1.0) - (A - 1.0) * c + two_sqA_a)
            b1 = 2.0 * A * ((A - 1.0) - (A + 1.0) * c)
            b2 = A * ((A + 1.0) - (A - 1.0) * c - two_sqA_a)
            a0 = (A + 1.0) + (A - 1.0) * c + two_sqA_a
            a1 = -2.0 * ((A - 1.0) + (A + 1.0) * c)
            a2 = (A + 1.0) + (A - 1.0) * c - two_sqA_a
        else:  # HIGH_SHELF
            b0 = A * ((A + 1.0) + (A - 1.0) * c + two_sqA_a)
            b1 = -2.0 * A * ((A - 1.0) + (A + 1.0) * c)
            b2 = A * ((A + 1.0) + (A - 1.0) * c - two_sqA_a)
            a0 = (A + 1.0) - (A - 1.0) * c + two_sqA_a
            a1 = 2.0 * ((A - 1.0) - (A + 1.0) * c)
            a2 = (A + 1.0) - (A - 1.0) * c - two_sqA_a
    elif kind in (FilterKind.LOW_PASS, FilterKind.HIGH_PASS):
        alpha = s / (2.0 * band.q)
        k = (1.0 - c) if kind is FilterKind.LOW_PASS else (1.0 + c)
        b0, b1, b2 = k / 2.0, k, k / 2.0
        if kind is FilterKind.HIGH_PASS:
            b1 = -k
        a0, a1, a2 = 1.0 + alpha, -2.0 * c, 1.0 - alpha
    elif kind is FilterKind.BAND_PASS:
        alpha = s / (2.0 * band.q)
        b0, b1, b2 = alpha, 0.0, -alpha          # constant 0 dB peak gain
        a0, a1, a2 = 1.0 + alpha, -2.0 * c, 1.0 - alpha
    elif kind is FilterKind.NOTCH:
        alpha = s / (2.0 * band.q)
        b0, b1, b2 = 1.0, -2.0 * c, 1.0
        a0, a1, a2 = 1.0 + alpha, -2.0 * c, 1.0 - alpha
    elif kind is FilterKind.ALL_PASS:
        alpha = s / (2.0 * band.q)
        b0, b1, b2 = 1.0 - alpha, -2.0 * c, 1.0 + alpha
        a0, a1, a2 = 1.0 + alpha, -2.0 * c, 1.0 - alpha
    else:  # pragma: no cover - enum exhaustiveness
        raise ValueError(f"unsupported kind {kind!r}")

    return Coefficients(b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0)


def _biquad_response(c: Coefficients, freqs: np.ndarray, fs: float) -> np.ndarray:
    """|H| in dB of one biquad at the given freqs."""
    w = 2.0 * np.pi * np.asarray(freqs, dtype=float) / fs
    z = np.exp(-1j * w)
    num = c.b0 + c.b1 * z + c.b2 * z**2
    den = 1.0 + c.a1 * z + c.a2 * z**2
    with np.errstate(divide="ignore", invalid="ignore"):
        mag = np.abs(num / den)
    db = 20.0 * np.log10(np.maximum(mag, 1e-12))
    return np.nan_to_num(db, nan=-400.0, posinf=-400.0, neginf=-400.0)


def _is_stable(c: Coefficients) -> bool:
    """Jury test for z² + a1 z + a2 (both roots inside the unit circle)."""
    a1, a2 = c.a1, c.a2
    return (abs(a2) < 1.0) and (1.0 + a2 > abs(a1))


def response_db(bands: list[BiquadBand], fs: float,
                freqs: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Total cascade magnitude (dB) and phase (deg) for enabled bands."""
    f = _FREQ_GRID if freqs is None else np.asarray(freqs, float)
    total_mag_db = np.zeros_like(f)
    phase = np.zeros(f.shape, dtype=complex)
    for band in bands:
        if not band.enabled:
            continue
        c = design_band(band, fs)
        w = 2.0 * np.pi * f / fs
        z = np.exp(-1j * w)
        num = c.b0 + c.b1 * z + c.b2 * z**2
        den = 1.0 + c.a1 * z + c.a2 * z**2
        h = num / den
        total_mag_db += 20.0 * np.log10(np.maximum(np.abs(h), 1e-12))
        phase *= h
    phase_deg = np.degrees(np.angle(phase))
    return total_mag_db, phase_deg


def neutral_curve_db(freqs: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Convenience for the GUI when every band is disabled (all-zero curve)."""
    f = np.asarray(freqs, float) if freqs is not None else _FREQ_GRID
    return np.zeros_like(f), np.zeros_like(f)


def low_pass_butter_reference(cutoff: float, fs: float,
                              ) -> tuple[float, float, float, float, float]:
    """scipy 2nd-order Butterworth lowpass (Q = 1/sqrt2) for cross-validation."""
    from scipy import signal as sp_signal

    b, a = sp_signal.butter(2, cutoff / (fs / 2.0), btype="low", output="ba")
    return (float(b[0]), float(b[1]), float(b[2]), float(a[1]), float(a[2]))


def high_pass_butter_reference(cutoff: float, fs: float,
                               ) -> tuple[float, float, float, float, float]:
    from scipy import signal as sp_signal

    b, a = sp_signal.butter(2, cutoff / (fs / 2.0), btype="high", output="ba")
    return (float(b[0]), float(b[1]), float(b[2]), float(a[1]), float(a[2]))
