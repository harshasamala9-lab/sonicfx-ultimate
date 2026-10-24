# Stage 2 — Offline DSP Foundations: Results & Notes

Status: **complete** (2026-10-24) — `pytest tests/` → **71 passed**, `ruff check` → clean.

These modules are the control/design plane. The real-time path is PipeWire's
filter-chain (Stage 3); nothing here processes live payload.

## Coverage against the build-prompt acceptance list

| Prompt requirement | Where implemented | Verified by |
|---|---|---|
| (1) "DSP unit tests verify filter behavior and channel handling" | `dsp/filters.py` (RBJ biquads incl. shelves/peaking/LPF/HPF/BP/notch/allpass, cascade `response_db`), `dsp/irs.py` channel-positioned IRs | `tests/test_filters.py` — Butterworth cross-check (rtol 1e−6), gain-at-f0 exact, neutral-at-0-gain identity, stability sweep over every kind × gain ±24 × Q 0.05–17 |
| (2) "Gain and limiting tests verify peak protection" | `dsp/dynamics.py` (soft-knee gain computer, peak-envelope compressor, look-ahead brickwall limiter) | `tests/test_dynamics.py` — output peak ≤ threshold + 0.3 dB at four input levels; never boosts; zero added latency (unique noise-burst correlation peak at lag 0); release smoothness < 0.02 dB/sample |
| (3) "Stereo-widening tests measure phase and mono compatibility" | `dsp/widening.py` (mid/side, width, fold-energy ratio, L/R correlation, interaural phase) | `tests/test_widening.py` — lossless M/S roundtrip; folding L+R cancels S at any width (ratio == 1.0 within 1e−9); out-of-phase programme folds to digital silence and reports corr ≈ −1; 0.5 ms right delay ⇒ −90° @ 500 Hz ±10° |
| Loudness plumbing for (F/D) | `dsp/metering.py` (ITU 48 kHz K-coefficients incl. RLB HPF redesign, EBU block/gate integration, 4× oversampled true-peak estimation, clip counting) | `tests/test_metering.py` — −20 dBFS stereo 997 Hz sine ⇒ −20.11 LUFS (anchor checked against EBU Tech 3341 concept); mono ⇒ −23.12; absolute gate drops silence; relative gate defeats a long quiet tail; two programs 20 LU apart measure 20 ± 0.3 |

## Recalibration note (important, documented deliberately)

First-run anchors assumed |H_K(997 Hz)| = 0 dB. Hand-evaluation of the ITU
48 kHz shelf table gives **+0.65 dB at 997 Hz** (RLB ≈ −0.04 dB there), which
matches the EBU Tech 3341 idea of the calibration sine (−23 dBFS stereo 1 kHz
⇒ −23.0 LUFS ⇒ +0.7 dB in-band). All loudness anchors were therefore corrected
by +0.58 LU — the engine was right; the tests were wrong, and now both agree.

## Real bugs the test suite caught and fixed (evidence it works)

1. `metering.to_48k` — wrong GCD ratio produced 480:441000 decimation; now exactly 160:147 @44.1 kHz.
2. `dynamics.simulate_compressor` — the output level was passed through `db_to_lin` as if it were the gain (sample-scale GR ⇒ 100+ dB nonsense); detector now: peak envelope (attack/release ballistics) → static curve → gain = curve − level.
3. `dynamics._env_coeffs` — inverted envelope semantics (`exp(−1/τf)` returned as the *progress* fraction); correct classic form `1 − exp(−1/τf)`; a 150 ms release now actually releases in 150 ms.
4. `irs.reverb_ir` — construction order let the diffuse tail clobber early reflections, and `window` was referenced before definition; rebuilt tail-first + additive taps + 10 ms fade-in (click-free).
5. `metering.integrated_lufs` — channel-weighting loop simplified to the ITU `G_ch` knob after lint flagged dead/redundant variables (LFE stays excluded via `CHANNEL_GAINS['LFE'] = 0.0`).

## Measured behaviour (this machine)

* Full suite: 71 tests in ~1.9 s (CPU-bound numpy/scipy only, no audio devices needed).
* Stable, deterministic (seeded) designs; no printf-style float drift.

## Boundaries (restated)

Python `dsp/` never touches the live stream: EQ/curve data produced here is
consumed as *logical* `Freq/Q/Gain` values by PipeWire's builtin bq_* operators
(identical RBJ family), and IR对象的 are baked at design time. Splitting "design"
(Python) from "real-time" (C) is what keeps the system audio path real-time-safe.
