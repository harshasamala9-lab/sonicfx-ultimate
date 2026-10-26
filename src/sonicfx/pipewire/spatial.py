"""Cinema Spatial design data and HRTF/room-response discovery (control plane).

The real-time processing lives in PipeWire's native filter-chain: SOFA HRTF
spatialisers, builtin biquads/mixers/delays/convolvers and the SWH look-ahead
limiter.  This module only describes the virtual layout, validates user
settings, finds a freely licensed SOFA file and generates the room response
used by the convolver stages.

No Dolby/Atmos branding or object-audio claim belongs here.  The binaural mode
is a virtual 7.1.4-style loudspeaker layout rendered with measured HRTFs; the
speaker mode is a transaural/ambience virtualiser for two loudspeakers.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

__all__ = [
    "DEFAULT_SOFA_CANDIDATES",
    "RoomSize",
    "SpatialMode",
    "SpatialSpec",
    "ensure_room_ir",
    "find_sofa_file",
    "infer_spatial_mode",
    "limiter_available",
    "room_ir_path",
]

#: System locations shipped by libmysofa/Ubuntu.  Both files are freely usable;
#: see /usr/share/doc/libmysofa1/copyright (BSD-3-clause, with the MIT KEMAR
#: dataset citation).  An explicit SONICFX_SOFA path or a user SOFA in
#: ~/.local/share/sonicfx/hrtf takes priority.
DEFAULT_SOFA_CANDIDATES = (
    Path("/usr/share/libmysofa/MIT_KEMAR_normal_pinna.sofa"),
    Path("/usr/share/libmysofa/default.sofa"),
)

#: User-supplied HRTF directory (first .sofa file wins).
USER_SOFA_DIR = Path.home() / ".local" / "share" / "sonicfx" / "hrtf"

#: Room-IR recipe version, so a tuning change invalidates old files.
_ROOM_IR_VERSION = 2

_SOFA_EXTS = (".sofa",)


class SpatialMode(StrEnum):
    """Output strategy for Cinema Spatial."""

    AUTO = "auto"
    BINAURAL = "binaural"
    SPEAKER = "speaker"

    @property
    def label(self) -> str:
        return {
            SpatialMode.AUTO: "Auto (from device)",
            SpatialMode.BINAURAL: "Headphones · binaural",
            SpatialMode.SPEAKER: "Stereo speakers · transaural",
        }[self]


class RoomSize(StrEnum):
    """Preset room characters; values feed the synthetic room response."""

    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"

    @property
    def label(self) -> str:
        return {
            RoomSize.SMALL: "Small room",
            RoomSize.MEDIUM: "Medium room",
            RoomSize.LARGE: "Large cinema",
        }[self]


@dataclass(frozen=True)
class SpatialSpec:
    """Immutable description of one Cinema Spatial stage.

    ``mode`` must be resolved before rendering: AUTO is a controller/UI choice,
    not a graph choice.  ``sofa_path`` is required for BINAURAL; speaker mode
    does not use HRTFs.  ``room_ir`` is filled by the supervised daemon after
    it has generated/cached the stereo room WAV.
    """

    mode: SpatialMode = SpatialMode.BINAURAL
    room: RoomSize = RoomSize.MEDIUM
    amount: float = 0.75
    sofa_path: str | None = None
    room_ir: str | None = None
    limiter: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "mode", SpatialMode(self.mode))
        object.__setattr__(self, "room", RoomSize(self.room))
        if self.mode is SpatialMode.AUTO:
            raise ValueError("SpatialSpec.mode must be resolved to binaural or speaker")
        if not math.isfinite(self.amount) or not 0.0 <= self.amount <= 1.0:
            raise ValueError("spatial amount must be a finite value in [0, 1]")
        object.__setattr__(self, "amount", float(self.amount))
        object.__setattr__(self, "sofa_path", str(self.sofa_path) if self.sofa_path else None)
        object.__setattr__(self, "room_ir", str(self.room_ir) if self.room_ir else None)
        object.__setattr__(self, "limiter", bool(self.limiter))

    # -- design data -------------------------------------------------------
    def room_params(self) -> dict[str, float]:
        """Synthetic room-response parameters for :mod:`sonicfx.dsp.irs`."""
        if self.room is RoomSize.SMALL:
            rt60, size, predelay, wet = 0.55, 0.24, 10.0, 0.085
        elif self.room is RoomSize.LARGE:
            rt60, size, predelay, wet = 1.25, 0.82, 24.0, 0.135
        else:
            rt60, size, predelay, wet = 0.85, 0.52, 16.0, 0.105
        # A loudspeaker already excites the real room; a lighter synthetic
        # tail avoids a church-like double reverb.  Headphones get the full
        # virtual room because they have no acoustic space of their own.
        if self.mode is SpatialMode.SPEAKER:
            wet *= 0.55
        return {
            "rt60_s": rt60,
            "size_norm": size,
            "damping_norm": 0.48,
            "predelay_ms": predelay,
            "wet_gain": wet,
            "spread_norm": 0.35 if self.mode is SpatialMode.BINAURAL else 0.25,
            "seed": 17.0,
        }

    def fingerprint_payload(self) -> dict[str, object]:
        """Values that change the rendered graph or its design."""
        return {
            "mode": self.mode.value,
            "room": self.room.value,
            "amount": round(self.amount, 6),
            "sofa_path": self.sofa_path,
            "limiter": self.limiter,
        }

    def resolved_sofa(self) -> str | None:
        """Return the explicit SOFA path or the first installed candidate."""
        if self.sofa_path:
            path = Path(self.sofa_path)
            return str(path) if path.is_file() else None
        found = find_sofa_file()
        return str(found) if found is not None else None


def find_sofa_file() -> Path | None:
    """Locate a usable SOFA HRTF, preferring an explicit user override.

    ``SONICFX_SOFA`` may name a file or a directory.  A directory is searched
    for ``*.sofa``.  This function never downloads anything.
    """
    override = os.environ.get("SONICFX_SOFA", "").strip()
    if override:
        candidate = Path(override).expanduser()
        if candidate.is_file() and candidate.suffix.lower() in _SOFA_EXTS:
            return candidate
        if candidate.is_dir():
            for path in sorted(candidate.glob("*.sofa")):
                return path
    if USER_SOFA_DIR.is_dir():
        for path in sorted(USER_SOFA_DIR.glob("*.sofa")):
            return path
    for path in DEFAULT_SOFA_CANDIDATES:
        if path.is_file():
            return path
    return None


def infer_spatial_mode(
    *,
    icon_name: str = "",
    form_factor: str = "",
    node_name: str = "",
    channels: int = 2,
) -> SpatialMode:
    """Best-effort Auto mode from PipeWire/PulseAudio device metadata.

    Headphone/headset metadata wins; HDMI, speaker metadata and the analog
    laptop output use the speaker virtualiser.  Unknown devices default to
    binaural because that is the only mode that can actually localise sound;
    the UI always exposes an override.
    """
    haystack = " ".join((icon_name, form_factor, node_name)).casefold()
    if any(word in haystack for word in ("headphone", "headset", "head-mounted", "earphone")):
        return SpatialMode.BINAURAL
    if "hdmi" in haystack or "displayport" in haystack:
        return SpatialMode.SPEAKER
    if any(word in haystack for word in ("speaker", "audio-card-analog", "builtin")):
        return SpatialMode.SPEAKER
    if channels >= 4:
        return SpatialMode.SPEAKER
    return SpatialMode.BINAURAL


def limiter_available(roots: tuple[Path, ...] | None = None) -> bool:
    """Whether the SWH look-ahead limiter is installed (never guesses).

    ``roots`` is injectable for tests; production uses the standard LADSPA
    search locations.
    """
    search = roots or (
        Path("/usr/lib/ladspa"),
        Path("/usr/local/lib/ladspa"),
        Path("/usr/lib/x86_64-linux-gnu/ladspa"),
    )
    return any(
        (root / "fast_lookahead_limiter_1913.so").is_file() for root in search
    )


def room_ir_path(state_dir: Path | str, spatial: SpatialSpec, sample_rate: int) -> Path:
    """Stable cache path for the room response (does not create it)."""
    payload = {
        "version": _ROOM_IR_VERSION,
        "room": spatial.room.value,
        "params": spatial.room_params(),
        "sample_rate": int(sample_rate),
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(blob).hexdigest()[:12]
    safe_room = re.sub(r"[^a-z0-9]+", "-", spatial.room.value.lower()).strip("-")
    return Path(state_dir) / "irs" / f"room-{safe_room}-{digest}.wav"


def ensure_room_ir(
    state_dir: Path | str, spatial: SpatialSpec, sample_rate: int
) -> Path:
    """Generate (once) and return the stereo room response file."""
    if spatial.room_ir:
        path = Path(spatial.room_ir)
        if path.is_file():
            return path
    path = room_ir_path(state_dir, spatial, sample_rate)
    if path.is_file():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    params = spatial.room_params()
    from sonicfx.dsp.irs import write_room_ir

    # scipy's WAV writer infers the container from the extension; keep a
    # .wav suffix on the temporary file so the atomic replace is valid.
    tmp = path.with_name(path.name + ".tmp.wav")
    try:
        write_room_ir(
            tmp,
            sample_rate,
            rt60_s=float(params["rt60_s"]),
            size_norm=float(params["size_norm"]),
            damping_norm=float(params["damping_norm"]),
            predelay_ms=float(params["predelay_ms"]),
            wet_gain=float(params["wet_gain"]),
            spread_norm=float(params["spread_norm"]),
            seed=int(params["seed"]),
        )
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return path
