# SonicFX Ultimate — Concrete Implementation Plan

This plan is derived from the live Stage 1 audit (`docs/stage1-environment-audit.md`).
It defines the real audio path, the module map, and the staged acceptance gates.
Every "runtime-DSP" statement below refers to code executing inside PipeWire
(C, in the audio thread) — the Python process only designs, configures,
observes and routes.

## 1. Central architecture decision

**Production audio path = PipeWire `filter-chain` graphs** (PipeWire 1.6.2 is
already installed and runs as user services). A SonicFX chain lives in its own
child PipeWire daemon, i.e. `pipewire -c ~/.local/state/sonicfx/chain-<gen>.conf`:

```
app stream  →  [SonicFX virtual sink]                 (capture node, media.class=Audio/Sink)
                    ↓  builtin biquads / mixer / convolver / LADSPA / LV2
               filter.graph DSP                     (C DSP in PipeWire thread)
                    ↓
               playback ports —pw-link→ selected physical sink
```

Why this satisfies §4 of the build prompt:

* The DSP runs in the PipeWire audio thread (real-time, native C + hosted
  LADSPA/LV2 plugins). **No audio payload ever passes through Python.**
* The chain is an isolated child daemon → crash-safe: if the app dies, the
  daemon keeps processing; if the daemon dies, its links die with it and
  WirePlumber reroutes streams to the fallback device. Nobody's system audio
  config is overwritten — the daemon reads its own config file.
* Parameters are driven through node control ports (e.g. biquad `Freq/Q/Gain`)
  — smoothed (`:s` control names) to avoid zipper noise.
* `pw-dump`/`pw-cli`/`wpctl` (subprocess "control plane") implement device
  selection, linking, reconnection and health checks.

**Fallback rule (never simulated):** if a feature's plugin is missing
(e.g. Calf reverb), the GUI shows "Not available — install package X";
the audit flags it. Nothing fakes it.

## 2. Runtime mapping — effect → engine feature

| Module | Real-time implementation (PipeWire-native) |
|---|---|
| Parametric / graphic EQ | builtin `bq_*` biquads (live `Freq/Q/Gain`, smoothed); Python designs + renders the curve |
| HPF/LPF, shelves | same biquad family (`bq_hpf/bq_lpf/bq_lowshelf/bq_highshelf`) |
| Bass boost | low-shelf + optional sub-harmonic stage (CV-free; bounded limiter + clip detect) |
| Stereo widening | LADSPA `matrix_spatialiser_1422` (swh) with mono-sum safety option (builtin `mixer` + polarity compensation) |
| Mid-side | builtin ops: mono mix (`M=0.5(L+R)`), side (`S=0.5(L−R)`), re-encode (gain ops) |
| Reverb / ambience | Calf Reverb LV2 (live size/decay/damping/wet) or convolver with program-generated IR |
| Virtual surround (HRTF-style) | builtin `convolver` per-channel, hesuvi/KEMAR-compatible configs; user IRs supported; synthetic mode documented as an estimate (no false Atmos claims) |
| Crossfeed / headphone | LADSPA `bs2b` (+ optional width/externalization shelf) |
| Compressor / limiter | Calf Compressor/Limiter LV2 (or swh `fast_lookahead_limiter_1913`, Zam fallbacks) with GR estimation on the meter tap |
| Dialogue clarity | speech EQ pattern + moderate compression presets on the same engine |
| Noise reduction (mic) | optional, opt-in build of rnnoise-LADSPA (documented, not bundled) |
| Loudness | meter tap: K-weighted LUFS-I (Python, 100 ms update on tap only) + optional slow target-gain correction |
| Meters / diagnostics | bounded low-rate monitor capture (not the audio path); GR estimated, clip detection from tap peaks |

## 3. Module map (src/sonicfx)

| Package | Responsibilities (control plane unless noted) |
|---|---|
| `app.py` | Qt bootstrap, error funnel, exit-time routing restore |
| `ui/` | all PySide6 widgets; talks only to a typed `EngineAPI` |
| `audio/` | `EngineAPI` facade; subprocess helpers (`run_json` for pw-dump/pw-cli), device registry, meter tap, status/health |
| `pipewire/` | chain generator (filter-chain conf from JSON), daemon lifecycle (spawn/stop), pw-link wiring, event reconciliation (`pw-dump` diff at 500 ms), routing safety rules |
| `dsp/` | Python design tools (biquad coefficients for display/validation, IR synth, K-weighting, curve math) — never in the audio path |
| `profiles/` | presets (12 required) + validation + import/export (JSON) |
| `database/` | SQLite (presets, app settings) with WAL, schema versioning |
| `diagnostics/` | env audit, structured JSONL logs, crash-routability recovery doc |

Strict rule: **`ui/` never touches subprocess PipeWire directly.** All session
knowledge enters through `audio/` abstractions so the GUI can be headless-upgraded.

## 4. Staged implementation gates

*Stage gates have hard acceptance checks; a stage is "done" only when its checks pass on this machine.*

**Stage 1 ✅ (done)** — audit tool written & passing; missing system packages listed; deps scripts created.

**Stage 2 — offline DSP foundations (no sudo needed)**
Acceptance: pytest set covering (a) biquad design math vs scipy reference
(SOS at displayed frequencies within ±0.3 dB at test points), (b) peak-protection
math, (c) LUFS/K-weighting meter math vs textbook values, (d) IR synthesis
sanity (decay, energy). All pure numpy/scipy; run in CI-able minutes.

**Stage 3 — real PipeWire path (sudo plugin packages needed)**
Build `sonicfx` chain daemon from JSON: create virtual sink; verify with real
audio that a GUI-driven param change measurably changes the output spectrum
(pink-noise probe recorded through the chain, offline FFT comparison);
bypass = unprocessed path (same probe, ~zero insert). Verify exit-time restore +
daemon-kill rerouting to hardware sink. Bounds-check: no memory alloc in audio
thread when changing `Freq` (pw-top CPU flat for 10 min loop).

**Stage 4 — GUI (PySide6) live with the engine**
Every visible knob maps to the same control port used in Stage 3's recorded test;
pytest-qt injects synthetic state changes and asserts "GUI → param" wiring.
Dashboard shows real values: live peak meter from tap, engine latency from
quantum math, health from reconciler.

**Stage 5 — cinema/spatial/headphone/dialogue depth**
Calf reverb/hesuvi envelopes etc. as in §2; each profile quantised + documented.
Stage gate focuses on accuracy and side-effects of reverb/IR regen (no clicks:
shared-delay convolver swap test).

**Stage 6 — profiles, persistence, diagnostics, recovery**
SQLite presets; import/export; crash-restore self-heal at startup (reconciler
detects our last-generation chains unlinked and cleans up); logs rotation.

**Stage 7 — packaging/perf/accessibility/install**
AppImage or .deb; latency numbers measured and reported honestly; long-session
soak (audio continuity across 60 min continuous pink noise via the chain);

## 5. Hard safety invariants (all stages)

1. Never modify default sink/source without explicit user opt-in.
2. Child daemon always started with our own config file; conf path recorded.
   Nothing is written to `/etc/pipewire`, `~/.config/pipewire`.
3. Every route change first records the pre-state (default sink, rerouteable
   streams); bypass/exit/crash ⇒ restore.
4. Routing loop protection: when scanning link targets, our own virtual
   sink/source node names are excluded.
5. All budgets bounded: taps store ≤ ~2 MB of transient audio, stop when
   overflow; the audio path itself never depends on Python data.
