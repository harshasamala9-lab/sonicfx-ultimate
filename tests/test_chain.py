"""Stage 3 acceptance tests: chain description → PipeWire config (no audio).

These tests lock down the *verified* child-daemon recipe (module order, SPA
factory mapping, generation marker) and the band → control mapping.
"""
from __future__ import annotations

import pytest

from sonicfx.dsp.filters import BiquadBand, FilterKind
from sonicfx.pipewire.chain import (
    MAX_BANDS,
    ChainSpec,
    ChainSpecError,
    render_chain_conf,
)


def _band(
    kind: FilterKind = FilterKind.PEAKING,
    freq: float = 1_000.0,
    gain: float = 0.0,
    q: float = 0.71,
) -> BiquadBand:
    return BiquadBand(kind, freq, gain, q)


class TestValidation:
    def test_rejects_unsupported_sample_rate(self) -> None:
        with pytest.raises(ChainSpecError):
            ChainSpec(sample_rate=32_000)

    def test_rejects_band_above_nyquist(self) -> None:
        # BiquadBand already caps at 20.5 kHz, so this exercises the chain-level
        # guard through a band that is legal for the DSP layer only on lower
        # rates; the chain check remains as defence for future rate support.
        with pytest.raises(ValueError):
            ChainSpec(sample_rate=44_100, bands=(_band(freq=23_000.0),))

    def test_rejects_too_many_bands(self) -> None:
        bands = tuple(_band(freq=100.0 + index * 10.0) for index in range(MAX_BANDS + 1))
        with pytest.raises(ChainSpecError):
            ChainSpec(bands=bands)

    @pytest.mark.parametrize(
        ("field", "bad_value"),
        [
            ("sink_name", "bad name"),
            ("sink_name", ""),
            ("playback_name", "x;y"),
            ("core_name", "-leading-dash"),
            ("sink_name", "a" * 65),
        ],
    )
    def test_rejects_unsafe_identifiers(self, field: str, bad_value: str) -> None:
        with pytest.raises(ChainSpecError):
            ChainSpec(**{field: bad_value})

    def test_rejects_target_with_separator(self) -> None:
        with pytest.raises(ChainSpecError):
            ChainSpec(target_sink="alsa_output/with/slash")

    def test_rejects_channels_outside_mono_stereo(self) -> None:
        with pytest.raises(ChainSpecError):
            ChainSpec(channels=3)


class TestRender:
    def test_verified_module_recipe_and_order(self) -> None:
        conf = render_chain_conf(ChainSpec())
        modules = (
            "libpipewire-module-protocol-native",
            "libpipewire-module-client-node",
            "libpipewire-module-adapter",
            "libpipewire-module-filter-chain",
        )
        positions = [conf.index(module) for module in modules]
        assert positions == sorted(positions)
        assert "audio.convert.* = audioconvert/libspa-audioconvert" in conf
        assert "support.*       = support/libspa-support" in conf
        assert "core.name = sonicfx-chain" in conf

    def test_generation_marker_and_sink_properties(self) -> None:
        conf = render_chain_conf(ChainSpec(), generation=7)
        assert 'sonicfx.generation = "7"' in conf
        assert 'node.name        = "sonicfx_sink"' in conf
        assert 'media.class      = "Audio/Sink"' in conf
        assert "node.passive     = true" in conf
        assert "node.autoconnect = true" in conf

    def test_autoconnect_can_be_disabled_for_explicit_wiring(self) -> None:
        conf = render_chain_conf(ChainSpec(autoconnect=False))
        assert "node.autoconnect = false" in conf

    def test_target_sink_is_optional_and_escaped(self) -> None:
        without = render_chain_conf(ChainSpec())
        assert "node.target" not in without
        with_target = render_chain_conf(ChainSpec(target_sink="alsa_output.usb"))
        assert 'node.target = "alsa_output.usb"' in with_target

    def test_target_sink_accepts_live_serial(self) -> None:
        conf = render_chain_conf(ChainSpec(target_sink=57))
        assert "node.target = 57" in conf
        with pytest.raises(ChainSpecError):
            ChainSpec(target_sink=0)

    def test_band_labels_and_control_mapping(self) -> None:
        spec = ChainSpec(
            bands=(
                BiquadBand(FilterKind.LOW_SHELF, 80.0, 4.5, 0.9),
                BiquadBand(FilterKind.PEAKING, 1_200.0, -3.0, 2.0),
                BiquadBand(FilterKind.HIGH_PASS, 30.0, 0.0, 0.707),
            )
        )
        conf = render_chain_conf(spec)
        assert "label = bq_lowshelf" in conf
        assert '"Freq" = 80 "Q" = 0.9 "Gain" = 4.5' in conf
        assert "label = bq_peaking" in conf
        assert '"Freq" = 1200 "Q" = 2 "Gain" = -3' in conf
        assert "label = bq_highpass" in conf
        assert '"Freq" = 30 "Q" = 0.707' in conf
        assert 'Q" = 0.707 "Gain"' not in conf
        assert "name  = band0" in conf and "name  = band2" in conf

    def test_bands_are_linked_in_processing_order(self) -> None:
        spec = ChainSpec(bands=(_band(), _band(freq=2_000.0), _band(freq=4_000.0)))
        conf = render_chain_conf(spec)
        assert 'output = "band0:Out" input = "band1:In"' in conf
        assert 'output = "band1:Out" input = "band2:In"' in conf
        assert 'output = "band2:Out"' not in conf

    def test_empty_spec_renders_neutral_band(self) -> None:
        conf = render_chain_conf(ChainSpec())
        assert "label = bq_peaking" in conf
        assert '"Gain" = 0' in conf

    def test_description_is_escaped(self) -> None:
        spec = ChainSpec(sink_description='Sonic "FX" \\ hall')
        conf = render_chain_conf(spec)
        assert 'node.description = "Sonic \\"FX\\" \\\\ hall"' in conf

    def test_description_control_characters_are_squashed(self) -> None:
        spec = ChainSpec(sink_description="Cinema\n\tHall")
        assert spec.sink_description == "Cinema Hall"

    def test_mono_positions(self) -> None:
        conf = render_chain_conf(ChainSpec(channels=1))
        assert "audio.position = [ MONO ]" in conf

    def test_fingerprint_is_stable_and_change_sensitive(self) -> None:
        first = ChainSpec(bands=(_band(gain=1.0),))
        same = ChainSpec(bands=(_band(gain=1.0),))
        changed = ChainSpec(bands=(_band(gain=2.0),))
        assert first.fingerprint() == same.fingerprint()
        assert first.fingerprint() != changed.fingerprint()
