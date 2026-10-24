"""Acceptance tests for the built-in enhancement presets."""
from __future__ import annotations

import pytest

from sonicfx.dsp.filters import FilterKind
from sonicfx.pipewire.chain import ChainSpec
from sonicfx.profiles.presets import (
    DEFAULT_PRESET_KEY,
    PRESETS,
    get_preset,
    preset_keys,
)


def test_keys_are_unique_and_default_exists() -> None:
    keys = preset_keys()
    assert len(keys) == len(set(keys))
    assert DEFAULT_PRESET_KEY in keys


def test_every_preset_has_user_facing_text() -> None:
    for preset in PRESETS:
        assert preset.label.strip()
        assert preset.description.strip()


def test_every_preset_renders_a_distinct_valid_chain() -> None:
    fingerprints = set()
    for preset in PRESETS:
        spec = ChainSpec(bands=preset.bands)
        fingerprints.add(spec.fingerprint())
        for band in preset.bands:
            assert 20.0 <= band.freq <= 20_500.0
            assert abs(band.gain_db) <= 6.0
    assert len(fingerprints) == len(PRESETS)


def test_movies_lifts_dialogue_presence() -> None:
    bands = get_preset("movies").bands
    presence = [
        band
        for band in bands
        if 1_500.0 <= band.freq <= 2_500.0 and band.gain_db > 0.0
    ]
    assert presence, "the movies preset must lift dialogue presence"


def test_night_reduces_bass_and_treble() -> None:
    bands = get_preset("night").bands
    low = [band for band in bands if band.kind is FilterKind.LOW_SHELF]
    high = [band for band in bands if band.kind is FilterKind.HIGH_SHELF]
    assert low and low[0].gain_db < 0.0
    assert high and high[0].gain_db < 0.0


def test_best_all_in_one_balances_bass_presence_and_air() -> None:
    bands = get_preset("best_all_in_one").bands
    assert any(
        band.kind is FilterKind.LOW_SHELF and band.gain_db > 0.0 for band in bands
    )
    assert any(
        1_500.0 <= band.freq <= 3_000.0 and band.gain_db > 0.0 for band in bands
    )
    assert any(
        band.kind is FilterKind.HIGH_SHELF and band.gain_db > 0.0 for band in bands
    )
    # The all-rounder must not be bass-only: it also clears the mud region.
    assert any(
        250.0 <= band.freq <= 600.0 and band.gain_db < 0.0 for band in bands
    )


def test_flat_is_neutral() -> None:
    assert get_preset("flat").bands == ()


def test_unknown_preset_lists_alternatives() -> None:
    with pytest.raises(KeyError, match="available"):
        get_preset("cinema-max")
