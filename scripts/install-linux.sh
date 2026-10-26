#!/usr/bin/env bash
# SonicFX Ultimate - Linux one-command installer (Ubuntu / Linux Mint / Debian)
#
# Target: 8 GB RAM, 4 GB free disk, a PipeWire desktop session.
# Installs only the runtime packages the app needs; no audio plugins, no
# default-device changes, no cloud services.
#
# From a checkout:
#   bash scripts/install-linux.sh
#
# From a remote machine over SSH (clones the public repository):
#   ssh user@host 'bash -s' < scripts/install-linux.sh
#
# Options:
#   --dev             also install pytest/ruff development tools
#   --no-apt          skip apt (dependencies must already be present)
#   --no-desktop      do not install the .desktop launcher
#   --force           bypass the 8 GB RAM / 4 GB free-disk checks
#   --dir PATH        install/clone the app into PATH
set -euo pipefail

REPO_URL="${SONICFX_REPO_URL:-https://github.com/harshasamala9-lab/sonicfx-ultimate.git}"
APP_DIR="${SONICFX_APP_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/sonicfx/app}"
WITH_DEV=0
WITH_APT=1
WITH_DESKTOP=1
FORCE=0
MIN_RAM_GB=8
MIN_FREE_GB=4

while [ "$#" -gt 0 ]; do
    case "$1" in
        --dev) WITH_DEV=1 ;;
        --no-apt) WITH_APT=0 ;;
        --no-desktop) WITH_DESKTOP=0 ;;
        --force) FORCE=1 ;;
        --dir)
            shift
            [ "$#" -gt 0 ] || { echo "--dir needs a path" >&2; exit 2; }
            APP_DIR="$1"
            ;;
        -h|--help)
            sed -n '2,26p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            exit 2
            ;;
    esac
    shift
done

