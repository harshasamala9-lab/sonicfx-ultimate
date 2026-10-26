"""PipeWire filter-chain configuration generation (control plane only).

A :class:`ChainSpec` is the immutable description of one processing chain.
:func:`render_chain_conf` turns it into a PipeWire configuration fragment that a
dedicated child daemon consumes::

    pipewire -c ~/.local/state/sonicfx/chains/chain-0001.conf

Verified integration recipe (PipeWire 1.6.2, see docs/implementation-plan.md §1):
the child must load ``protocol-native``, ``client-node`` and ``adapter`` before
``filter-chain``; the SPA factory mapping needs ``audio.convert.*`` and
``support.*``.  Missing pieces fail with "can't find protocol", "Protocol
error" (node export) or "no adapter factory found".

The DSP itself runs inside that child daemon in the PipeWire audio thread.
Nothing in this module touches audio.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, replace

from sonicfx.dsp.filters import BiquadBand, FilterKind
from sonicfx.pipewire.spatial import SpatialSpec

#: Sink/playback/core names end up in SPA JSON unquoted-safe positions, so keep
#: them ASCII and boring.  They are also the stable identifiers clients target
#: across daemon restarts.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")

#: Hard cap: each band creates several control ports; 32 bands is far beyond
#: what the UI can present and keeps the node's port table bounded.
MAX_BANDS = 32

#: Kinds that take a Gain control.  LPF/HPF/notch/all-pass/band-pass do not.
_SHAPED_KINDS = frozenset({FilterKind.PEAKING, FilterKind.LOW_SHELF, FilterKind.HIGH_SHELF})


class ChainSpecError(ValueError):
    """Invalid chain description; message is safe to show in the GUI."""


def _fmt(value: float) -> str:
    """Render a float for SPA JSON without exponent noise."""
    if not math.isfinite(value):
        raise ChainSpecError(f"non-finite control value: {value!r}")
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text or "0"


def _clean_description(text: str) -> str:
    """Make a user-facing label safe for a quoted SPA JSON string."""
    cleaned = "".join(ch if ch.isprintable() else " " for ch in text)
    cleaned = " ".join(cleaned.split())[:96]
    return cleaned or "SonicFX Ultimate"


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


@dataclass(frozen=True)
class ChainSpec:
    """Immutable description of one SonicFX processing chain.

    Attributes:
        bands: equaliser bands in processing order (first band sees the input).
        sample_rate: advisory design rate recorded for validation/UI; the graph
            follows the session rate negotiated by PipeWire.
        channels: 1 (mono) or 2 (stereo).
        sink_name: stable name of the virtual sink clients target.
        sink_description: human label shown by desktop volume controls.
        playback_name: name of the passive playback follower node.
        core_name: identity of the child daemon (its own PipeWire core).
        target_sink: optional playback destination — a node name (``str``) or
            a live node serial (``int``); ``None`` lets the session manager
            choose.
    """

    bands: tuple[BiquadBand, ...] = ()
    sample_rate: int = 48_000
    channels: int = 2
    sink_name: str = "sonicfx_sink"
    sink_description: str = "SonicFX Ultimate"
    playback_name: str = "sonicfx_out"
    core_name: str = "sonicfx-chain"
    target_sink: str | int | None = None
    #: Let the session manager auto-link the playback side. Production routing
    #: uses explicit ``pw-link`` calls and keeps this off; probes/tests keep it
    #: off too so nothing is ever sent to speakers by accident.
    autoconnect: bool = True
    #: Optional Cinema Spatial stage.  When present, the biquad chain is kept
    #: as the input tone stage and a native HRTF/room/limiter graph follows it.
    spatial: SpatialSpec | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "bands", tuple(self.bands))
        object.__setattr__(
            self, "sink_description", _clean_description(self.sink_description)
        )
        if len(self.bands) > MAX_BANDS:
            raise ChainSpecError(f"too many bands ({len(self.bands)} > {MAX_BANDS})")
        if self.sample_rate not in (44_100, 48_000, 88_200, 96_000, 192_000):
            raise ChainSpecError(f"unsupported sample rate: {self.sample_rate}")
        if self.channels not in (1, 2):
            raise ChainSpecError("only mono and stereo chains are supported")
        if self.spatial is not None:
            if not isinstance(self.spatial, SpatialSpec):
                raise ChainSpecError("spatial must be a SpatialSpec")
            if self.channels != 2:
                raise ChainSpecError("Cinema Spatial requires a stereo chain")
            if (
                self.spatial.mode.value == "binaural"
                and not self.spatial.resolved_sofa()
            ):
                raise ChainSpecError(
                    "Cinema Spatial binaural mode needs a SOFA HRTF file; "
                    "install libmysofa1 or set SONICFX_SOFA"
                )
        for label, value in (
            ("sink_name", self.sink_name),
            ("playback_name", self.playback_name),
            ("core_name", self.core_name),
        ):
            if not _SAFE_NAME.match(value):
                raise ChainSpecError(f"invalid {label}: {value!r}")
        if self.target_sink is not None:
            if isinstance(self.target_sink, bool) or not isinstance(
                self.target_sink, (int, str)
            ):
                raise ChainSpecError(
                    "target_sink must be a node serial (int) or node name (str)"
                )
            if isinstance(self.target_sink, int):
                if self.target_sink <= 0:
                    raise ChainSpecError("target_sink serial must be positive")
            elif not _SAFE_NAME.match(self.target_sink):
                raise ChainSpecError(f"invalid target_sink: {self.target_sink!r}")
        nyquist = self.sample_rate / 2.0
        for band in self.bands:
            if band.freq >= nyquist:
                raise ChainSpecError(
                    f"band at {band.freq:g} Hz is above Nyquist for {self.sample_rate} Hz"
                )

    # -- helpers -----------------------------------------------------------
    def with_bands(self, *bands: BiquadBand) -> ChainSpec:
        """Return a copy with the given bands (keeps all runtime identifiers)."""
        return replace(self, bands=tuple(bands))

    def fingerprint(self) -> str:
        """Stable digest of everything that affects the rendered graph."""
        payload = {
            "bands": [
                [b.kind.value, b.freq, b.gain_db, b.q, b.enabled] for b in self.bands
            ],
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "sink_name": self.sink_name,
            "sink_description": self.sink_description,
            "playback_name": self.playback_name,
            "core_name": self.core_name,
            "target_sink": self.target_sink,
            "autoconnect": self.autoconnect,
            "spatial": (
                self.spatial.fingerprint_payload() if self.spatial is not None else None
            ),
        }
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _control_block(band: BiquadBand) -> str:
    params = band.control_params()
    if band.kind not in _SHAPED_KINDS:
        params.pop("Gain", None)
    return " ".join(f'"{key}" = {_fmt(value)}' for key, value in params.items())


def render_chain_conf(spec: ChainSpec, *, generation: int = 0) -> str:
    """Render the complete child-daemon configuration for ``spec``.

    ``generation`` is embedded as a node property so readiness checks can tell
    a freshly published node from a lingering one left by the previous
    generation.
    """
    if generation < 0:
        raise ChainSpecError("generation must be >= 0")
    if spec.spatial is not None:
        # Imported lazily: spatial_graph uses the quoting/format helpers defined
        # in this module, so a top-level import would be circular.
        from sonicfx.pipewire.spatial_graph import render_spatial_conf

        return render_spatial_conf(spec, generation=generation)

    bands = spec.bands or (BiquadBand(FilterKind.PEAKING, 1_000.0, 0.0, 0.5),)
    nodes: list[str] = []
    links: list[str] = []
    for index, band in enumerate(bands):
        node_name = f"band{index}"
        nodes.append(
            "                    {\n"
            "                        type  = builtin\n"
            f"                        name  = {node_name}\n"
            f"                        label = {band.kind.pipewire_label}\n"
            f"                        control = {{ {_control_block(band)} }}\n"
            "                    }"
        )
        if index:
            links.append(
                f'                    {{ output = "band{index - 1}:Out" '
                f'input = "{node_name}:In" }}'
            )

    positions = "FL FR" if spec.channels == 2 else "MONO"
    description = _escape(spec.sink_description)
    if spec.target_sink is None:
        target_line = ""
    elif isinstance(spec.target_sink, int):
        target_line = f"\n                node.target = {spec.target_sink}"
    else:
        target_line = f'\n                node.target = "{_escape(spec.target_sink)}"'
    nodes_block = "\n".join(nodes)
    links_block = "\n".join(links) if links else ""

    return f"""# Generated by SonicFX Ultimate — do not edit by hand.
# Regenerated atomically for each parameter change; the daemon is restarted.
context.properties = {{
    core.name = {spec.core_name}
    core.daemon = false
}}

context.spa-libs = {{
    audio.convert.* = audioconvert/libspa-audioconvert
    support.*       = support/libspa-support
}}

context.modules = [
    {{ name = libpipewire-module-protocol-native }}
    {{ name = libpipewire-module-client-node }}
    {{ name = libpipewire-module-adapter }}
    {{   name = libpipewire-module-filter-chain
        args = {{
            node.description = "{description}"
            media.name       = "{description}"
            filter.graph = {{
                nodes = [
{nodes_block}
                ]
                links = [
{links_block}
                ]
            }}
            audio.channels = {spec.channels}
            audio.position = [ {positions} ]
            capture.props = {{
                node.name        = "{spec.sink_name}"
                node.description = "{description}"
                media.class      = "Audio/Sink"
                sonicfx.generation = "{generation}"
            }}
            playback.props = {{
                node.name        = "{spec.playback_name}"
                node.passive     = true
                node.autoconnect = {"true" if spec.autoconnect else "false"}{target_line}
            }}
        }}
    }}
]
"""
