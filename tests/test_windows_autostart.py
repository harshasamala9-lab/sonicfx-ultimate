"""Windows Startup-folder helper tests (pure file operations)."""
from __future__ import annotations

from pathlib import Path

from sonicfx.windows import autostart


def test_enable_is_idempotent_and_disable_removes(tmp_path: Path) -> None:
    base = tmp_path / "Startup"

    path = autostart.enable(base)
    first = path.read_bytes()

    assert path.is_file()
    assert path.name == "SonicFX Ultimate.cmd"
    assert autostart.is_enabled(base) is True
    assert b"-m sonicfx.app" in first
    assert b"\r\n" in first

    autostart.enable(base)
    assert path.read_bytes() == first

    autostart.disable(base)
    assert autostart.is_enabled(base) is False
    assert not path.exists()


def test_missing_appdata_is_reported(monkeypatch) -> None:
    monkeypatch.delenv("APPDATA", raising=False)
    assert autostart.is_enabled() is False
    try:
        autostart.enable()
    except OSError as exc:
        assert "APPDATA" in str(exc)
    else:  # pragma: no cover - guard against a silent write
        raise AssertionError("enable() should fail without APPDATA")
