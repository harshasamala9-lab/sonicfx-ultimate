# SonicFX Ultimate

A free, local, open-source, production-quality **system-wide audio enhancer for
Ubuntu / Linux Mint (PipeWire)** : cinema-style spatialisation, parametric EQ,
dynamics, headphone virtualisation and more — processing *live* audio from
browsers, VLC, games and any desktop app through a real PipeWire DSP path.

* No subscriptions, accounts, ads, paid APIs or cloud: all local processing.
* Real DSP in the PipeWire real-time thread (C filters + LADSPA/LV2) — no
  Python in the audio path.
* Honest about limits (see `docs/feature-limitations.md`): it never claims
  Dolby Atmos certification/equivalence or measurement-backed HRTF.

## Status

| Stage | Scope | State |
|---|---|---|
| 1 | Environment audit, dependency scripts | ✅ done (`docs/stage1-environment-audit.md`) |
| 2 | Offline DSP foundations (design plane) | ✅ done — 71 tests pass (`docs/stage2-dsp-results.md`) |
| 3 | Real PipeWire filter-chain path | ✅ verified — real DSP measured, 144.6 ms apply gap (`docs/stage3-results.md`); hardware wiring + soak open |
| 4–7 | GUI, profiles, diagnostics, packaging | pending |

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
  app.py          # Qt bootstrap + global error funnel (Stage 4+)
  ui/             # PySide6 GUI (Stage 4)
  audio/          # EngineAPI facade, device registry, meter tap (Stage 3/4)
  pipewire/       # filter-chain config generator + daemon lifecycle (Stage 3)
  dsp/            # offline design/manipulation (coefficients, IRs, meters' math) (Stage 2)
  profiles/       # built-in enhancement presets (Stage 3) + custom profiles (Stage 6)
  database/       # SQLite persistence (Stage 6)
  diagnostics/    # environment audit, logs, recovery (Stage 1/6)
docs/             # architecture, limitations, latency, LICENSES
tests/            # pytest suites
scripts/          # install / setup / check-environment / packaging
```

## License

MIT for SonicFX itself. Runtime dependencies each carry their own licenses —
see `docs/licenses.md`.
