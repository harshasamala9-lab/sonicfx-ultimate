#!/usr/bin/env bash
# SonicFX Ultimate — venv bootstrap (no sudo required, disable safe defaults).
set -euo pipefail
VENV_DIR="${SONICFX_VENV:-$PWD/.venv}"

if [ ! -x "$VENV_DIR/bin/python" ]; then
    python3 -m venv "$VENV_DIR"
    "$VENV_DIR/bin/pip" check >/dev/null 2>&1 || true
fi
"$VENV_DIR/bin/python" -m pip install --quiet --upgrade pip
# Runtime deps; dev tools (incl. pytest) come from .[dev]
"$VENV_DIR/bin/python" -m pip install -e ".[dev]"
"$VENV_DIR/bin/python" -m sonicfx.diagnostics.environment --json /tmp/opencode/sonicfx-audit.json
