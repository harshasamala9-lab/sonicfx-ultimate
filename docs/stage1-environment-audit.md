# Stage 1 — Environment Audit (Ubuntu 26.04, PipeWire 1.6.2)

Audit data collected live on this machine on **2026-10-24** via
`src/sonicfx/diagnostics/environment.py` (stdlib-only, re-runnable, `--json` output).

## System

| Item | Value |
|---|---|
| OS | Ubuntu 26.04.1 LTS "Resolute Raccoon" (ID=ubuntu) — X11/Wayland Wayland+GNOME |
| Kernel | 7.0.0-38-generic |
| CPU | Intel Core i5-7200U @ 2.50 GHz — 2C/4T, AVX2 capable |
| RAM | 7.1 GiB (≈2.9 GiB available) |
| Disk | 408 GiB free |
| Session | Wayland / GNOME (ubuntu) |

## Audio stack (all user services)

| Service | State | Version |
|---|---|---|
| pipewire.service | active (running) | libpipewire 1.6.2 |
| pipewire-pulse.service | active (running) | libpipewire 1.6.2 |
| wireplumber.service | active (running) | libwireplumber 0.5.13 |

Session clock: `clock.rate=48000`, `clock.quantum=1024`, `clock.min-quantum=32`,
`clock.max-quantum=2048`, `clock.force-rate=0`.

Devices found (live graph, at audit time):

* Sinks: `alsa_output.pci-0000_00_1f.3.analog-stereo` (Built-in Audio Analog Stereo, 2ch FL/FR),
  `bluez_output.30_C0_1B_D2_9B_F2.1` (Bluetooth "JBL Tuner FM", **currently default**)
* Sources: `alsa_input.pci-0000_00_1f.3.analog-stereo` (Built-in Audio Analog Stereo)
* Video sources: Integrated Webcam HD (V4L2) — irrelevant to audio path
* Active client stream observed: Brave browser routing to the Bluetooth sink

**Important routing note:** the default output is currently the user's Bluetooth
headset. SonicFX must never hijack this default silently — every default-sink
change must be an explicit user action and must be revertible.

## Native DSP hosting capacity (verified on this system)

* `libpipewire-module-filter-chain.so` present — this is PipeWire's in-process
  filter-chain module (runs inside the PipeWire real-time thread, **not** Python).
* Stock filter-chain graph files at `/usr/share/pipewire/filter-chain/` (live-verified):
  `sink-eq6.conf` (parametric EQ using builtin `bq_lowshelf/bq_peaking/bq_highshelf`
  with `Freq/Q/Gain` controls), `sink-matrix-spatialiser.conf` (LADSPA
  `matrix_spatialiser_1422` stereo widening), `sink-virtual-surround-7.1-hesuvi.conf`,
  `sink-virtual-surround-5.1-kemar.conf` (convolver-based HRTF-style surround),
  `sink-make-LFE.conf`, `sink-upmix-5.1-filter.conf`, `sink-dolby-surround.conf`,
  `sink-dolby-pro-logic-ii.conf`, `sink-mix-FL-FR.conf`, `sink-eq6.conf`,
  `source-rnnoise.conf`, `demonic.conf`.
* `pw-loopback`, `pw-link`, `pw-cli`, `pw-dump`, `pw-metadata`, `pw-top`, `wpctl`
  all installed.
* The **stock widening spatialiser needs `swh-plugins`**, which is **not installed yet**
  → Stage 1 action item (see below).

## Missing on this system (to be installed)

1. `swh-plugins`, `calf-plugins`, `zam-plugins`, `bs2b-ladspa`, `amb-plugins`
   — the compressor / limiter / reverb / widening / crossfeed plugin sets used
   in the real-time filter-chain graphs.
2. `pulseaudio-utils` (provides `pactl`, helpful for diagnostics).
3. Python venv deps: `scipy`, `PySide6` (numpy 2.3.5 and pytest already present;
   a project venv is being populated by `scripts/setup-venv.sh`).
4. RNNoise mic-noise-suppression LADSPA plugin: **no Ubuntu package exists**
   for `librnnoise_ladspa` on this release. It is available as opt-in source
   build (gcc present); see `docs/feature-limitations.md`.

## Toolchain

* gcc 15, g++, cmake, make, pkg-config — all present (only needed for the
  optional RNNoise/LADSPA source build and any future native extension).
* Python 3.14.4 (system) — pip 25.1.1, venv, python3-dev.
* PyPI reachable → the venv install is fully reproducible offline afterwards.

## Stage-1 acceptance status

* ✅ OS, PipeWire services, versions and devices verified.
* ✅ Audit tool runs and produces human + JSON reports (exit code guarded).
* ✅ Readiness for real-time hosting: **READY** (critical checks OK).
* ⚠ Plugin packages (`swh-plugins` etc.) pending — `scripts/install-deps.sh`
  is provided and idempotent; the user runs it with sudo. Until it runs,
  widening (matrix spatialiser), compressor/limiter/reverb (Calf/Zam/SWH),
  and bs2b crossfeed remain "not installed" — explicitly surfaced by the
  audit and by the app (never simulated).
