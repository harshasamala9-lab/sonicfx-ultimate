# SonicFX Ultimate

A free, local, open-source **system-wide audio enhancer for Ubuntu / Linux Mint
(PipeWire)**. It runs real-time parametric EQ presets (cinema, music, night,
headphones, voice, gaming, bass) on live audio from browsers, VLC, games and any
desktop app, and can become your **default output** so everything is processed.

* No subscriptions, accounts, ads, paid APIs or cloud: all local processing.
* Real DSP in the PipeWire real-time thread (C biquad filters) — no Python in
  the audio path.
* Explicit routing to the hardware sink you choose; the previous system default
  is saved and restored when you stop.
* Honest about limits (see `docs/feature-limitations.md`): it never claims
  Dolby/Dolby Atmos compatibility and does not fake unavailable features.

![SonicFX Ultimate window](docs/ui-preview.png)

## Status

| Stage | Scope | State |
|---|---|---|
| 1 | Environment audit, dependency scripts | ✅ done (`docs/stage1-environment-audit.md`) |
| 2 | Offline DSP foundations (design plane) | ✅ done — 71 tests (`docs/stage2-dsp-results.md`) |
| 3 | Real PipeWire filter-chain path | ✅ verified — real DSP measured, 144.6 ms restart (`docs/stage3-results.md`) |
| 4 | PySide6 GUI + explicit routing + default sink | ✅ working live (`docs/stage4-gui-routing.md`); live meter, reconciler and soak still open |
| 5–7 | Spatial/dynamics depth, profiles/diagnostics, packaging | pending |

## Run it

```bash
.venv/bin/sonicfx
```

On first launch the window starts the engine, selects the current default
hardware output and — because this profile opted in — makes **SonicFX the
default output**. Apps keep playing normally; their audio is routed through
SonicFX and then to the chosen device. The **Songs / Music** preset is the first
choice, with all other presets one click away.

* Toggle **Use SonicFX for all system audio** at any time.
* **Stop engine** (or closing the window) restores the previous default device
  and stops the chain. If the previous device has disconnected, the selected
  output is used instead.
* **Launch SonicFX at login** is enabled on first run so default routing
  survives a reboot; untick it to opt out.
* DSP edits regenerate the chain and briefly restart it (debounced; ~150–260 ms).
  This is a PipeWire filter-chain limitation and is shown honestly in the UI.
* Everything is local. The app does not require an internet connection.

The default-routing preference and the previous device are stored in
`~/.local/state/sonicfx/routing.json`; window and audio preferences live in
`~/.config/SonicFX/`. See `docs/stage4-gui-routing.md` for the live verification
and safety rules.

## Quick start (developer)

```bash
# 1) System packages (once, needs sudo — safe & side-effect free beyond apt)
bash scripts/install-deps.sh

# 2) Python environment (no sudo)
bash scripts/setup-venv.sh

# 3) Verify the environment produces the audit report
.venv/bin/python -m sonicfx.diagnostics.environment
```

## Free 4K movie companion

A separate, fully free playback stack for watching 4K movies and listening to
music on the same laptop:

```bash
sudo scripts/install-media-4k.sh
```

It installs Intel iHD VA-API hardware decoding, mpv + Celluloid, GStreamer
VA-API plugins and Firefox VA-API preferences. Details, verification commands
and troubleshooting: `docs/media-4k-setup.md`. It does not change your default
audio devices or SonicFX routing.

## Repository layout

```
src/sonicfx/
  app.py          # Qt bootstrap + signal handling + error funnel
  autostart.py    # XDG login autostart entry
  ui/             # PySide6 window, theme, widgets, threaded controller
  audio/          # EngineAPI facade + explicit output routing
  pipewire/       # chain generator, supervised daemon, wiring, default-sink routing
  dsp/            # offline design math (coefficients, response curves, IRs) (Stage 2)
  profiles/       # built-in enhancement presets + custom profiles (Stage 6)
  database/       # SQLite persistence (Stage 6)
  diagnostics/    # environment audit, logs, recovery (Stage 1/6)
docs/             # architecture, stage results, limitations, licenses
tests/            # pytest suites (154 tests; live PipeWire suite opt-in)
scripts/          # install / setup / probe / preview / packaging
```

## License

MIT for SonicFX itself. Runtime dependencies each carry their own licenses —
see `docs/licenses.md`.
