"""Native PipeWire graph rendering for Cinema Spatial (control plane only).

The rendered graph is executed by the supervised child ``pipewire`` daemon.
Python never runs inside the audio callback.  Two topologies are supported:

* **binaural** — a virtual 7.1.4-style loudspeaker ring rendered with the SOFA
  HRTF spatialiser, a stereo room convolver and a look-ahead limiter.  This is
  the mode for headphones.
* **speaker** — a transaural/ambience virtualiser for two loudspeakers:
  mid/side ambience extraction, early-reflection/room convolution, a simplified
  crosstalk-cancellation matrix and the same limiter.

Audio is stereo in and stereo out.  The virtual channels exist only inside the
native graph.  No object metadata is decoded; see docs/feature-limitations.md.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from sonicfx.dsp.filters import BiquadBand, FilterKind
from sonicfx.pipewire.chain import (
    ChainSpecError,
    _clean_description,
    _escape,
    _fmt,
)
from sonicfx.pipewire.spatial import SpatialMode, SpatialSpec

if TYPE_CHECKING:
    from sonicfx.pipewire.chain import ChainSpec

_SHAPED = frozenset({FilterKind.PEAKING, FilterKind.LOW_SHELF, FilterKind.HIGH_SHELF})

#: Per-speaker azimuths/elevations (degrees).  PipeWire's SOFA convention:
#: 0° front, 90° left, 180° behind, 270° right; elevation −90…90°.
_BINAURAL_SPEAKERS: tuple[tuple[str, float, float], ...] = (
    ("FL", 30.0, 0.0),
    ("FR", 330.0, 0.0),
    ("FC", 0.0, 0.0),
    ("SL", 90.0, 0.0),
    ("SR", 270.0, 0.0),
    ("BL", 150.0, 0.0),
    ("BR", 210.0, 0.0),
    ("TFL", 30.0, 45.0),
    ("TFR", 330.0, 45.0),
    ("TBL", 150.0, 45.0),
    ("TBR", 210.0, 45.0),
)


class _Graph:
    """Small SPA-JSON graph writer; all names are generated, never user text."""

    def __init__(self) -> None:
        self.nodes: list[str] = []
        self.links: list[str] = []

    # -- primitives --------------------------------------------------------
    @staticmethod
    def _value(value: object) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, str):
            return f'"{_escape(value)}"'
        if isinstance(value, (int, float)):
            return _fmt(float(value))
        raise ChainSpecError(f"unsupported graph value: {value!r}")

    @classmethod
    def _config_key(cls, key: str) -> str:
        if all(ch.isalnum() or ch in "_." for ch in key):
            return key
        return f'"{_escape(key)}"'

    def add(
        self,
        name: str,
        label: str,
        *,
        kind: str = "builtin",
        plugin: str | None = None,
        config: dict[str, object] | None = None,
        control: dict[str, object] | None = None,
    ) -> None:
        head = f"            {{ type = {kind} name = {name}"
        if plugin is not None:
            head += f" plugin = {plugin}"
        head += f" label = {label}"
        lines = [head]
        if config:
            body = " ".join(
                f"{self._config_key(key)} = {self._value(value)}"
                for key, value in config.items()
            )
            lines.append(f"              config = {{ {body} }}")
        if control:
            body = " ".join(
                f'"{_escape(key)}" = {self._value(value)}'
                for key, value in control.items()
            )
            lines.append(f"              control = {{ {body} }}")
        lines[-1] += " }"
        self.nodes.append("\n".join(lines))

    def link(self, output: str, input_: str) -> None:
        self.links.append(
            f'            {{ output = "{output}" input = "{input_}" }}'
        )

    # -- helpers -----------------------------------------------------------
    def mixer(self, name: str, sources: list[tuple[str, float]]) -> str:
        if not sources:
            raise ChainSpecError(f"mixer {name} needs at least one input")
        if len(sources) > 8:
            raise ChainSpecError(f"mixer {name} has more than 8 inputs")
        self.add(
            name,
            "mixer",
            control={f"Gain {index + 1}": gain for index, (_src, gain) in enumerate(sources)},
        )
        for index, (source, _gain) in enumerate(sources):
            self.link(source, f"{name}:In {index + 1}")
        return f"{name}:Out"

    def linear(self, name: str, multiplier: float) -> str:
        self.add(name, "linear", control={"Mult": multiplier, "Add": 0.0})
        return f"{name}:Out"

    def spatial(
        self,
        name: str,
        azimuth: float,
        elevation: float,
        sofa_path: str,
        radius: float = 1.2,
    ) -> tuple[str, str]:
        self.add(
            name,
            "spatializer",
            kind="sofa",
            config={
                "filename": sofa_path,
                "blocksize": 128,
                "tailsize": 256,
                "normalize": True,
                "gain": 1.0,
                "latency": 0.0,
            },
            control={"Azimuth": azimuth, "Elevation": elevation, "Radius": radius},
        )
        return f"{name}:Out L", f"{name}:Out R"

    def convolver(self, name: str, filename: str, channel: int) -> str:
        self.add(
            name,
            "convolver",
            config={
                "filename": filename,
                "blocksize": 128,
                "tailsize": 256,
                "gain": 1.0,
                "channel": channel,
            },
        )
        return f"{name}:Out"

    def limiter(self, name: str) -> tuple[str, str]:
        self.add(
            name,
            "fastLookaheadLimiter",
            kind="ladspa",
            plugin="fast_lookahead_limiter_1913",
            control={
                "Input gain (dB)": 0.0,
                "Limit (dB)": -1.0,
                "Release time (s)": 0.12,
            },
        )
        return f"{name}:Output 1", f"{name}:Output 2"


def _filtered_chain(
    graph: _Graph,
    name: str,
    source: str,
    *,
    highpass: float | None = None,
    lowpass: float | None = None,
    allpass: float | None = None,
    allpass_q: float = 0.9,
    delay_s: float | None = None,
    gain: float = 1.0,
) -> str:
    """Build a mono high-pass/low-pass/all-pass/delay/gain chain."""
    current = source
    if highpass is not None:
        node = f"{name}Hp"
        graph.add(node, "bq_highpass", control={"Freq": highpass, "Q": 0.707})
        graph.link(current, f"{node}:In")
        current = f"{node}:Out"
    if lowpass is not None:
        node = f"{name}Lp"
        graph.add(node, "bq_lowpass", control={"Freq": lowpass, "Q": 0.707})
        graph.link(current, f"{node}:In")
        current = f"{node}:Out"
    if allpass is not None:
        node = f"{name}Ap"
        graph.add(node, "bq_allpass", control={"Freq": allpass, "Q": allpass_q})
        graph.link(current, f"{node}:In")
        current = f"{node}:Out"
    if delay_s is not None:
        node = f"{name}Delay"
        graph.add(
            node,
            "delay",
            config={"max-delay": max(0.05, delay_s * 2.0), "latency": 0.0},
            control={"Delay (s)": delay_s, "Feedback": 0.0, "Feedforward": 0.0},
        )
        graph.link(current, f"{node}:In")
        current = f"{node}:Out"
    if gain != 1.0:
        node = f"{name}Gain"
        graph.add(node, "linear", control={"Mult": gain, "Add": 0.0})
        graph.link(current, f"{node}:In")
        current = f"{node}:Out"
    return current


def _tone_stage(
    graph: _Graph, bands: tuple[BiquadBand, ...]
) -> tuple[str, str, str, str]:
    """Input EQ; returns (left_out, right_out, left_in, right_in)."""
    active = [band for band in bands if band.enabled]
    if not active:
        graph.add("hubL", "copy")
        graph.add("hubR", "copy")
        return "hubL:Out", "hubR:Out", "hubL:In", "hubR:In"
    outputs: list[str] = []
    inputs: list[str] = []
    for channel in ("L", "R"):
        previous = ""
        for index, band in enumerate(active):
            node = f"eq{channel}{index}"
            controls = band.control_params()
            if band.kind not in _SHAPED:
                controls.pop("Gain", None)
            graph.add(node, band.kind.pipewire_label, control=controls)
            if index == 0:
                inputs.append(f"{node}:In")
            else:
                graph.link(previous, f"{node}:In")
            previous = f"{node}:Out"
        outputs.append(previous)
    return outputs[0], outputs[1], inputs[0], inputs[1]


def _bus(graph: _Graph, prefix: str, sources: list[str]) -> str:
    """Mix many mono sources with ≤8-input mixers, then apply headroom."""
    groups = [sources[index : index + 6] for index in range(0, len(sources), 6)]
    group_outputs: list[str] = []
    for index, group in enumerate(groups):
        name = f"{prefix}Grp{index}"
        group_outputs.append(
            graph.mixer(name, [(source, 1.0) for source in group])
        )
    mix_name = f"{prefix}Mix"
    mixed = graph.mixer(mix_name, [(source, 1.0) for source in group_outputs])
    trim_name = f"{prefix}Trim"
    graph.add(trim_name, "linear", control={"Mult": 0.5, "Add": 0.0})
    graph.link(mixed, f"{trim_name}:In")
    return f"{trim_name}:Out"


def _add_room(graph: _Graph, left: str, right: str, ir_path: str | None) -> tuple[str, str]:
    if not ir_path:
        return left, right
    conv_l = graph.convolver("convL", ir_path, 0)
    conv_r = graph.convolver("convR", ir_path, 1)
    graph.link(left, "convL:In")
    graph.link(right, "convR:In")
    return conv_l, conv_r


def _finish(graph: _Graph, left: str, right: str, limiter: bool) -> tuple[str, str]:
    if limiter:
        out_l, out_r = graph.limiter("lim")
        graph.link(left, "lim:Input 1")
        graph.link(right, "lim:Input 2")
        return out_l, out_r
    # No limiter installed: conservative fixed headroom instead of a graph
    # that silently clips.  The UI states that the limiter is unavailable.
    safety_l = graph.linear("safetyL", 0.55)
    safety_r = graph.linear("safetyR", 0.55)
    graph.link(left, "safetyL:In")
    graph.link(right, "safetyR:In")
    return safety_l, safety_r


def _binaural_layout(
    graph: _Graph, left: str, right: str, spatial: SpatialSpec, sofa_path: str
) -> tuple[tuple[str, str], ...]:
    """Build the 7.1.4-style virtual speaker feeds; return spatialiser outputs.

    The front stage is a real three-channel extraction rather than three
    copies of the mid signal: a band-limited centre (120 Hz–7 kHz) is removed
    from the front L/R feeds, which prevents the classic comb-filtered,
    triple-centre phantom image.  Bass is managed separately so the LFE is the
    only deep-bass path, and each surround/height feed gets its own all-pass
    decorrelator instead of being a delayed clone of the same side signal.
    """
    amount = spatial.amount
    side_gain = 0.68 * amount
    rear_gain = 0.52 * amount
    height_gain = 0.38 * amount

    mid = graph.mixer("mid", [(left, 0.5), (right, 0.5)])
    side = graph.mixer("side", [(left, 0.5), (right, -0.5)])
    right_side = graph.mixer("rside", [(left, -0.5), (right, 0.5)])

    # Band-limited centre extraction.  The same filtered centre is subtracted
    # from L/R and sent to the centre speaker, so the sum remains flat while
    # the correlated dialogue stops arriving from three directions at once.
    centre_band = _filtered_chain(
        graph, "centreBand", mid, highpass=120.0, lowpass=7000.0
    )
    centre = _filtered_chain(graph, "centre", centre_band, gain=0.92)
    front_l = graph.mixer("frontL", [(left, 1.0), (centre_band, -0.5)])
    front_r = graph.mixer("frontR", [(right, 1.0), (centre_band, -0.5)])
    front_l = _filtered_chain(graph, "frontLhp", front_l, highpass=55.0, gain=0.80)
    front_r = _filtered_chain(graph, "frontRhp", front_r, highpass=55.0, gain=0.80)

    # 4th-order-ish bass management: the satellites are high-passed above the
    # sub, and the LFE is low-passed twice.  Deep bass therefore arrives once,
    # equally at both ears, instead of being summed four times.
    lfe = _filtered_chain(graph, "lfe1", mid, highpass=25.0, lowpass=90.0)
    lfe = _filtered_chain(graph, "lfe2", lfe, lowpass=80.0, gain=0.62)

    feeds: dict[str, str] = {
        "FL": front_l,
        "FR": front_r,
        "FC": centre,
    }
    feeds["SL"] = _filtered_chain(
        graph, "sl", side, highpass=150.0, allpass=430.0, delay_s=0.012,
        gain=side_gain,
    )
    feeds["SR"] = _filtered_chain(
        graph, "sr", right_side, highpass=150.0, allpass=570.0, delay_s=0.018,
        gain=side_gain,
    )
    feeds["BL"] = _filtered_chain(
        graph, "bl", side, highpass=200.0, allpass=820.0, delay_s=0.025,
        gain=rear_gain,
    )
    feeds["BR"] = _filtered_chain(
        graph, "br", right_side, highpass=200.0, allpass=1080.0, delay_s=0.032,
        gain=rear_gain,
    )
    height_specs = (
        ("TFL", side, 0.008, 2100.0),
        ("TFR", right_side, 0.014, 2700.0),
        ("TBL", side, 0.021, 3300.0),
        ("TBR", right_side, 0.027, 3900.0),
    )
    for name, source, delay_s, allpass in height_specs:
        feeds[name] = _filtered_chain(
            graph, name.lower(), source, highpass=2200.0, allpass=allpass,
            delay_s=delay_s, gain=height_gain,
        )

    outputs: list[tuple[str, str]] = []
    for name, azimuth, elevation in _BINAURAL_SPEAKERS:
        # A small distance spread adds front-to-back depth without turning the
        # rear into a different loudness.  The SOFA HRTF itself supplies the
        # elevation and interaural cues.
        radius = 1.15 if name in {"FL", "FR", "FC"} else 1.55
        speaker = graph.spatial(f"spk{name}", azimuth, elevation, sofa_path, radius)
        graph.link(feeds[name], f"spk{name}:In")
        outputs.append(speaker)
    # LFE is not spatialised: low frequencies are not localisable and the
    # direct-to-both-ears path avoids an unnecessary HRTF colouration.
    outputs.append((lfe, lfe))
    return tuple(outputs)


def _speaker_layout(
    graph: _Graph, left: str, right: str, spatial: SpatialSpec
) -> tuple[str, str]:
    """Transaural/ambience virtualiser for two loudspeakers.

    Three decorrelated side-signal taps create a wide, enveloping field; a
    common high-frequency air tap adds height without hard phase cancellation;
    the room convolver supplies lateral early reflections; and the final
    matrix is a simplified transaural inverse (negative, delayed, band-limited
    crossfeed with makeup gain) rather than a plain stereo widener.
    """
    amount = spatial.amount
    mid = graph.mixer("mid", [(left, 0.5), (right, 0.5)])
    side = graph.mixer("side", [(left, 0.5), (right, -0.5)])
    lfe = _filtered_chain(graph, "lfe1", mid, highpass=25.0, lowpass=90.0)
    lfe = _filtered_chain(graph, "lfe2", lfe, lowpass=80.0, gain=0.42)
    left_main = _filtered_chain(graph, "leftMain", left, highpass=45.0, gain=0.95)
    right_main = _filtered_chain(graph, "rightMain", right, highpass=45.0, gain=0.95)
    amb1 = _filtered_chain(
        graph, "amb1", side, highpass=250.0, allpass=670.0, delay_s=0.012,
        gain=0.68 * amount,
    )
    amb2 = _filtered_chain(
        graph, "amb2", side, highpass=500.0, allpass=1300.0, delay_s=0.023,
        gain=0.46 * amount,
    )
    amb3 = _filtered_chain(
        graph, "amb3", side, highpass=3000.0, allpass=3100.0, delay_s=0.009,
        gain=0.28 * amount,
    )
    air = _filtered_chain(
        graph, "air", side, highpass=5000.0, allpass=4200.0, delay_s=0.006,
        gain=0.16 * amount,
    )
    left_pre = graph.mixer(
        "leftPre",
        [(left_main, 1.0), (amb1, 1.0), (amb2, 1.0), (amb3, 1.0), (lfe, 1.0), (air, 1.0)],
    )
    right_pre = graph.mixer(
        "rightPre",
        [(right_main, 1.0), (amb1, -1.0), (amb2, 1.0), (amb3, -1.0), (lfe, 1.0), (air, 1.0)],
    )
    left_pre = _filtered_chain(graph, "leftTrim", left_pre, gain=0.60)
    right_pre = _filtered_chain(graph, "rightTrim", right_pre, gain=0.60)

    room_l, room_r = _add_room(graph, left_pre, right_pre, spatial.room_ir)
    # Approximate transaural crosstalk cancellation: a delayed, band-limited
    # negative copy of the opposite speaker.  The 1.18 make-up gain restores
    # the level lost to the negative cross term; the high-pass keeps bass
    # mono-safe.  This is most effective in a near-field sweet spot.
    cross_l = _filtered_chain(
        graph, "crossL", room_r, highpass=150.0, lowpass=4200.0,
        allpass=900.0, delay_s=0.00028, gain=-0.42,
    )
    cross_r = _filtered_chain(
        graph, "crossR", room_l, highpass=150.0, lowpass=4200.0,
        allpass=900.0, delay_s=0.00028, gain=-0.42,
    )
    left_out = graph.mixer("leftCtc", [(room_l, 1.18), (cross_l, 1.0)])
    right_out = graph.mixer("rightCtc", [(room_r, 1.18), (cross_r, 1.0)])
    return left_out, right_out


def _render_filter_graph(spec: ChainSpec) -> tuple[list[str], list[str], list[str], list[str]]:
    spatial = spec.spatial
    if spatial is None:  # pragma: no cover - guarded by render_chain_conf
        raise ChainSpecError("spatial graph requested without a SpatialSpec")
    graph = _Graph()
    left, right, left_in, right_in = _tone_stage(graph, spec.bands)
    if spatial.mode is SpatialMode.BINAURAL:
        sofa_path = spatial.resolved_sofa()
        if not sofa_path:
            raise ChainSpecError(
                "binaural Cinema Spatial needs a SOFA HRTF file; "
                "install libmysofa1 or set SONICFX_SOFA"
            )
        speakers = _binaural_layout(graph, left, right, spatial, sofa_path)
        left_sources = [pair[0] for pair in speakers]
        right_sources = [pair[1] for pair in speakers]
        left = _bus(graph, "L", left_sources)
        right = _bus(graph, "R", right_sources)
        left, right = _add_room(graph, left, right, spatial.room_ir)
    else:
        left, right = _speaker_layout(graph, left, right, spatial)
        # _speaker_layout already applies the room; it does not include the
        # limiter yet so the shared finish stage can do so once.
    left, right = _finish(graph, left, right, spatial.limiter)
    if not graph.nodes:  # pragma: no cover - impossible for valid specs
        raise ChainSpecError("empty spatial graph")
    return graph.nodes, graph.links, [left_in, right_in], [left, right]


def render_spatial_conf(spec: ChainSpec, *, generation: int = 0) -> str:
    """Render the complete child-daemon config for a Cinema Spatial chain."""
    if generation < 0:
        raise ChainSpecError("generation must be >= 0")
    spatial = spec.spatial
    if spatial is None:
        raise ChainSpecError("spatial graph requested without a SpatialSpec")
    nodes, links, inputs, outputs = _render_filter_graph(spec)
    description = _escape(_clean_description(spec.sink_description))
    positions = "FL FR"
    if spec.target_sink is None:
        target_line = ""
    elif isinstance(spec.target_sink, int):
        target_line = f"\n                node.target = {spec.target_sink}"
    else:
        target_line = f'\n                node.target = "{_escape(spec.target_sink)}"'
    node_block = "\n".join(nodes)
    link_block = "\n".join(links)
    input_block = " ".join(f'"{port}"' for port in inputs)
    output_block = " ".join(f'"{port}"' for port in outputs)
    return f"""# Generated by SonicFX Ultimate — Cinema Spatial — do not edit by hand.
# Native PipeWire graph: {spatial.mode.value} mode, {spatial.room.value} room.
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
{node_block}
                ]
                links = [
{link_block}
                ]
                inputs = [ {input_block} ]
                outputs = [ {output_block} ]
            }}
            audio.channels = 2
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
                audio.channels   = 2
                audio.position   = [ FL FR ]
            }}
        }}
    }}
]
"""
