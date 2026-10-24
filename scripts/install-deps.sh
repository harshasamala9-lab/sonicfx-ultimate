#!/usr/bin/env bash
# SonicFX Ultimate — Stage 1 system-dependency installer (needs sudo).
# Installs ONLY packages from the Ubuntu/Linux Mint repositories.
# Nothing here touches that repository's core functionality.
# Safe to run repeatedly (idempotent via apt).
# Docs: docs/stage1-environment-audit.md and docs/implementation-plan.md
set -euo pipefail

echo "== Updating package lists (requires privilege) =="
sudo apt-get update

echo "== Installing runtime DSP plugin packages =="
# swh-plugins : LADSPA - Matrix Spatialiser (stereo widening), lookahead limiter,
#               Dyson compressor, FADSR… (GPL-2, Steve Harris)
# calf-plugins: LV2/LADSPA - Compressor, Limiter, Reverb, Multiband, Gate, Deesser
#               (LGPL-2.1+, Calf Studio Gear)
# zam-plugins : LV2 - ZaMaximX2 look-ahead limiter, ZamCompX2 dial-up compressor,
#               ZamGate… (GPL-3+, by Damien Zammit)
# bs2b-ladspa : LADSPA - Bauer stereophonic-to-binaural crossfeed (GPL-2+/MIT)
# amb-plugins : LADSPA - ambisonics encoder/decoder helpers (GPL-2+)
# swh-plugins sets the widening matrix spatialiser used by the Cinema module.
sudo apt-get install -y \
    swh-plugins \
    calf-plugins \
    zam-plugins \
    bs2b-ladspa \
    amb-plugins \
    pulseaudio-utils

echo "== Optional: build tooling for the (opt-in) RNNoise mic-noise plugin =="
# Building the optional RNNoise LADSPA plugin is opt-in; see docs/feature-limitations.md.
sudo apt-get install -y build-essential git || echo "(optional build tools skipped)"

echo "== Done. Verifying… =="
python3 src/sonicfx/diagnostics/environment.py 2>/dev/null || {
    VENV="${SONICFX_VENV:-$PWD/.venv/bin/python}"
    if [ -x "$VENV" ]; then "$VENV" src/sonicfx/diagnostics/environment.py; else
        echo "(sonicfx not installed yet — rerun after 'pip install -e .[dev]')"
    fi
}
