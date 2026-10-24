# Stage 3 results — real PipeWire DSP path

Date: 2026-10-24
System: Ubuntu 26.04.1, PipeWire 1.6.2, WirePlumber 0.5.13, 48 kHz
Scope: prove that SonicFX processing is real, measured, and supervised — not
simulated.

## What was proven

| Check | Result |
|---|---|
| Child daemon publishes virtual sink + playback nodes into the main registry | **Pass** — 0 errors, `sonicfx_probe_sink` (Audio/Sink) + `sonicfx_probe_out` |
| Real audio flows through the filter graph | **Pass** — deterministic noise played into the sink, post-DSP output captured |
| Configured EQ change is measurable | **Pass** — +12 dB / 1 kHz band ⇒ **+11.78 dB** measured at 1 kHz |
| Untouched reference band stays flat | **Pass** — 3–5 kHz changed by +1.06 dB (noise variance), insertion **+10.72 dB** |
| Supervised restart gap | **Pass** — **144.6 ms** end-to-end (gate: ≤ 400 ms) |
| Clean shutdown | **Pass** — no processes or nodes left behind |

## Measurement method (fully silent)

```
pw-play ──▶ sonicfx virtual sink ──▶ filter.graph DSP ──▶ null sink ──▶ monitor
                                                                 │
                                                     pw-record ◀──┘
```

* A private `support.null-audio-sink` is hosted by a live `pw-cli` process, so
  the chain's playback output goes to a silent sink, never to speakers.
* The chain is started with `autoconnect = false`; the playback ports
  (`output_FL/FR`) are wired explicitly with `pw-link` to the null sink's
  `playback_FL/FR` — the same explicit wiring the production engine will use.
* One seeded noise file (−20 dBFS RMS, 48 kHz stereo) is played twice:
  flat chain vs. +12 dB / Q=1 peaking at 1 kHz.
* The null sink monitor is recorded at 48 kHz/16-bit and analysed with Welch
  PSDs (`scipy.signal.welch`, 16 384-point segments).
* Band ratio (boost vs flat) is compared against an untouched 3–5 kHz
  reference, removing any absolute-level differences.

## Bugs found and fixed during acceptance

1. **Zombie child detection** — `kill(pid, 0)` succeeds for zombies, so every
   stop waited out the full SIGTERM/SIGKILL timeouts (restart gap was 3.6 s).
   Fixed by checking `/proc/<pid>/stat` state `Z` and reaping the child.
2. **Monitor tap** — a virtual sink's own monitor is pre-DSP. The probe now
   captures the *post-DSP* signal at a dedicated null sink.
3. **Name-based `--target`** — PipeWire/WirePlumber did not reliably bind the
   player/recorder to private nodes by name. The rig now uses numeric node
   serials and explicit `pw-link` connections.
4. **Buffer scaling** — averaging stereo int16 channels promotes to float64;
   normalisation must use the original dtype's full-scale value.

## Reproduce

```bash
.venv/bin/python scripts/stage3_audio_probe.py
```

Outputs `result.json` plus both WAV captures under
`/tmp/opencode/sonicfx-stage3-probe/`. Exit code 0 = accepted.

## Completed in Stage 4

* `pipewire/wiring.py` — production explicit routing to the user-selected
  hardware sink, self-node exclusion (no loops), and link verification. Live
  test: FL/FR links created to a null sink, re-created after apply, measured
  255 ms including re-wiring (`docs/stage4-gui-routing.md`).
* `pipewire/routing.py` + GUI — opt-in default-sink switch with atomic state,
  crash recovery and restore on stop/exit. Verified live with Chromium audio
  moving to `sonicfx_sink` and back through `sonicfx_out` to the JBL sink.
* GUI stop path and offscreen tests; real daemon-kill/soak remain open.

## Still open in Stage 3

* Daemon-kill/bypass behaviour test at the session level: streams return to the
  hardware sink with no stuck streams.
* 10-minute soak with `pw-top`: flat CPU, zero xruns.
