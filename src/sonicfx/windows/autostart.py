"""Windows login autostart via the per-user Startup folder.

A tiny ``.cmd`` launcher is used instead of a registry Run key: it is easy to
inspect, needs no administrator rights, and is removed cleanly when the user
turns the option off.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ENTRY_NAME = "SonicFX Ultimate.cmd"


def startup_dir(base: Path | str | None = None) -> Path:
    if base is not None:
        return Path(base)
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise OSError("APPDATA is not set; cannot locate the Windows Startup folder")
    return (
        Path(appdata)
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs"
        / "Startup"
    )


def launcher_path(base: Path | str | None = None) -> Path:
    return startup_dir(base) / ENTRY_NAME


def launcher_text() -> str:
    executable = Path(sys.executable)
    pythonw = executable.with_name("pythonw.exe")
    if pythonw.is_file():
        executable = pythonw
    return (
        "@echo off\r\n"
        "rem SonicFX Ultimate - start the Equalizer APO-backed Windows app.\r\n"
        f'start "" "{executable}" -m sonicfx.app\r\n'
    )


def enable(base: Path | str | None = None) -> Path:
    path = launcher_path(base)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".cmd.tmp")
    try:
        temporary.write_text(launcher_text(), encoding="utf-8", newline="")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def disable(base: Path | str | None = None) -> None:
    launcher_path(base).unlink(missing_ok=True)


def is_enabled(base: Path | str | None = None) -> bool:
    try:
        return launcher_path(base).is_file()
    except OSError:
        return False
