"""Explicit routing of the SonicFX chain playback to a chosen output device.

The chain's playback side is a passive ``Stream/Output/Audio`` node. SonicFX
never lets the session manager guess where it goes: the user picks an output in
the GUI and this module links ``<playback>:output_FL/FR`` to that device's
input ports with ``pw-link``, removing any previous links from our playback
node first. Routing into SonicFX itself is refused (loop prevention).

All of this is control plane: no Python runs in the audio graph.
"""
from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass

__all__ = [
    "LinkPair",
    "SinkInfo",
    "WiringError",
    "default_sink_name",
    "link_playback",
    "list_sinks",
    "select_link_pairs",
    "sink_infos_from_dump",
]


class WiringError(RuntimeError):
    """The chain could not be routed to the requested output."""


@dataclass(frozen=True)
class SinkInfo:
    """One selectable hardware/virtual output with Auto-mode metadata."""

    name: str
    serial: int
    description: str
    is_default: bool = False
    icon_name: str = ""
    form_factor: str = ""
    api: str = ""
    channels: int = 2


@dataclass(frozen=True)
class LinkPair:
    """One created link, source port → target port."""

    source: str
    target: str


def _run(args: list[str], *, timeout: float = 5.0) -> str:
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout, check=False
        )
    except FileNotFoundError as exc:
        raise WiringError(f"{args[0]} is not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise WiringError(f"{args[0]} timed out") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise WiringError(f"{args[0]} failed: {detail}")
    return result.stdout


# -- pure helpers (unit-tested without PipeWire) -----------------------------
def parse_link_list(text: str) -> list[tuple[str, str]]:
    """Parse ``pw-link -l`` output into ``(source_port, target_port)`` pairs."""
    pairs: list[tuple[str, str]] = []
    current: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("|->"):
            if current:
                pairs.append((current, line[3:].strip()))
        elif line.startswith("|<-"):
            if current:
                pairs.append((line[3:].strip(), current))
        else:
            current = line
    return pairs


def _int_prop(value: object, default: int) -> int:
    try:
        number = int(float(str(value)))
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def sink_infos_from_dump(
    objects: list[dict], default_name: str | None = None
) -> list[SinkInfo]:
    """Extract selectable sinks from parsed ``pw-dump`` objects.

    SonicFX's own nodes (``sonicfx*``) are excluded so the chain can never be
    routed into itself. The default sink is listed first.

    PipeWire puts ``device.form-factor`` and ``device.icon-name`` on the
    separate ``Audio/Device`` object, not on the sink node.  Auto mode needs
    that metadata, so the sink's ``device.id`` is resolved against the device
    objects before falling back to node properties.
    """
    devices: dict[int, dict] = {}
    for obj in objects:
        if obj.get("type") != "PipeWire:Interface:Device":
            continue
        raw_id = obj.get("id")
        try:
            device_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        devices[device_id] = (obj.get("info") or {}).get("props") or {}

    def prop(*sources: dict, keys: tuple[str, ...]) -> object:
        for source in sources:
            for key in keys:
                value = source.get(key)
                if value not in (None, ""):
                    return value
        return ""

    sinks: list[SinkInfo] = []
    for obj in objects:
        if obj.get("type") != "PipeWire:Interface:Node":
            continue
        props = (obj.get("info") or {}).get("props") or {}
        if props.get("media.class") != "Audio/Sink":
            continue
        name = str(props.get("node.name") or "")
        if not name or name.startswith("sonicfx"):
            continue
        device_props: dict = {}
        raw_device = props.get("device.id")
        if raw_device is not None:
            try:
                device_props = devices.get(int(float(str(raw_device))), {})
            except (TypeError, ValueError):
                device_props = {}
        description = str(
            prop(
                props,
                device_props,
                keys=(
                    "node.description",
                    "node.nick",
                    "device.description",
                    "device.nick",
                ),
            )
            or name
        )
        try:
            serial = int(props.get("object.serial") or obj.get("id") or 0)
        except (TypeError, ValueError):
            serial = 0
        sinks.append(
            SinkInfo(
                name=name,
                serial=serial,
                description=description,
                is_default=name == default_name,
                icon_name=str(
                    prop(
                        props,
                        device_props,
                        keys=("device.icon_name", "device.icon-name"),
                    )
                ),
                form_factor=str(
                    prop(
                        props,
                        device_props,
                        keys=("device.form_factor", "device.form-factor"),
                    )
                ),
                api=str(prop(props, device_props, keys=("device.api",))),
                channels=_int_prop(
                    prop(props, keys=("audio.channels", "device.channels")), 2
                ),
            )
        )
    sinks.sort(key=lambda sink: (not sink.is_default, sink.description.casefold()))
    return sinks


def _port_channel(port: str) -> str | None:
    """Return ``FL``/``FR`` for a port name, or ``None`` when it has no side."""
    leaf = port.rsplit(":", 1)[-1].upper()
    for token in leaf.replace("-", "_").split("_"):
        if token in ("FL", "FR"):
            return token
    return None


def select_link_pairs(
    playback_name: str,
    target_name: str,
    outputs: set[str],
    inputs: set[str],
) -> tuple[tuple[str, str], tuple[str, str]]:
    """Choose the FL/FR ``(source, target)`` port pairs for the link."""
    pairs: list[tuple[str, str]] = []
    for channel in ("FL", "FR"):
        source = f"{playback_name}:output_{channel}"
        if source not in outputs:
            raise WiringError(f"playback port {source!r} does not exist")
        candidates = sorted(
            (port for port in inputs if _port_channel(port) == channel),
            # A sink's real inputs are playback_FL/FR; prefer them over any
            # similarly-named monitor port so capture monitoring is never wired.
            key=lambda port: (
                0 if port.rsplit(":", 1)[-1].lower().startswith("playback") else 1,
                port,
            ),
        )
        if not candidates:
            raise WiringError(
                f"output {target_name!r} has no {channel} input port"
            )
        pairs.append((source, candidates[0]))
    return pairs[0], pairs[1]


# -- live session helpers ----------------------------------------------------
def _nodes() -> list[tuple[int, dict[str, str]]]:
    try:
        objects = json.loads(_run(["pw-dump"]))
    except (WiringError, json.JSONDecodeError):
        return []
    nodes: list[tuple[int, dict[str, str]]] = []
    for obj in objects:
        if obj.get("type") != "PipeWire:Interface:Node":
            continue
        raw_props = (obj.get("info") or {}).get("props") or {}
        props = {str(key): str(value) for key, value in raw_props.items()}
        try:
            serial = int(props.get("object.serial") or obj.get("id") or 0)
        except (TypeError, ValueError):
            serial = 0
        nodes.append((serial, props))
    return nodes


def default_sink_name() -> str | None:
    """Node name of the current default sink (via WirePlumber), if available."""
    try:
        output = _run(["wpctl", "inspect", "@DEFAULT_AUDIO_SINK@"])
    except WiringError:
        return None
    for raw in output.splitlines():
        line = raw.strip().lstrip("*").strip()
        if not line.startswith("node.name"):
            continue
        _, _, value = line.partition("=")
        value = value.strip().strip('"')
        if value:
            return value
    return None


def list_sinks() -> list[SinkInfo]:
    """Selectable outputs, default first, SonicFX's own nodes excluded."""
    default = default_sink_name()
    try:
        objects = json.loads(_run(["pw-dump"]))
    except (WiringError, json.JSONDecodeError):
        return []
    return sink_infos_from_dump(objects, default_name=default)


def _resolve_target(target: str | int) -> tuple[str, int]:
    nodes = _nodes()
    if isinstance(target, int):
        for serial, props in nodes:
            if serial == target and props.get("node.name"):
                return props["node.name"], serial
        raise WiringError(f"output node serial {target} not found")
    for serial, props in nodes:
        if props.get("node.name") == target:
            return target, serial
    raise WiringError(f"output device {target!r} not found")


def link_playback(
    playback_name: str, target: str | int, *, timeout: float = 3.0
) -> tuple[LinkPair, LinkPair]:
    """Route ``playback_name`` to the chosen output, replacing old links."""
    if isinstance(target, int) and target <= 0:
        raise WiringError("output serial must be positive")
    if isinstance(target, str) and target.startswith("sonicfx"):
        raise WiringError("refusing to route SonicFX into itself")
    target_name, _serial = _resolve_target(target)
    if target_name.startswith("sonicfx") or target_name == playback_name:
        raise WiringError("refusing to route SonicFX into itself")

    deadline = time.monotonic() + timeout
    while True:
        outputs = {
            line for line in _run(["pw-link", "-o"]).splitlines()
            if line.strip().startswith(f"{playback_name}:")
        }
        inputs = {
            line for line in _run(["pw-link", "-i"]).splitlines()
            if line.strip().startswith(f"{target_name}:")
        }
        outputs = {line.strip() for line in outputs}
        inputs = {line.strip() for line in inputs}
        if inputs and f"{playback_name}:output_FL" in outputs:
            break
        if time.monotonic() >= deadline:
            raise WiringError(
                f"ports for {playback_name!r} or {target_name!r} did not appear"
            )
        time.sleep(0.05)

    fl_pair, fr_pair = select_link_pairs(
        playback_name, target_name, outputs, inputs
    )

    # Drop previous routes from our playback node (no stale duplicates).
    for source, dest in parse_link_list(_run(["pw-link", "-l"])):
        if source.startswith(f"{playback_name}:"):
            subprocess.run(
                ["pw-link", "-d", source, dest],
                capture_output=True,
                check=False,
                timeout=5.0,
            )

    created: list[LinkPair] = []
    for source, dest in (fl_pair, fr_pair):
        result = subprocess.run(
            ["pw-link", source, dest],
            capture_output=True,
            text=True,
            check=False,
            timeout=5.0,
        )
        if result.returncode != 0 and "exist" not in result.stderr.lower():
            raise WiringError(
                f"could not link {source} -> {dest}: {result.stderr.strip()}"
            )
        created.append(LinkPair(source=source, target=dest))

    active = set(parse_link_list(_run(["pw-link", "-l"])))
    missing = [pair for pair in created if (pair.source, pair.target) not in active]
    if missing:
        raise WiringError(f"link verification failed: {missing}")
    return created[0], created[1]
