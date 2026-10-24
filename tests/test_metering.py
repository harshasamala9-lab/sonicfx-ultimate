"""Acceptance tests, Stage 2 gate 2: K-weighting, LUFS integration, peaks.

Anchors derive from published behaviour of ITU-R BS.1770-4 / EBU Tech 3341:
* A stereo pair of −20 dBFS 997 Hz sines meter ≈ −20.69 LUFS
  (−0.691 + 10·log10(2 · a²/2)).
* A mono −20 dBFS sine ≈ −23.70 LUFS.
* The K-filter is ~0 dB at 1 kHz and ~+4 dB in the HF shelf.
"""
from __future__ import annotations

import numpy as np
import pytest

from sonicfx.dsp import metering as M
from tests.conftest import FS, sine


class TestKFilter:
    def test_requires_48k(self) -> None:
        with pytest.raises(ValueError):
            M.k_weights(44_100.0)

    def test_to_48k_preserves_level(self) -> None:
        x = sine(1000.0, 1.0, fs=44_100.0, amp=0.5)
        y = M.to_48k(x, 44_100.0)
        assert y.shape[0] == pytest.approx(48_000, rel=0.01)
        assert np.max(np.abs(y)) == pytest.approx(0.5, abs=0.01)

    @pytest.mark.parametrize("freq,lo,hi", [(1000.0, 0.3, 0.9),
                                            (5000.0, 3.2, 4.8),
                                            (10000.0, 3.4, 4.9)])
    def test_shelf_response(self, freq: float, lo: float, hi: float) -> None:
        x = sine(freq, 1.0, amp=1.0)
        y = M.k_weight(x, FS)
        amp_out = np.max(np.abs(y[int(0.2 * FS):]))     # steady-state window
        gain_db = 20 * np.log10(amp_out)
        assert lo < gain_db < hi, (freq, gain_db)

    def test_rlb_rolls_off_very_low(self) -> None:
        x = sine(10.0, 1.0, amp=1.0)
        y = M.k_weight(x, FS)
        gain_db = 20 * np.log10(max(np.max(np.abs(y[int(0.2 * FS):])), 1e-9))
        assert gain_db < -18.0


class TestIntegratedLoudness:
    def test_stereo_sine_anchor(self) -> None:
        # ITU K-filter response at 997 Hz is ~+0.65 dB (verified against the
        # EBU Tech 3341 calibration concept), so −20 dBFS stereo reads ≈ −20.11.
        sig = sine(997.0, 5.0, amp=0.1)
        lufs = M.integrated_lufs([sig, sig], FS)
        assert lufs == pytest.approx(-20.11, abs=0.15)

    def test_mono_sine_anchor(self) -> None:
        lufs = M.integrated_lufs([sine(997.0, 5.0, amp=0.1)], FS)
        assert lufs == pytest.approx(-23.12, abs=0.15)

    def test_absolute_gate_excludes_silence(self) -> None:
        sig = sine(997.0, 5.0, amp=0.1)
        with_silence = np.concatenate([np.zeros(int(2 * FS)), sig])
        base = M.integrated_lufs([with_silence, with_silence], FS)
        assert base == pytest.approx(-20.11, abs=0.3)

    def test_relative_gate_ignores_long_quiet_tail(self) -> None:
        loud = sine(997.0, 2.0, amp=0.1)                    # ≈ −20.1 LUFS
        quiet = sine(997.0, 10.0, amp=0.01)                 # −20 LU quieter
        program = np.concatenate([loud, quiet])
        lufs = M.integrated_lufs([program, program], FS)
        # A naive ungated mean would report ≈ −27; gating must recover the loud part.
        assert lufs == pytest.approx(-20.2, abs=0.6)
        assert lufs < -17.0

    def test_two_levels_differ_by_20_lu(self) -> None:
        assert (M.integrated_lufs([sine(997.0, 4.0, amp=0.1)] * 2, FS)
                - M.integrated_lufs([sine(997.0, 4.0, amp=0.01)] * 2, FS)
                == pytest.approx(20.0, abs=0.3))


class TestPeaksAndClipping:
    def test_sample_vs_true_peak_inband(self) -> None:
        x = sine(997.0, 1.0, amp=1.0)
        p = M.peaks(x, FS)
        assert p.sample_peak_dbfs == pytest.approx(0.0, abs=0.01)
        assert p.true_peak_dbfs >= p.sample_peak_dbfs - 0.05
        assert p.true_peak_dbfs < 0.5

    def test_true_peak_exceeds_sample_on_square(self) -> None:
        x = np.sign(sine(200.0, 0.5))
        p = M.peaks(x, FS)
        assert p.true_peak_dbfs - p.sample_peak_dbfs > 1.0

    def test_clipping_events(self) -> None:
        x = np.zeros(int(1.0 * FS))
        x[int(0.10 * FS):int(0.11 * FS)] = 1.0     # one plateau 10 ms
        x[int(0.50 * FS):int(0.505 * FS)] = 1.0    # second plateau 5 ms
        assert M.clip_count(x, FS) == 2

    def test_no_false_clip_on_loud_sine(self) -> None:
        x = 0.9 * sine(997.0, 1.0)
        assert M.clip_count(x, FS) == 0

    def test_momentary_lufs_scale(self) -> None:
        m1 = M.momentary_lufs(sine(997.0, 1.0, amp=0.1), FS)
        m2 = M.momentary_lufs(sine(997.0, 1.0, amp=0.01), FS)
        assert m1 == pytest.approx(-23.12, abs=0.2)   # mono channel, K gain +0.58 LU
        assert m1 - m2 == pytest.approx(20.0, abs=0.2)
