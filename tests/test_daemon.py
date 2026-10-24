"""Unit tests for the supervised child-daemon lifecycle (no live PipeWire)."""
from __future__ import annotations

import json
import os

import pytest

from sonicfx.pipewire.chain import ChainSpec
from sonicfx.pipewire.daemon import ChainDaemon, DaemonError


def test_status_without_state(tmp_path) -> None:
    daemon = ChainDaemon(tmp_path)
    status = daemon.status()
    assert status.running is False
    assert status.pid is None
    assert status.generation == 0


def test_write_conf_is_atomic_and_private(tmp_path) -> None:
    daemon = ChainDaemon(tmp_path)
    conf = daemon._write_conf(ChainSpec(), generation=3)
    assert conf.name == "chain-0003.conf"
    assert conf.stat().st_mode & 0o777 == 0o600
    assert 'sonicfx.generation = "3"' in conf.read_text(encoding="utf-8")
    assert not list(daemon.chain_dir.glob("*.tmp"))


def test_write_conf_prunes_old_generations(tmp_path) -> None:
    daemon = ChainDaemon(tmp_path)
    for generation in range(1, 6):
        daemon._write_conf(ChainSpec(), generation=generation)
    names = sorted(path.name for path in daemon.chain_dir.glob("chain-*.conf"))
    assert names == ["chain-0003.conf", "chain-0004.conf", "chain-0005.conf"]


def test_stale_pid_file_is_not_reported_running(tmp_path) -> None:
    daemon = ChainDaemon(tmp_path)
    daemon.state_dir.mkdir(parents=True, exist_ok=True)
    daemon._pid_path.write_text(
        json.dumps(
            {
                "pid": 999_999,
                "conf": str(tmp_path / "chain-0001.conf"),
                "sink": "sonicfx_sink",
                "generation": 1,
            }
        ),
        encoding="utf-8",
    )
    status = daemon.status()
    assert status.running is False
    assert status.pid is None


def test_pid_match_requires_conf_in_cmdline(tmp_path) -> None:
    daemon = ChainDaemon(tmp_path)
    conf = tmp_path / "chain-0001.conf"
    conf.write_text("#", encoding="utf-8")
    # This test process's command line does not reference the conf path.
    assert daemon._pid_matches_conf(os.getpid(), conf) is False
    assert daemon._pid_matches_conf(999_999, conf) is False


def test_stop_without_start_is_a_noop(tmp_path) -> None:
    ChainDaemon(tmp_path).stop()
    assert not (tmp_path / "chain.pid").exists()


def test_missing_binary_raises_daemon_error(tmp_path) -> None:
    daemon = ChainDaemon(tmp_path, binary="/nonexistent/pipewire")
    with pytest.raises(DaemonError, match="not found"):
        daemon.start(ChainSpec())


def test_latest_generation_counts_existing_confs(tmp_path) -> None:
    daemon = ChainDaemon(tmp_path)
    daemon._write_conf(ChainSpec(), generation=4)
    assert daemon._latest_generation() == 4
