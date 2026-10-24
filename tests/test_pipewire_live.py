"""Live PipeWire integration test (opt-in).

Run explicitly on a real session::

    SONICFX_LIVE=1 .venv/bin/python -m pytest tests/test_pipewire_live.py -v

Proves the Stage 3 path end-to-end at the session level: node publication into
the main registry, generation-marked readiness, supervised restart and a clean
stop that leaves no process behind.
"""
from __future__ import annotations

import os

import pytest

from sonicfx.dsp.filters import BiquadBand, FilterKind
from sonicfx.pipewire.chain import ChainSpec
from sonicfx.pipewire.daemon import ChainDaemon

pytestmark = pytest.mark.skipif(
    os.environ.get("SONICFX_LIVE") != "1",
    reason="live PipeWire test; set SONICFX_LIVE=1 to enable",
)


def _spec(gain_db: float) -> ChainSpec:
    return ChainSpec(
        bands=(BiquadBand(FilterKind.PEAKING, 1_000.0, gain_db, 1.0),),
        sink_name="sonicfx_live_sink",
        sink_description="SonicFX Live Test",
        playback_name="sonicfx_live_out",
        core_name="sonicfx-live-test",
    )


def test_publish_restart_and_stop(tmp_path) -> None:
    daemon = ChainDaemon(tmp_path, ready_timeout=6.0)
    try:
        first = daemon.start(_spec(3.0))
        assert first.running is True
        assert first.sink_name == "sonicfx_live_sink"
        assert first.pid is not None

        second, gap_ms = daemon.restart(_spec(6.0))
        assert second.running is True
        assert second.generation == first.generation + 1
        assert gap_ms < 6_000.0  # sanity bound; the real target is reported
    finally:
        daemon.stop()
    assert daemon.status().running is False
