#!/usr/bin/env python3
"""Stage 3 acceptance probe — prove real DSP in the live PipeWire path.

Measurement chain (fully silent, objective):

    pw-play ──▶ sonicfx virtual sink ──▶ filter.graph DSP ──▶ null sink ──▶ monitor
                                                                    │
                                                        pw-record ◀──┘

A private ``support.null-audio-sink`` (hosted by a live ``pw-cli`` process)
receives the *processed* output, so its monitor is the post-DSP signal and no
sound ever reaches the speakers. The same seeded noise file is captured twice:
once with a flat chain and once with a +12 dB / 1 kHz peaking band. The 1 kHz
band must rise by ~12 dB relative to an untouched 3–5 kHz reference band.

Exit codes: 0 accepted, 1 measurement failed, 2 engine/capture error.

Usage::

    .venv/bin/python scripts/stage3_audio_probe.py
"""
from __future__ import annotations

import argparse
import json
import signal
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
from scipy.io import wavfile
from scipy.signal import welch

from sonicfx.dsp.filters import BiquadBand, FilterKind
from sonicfx.pipewire.chain import ChainSpec
from sonicfx.pipewire.daemon import ChainDaemon, DaemonError

FS = 48_000
DURATION_S = 3.0
BOOST_DB = 12.0
TEST_BAND_HZ = (900.0, 1_100.0)
REFERENCE_BAND_HZ = (3_000.0, 5_000.0)
NULL_SINK = "sonicfx_null"
CHAIN_SINK = "sonicfx_probe_sink"
CHAIN_PLAYBACK = "sonicfx_probe_out"


def node_names() -> set[str]:
    """Names of nodes currently in the session registry (best effort)."""
    return {str(props.get("node.name")) for props in _node_props() if props.get("node.name")}


