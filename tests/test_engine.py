"""Engine facade tests: explicit routing, restart avoidance and failure cleanup."""
from __future__ import annotations

import pytest

from sonicfx.audio.engine import Engine, EngineError
from sonicfx.dsp.filters import BiquadBand, FilterKind
from sonicfx.pipewire.chain import ChainSpec
from sonicfx.pipewire.daemon import DaemonError, DaemonStatus
from sonicfx.pipewire.wiring import LinkPair, WiringError


class FakeDaemon:
    def __init__(self) -> None:
        self.started: list[ChainSpec] = []
        self.restarts = 0
        self.stops = 0
        self.running = False
        self.generation = 0
        self.start_error: Exception | None = None

    def start(self, spec: ChainSpec) -> DaemonStatus:
        self.started.append(spec)
        if self.start_error is not None:
            raise self.start_error
        self.running = True
        self.generation += 1
        return self.status()

    def restart(self, spec: ChainSpec) -> tuple[DaemonStatus, float]:
        self.restarts += 1
        self.started.append(spec)
        self.running = True
        self.generation += 1
        return self.status(), 150.0

    def stop(self) -> None:
        self.stops += 1
        self.running = False

    def status(self) -> DaemonStatus:
        return DaemonStatus(
            running=self.running,
            pid=4242 if self.running else None,
            sink_name="sonicfx_sink" if self.running else None,
            generation=self.generation,
            conf_path=None,
        )


@pytest.fixture
def wired(monkeypatch):
    calls: list[tuple[str, str | int]] = []

    def fake_link(playback_name: str, target: str | int):
        calls.append((playback_name, target))
        return (
            LinkPair(f"{playback_name}:output_FL", "out:playback_FL"),
            LinkPair(f"{playback_name}:output_FR", "out:playback_FR"),
        )

    monkeypatch.setattr("sonicfx.audio.engine.wiring.link_playback", fake_link)
    return calls


def _spec(gain: float = 3.0) -> ChainSpec:
    return ChainSpec(bands=(BiquadBand(FilterKind.PEAKING, 1_000.0, gain, 1.0),))


def test_start_routes_explicitly_and_disables_autoconnect(wired) -> None:
    daemon = FakeDaemon()
    engine = Engine(daemon)

    status = engine.start(_spec(), output="alsa_output.jbl")

    assert wired == [("sonicfx_out", "alsa_output.jbl")]
    assert daemon.started[0].autoconnect is False
    assert status.running is True
    assert status.output == "alsa_output.jbl"


def test_start_without_output_keeps_autoconnect(wired) -> None:
    daemon = FakeDaemon()
    status = Engine(daemon).start(_spec())

    assert wired == []
    assert daemon.started[0].autoconnect is True
    assert status.output is None


def test_wiring_failure_stops_the_chain(wired, monkeypatch) -> None:
    daemon = FakeDaemon()

    def fail(*_args):
        raise WiringError("output gone")

    monkeypatch.setattr("sonicfx.audio.engine.wiring.link_playback", fail)

    with pytest.raises(EngineError, match="could not start"):
        Engine(daemon).start(_spec(), output="alsa_output.gone")
    assert daemon.stops == 1
    assert daemon.running is False


def test_apply_unchanged_graph_does_not_restart(wired) -> None:
    daemon = FakeDaemon()
    engine = Engine(daemon)
    engine.start(_spec(), output="alsa_output.jbl")

    status = engine.apply(_spec(), output="alsa_output.jbl")

    assert daemon.restarts == 0
    assert status.running is True


def test_apply_changed_output_restarts_and_rewires(wired) -> None:
    daemon = FakeDaemon()
    engine = Engine(daemon)
    engine.start(_spec(), output="alsa_output.jbl")

    status = engine.apply(_spec(), output="alsa_output.usb")

    assert daemon.restarts == 1
    assert wired[-1] == ("sonicfx_out", "alsa_output.usb")
    assert status.output == "alsa_output.usb"
    assert status.gap_ms is not None and status.gap_ms >= 0


def test_apply_changed_band_restarts(wired) -> None:
    daemon = FakeDaemon()
    engine = Engine(daemon)
    engine.start(_spec(3.0), output="alsa_output.jbl")

    engine.apply(_spec(6.0), output="alsa_output.jbl")

    assert daemon.restarts == 1


def test_bypass_and_reenable_keep_the_output(wired) -> None:
    daemon = FakeDaemon()
    engine = Engine(daemon)
    engine.start(_spec(), output="alsa_output.jbl")

    off = engine.set_bypass(True)
    on = engine.set_bypass(False)

    assert off.running is False
    assert on.running is True
    assert wired[-1] == ("sonicfx_out", "alsa_output.jbl")


def test_status_reports_daemon_failure_without_raising() -> None:
    daemon = FakeDaemon()
    engine = Engine(daemon)
    daemon.start_error = DaemonError("boom")

    with pytest.raises(EngineError, match="boom"):
        engine.start(_spec(), output="alsa_output.jbl")

    status = engine.status()
    assert status.running is False
    assert status.last_error is not None and "boom" in status.last_error
