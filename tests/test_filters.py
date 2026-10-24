"""Acceptance tests, Stage 2 gate 1: biquad design & response math.

Cross-validates RBJ designs at 48 kHz against scipy Butterworth references and
verifies stability, neutrality and the filter-chain control mapping.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from sonicfx.dsp import filters as F
from sonicfx.dsp.filters import BiquadBand, FilterKind, design_band, response_db
from tests.conftest import FS

Q_BUTTER = 1.0 / math.sqrt(2.0)


def _mag_db(c: F.Coefficients, freqs_ms, fs: float = FS) -> np.ndarray:
    f = np.asarray(freqs_ms, dtype=float)
    w = 2 * np.pi * f / fs
    z = np.exp(-1j * w)
    h = (c.b0 + c.b1 * z + c.b2 * z**2) / (1 + c.a1 * z + c.a2 * z**2)
    return 20 * np.log10(np.maximum(np.abs(h), 1e-12))


class TestLowHighPass:
    def test_lowpass_matches_butterworth_q0707(self) -> None:
        band = BiquadBand(FilterKind.LOW_PASS, freq=800.0, q=Q_BUTTER)
        c = design_band(band, FS)
        ref = F.low_pass_butter_reference(800.0, FS)
        np.testing.assert_allclose(c.as_tuple(), ref, rtol=1e-6, atol=1e-9)

    def test_highpass_matches_butterworth_q0707(self) -> None:
        band = BiquadBand(FilterKind.HIGH_PASS, freq=1200.0, q=Q_BUTTER)
        c = design_band(band, FS)
        ref = F.high_pass_butter_reference(1200.0, FS)
        np.testing.assert_allclose(c.as_tuple(), ref, rtol=1e-6, atol=1e-9)

    def test_lowpass_attenuation_shape(self) -> None:
        c = design_band(BiquadBand(FilterKind.LOW_PASS, freq=500.0, q=Q_BUTTER), FS)
        mags = _mag_db(c, [200.0, 1000.0, 4000.0])
        assert mags[0] > -0.25           # passband (2nd-order Butter: −0.17 dB at 0.4 oct)
        assert mags[1] < -10.0
        assert mags[2] < -34.0   # 2nd-order Butterworth: −36.1 dB at 2 octaves above
        assert _is_stable(c)


class TestShelves:
    def test_lowshelf_boost_far_sides(self) -> None:
        # 120 Hz +6 dB, steep slope (S=1 ⇔ q=2)
        band = BiquadBand(FilterKind.LOW_SHELF, freq=120.0, gain_db=6.0, q=2.0)
        c = design_band(band, FS)
        f = np.array([25.0, 100.0, 2000.0, 15000.0])
        mags = _mag_db(c, f)
        assert mags[0] == pytest.approx(6.0, abs=0.35)   # fully within the shelf
        assert 2.5 < mags[1] <= 6.5                       # transitioning below corner
        assert mags[2] < 0.7                              # transitioned toward 0
        assert abs(mags[3]) < 0.25                        # unity above the shelf
        assert _is_stable(c)

    def test_highshelf_cut_far_sides(self) -> None:
        band = BiquadBand(FilterKind.HIGH_SHELF, freq=4000.0, gain_db=-6.0, q=2.0)
        c = design_band(band, FS)
        mags = _mag_db(c, np.array([200.0, 12000.0]))
        assert abs(mags[0]) < 0.25        # unity below the corner
        assert mags[1] == pytest.approx(-6.0, abs=0.25)
        assert _is_stable(c)

    def test_lowshelf_gain_at_corner_between(self) -> None:
        band = BiquadBand(FilterKind.LOW_SHELF, freq=250.0, gain_db=12.0, q=2.0)
        mags = _mag_db(design_band(band, FS), np.array([250.0]))
        assert 0.0 < mags[0] < 12.0       # transition point sits between the shelf levels


class TestPeaking:
    def test_gain_exactly_at_f0(self) -> None:
        for gain in (6.0, 12.0, -8.0):
            band = BiquadBand(FilterKind.PEAKING, freq=1000.0, gain_db=gain, q=1.0)
            mag = _mag_db(design_band(band, FS), np.array([1000.0]))[0]
            assert mag == pytest.approx(gain, abs=0.1), f"gain {gain}"

    def test_neutral_when_zero_gain(self) -> None:
        band = BiquadBand(FilterKind.PEAKING, freq=777.0, gain_db=0.0, q=2.4)
        c = design_band(band, FS)
        assert c.b0 == pytest.approx(1.0, 1e-12)
        assert c.a1 == pytest.approx(c.b1, 1e-12)
        mags = _mag_db(c, np.array([30.0, 500.0, 20000.0]))
        np.testing.assert_allclose(mags, 0.0, atol=1e-9)

    def test_q_widens_narrowing_band(self) -> None:
        wide = design_band(BiquadBand(FilterKind.PEAKING, 1000.0, 6.0, q=0.5), FS)
        narrow = design_band(BiquadBand(FilterKind.PEAKING, 1000.0, 6.0, q=8.0), FS)
        o2 = np.array([2000.0])
        w2 = _mag_db(wide, o2)[0]
        n2 = _mag_db(narrow, o2)[0]
        assert w2 > n2 + 3.0   # wider Q leaves more level at one octave above


class TestStabilitySweep:
    @pytest.mark.parametrize("gain", [-24.0, 0.0, 24.0])
    @pytest.mark.parametrize("q", [0.05, 0.71, 4.0, 17.0])
    def test_all_kinds_stable(self, gain: float, q: float) -> None:
        freqs = np.array([25.0, 341.0, 3141.0, 20400.0])
        for kind in FilterKind:
            band = BiquadBand(kind, freq=float(freqs[0]), gain_db=gain, q=q)
            for fq in freqs:
                band = BiquadBand(kind, freq=float(fq), gain_db=gain, q=q)
                c = design_band(band, FS)
                assert _is_stable(c), (kind, fq, gain, q)

    def test_response_finite(self) -> None:
        bands = [
            BiquadBand(FilterKind.LOW_SHELF, 80.0, 9.0, 1.2),
            BiquadBand(FilterKind.PEAKING, 630.0, -5.0, 2.0),
            BiquadBand(FilterKind.PEAKING, 3800.0, 4.5, 0.9),
            BiquadBand(FilterKind.HIGH_SHELF, 9000.0, 3.0, 1.0),
        ]
        mags, phase = response_db(bands, FS)
        assert np.all(np.isfinite(mags)) and np.all(np.isfinite(phase))
        assert mags.shape == phase.shape == (449,)   # default log grid


class TestValidation:
    def test_rejects_out_of_range(self) -> None:
        with pytest.raises(ValueError):
            BiquadBand(FilterKind.PEAKING, freq=10.0)          # below 20 Hz
        with pytest.raises(ValueError):
            BiquadBand(FilterKind.PEAKING, freq=21000.0)       # above 20.5 kHz
        with pytest.raises(ValueError):
            BiquadBand(FilterKind.PEAKING, 1000.0, gain_db=40.0)
        with pytest.raises(ValueError):
            BiquadBand(FilterKind.PEAKING, 1000.0, q=25.0)

    def test_control_params_mapping(self) -> None:
        # Logical mapping to PipeWire's builtin `bq_*` control ports
        pk = BiquadBand(FilterKind.PEAKING, 1000.0, 6.0, 1.5)
        assert pk.control_params() == {"Freq": 1000.0, "Q": 1.5, "Gain": 6.0}
        lp = BiquadBand(FilterKind.LOW_PASS, 400.0, q=0.7)
        assert lp.control_params()["Gain"] == 0.0
        assert FilterKind.PEAKING.pipewire_label == "bq_peaking"
        assert FilterKind.LOW_SHELF.pipewire_label == "bq_lowshelf"
        assert FilterKind.HIGH_PASS.pipewire_label == "bq_highpass"

    def test_disabled_bands_excluded_from_response(self) -> None:
        enabled = BiquadBand(FilterKind.PEAKING, 1000.0, -12.0, 1.0)
        flat = BiquadBand(FilterKind.PEAKING, 100.0, 10.0, 1.0, enabled=False)
        mags, _ = response_db([enabled, flat], FS, np.array([1000.0]))
        assert mags[0] == pytest.approx(-12.0, abs=0.1)    # disabled band had no effect


def _is_stable(c):
    """Jury test (z² + a1z + a2 roots inside the unit circle)."""
    return abs(c.a2) < 1.0 and (1.0 + c.a2) > abs(c.a1)
