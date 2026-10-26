"""Equalizer APO config rendering and include-safety tests (no Windows needed)."""
from __future__ import annotations

from pathlib import Path

import pytest

from sonicfx.dsp.filters import BiquadBand, FilterKind
from sonicfx.profiles.presets import get_preset
from sonicfx.windows.apo import (
    INCLUDE_LINE,
    ApoError,
    EqualizerApo,
    discover_config_dir,
    render_apo_config,
)


def test_flat_config_has_no_filters_or_unnecessary_preamp() -> None:
    text = render_apo_config(())
    assert "Filter" not in text
    assert "Preamp" not in text
    assert "Equalizer APO" in text


def test_filters_use_documented_apo_syntax() -> None:
    bands = (
        BiquadBand(FilterKind.HIGH_PASS, 30.0, 0.0, 0.707),
        BiquadBand(FilterKind.LOW_SHELF, 65.0, 4.5, 0.7),
        BiquadBand(FilterKind.PEAKING, 3_000.0, 2.5, 1.1),
        BiquadBand(FilterKind.HIGH_SHELF, 10_000.0, 1.5, 0.7),
    )
    text = render_apo_config(bands)
    assert "Preamp: -4.5 dB" in text
    assert "Filter 1: ON HPQ Fc 30 Hz Q 0.707" in text
    assert "Filter 2: ON LS Fc 65 Hz Gain +4.5 dB Q 0.7" in text
    assert "Filter 3: ON PK Fc 3000 Hz Gain +2.5 dB Q 1.1" in text
    assert "Filter 4: ON HS Fc 10000 Hz Gain +1.5 dB Q 0.7" in text
    assert "nan" not in text.lower()


@pytest.mark.parametrize(
    ("kind", "token"),
    [
        (FilterKind.LOW_PASS, "LPQ"),
        (FilterKind.HIGH_PASS, "HPQ"),
        (FilterKind.BAND_PASS, "BP"),
        (FilterKind.NOTCH, "NO"),
        (FilterKind.ALL_PASS, "AP"),
    ],
)
def test_every_supported_filter_kind_maps_to_apo(kind, token) -> None:
    text = render_apo_config((BiquadBand(kind, 1_000.0, 0.0, 1.25),))
    assert f"ON {token} Fc 1000 Hz Q 1.25" in text


def test_disabled_bands_are_skipped_and_explicit_preamp_wins() -> None:
    bands = (
        BiquadBand(FilterKind.PEAKING, 1_000.0, 9.0, 1.0, enabled=False),
        BiquadBand(FilterKind.NOTCH, 60.0, 0.0, 4.0),
    )
    text = render_apo_config(bands, preamp_db=-1.0)
    assert "Filter 1: ON NO Fc 60 Hz Q 4" in text
    assert "Gain" not in text
    assert "Preamp: -1.0 dB" in text


def test_apo_apply_adds_include_once_and_preserves_user_config(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_file = config_dir / "config.txt"
    config_file.write_text("Preamp: -2 dB\nFilter: ON PK Fc 100 Hz Gain 1 dB Q 1\n")
    apo = EqualizerApo(config_dir=config_dir)

    status = apo.apply(get_preset("cinema_immersion").bands)

    assert status.installed is True
    assert status.include_active is True
    assert config_file.read_text().count(INCLUDE_LINE) == 1
    assert "Preamp: -2 dB" in config_file.read_text()
    assert (config_dir / "sonicfx.txt").read_text().count("Filter") >= 5
    assert apo.backup_file is not None
    assert apo.backup_file.read_text().startswith("Preamp: -2 dB")

    # Re-applying must not duplicate the include or replace the user's file.
    apo.apply(get_preset("cinema_immersion").bands)
    assert config_file.read_text().count(INCLUDE_LINE) == 1


def test_apo_disable_removes_only_sonicfx_include(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_file = config_dir / "config.txt"
    config_file.write_text(
        "Preamp: -2 dB\n"
        f"{INCLUDE_LINE}\n"
        "Filter: ON PK Fc 100 Hz Gain 1 dB Q 1\n"
    )
    include = config_dir / "sonicfx.txt"
    include.write_text("Preamp: -4.5 dB\n")
    apo = EqualizerApo(config_dir=config_dir)

    status = apo.disable()

    assert status.include_active is False
    remaining = config_file.read_text()
    assert INCLUDE_LINE not in remaining
    assert "Preamp: -2 dB" in remaining
    assert "Filter: ON PK" in remaining
    assert "disabled" in include.read_text()


def test_apo_missing_installation_raises_actionable_error(tmp_path: Path) -> None:
    apo = EqualizerApo(config_dir=tmp_path / "missing")
    assert apo.installed is False
    with pytest.raises(ApoError, match="not installed"):
        apo.apply(())


def test_discovery_honours_test_override(tmp_path: Path, monkeypatch) -> None:
    config_dir = tmp_path / "override"
    config_dir.mkdir()
    monkeypatch.setenv("SONICFX_APO_CONFIG_DIR", str(config_dir))
    assert discover_config_dir() == config_dir
