"""Unit tests for the login autostart desktop entry (no desktop required)."""
from __future__ import annotations

import sys

from sonicfx import autostart


def test_enable_creates_executable_desktop_entry(tmp_path) -> None:
    path = autostart.enable(tmp_path)

    assert path == tmp_path / "autostart" / "sonicfx-ultimate.desktop"
    text = path.read_text(encoding="utf-8")
    assert "[Desktop Entry]" in text
    assert "Name=SonicFX Ultimate" in text
    assert "-m sonicfx.app" in text
    assert sys.executable in text
    assert path.stat().st_mode & 0o777 == 0o644
    assert not list(path.parent.glob("*.tmp"))


def test_is_enabled_and_disable_are_idempotent(tmp_path) -> None:
    assert autostart.is_enabled(tmp_path) is False
    autostart.disable(tmp_path)  # missing is fine

    autostart.enable(tmp_path)
    assert autostart.is_enabled(tmp_path) is True
    autostart.disable(tmp_path)
    assert autostart.is_enabled(tmp_path) is False
    autostart.disable(tmp_path)


def test_enable_replaces_existing_entry(tmp_path) -> None:
    path = autostart.enable(tmp_path)
    path.write_text("stale", encoding="utf-8")
    autostart.enable(tmp_path)
    assert "Exec=" in path.read_text(encoding="utf-8")


def test_respects_xdg_config_home(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    path = autostart.enable()
    assert path == tmp_path / "xdg" / "autostart" / "sonicfx-ultimate.desktop"
    assert autostart.is_enabled() is True
    autostart.disable()