def _node_props() -> list[dict[str, str]]:
    try:
        result = subprocess.run(
            ["pw-dump"], capture_output=True, text=True, timeout=5.0, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    try:
        objects = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    props_list: list[dict[str, str]] = []
    for obj in objects:
        if obj.get("type") != "PipeWire:Interface:Node":
            continue
        props = (obj.get("info") or {}).get("props") or {}
        merged = {str(key): str(value) for key, value in props.items()}
        merged.setdefault("object.serial", str(obj.get("id", "")))
        props_list.append(merged)
    return props_list


def node_serial(name: str) -> int | None:
    """Live serial (object.serial) for a node name, or ``None``."""
    for props in _node_props():
        if props.get("node.name") != name:
            continue
        try:
            return int(props.get("object.serial") or 0) or None
        except (TypeError, ValueError):
            continue
    return None


class NullSink:
    """A silent sink hosted by a live ``pw-cli`` process (lifetime-bound)."""

    def __init__(self, name: str = NULL_SINK) -> None:
        self.name = name
        self._proc: subprocess.Popen[str] | None = None

    def __enter__(self) -> NullSink:
        self._proc = subprocess.Popen(
            ["pw-cli"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        stdin = self._proc.stdin
        if stdin is None:  # pragma: no cover - defensive
            raise RuntimeError("pw-cli stdin unavailable")
        stdin.write(
            "create-node adapter { factory.name=support.null-audio-sink "
            f"node.name={self.name} media.class=Audio/Sink "
            "audio.position=[ FL FR ] }\n"
        )
        stdin.flush()
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if self.name in node_names():
                return self
            time.sleep(0.1)
        raise RuntimeError(f"null sink {self.name!r} did not appear")

    def __exit__(self, *exc_info: object) -> None:
        if self._proc is None:
            return
        try:
            if self._proc.stdin is not None:
                self._proc.stdin.close()
            self._proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            self._proc.terminate()
        finally:
            self._proc = None


def make_probe_noise(path: Path) -> None:
    """Write a deterministic stereo noise WAV at −20 dBFS RMS."""
    rng = np.random.default_rng(20261024)
    samples = int(FS * DURATION_S)
    noise = rng.standard_normal((samples, 2))
    noise *= 10.0 ** (-20.0 / 20.0) / float(np.sqrt(np.mean(noise**2)))
    pcm = np.clip(noise, -1.0, 1.0)
    wavfile.write(path, FS, (pcm * 32_767.0).astype(np.int16))


def link_lines() -> str:
    """Links mentioning our chain/null sink, for diagnostics."""
    try:
        out = subprocess.run(
            ["pw-link", "-l"], capture_output=True, text=True, timeout=5.0, check=False
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return "<pw-link unavailable>"
    return "\n".join(
        line for line in out.splitlines() if CHAIN_PLAYBACK in line or NULL_SINK in line
    )


def port_lines(flag: str) -> list[str]:
    out = subprocess.run(
        ["pw-link", flag], capture_output=True, text=True, timeout=5.0, check=False
    ).stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


def wait_for_ports(required: list[str], timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        available = set(port_lines("-o")) | set(port_lines("-i"))
        if all(port in available for port in required):
            return
        time.sleep(0.1)
    raise RuntimeError(f"ports did not appear: {required}")


def link_chain_to_null() -> None:
    """Explicitly connect the chain's playback outputs to the null sink."""
    for channel in ("FL", "FR"):
        out_port = f"{CHAIN_PLAYBACK}:output_{channel}"
        in_port = f"{NULL_SINK}:playback_{channel}"
        wait_for_ports([out_port, in_port])
        result = subprocess.run(
            ["pw-link", out_port, in_port],
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
        if result.returncode != 0 and "exist" not in result.stderr.lower():
            raise RuntimeError(
                f"could not link {out_port} -> {in_port}: {result.stderr.strip()}"
            )


def capture(record_target: int, play_target: int, source: Path, target_wav: Path) -> None:
    """Play ``source`` into the chain and record the null sink's monitor."""
    record = subprocess.Popen(
        [
            "pw-record",
            "--target",
            str(record_target),
            "--rate",
            str(FS),
            "--channels",
            "2",
            "--format",
            "s16",
            str(target_wav),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    time.sleep(0.6)
    links_snapshot = link_lines()
    try:
        play = subprocess.run(
            ["pw-play", "--target", str(play_target), str(source)],
            capture_output=True,
            text=True,
            timeout=30.0,
        )
        if play.returncode != 0:
            raise RuntimeError(f"pw-play failed: {play.stderr.strip()}")
        time.sleep(0.4)
    finally:
        record.send_signal(signal.SIGINT)
        try:
            _, record_err = record.communicate(timeout=5.0)
        except subprocess.TimeoutExpired:
            record.kill()
            _, record_err = record.communicate()
    if not target_wav.exists() or target_wav.stat().st_size < 1_000:
        raise RuntimeError(
            "pw-record produced no data: "
            + record_err.decode("utf-8", errors="replace")[:500]
            + "\nlinks:\n"
            + links_snapshot
        )


def read_mono(path: Path) -> np.ndarray:
    rate, data = wavfile.read(path)
    if rate != FS:
        raise RuntimeError(f"unexpected capture rate {rate} Hz")
    integer = np.issubdtype(data.dtype, np.integer)
    scale = float(np.iinfo(data.dtype).max) if integer else 1.0
    data = data.astype(np.float64) / scale
    if data.ndim == 2:
        data = data.mean(axis=1)
    peak = float(np.max(np.abs(data))) if data.size else 0.0
    if peak < 1e-3:
        raise RuntimeError(f"capture {path.name} is silent (peak {peak:.6f})")
    return data


def band_power_db(data: np.ndarray, band: tuple[float, float]) -> float:
    freqs, psd = welch(data, fs=FS, nperseg=16_384, noverlap=8_192)
    mask = (freqs >= band[0]) & (freqs <= band[1])
    return float(10.0 * np.log10(max(float(np.mean(psd[mask])), 1e-20)))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workdir",
        type=Path,
        default=Path("/tmp/opencode/sonicfx-stage3-probe"),
        help="directory for probe audio and state files",
    )
    args = parser.parse_args()
    workdir: Path = args.workdir
    workdir.mkdir(parents=True, exist_ok=True)

    noise_path = workdir / "probe_noise.wav"
    flat_capture = workdir / "capture_flat.wav"
    boost_capture = workdir / "capture_boost.wav"
    make_probe_noise(noise_path)

    daemon = ChainDaemon(workdir / "state", ready_timeout=6.0)
    result: dict[str, object] = {
        "sink": CHAIN_SINK,
        "boost_db": BOOST_DB,
        "restart_gap_ms": None,
        "flat_band_db": None,
        "boost_band_db": None,
        "band_delta_db": None,
        "reference_delta_db": None,
        "insertion_db": None,
        "accepted": False,
    }
    try:
        with NullSink():
            null_serial = node_serial(NULL_SINK)
            if null_serial is None:
                raise RuntimeError("null sink serial not found in the registry")
            flat = ChainSpec(
                bands=(BiquadBand(FilterKind.PEAKING, 1_000.0, 0.0, 1.0),),
                sink_name=CHAIN_SINK,
                sink_description="SonicFX Probe",
                playback_name=CHAIN_PLAYBACK,
                core_name="sonicfx-probe",
                autoconnect=False,
            )
            boost = flat.with_bands(
                BiquadBand(FilterKind.PEAKING, 1_000.0, BOOST_DB, 1.0)
            )
            try:
                status = daemon.start(flat)
                result["pid"] = status.pid
                link_chain_to_null()
                time.sleep(0.2)
                sink_serial = node_serial(CHAIN_SINK)
                if sink_serial is None:
                    raise RuntimeError("chain sink serial not found")
                capture(null_serial, sink_serial, noise_path, flat_capture)

                _status, gap_ms = daemon.restart(boost)
                result["restart_gap_ms"] = round(gap_ms, 1)
                link_chain_to_null()
                time.sleep(0.2)
                sink_serial = node_serial(CHAIN_SINK)
                if sink_serial is None:
                    raise RuntimeError("chain sink serial not found after restart")
                capture(null_serial, sink_serial, noise_path, boost_capture)
            finally:
                daemon.stop()
    except (DaemonError, RuntimeError, OSError) as exc:
        print(f"ENGINE/CAPTURE ERROR: {exc}", file=sys.stderr)
        return 2

    try:
        flat_audio = read_mono(flat_capture)
        boost_audio = read_mono(boost_capture)
    except RuntimeError as exc:
        print(f"MEASUREMENT ERROR: {exc}", file=sys.stderr)
        return 1

    flat_rms = float(np.sqrt(np.mean(flat_audio**2)))
    boost_rms = float(np.sqrt(np.mean(boost_audio**2)))
    result["flat_rms"] = round(flat_rms, 4)
    result["boost_rms"] = round(boost_rms, 4)
    if not 0.02 <= flat_rms <= 0.4:
        print(
            f"MEASUREMENT ERROR: flat capture RMS {flat_rms:.3f} is not the probe "
            "noise (~0.10); wrong monitor tap?",
            file=sys.stderr,
        )
        return 1

    flat_band = band_power_db(flat_audio, TEST_BAND_HZ)
    boost_band = band_power_db(boost_audio, TEST_BAND_HZ)
    band_delta = boost_band - flat_band
    reference_delta = band_power_db(boost_audio, REFERENCE_BAND_HZ) - band_power_db(
        flat_audio, REFERENCE_BAND_HZ
    )
    insertion = band_delta - reference_delta
    accepted = 10.5 <= insertion <= 13.5

    result.update(
        flat_band_db=round(flat_band, 2),
        boost_band_db=round(boost_band, 2),
        band_delta_db=round(band_delta, 2),
        reference_delta_db=round(reference_delta, 2),
        insertion_db=round(insertion, 2),
        accepted=accepted,
    )

    print(json.dumps(result, indent=2))
    print(f"\n1 kHz band change : {band_delta:+.2f} dB (target {BOOST_DB:+.2f} dB)")
    print(f"reference change  : {reference_delta:+.2f} dB (target ~0 dB)")
    print(f"insertion measured: {insertion:+.2f} dB")
    print(f"restart gap       : {result['restart_gap_ms']} ms")
    print("ACCEPTED" if accepted else "FAILED")
    (workdir / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return 0 if accepted else 1


if __name__ == "__main__":
    sys.exit(main())
