"""Acceptance tests, Stage 2 gate 5: stereo widening + mono compatibility maths."""
from __future__ import annotations

import numpy as np
import pytest
from numpy.random import default_rng

from sonicfx.dsp import widening as W
from tests.conftest import FS, sine


def _music_like(n_seconds: float = 1.0) -> tuple[np.ndarray, np.ndarray]:
    """Simplified stereo music: correlated body plus decorrelated side content."""
    rng = default_rng(17)
    n = int(n_seconds * FS)
    mono = 0.3 * sine(220.0, n_seconds) + 0.02 * rng.standard_normal(n)
    diff = 0.05 * rng.standard_normal(n)
    return mono + diff, mono - diff


class TestMidSide:
    def test_roundtrip_lossless(self) -> None:
        left, right = _music_like()
        m, s = W.to_mid_side(left, right)
        left2, right2 = W.from_mid_side(m, s)
        np.testing.assert_allclose(left2, left, atol=1e-12)
        np.testing.assert_allclose(right2, right, atol=1e-12)

    def test_identity_width(self) -> None:
        left, right = _music_like()
        left2, right2 = W.apply_width(left, right, 1.0)
        np.testing.assert_allclose(left2, left, atol=1e-12)
        np.testing.assert_allclose(right2, right, atol=1e-12)

    def test_mono_width_zero(self) -> None:
        left, right = _music_like()
        left2, right2 = W.apply_width(left, right, 0.0)
        np.testing.assert_allclose(left2, right2, atol=1e-12)
        m, _ = W.to_mid_side(left, right)
        np.testing.assert_allclose(left2, m, atol=1e-12)


class TestMonoCompatibility:
    def test_width_does_not_change_mono_fold(self) -> None:
        """Key invariant: folding L+R cancels the side channel whatever the width."""
        left, right = _music_like()
        for width in (0.0, 1.0, 2.0, 3.0):
            assert W.mono_energy_ratio(left, right, width) == pytest.approx(1.0,
                                                                            abs=1e-9)

    def test_out_of_phase_program_folds_to_silence(self) -> None:
        x = sine(200.0, 0.5, amp=0.5)
        fold = W.mono_sum(x, -x)
        assert np.max(np.abs(fold)) < 1e-12

    def test_report_flags_out_of_phase(self) -> None:
        x = sine(200.0, 1.0, amp=0.5)
        report = W.mono_compat_report(x, -x)
        assert report["correlation"] < -0.99

    def test_correlation_bounds(self) -> None:
        mono = sine(100.0, 1.0, amp=1.0)
        assert W.channel_correlation(mono, mono) == pytest.approx(1.0, abs=1e-6)
        rng = default_rng(5)
        a = rng.standard_normal(int(2 * FS))
        b = rng.standard_normal(int(2 * FS))
        corr = W.channel_correlation(a, b)   # independent noise → ≈ 0 within 1/√N
        assert corr == pytest.approx(0.0, abs=0.05)

    def test_widening_report_blocks_extreme(self) -> None:
        """Very wide settings must de-correlate L/R (mono image dissolves)."""
        left, right = _music_like()
        base = W.mono_compat_report(left, right, 1.0)
        wide = W.mono_compat_report(left, right, 4.0)
        assert abs(base["correlation"]) > abs(wide["correlation"])


class TestInterauralPhase:
    def test_half_ms_right_delay_is_90_deg_at_500hz(self) -> None:
        x = sine(500.0, 1.0, amp=0.5)
        left = x
        right = np.concatenate([np.zeros(int(0.5e-3 * FS)), x[:-int(0.5e-3 * FS)]])
        phase = W.interaural_phase(left, right, FS, (300.0, 700.0))
        assert phase == pytest.approx(-90.0, abs=10.0)

    def test_identical_channels_zero_phase(self) -> None:
        x = sine(300.0, 1.0, amp=0.5)
        assert W.interaural_phase(x, x, FS, (100.0, 500.0)) == pytest.approx(0.0,
                                                                             abs=2.0)
