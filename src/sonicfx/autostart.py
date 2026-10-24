"""XDG login autostart entry for SonicFX Ultimate (opt-out in the GUI).

The entry launches the same interpreter that runs the app via
``-m sonicfx.app``, so it works from an editable virtualenv without assuming
anything about ``PATH``. Nothing here touches audio; the app itself restores
the previous default output when it exits.

Path can be overridden with ``XDG_CONFIG_HOME`` (standard) or by passing
``config_home`` in tests.
"""
from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path

APP_ID = "sonicfx-ultimate"
ENTRY_NAME = f"{APP_ID}.desktop"


def autostart_dir(config_home: Path | str | None = None) -> Path:
    if config_home is not None:
        base = Path(config_home)
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "autostart"


def entry_path(config_home: Path | str | None = None) -> Path:
    return autostart_dir(config_home) / ENTRY_NAME


def desktop_entry_text() -> str:
    """Desktop Entry launching the current environment's SonicFX app."""
    exec_line = f"{shlex.quote(sys.executable)} -m sonicfx.app"
    return f"""[Desktop Entry]
Type=Application
Version=1.0
Name=SonicFX Ultimate
GenericName=System-wide audio enhancer
Comment=Local PipeWire audio enhancement (starts the engine and restores routing on exit)
Exec={exec_line}
Terminal=false
Categories=AudioVideo;Audio;
StartupNotify=false
X-GNOME-Autostart-enabled=true
"""


def enable(config_home: Path | str | None = None) -> Path:
    """Create/replace the autostart entry atomically; returns its path."""
    path = entry_path(config_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".desktop.tmp")
    try:
        tmp.write_text(desktop_entry_text(), encoding="utf-8")
        tmp.chmod(0o644)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return path


def disable(config_home: Path | str | None = None) -> None:
    """Remove the autostart entry (missing entry is not an error)."""
    entry_path(config_home).unlink(missing_ok=True)


def is_enabled(config_home: Path | str | None = None) -> bool:
    return entry_path(config_home).is_file()
