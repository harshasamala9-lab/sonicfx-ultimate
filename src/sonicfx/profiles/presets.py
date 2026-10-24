"""Built-in enhancement presets (movies, songs, night, headphones, …).

Each preset is a ready-made tuple of :class:`~sonicfx.dsp.filters.BiquadBand`
objects; the chain builder renders it into a real PipeWire filter graph. Values
are deliberately conservative: every boost is paired with cuts elsewhere where
musically sensible, and no band exceeds ±6 dB so presets cannot cause clipping
on their own.

The presets are plain data — user profiles (Stage 6) store their own band sets
in the same shape.
"""
from __future__ import annotations

from dataclasses import dataclass

from sonicfx.dsp.filters import BiquadBand, FilterKind

__all__ = [
    "DEFAULT_PRESET_KEY",
    "PRESETS",
    "Preset",
    "get_preset",
    "preset_keys",
]


@dataclass(frozen=True)
class Preset:
    """One built-in preset: identity, user-facing text and DSP bands."""

    key: str
    label: str
    description: str
    bands: tuple[BiquadBand, ...]


PRESETS: tuple[Preset, ...] = (
    Preset(
        key="flat",
        label="Flat (reference)",
        description="No equalisation — the untouched signal, for honest A/B checks.",
        bands=(),
    ),
    Preset(
        key="movies",
        label="Movies / Cinema",
        description=(
            "Warm low end plus clear dialogue presence for films at normal "
            "listening levels."
        ),
        bands=(
            BiquadBand(FilterKind.LOW_SHELF, 80.0, 2.5, 0.7),
            BiquadBand(FilterKind.PEAKING, 350.0, -1.5, 1.0),
            BiquadBand(FilterKind.PEAKING, 2_000.0, 2.5, 1.0),
            BiquadBand(FilterKind.PEAKING, 4_500.0, 1.2, 1.4),
            BiquadBand(FilterKind.HIGH_SHELF, 10_000.0, 1.0, 0.7),
        ),
    ),
    Preset(
        key="songs",
        label="Songs / Music",
        description="Fuller bass and airy treble — a gentle smile curve for music.",
        bands=(
            BiquadBand(FilterKind.LOW_SHELF, 90.0, 3.0, 0.7),
            BiquadBand(FilterKind.PEAKING, 420.0, -1.0, 1.0),
            BiquadBand(FilterKind.PEAKING, 3_000.0, 1.2, 1.2),
            BiquadBand(FilterKind.HIGH_SHELF, 12_000.0, 2.0, 0.7),
        ),
    ),
    Preset(
        key="night",
        label="Night",
        description=(
            "Quiet-hours curve: bass and treble trimmed so dialogue stays "
            "intelligible without waking anyone."
        ),
        bands=(
            BiquadBand(FilterKind.LOW_SHELF, 120.0, -3.5, 0.7),
            BiquadBand(FilterKind.PEAKING, 2_000.0, 2.0, 1.0),
            BiquadBand(FilterKind.HIGH_SHELF, 9_000.0, -2.0, 0.7),
        ),
    ),
    Preset(
        key="headphones",
        label="Headphones",
        description=(
            "Tames sibilance and adds weight for small drivers (crossfeed and "
            "HRTF-style spatialisation arrive with the Stage 5 headphone block)."
        ),
        bands=(
            BiquadBand(FilterKind.LOW_SHELF, 70.0, 2.0, 0.7),
            BiquadBand(FilterKind.PEAKING, 3_200.0, -1.5, 1.6),
            BiquadBand(FilterKind.HIGH_SHELF, 9_500.0, -1.5, 0.7),
        ),
    ),
    Preset(
        key="voice",
        label="Podcast / Voice",
        description="Cuts rumble and mud, lifts presence for spoken-word clarity.",
        bands=(
            BiquadBand(FilterKind.HIGH_PASS, 85.0, 0.0, 0.707),
            BiquadBand(FilterKind.PEAKING, 480.0, -1.5, 1.0),
            BiquadBand(FilterKind.PEAKING, 2_200.0, 2.5, 1.1),
            BiquadBand(FilterKind.HIGH_SHELF, 8_000.0, -1.0, 0.7),
        ),
    ),
    Preset(
        key="gaming",
        label="Gaming",
        description=(
            "Punch and positional cues: low impact plus crisp highs without "
            "harsh mids."
        ),
        bands=(
            BiquadBand(FilterKind.LOW_SHELF, 60.0, 2.0, 0.7),
            BiquadBand(FilterKind.PEAKING, 300.0, -1.5, 1.0),
            BiquadBand(FilterKind.PEAKING, 3_500.0, 2.5, 1.2),
            BiquadBand(FilterKind.HIGH_SHELF, 9_000.0, 1.5, 0.7),
        ),
    ),
    Preset(
        key="bass",
        label="Bass Punch",
        description=(
            "Sub-heavy shelf for music; pair it with the limiter so nothing "
            "clips on bass-heavy tracks."
        ),
        bands=(
            BiquadBand(FilterKind.LOW_SHELF, 65.0, 4.5, 0.75),
            BiquadBand(FilterKind.PEAKING, 180.0, 1.5, 1.2),
            BiquadBand(FilterKind.PEAKING, 500.0, -1.0, 1.0),
        ),
    ),
)

#: Safe first-run choice: no colouration until the user opts into a preset.
DEFAULT_PRESET_KEY = "flat"

_BY_KEY = {preset.key: preset for preset in PRESETS}


def preset_keys() -> tuple[str, ...]:
    """Keys of all built-in presets, in display order."""
    return tuple(preset.key for preset in PRESETS)


def get_preset(key: str) -> Preset:
    """Return the preset named ``key`` or raise a helpful :class:`KeyError`."""
    try:
        return _BY_KEY[key]
    except KeyError as exc:
        raise KeyError(
            f"unknown preset {key!r}; available: {', '.join(preset_keys())}"
        ) from exc
