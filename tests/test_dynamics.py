"""Acceptance tests, Stage 2 gate 3: compressor curve + look-ahead limiter math."""
from __future__ import annotations

import numpy as np
import pytest

from sonicfx.dsp import dynamics as D
from sonicfx.dsp.dynamics import (brickwall_limiter, compressor_curve_db,
                                  estimate_gain_reduction_db, simulate_compressor)
from tests.conftest import FS, sine


class TestCompressorCurve:
    def test_below_knee_unity(self) -> None:
        assert compressor_curve_db(np.array([-30.0, -20.0, -20.5]),
                                   threshold_db=-10.0, ratio=4.0, knee_db=6.0
                                   ) == pytest.approx([-30.0, -20.0, -20.5], abs=1e-9)

    def test_hard_knee_slope(self) -> None:
        out = compressor_curve_db(np.array([-10.0, -4.0, 2.0]),   # thr+0, +6, +12
                                  threshold_db=-10.0, ratio=4.0, knee_db=0.0)
        np.testing.assert_allclose(out, [-10.0, -8.5, -7.0], atol=1e-9)

    def test_soft_knee_continuity_at_edges(self) -> None:
        thr, ratio, knee = -10.0, 4.0, 6.0
        eps = 1e-4
        edges = [
            (thr - knee / 2 + eps, thr - knee / 2 - eps),   # below-knee edge
            (thr + knee / 2 - eps, thr + knee / 2 + eps),   # above-knee edge
        ]
        for inside, outside in edges:
            vals = compressor_curve_db(np.array([outside, inside]),
                                       thr, ratio, knee)
            assert abs(vals[1] - vals[0]) < 0.05, (inside, outside, vals)

    def test_soft_knee_smooths_gain_reduction(self) -> None:
        gains = D.compressor_gain_db(np.linspace(-14, -2, 200),
                                     threshold_db=-8.0, ratio=3.0, knee_db=12.0)
        # gain reduction must be strictly monotonic and smooth (no jumps)
        assert np.all(np.diff(gains) <= 1e-9)
        assert np.max(np.abs(np.diff(gains))) < 0.5


class TestCompressorSimulation:
    def test_reduces_high_level_peaks(self) -> None:
        x = sine(200.0, 1.0, amp=0.9)          # ≈ −0.9 dBFS peak
        y, gr = simulate_compressor(x, FS, threshold_db=-12.0, ratio=3.0,
                                    attack_ms=10.0, release_ms=120.0)
        # A 10 ms attack legitimately lets the very first transient through, so
        # compare the settled window, not the global peak.
        settled_in = np.max(np.abs(x[int(0.1 * FS):]))
        settled_out = np.max(np.abs(y[int(0.1 * FS):]))
        assert settled_out < settled_in
        assert -np.min(gr) > 4.0   # ≥ 4 dB GR eventually on a −12 thr
        assert np.max(gr) <= 1e-6  # never boosts

    def test_attack_release_shape(self) -> None:
        # step from quiet to loud: gain reduction must build smoothly, hold steady
        x = np.concatenate([0.05 * sine(1000.0, 0.3), sine(1000.0, 0.5)])
        y, gr = simulate_compressor(x, FS, threshold_db=-14.0, ratio=3.0,
                                    attack_ms=10.0, release_ms=150.0)
        reduction = -gr
        build = reduction[int(0.30 * FS):int(0.45 * FS)]
        assert np.all(np.diff(build) >= -0.5)              # monotonic-ish build-up
        assert np.max(np.abs(np.diff(build))) < 1.0        # no sample-scale jumps
        steady = reduction[int(0.75 * FS):int(0.8 * FS)]
        assert steady.mean() > 5.0                         # converged deep reduction
        assert np.max(np.abs(np.diff(steady))) < 0.05      # settled: ripple gone
        assert gr.max() <= 1e-6                            # never boosts


class TestLimiter:
    def test_brickwall_never_exceeds_threshold(self) -> None:
        for amp in (0.5, 0.9, 1.0, 1.5):
            x = sine(1000.0, 1.0, amp=amp)
            y, _ = brickwall_limiter(x, FS, threshold_db=-3.0)
            peak_db = 20 * np.log10(np.max(np.abs(y)))
            assert peak_db <= -3.0 + 0.3, (amp, peak_db)

    def test_gr_matches_headroom(self) -> None:
        x = sine(1000.0, 1.0, amp=1.0)                     # 0 dBFS
        y, gr = brickwall_limiter(x, FS, threshold_db=-3.0)
        assert -np.min(gr) == pytest.approx(3.0, abs=0.4)  # −3 dB threshold ⇒ ~3 dB GR

    def test_quiet_signal_untouched(self) -> None:
        x = sine(500.0, 1.0, amp=0.1)                       # −20 dBFS
        y, gr = brickwall_limiter(x, FS, threshold_db=-3.0)
        np.testing.assert_allclose(y, x, atol=1e-6)
        assert np.max(np.abs(gr)) < 1e-3

    def test_release_smoothness(self) -> None:
        x = np.concatenate([sine(1000.0, 0.4, amp=1.4), sine(1000.0, 0.6, amp=0.05)])
        y, gr = brickwall_limiter(x, FS, threshold_db=-2.0, release_ms=80.0)
        d = np.abs(np.diff(gr))
        # after attack phase, release must be smooth (per-sample GR change tiny)
        assert np.max(d[int(0.5 * FS):]) < 0.02

    def test_gr_estimate(self) -> None:
        x = sine(997.0, 1.0, amp=1.0)
        y, _ = brickwall_limiter(x, FS, threshold_db=-3.0)
        est = estimate_gain_reduction_db(x, y, FS)
        assert est == pytest.approx(3.0, abs=0.4)

    def test_limiter_output_latency_free(self) -> None:
        """Output stays sample-aligned with input (look-ahead must not delay y).

        Uses a noise burst so the cross-correlation peak is unique (a periodic
        sine self-correlates identically at every period).
        """
        rng = np.random.default_rng(23)
        x = 0.3 * rng.standard_normal(int(0.5 * FS))
        y, _ = brickwall_limiter(x, FS, threshold_db=-1.0, lookahead_ms=5.0)
        seg = int(FS // 10)
        corr = np.correlate(y[:seg], x[:seg], mode="full")
        # mode="full": lag 0 sits at index (seg − 1); require a unique sharp peak there
        peak_idx = int(np.argmax(np.abs(corr)))
        assert peak_idx == seg - 1
        assert np.max(np.abs(corr[[seg - 2, seg]])) < 0.5 * np.abs(corr[seg - 1])