say() { printf '\n== %s ==\n' "$*"; }
ok() { printf '   OK  %s\n' "$*"; }
warn() { printf '   WARN %s\n' "$*" >&2; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[ "$(uname -s)" = "Linux" ] || die "This installer is for Linux only. On Windows use scripts/install-windows.ps1."

if [ "$FORCE" -eq 0 ]; then
    say "Checking the 8 GB RAM / 4 GB free-disk target"
    ram_kb="$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)"
    ram_gb="$((ram_kb / 1024 / 1024))"
    free_kb="$(df -Pk "$HOME" | awk 'NR==2 {print $4}')"
    free_gb="$((free_kb / 1024 / 1024))"
    printf '   RAM: %s GB   Free disk: %s GB   App: %s\n' "$ram_gb" "$free_gb" "$APP_DIR"
    [ "$ram_gb" -ge "$MIN_RAM_GB" ] || die "Need at least ${MIN_RAM_GB} GB RAM (found ${ram_gb} GB). Re-run with --force to try anyway."
    [ "$free_gb" -ge "$MIN_FREE_GB" ] || die "Need at least ${MIN_FREE_GB} GB free disk (found ${free_gb} GB). Re-run with --force to try anyway."
fi

if [ "$(id -u)" -eq 0 ]; then
    SUDO=""
else
    command -v sudo >/dev/null 2>&1 || die "sudo is required to install system packages."
    SUDO="sudo"
fi

install_apt_packages() {
    [ "$WITH_APT" -eq 1 ] || { warn "Skipping apt (--no-apt)"; return 0; }
    say "Installing runtime packages (apt)"
    $SUDO env DEBIAN_FRONTEND=noninteractive apt-get update
    $SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        pipewire \
        pipewire-pulse \
        wireplumber \
        pulseaudio-utils \
        python3 \
        python3-venv \
        python3-pip \
        git \
        curl \
        libegl1 \
        libgl1 \
        libfontconfig1 \
        libxkbcommon-x11-0 \
        libxcb-cursor0 \
        libxcb-icccm4 \
        libxcb-image0 \
        libxcb-keysyms1 \
        libxcb-randr0 \
        libxcb-render-util0 \
        libxcb-shape0 \
        libxcb-xinerama0
    ok "runtime packages"
}

python_is_new_enough() {
    "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' \
        >/dev/null 2>&1
}

find_python() {
    for candidate in python3.13 python3.12 python3.11 python3; do
        if command -v "$candidate" >/dev/null 2>&1 && python_is_new_enough "$candidate"; then
            command -v "$candidate"
            return 0
        fi
    done
    return 1
}

install_python_312() {
    [ "$WITH_APT" -eq 1 ] || die "Python 3.11+ is required. Install it, then re-run."
    warn "No Python 3.11+ found; installing Python 3.12 from deadsnakes"
    $SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        software-properties-common
    $SUDO add-apt-repository -y ppa:deadsnakes/ppa
    $SUDO env DEBIAN_FRONTEND=noninteractive apt-get update
    $SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        python3.12 python3.12-venv python3.12-dev
    command -v python3.12
}

install_apt_packages

PYTHON="$(find_python || true)"
if [ -z "$PYTHON" ]; then
    PYTHON="$(install_python_312 || true)"
fi
[ -n "$PYTHON" ] || die "Python 3.11+ could not be installed."
ok "Python: $PYTHON ($($PYTHON --version 2>&1))"

# Use the checkout when this script is run from one. If piped over SSH, clone.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" 2>/dev/null && pwd || true)"
CHECKOUT=""
if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/../pyproject.toml" ] \
        && [ -d "$SCRIPT_DIR/../src/sonicfx" ]; then
    CHECKOUT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
fi

if [ -n "$CHECKOUT" ]; then
    APP_DIR="$CHECKOUT"
    ok "Using checkout at $APP_DIR"
else
    say "Fetching SonicFX Ultimate"
    command -v git >/dev/null 2>&1 || die "git is required to fetch the app."
    if [ -d "$APP_DIR/.git" ]; then
        git -C "$APP_DIR" fetch --depth 1 origin main
        git -C "$APP_DIR" reset --hard origin/main
    else
        mkdir -p "$(dirname "$APP_DIR")"
        git clone --depth 1 "$REPO_URL" "$APP_DIR"
    fi
    ok "Source at $APP_DIR"
fi

[ -f "$APP_DIR/pyproject.toml" ] || die "pyproject.toml not found in $APP_DIR"

say "Creating the isolated Python environment"
if [ ! -x "$APP_DIR/.venv/bin/python" ]; then
    "$PYTHON" -m venv "$APP_DIR/.venv"
fi
"$APP_DIR/.venv/bin/python" -m pip install --no-cache-dir --upgrade pip
if [ "$WITH_DEV" -eq 1 ]; then
    "$APP_DIR/.venv/bin/python" -m pip install --no-cache-dir -e "$APP_DIR[dev]"
else
    "$APP_DIR/.venv/bin/python" -m pip install --no-cache-dir "$APP_DIR"
fi
ok "SonicFX package installed"

BIN_DIR="$HOME/.local/bin"
mkdir -p "$BIN_DIR"
cat > "$BIN_DIR/sonicfx" <<EOF
#!/bin/sh
exec "$APP_DIR/.venv/bin/python" -m sonicfx.app "\$@"
EOF
chmod +x "$BIN_DIR/sonicfx"
ok "Launcher: $BIN_DIR/sonicfx"

if [ "$WITH_DESKTOP" -eq 1 ]; then
    APPS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
    mkdir -p "$APPS_DIR"
    cat > "$APPS_DIR/sonicfx-ultimate.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=SonicFX Ultimate
Comment=Free, local system-wide audio enhancement (PipeWire)
Exec=$APP_DIR/.venv/bin/python -m sonicfx.app
Icon=audio-x-generic
Terminal=false
Categories=AudioVideo;Audio;Utility;
StartupNotify=true
EOF
    chmod +x "$APPS_DIR/sonicfx-ultimate.desktop"
    command -v update-desktop-database >/dev/null 2>&1 && \
        update-desktop-database "$APPS_DIR" >/dev/null 2>&1 || true
    ok "Desktop entry"
fi

# Keep the low-spec footprint predictable: caches are not needed at runtime.
"$APP_DIR/.venv/bin/python" -m pip cache purge >/dev/null 2>&1 || true

say "Done"
printf '   Run: %s/sonicfx\n' "$BIN_DIR"
printf '   If %s is not on PATH, add it to your shell profile.\n' "$BIN_DIR"
if ! pgrep -x pipewire >/dev/null 2>&1; then
    warn "PipeWire is not running in this session. Log out and back in, then launch SonicFX."
fi
printf '   On first launch the app starts its PipeWire chain. Stop the engine to bypass it.\n'
printf '   Cinema Immersion is a stereo tonal profile, not Dolby/Atmos object audio.\n'
