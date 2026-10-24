"""Acceptance tests, Stage 2 gate 4: IR synthesis + RT60 estimation."""
from __future__ import annotations

import numpy as np
import pytest

from sonicfx.dsp import irs as R
from tests.conftest import FS


class TestReverbIR:
    def test_deterministic_with_seed(self) -> None:
        a = R.reverb_ir(FS, rt60_s=1.0, size_norm=0.5, damping_norm=0.3, seed=11)
        b = R.reverb_ir(FS, rt60_s=1.0, size_norm=0.5, damping_norm=0.3, seed=11)
        np.testing.assert_array_equal(a, b)

    def test_peak_normalised(self) -> None:
        ir = R.reverb_ir(FS, rt60_s=1.5, seed=3)
        assert np.max(np.abs(ir)) == pytest.approx(1.0, abs=1e-9)

    def test_predelay_is_silent(self) -> None:
        ir = R.reverb_ir(FS, rt60_s=1.0, predelay_ms=20.0, seed=5)
        pre = int(20.0 / 1000.0 * FS)
        assert np.all(ir[:pre] == 0.0)
        assert not np.all(ir[pre:pre + int(0.05 * FS)] == 0.0)

    def test_length_tracks_rt60(self) -> None:
        ir = R.reverb_ir(FS, rt60_s=2.0, predelay_ms=10.0, seed=1)
        expected = FS * (10.0 / 1000.0 + 2.0 * 1.15) + 1
        assert ir.shape[0] == int(expected)

    def test_rt60_estimation_accuracy(self) -> None:
        for rt60 in (0.6, 1.2, 2.5):
            ir = R.reverb_ir(FS, rt60_s=rt60, size_norm=0.5,
                             damping_norm=0.25, predelay_ms=10.0, seed=42)
            est = R.estimate_rt60(ir, FS)
            assert est == pytest.approx(rt60, rel=0.25), (rt60, est)

    def test_more_damping_darkens_tail(self) -> None:
        from scipy import signal as sp_signal

        def hf_ratio(damping: float) -> float:
            ir = R.reverb_ir(FS, rt60_s=1.2, damping_norm=damping,
                             predelay_ms=5.0, seed=9)
            # energy above 4 kHz / total, computed on bandpass-filtered tail
            sos = sp_signal.butter(2, 4000.0, fs=FS, btype="highpass", output="sos")
            y = sp_signal.sosfilt(sos, ir)
            half = int(FS // 2)
            e_hf = float(np.mean(y[half:] ** 2)) + 1e-30
            e_all = float(np.mean(ir[half:] ** 2)) + 1e-30
            return e_hf / e_all

        assert hf_ratio(0.9) < hf_ratio(0.1)

    def test_validation_errors(self) -> None:
        with pytest.raises(ValueError):
            R.reverb_ir(FS, rt60_s=20.0)
        with pytest.raises(ValueError):
            R.reverb_ir(FS, rt60_s=1.0, damping_norm=1.5)
        with pytest.raises(ValueError):
            R.reverb_ir(0.0, rt60_s=1.0)


class TestReflectionTaps:
    def test_taps_sorted_and_bounded(self) -> None:
        taps = R.early_reflection_taps(FS, size_norm=0.6, seed=2)
        assert len(taps) >= 5
        delays = [t[0] for t in taps]
        gains = [t[1] for t in taps]
        assert delays == sorted(delays)
        assert delays[0] > 0.0
        assert all(0.0 <= g <= 1.0 for g in gains)
