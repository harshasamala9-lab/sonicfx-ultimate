"""Unit tests for opt-in default-sink routing and crash-safe restore."""
from __future__ import annotations

import json
import os

import pytest

from sonicfx.pipewire.routing import DefaultSinkRouter, RoutingError


class TestState:
    def test_state_roundtrip_is_private_and_records_previous(self, tmp_path) -> None:
        router = DefaultSinkRouter(tmp_path)
        router._write_state("sonicfx_sink", "alsa_output.jbl")

        state = router.read_state()
        assert state == {
            "sonicfx_sink": "sonicfx_sink",
            "previous_default": "alsa_output.jbl",
            "pid": os.getpid(),
        }
        assert router.previous_default() == "alsa_output.jbl"
        assert router.state_path.stat().st_mode & 0o777 == 0o600
        assert not list(tmp_path.glob("*.tmp"))

    def test_missing_or_broken_state_is_tolerated(self, tmp_path) -> None:
        router = DefaultSinkRouter(tmp_path)
        assert router.read_state() is None
        assert router.previous_default() is None
        router.state_dir.mkdir(parents=True, exist_ok=True)
        router.state_path.write_text("{not json", encoding="utf-8")
        assert router.read_state() is None


class TestGuards:
    def test_refuses_to_make_an_arbitrary_sink_default(self, tmp_path) -> None:
        router = DefaultSinkRouter(tmp_path)
        with pytest.raises(RoutingError, match="own virtual sink"):
            router.make_default("alsa_output.usb")

    def test_refuses_missing_sonicfx_sink(self, tmp_path, monkeypatch) -> None:
        router = DefaultSinkRouter(tmp_path)
        monkeypatch.setattr(
            "sonicfx.pipewire.routing._sink_global_id", lambda _name: None
        )
        with pytest.raises(RoutingError, match="not running"):
            router.make_default("sonicfx_sink")
        assert router.read_state() is None


class TestRestore:
    def test_restore_uses_recorded_device_and_clears_state(
        self, tmp_path, monkeypatch
    ) -> None:
        router = DefaultSinkRouter(tmp_path)
        router._write_state("sonicfx_sink", "alsa_output.jbl")
        restored: list[str] = []
        monkeypatch.setattr(
            router, "_restore_named", lambda name: restored.append(name) or True
        )

        assert router.restore() is True
        assert restored == ["alsa_output.jbl"]
        assert router.read_state() is None

    def test_restore_without_state_does_nothing(self, tmp_path) -> None:
        assert DefaultSinkRouter(tmp_path).restore() is False

    def test_restore_without_previous_just_clears_state(self, tmp_path) -> None:
        router = DefaultSinkRouter(tmp_path)
        router._write_state("sonicfx_sink", None)
        assert router.restore() is False
        assert router.read_state() is None

    def test_restore_to_hardware_sink(self, tmp_path, monkeypatch) -> None:
        router = DefaultSinkRouter(tmp_path)
        restored: list[str] = []
        monkeypatch.setattr(
            router, "_restore_named", lambda name: restored.append(name) or True
        )
        assert router.restore_to("alsa_output.usb") is True
        assert restored == ["alsa_output.usb"]

    def test_restore_to_refuses_sonicfx(self, tmp_path) -> None:
        with pytest.raises(RoutingError, match="refusing"):
            DefaultSinkRouter(tmp_path).restore_to("sonicfx_sink")


class TestCrashRecovery:
    def test_recovers_when_recorded_owner_is_dead(self, tmp_path, monkeypatch) -> None:
        router = DefaultSinkRouter(tmp_path)
        router.state_dir.mkdir(parents=True, exist_ok=True)
        router.state_path.write_text(
            json.dumps(
                {
                    "sonicfx_sink": "sonicfx_sink",
                    "previous_default": "alsa_output.jbl",
                    "pid": 999_999_999,
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(router, "restore", lambda: True)
        assert router.recover_stale(pid=os.getpid()) is True

    def test_skips_when_another_live_instance_owns_the_switch(
        self, tmp_path, monkeypatch
    ) -> None:
        router = DefaultSinkRouter(tmp_path)
        router._write_state("sonicfx_sink", "alsa_output.jbl")
        monkeypatch.setattr(router, "restore", lambda: True)
        assert router.recover_stale(pid=os.getpid() + 1) is False

    def test_no_state_is_not_an_error(self, tmp_path) -> None:
        assert DefaultSinkRouter(tmp_path).recover_stale(pid=os.getpid()) is False
