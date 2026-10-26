"""Cinema Spatial design, rendering and daemon-preparation tests (no audio)."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from scipy.io import wavfile

from sonicfx.dsp import irs
from sonicfx.pipewire import spatial as spatial_module
from sonicfx.pipewire.chain import ChainSpec, ChainSpecError, render_chain_conf
from sonicfx.pipewire.daemon import ChainDaemon
from sonicfx.pipewire.spatial import (
    RoomSize,
    SpatialMode,
    SpatialSpec,
    ensure_room_ir,
    find_sofa_file,
    infer_spatial_mode,
    limiter_available,
    room_ir_path,
)
from sonicfx.profiles.presets import get_preset


@pytest.fixture
def sofa(tmp_path: Path) -> Path:
    path = tmp_path / "test-hrtf.sofa"
    path.write_bytes(b"SOFA")
    return path


def _spec(
    sofa: Path | None = None,
    *,
    mode: SpatialMode = SpatialMode.BINAURAL,
    room: RoomSize = RoomSize.MEDIUM,
    amount: float = 0.75,
    room_ir: str | None = None,
    limiter: bool = True,
) -> SpatialSpec:
    return SpatialSpec(
        mode=mode,
        room=room,
        amount=amount,
        sofa_path=str(sofa) if sofa is not None else None,
        room_ir=room_ir,
        limiter=limiter,
    )


class TestAutoMode:
    @pytest.mark.parametrize(
        ("kwargs", "expected"),
        [
            ({"icon_name": "audio-headphones"}, SpatialMode.BINAURAL),
            ({"form_factor": "headset"}, SpatialMode.BINAURAL),
            ({"node_name": "bluez_output.AC_12"}, SpatialMode.BINAURAL),
            ({"icon_name": "audio-speakers"}, SpatialMode.SPEAKER),
            ({"node_name": "alsa_output.hdmi-stereo"}, SpatialMode.SPEAKER),
            ({"channels": 6}, SpatialMode.SPEAKER),
            ({"node_name": "alsa_output.pci-0000"}, SpatialMode.BINAURAL),
        ],
    )
    def test_metadata_inference(self, kwargs, expected) -> None:
        assert infer_spatial_mode(**kwargs) is expected

    def test_auto_is_not_renderable(self) -> None:
        with pytest.raises(ValueError, match="resolved"):
            SpatialSpec(mode=SpatialMode.AUTO)


class TestSofaDiscovery:
    def test_no_hrtf_returns_none(self, tmp_path, monkeypatch) -> None:
        monkeypatch.delenv("SONICFX_SOFA", raising=False)
        monkeypatch.setattr(spatial_module, "DEFAULT_SOFA_CANDIDATES", ())
        monkeypatch.setattr(spatial_module, "USER_SOFA_DIR", tmp_path / "missing")
        assert find_sofa_file() is None

    def test_environment_file_wins(self, tmp_path, monkeypatch, sofa) -> None:
        monkeypatch.setenv("SONICFX_SOFA", str(sofa))
        monkeypatch.setattr(spatial_module, "DEFAULT_SOFA_CANDIDATES", ())
        monkeypatch.setattr(spatial_module, "USER_SOFA_DIR", tmp_path / "user")
        assert find_sofa_file() == sofa

    def test_environment_directory(self, tmp_path, monkeypatch, sofa) -> None:
        monkeypatch.setenv("SONICFX_SOFA", str(sofa.parent))
        monkeypatch.setattr(spatial_module, "DEFAULT_SOFA_CANDIDATES", ())
        monkeypatch.setattr(spatial_module, "USER_SOFA_DIR", tmp_path / "user")
        assert find_sofa_file() == sofa

    def test_user_directory(self, tmp_path, monkeypatch, sofa) -> None:
        monkeypatch.delenv("SONICFX_SOFA", raising=False)
        monkeypatch.setattr(spatial_module, "DEFAULT_SOFA_CANDIDATES", ())
        user = tmp_path / "user"
        user.mkdir()
        (user / "custom.sofa").write_bytes(b"SOFA")
        monkeypatch.setattr(spatial_module, "USER_SOFA_DIR", user)
        assert find_sofa_file() == user / "custom.sofa"


class TestLimiterDiscovery:
    def test_limiter_roots_are_checked(self, tmp_path, monkeypatch) -> None:
        assert limiter_available(roots=(tmp_path,)) is False
        (tmp_path / "fast_lookahead_limiter_1913.so").write_bytes(b"elf")
        assert limiter_available(roots=(tmp_path,)) is True


class TestSpatialSpec:
    def test_defaults_and_labels(self, sofa) -> None:
        spec = _spec(sofa)
        assert spec.mode is SpatialMode.BINAURAL
        assert spec.room is RoomSize.MEDIUM
        assert spec.amount == pytest.approx(0.75)
        assert "Headphones" in spec.mode.label
        assert "Medium" in spec.room.label

    @pytest.mark.parametrize("amount", [-0.1, 1.1, float("nan"), float("inf")])
    def test_amount_is_bounded(self, amount) -> None:
        with pytest.raises(ValueError, match="amount"):
            _spec(mode=SpatialMode.SPEAKER, amount=amount)

    def test_missing_explicit_sofa_is_not_resolved(self, tmp_path) -> None:
        spec = _spec(tmp_path / "missing.sofa")
        assert spec.resolved_sofa() is None

    def test_sofa_paths_are_normalised(self, sofa) -> None:
        spec = _spec(sofa)
        assert isinstance(spec.sofa_path, str)
        assert spec.resolved_sofa() == str(sofa)

    def test_speaker_room_is_drier_than_binaural(self) -> None:
        binaural = _spec(mode=SpatialMode.BINAURAL).room_params()
        speaker = _spec(mode=SpatialMode.SPEAKER).room_params()
        assert speaker["wet_gain"] < binaural["wet_gain"]
        assert speaker["spread_norm"] < binaural["spread_norm"]
        assert binaural["spread_norm"] <= 1.0


class TestChainValidation:
    def test_binaural_requires_an_existing_hrtf(self, tmp_path) -> None:
        with pytest.raises(ChainSpecError, match="SOFA"):
            ChainSpec(spatial=_spec(tmp_path / "missing.sofa"))

    def test_speaker_does_not_require_an_hrtf(self) -> None:
        spec = ChainSpec(spatial=_spec(mode=SpatialMode.SPEAKER))
        assert spec.spatial is not None
        assert spec.spatial.sofa_path is None
        # A system SOFA may still be discovered, but the speaker renderer does
        # not consult it; only the chain's stereo/spatial checks apply.
        assert "type = sofa" not in render_chain_conf(spec)

    def test_spatial_requires_stereo(self, sofa) -> None:
        with pytest.raises(ChainSpecError, match="stereo"):
            ChainSpec(channels=1, spatial=_spec(sofa))

    def test_fingerprint_is_stable_and_sensitive(self, sofa) -> None:
        base = ChainSpec(spatial=_spec(sofa))
        same = ChainSpec(spatial=_spec(sofa))
        changed_amount = ChainSpec(spatial=_spec(sofa, amount=0.5))
        changed_room = ChainSpec(spatial=_spec(sofa, room=RoomSize.LARGE))
        changed_limiter = ChainSpec(spatial=_spec(sofa, limiter=False))
        assert base.fingerprint() == same.fingerprint()
        assert len(
            {
                base.fingerprint(),
                changed_amount.fingerprint(),
                changed_room.fingerprint(),
                changed_limiter.fingerprint(),
            }
        ) == 4

    def test_room_ir_does_not_change_graph_fingerprint(self, sofa) -> None:
        first = ChainSpec(spatial=_spec(sofa, room_ir="/tmp/a.wav"))
        second = ChainSpec(spatial=_spec(sofa, room_ir="/tmp/b.wav"))
        assert first.fingerprint() == second.fingerprint()


class TestRender:
    def test_binaural_topology(self, sofa, tmp_path) -> None:
        room = tmp_path / "room.wav"
        spec = ChainSpec(
            spatial=_spec(sofa, room_ir=str(room)),
            bands=get_preset("cinema_spatial").bands,
        )
        conf = render_chain_conf(spec, generation=4)
        assert conf.count("type = sofa") == 11
        assert conf.count("label = bq_allpass") >= 8
        assert conf.count("label = convolver") == 2
        assert "label = fastLookaheadLimiter" in conf
        assert 'input = "lim:Input 1"' in conf
        assert 'input = "lim:Input 2"' in conf
        assert 'outputs = [ "lim:Output 1" "lim:Output 2" ]' in conf
        assert 'inputs = [ "eqL0:In" "eqR0:In" ]' in conf
        assert "name = centreBandHp" in conf
        assert "name = frontLhp" in conf
        assert "name = lfe2Lp" in conf
        assert "safetyL" not in conf
        assert 'node.name        = "sonicfx_sink"' in conf
        assert 'sonicfx.generation = "4"' in conf

    def test_speaker_topology(self, tmp_path) -> None:
        room = tmp_path / "room.wav"
        spec = ChainSpec(spatial=_spec(mode=SpatialMode.SPEAKER, room_ir=str(room)))
        conf = render_chain_conf(spec)
        assert "type = sofa" not in conf
        assert "label = convolver" in conf
        assert "name = crossL" in conf
        assert "name = crossR" in conf
        assert "name = amb1Ap" in conf
        assert "name = leftCtc" in conf
        assert 'outputs = [ "lim:Output 1" "lim:Output 2" ]' in conf

    def test_limiter_fallback_uses_fixed_headroom(self, sofa, tmp_path) -> None:
        spec = ChainSpec(
            spatial=_spec(sofa, room_ir=str(tmp_path / "room.wav"), limiter=False)
        )
        conf = render_chain_conf(spec)
        assert "fastLookaheadLimiter" not in conf
        assert "name = safetyL" in conf
        assert "name = safetyR" in conf
        assert 'outputs = [ "safetyL:Out" "safetyR:Out" ]' in conf

    def test_room_ir_is_wired_after_the_virtual_bus(self, sofa, tmp_path) -> None:
        room = tmp_path / "room.wav"
        conf = render_chain_conf(
            ChainSpec(spatial=_spec(sofa, room_ir=str(room)))
        )
        # The convolver is fed by the mixed virtual bus, not by a single
        # speaker or by the dry input.
        assert 'output = "LTrim:Out" input = "convL:In"' in conf
        assert 'output = "RTrim:Out" input = "convR:In"' in conf
        assert 'output = "LMix:Out" input = "LTrim:In"' in conf

    def test_empty_bands_still_have_a_tone_hub(self, sofa, tmp_path) -> None:
        conf = render_chain_conf(
            ChainSpec(spatial=_spec(sofa, room_ir=str(tmp_path / "room.wav")))
        )
        assert "name = hubL" in conf
        assert "name = hubR" in conf


class TestRoomResponse:
    def test_room_path_is_stable_and_mode_sensitive(self, tmp_path) -> None:
        binaural = _spec(mode=SpatialMode.BINAURAL)
        speaker = _spec(mode=SpatialMode.SPEAKER)
        first = room_ir_path(tmp_path, binaural, 48_000)
        second = room_ir_path(tmp_path, binaural, 48_000)
        other_rate = room_ir_path(tmp_path, binaural, 44_100)
        other_mode = room_ir_path(tmp_path, speaker, 48_000)
        assert first == second
        assert first != other_rate
        assert first != other_mode

    def test_ensure_room_ir_writes_a_stereo_wav_once(self, tmp_path) -> None:
        spec = _spec(mode=SpatialMode.SPEAKER, room=RoomSize.SMALL)
        path = ensure_room_ir(tmp_path, spec, 48_000)
        assert path.is_file()
        assert path.parent == tmp_path / "irs"
        fs, data = wavfile.read(path)
        assert fs == 48_000
        assert data.ndim == 2 and data.shape[1] == 2
        assert data.dtype == np.int16
        assert np.min(np.abs(data[0])) > 30_000
        mtime = path.stat().st_mtime_ns
        again = ensure_room_ir(tmp_path, spec, 48_000)
        assert again == path
        assert path.stat().st_mtime_ns == mtime

    def test_ensure_room_ir_reuses_an_explicit_file(self, tmp_path) -> None:
        explicit = tmp_path / "given.wav"
        explicit.write_bytes(b"not really a wav")
        spec = _spec(
            mode=SpatialMode.SPEAKER,
            room_ir=str(explicit),
        )
        assert ensure_room_ir(tmp_path, spec, 48_000) == explicit

    def test_stereo_ir_has_direct_path_and_early_reflections(self) -> None:
        ir = irs.stereo_room_ir(
            48_000, rt60_s=0.85, size_norm=0.5, wet_gain=0.1, spread_norm=0.35
        )
        assert ir.shape[1] == 2
        assert np.allclose(ir[0], 1.0, atol=1e-6)
        assert abs(ir[int(0.007 * 48_000), 0]) > 0.05
        assert abs(ir[int(0.008 * 48_000), 1]) > 0.05

    def test_spread_controls_side_energy(self) -> None:
        narrow = irs.stereo_room_ir(48_000, rt60_s=0.85, wet_gain=0.15, spread_norm=0.0)
        wide = irs.stereo_room_ir(48_000, rt60_s=0.85, wet_gain=0.15, spread_norm=1.0)
        side_narrow = (narrow[:, 0] - narrow[:, 1]) / 2.0
        side_wide = (wide[:, 0] - wide[:, 1]) / 2.0
        assert np.sqrt(np.mean(side_wide**2)) > np.sqrt(np.mean(side_narrow**2)) * 2.0
        assert np.allclose(narrow[0], wide[0])

    def test_write_room_ir_is_16_bit_stereo(self, tmp_path) -> None:
        path = irs.write_room_ir(
            tmp_path / "room.wav",
            48_000,
            rt60_s=0.6,
            wet_gain=0.1,
            spread_norm=0.3,
            seed=3,
        )
        fs, data = wavfile.read(path)
        assert fs == 48_000
        assert data.shape[1] == 2
        assert data.dtype == np.int16


class TestDaemonPreparation:
    def test_prepare_spec_generates_room_ir(self, tmp_path) -> None:
        daemon = ChainDaemon(tmp_path)
        spec = ChainSpec(
            spatial=_spec(mode=SpatialMode.SPEAKER, room=RoomSize.SMALL)
        )
        prepared = daemon._prepare_spec(spec)
        assert prepared is not spec
        assert prepared.spatial is not None
        assert prepared.spatial.room_ir is not None
        room = Path(prepared.spatial.room_ir)
        assert room.is_file()
        assert room.parent == tmp_path / "irs"
        assert daemon._prepare_spec(prepared) is prepared

    def test_prepared_room_is_rendered_into_the_conf(self, tmp_path) -> None:
        daemon = ChainDaemon(tmp_path)
        prepared = daemon._prepare_spec(
            ChainSpec(spatial=_spec(mode=SpatialMode.SPEAKER))
        )
        conf = daemon._write_conf(prepared, generation=2)
        text = conf.read_text(encoding="utf-8")
        assert prepared.spatial is not None
        assert prepared.spatial.room_ir in text
        assert "label = convolver" in text

    def test_prepare_spec_without_spatial_is_a_noop(self, tmp_path) -> None:
        daemon = ChainDaemon(tmp_path)
        spec = ChainSpec()
        assert daemon._prepare_spec(spec) is spec


class TestPreset:
    def test_cinema_spatial_requests_the_native_stage(self) -> None:
        preset = get_preset("cinema_spatial")
        assert preset.spatial is True
        assert any(band.freq > 1_000.0 for band in preset.bands)
        assert get_preset("songs").spatial is False

    def test_preset_builds_a_speaker_chain_without_an_hrtf(self) -> None:
        spec = ChainSpec(
            bands=get_preset("cinema_spatial").bands,
            spatial=_spec(mode=SpatialMode.SPEAKER),
        )
        assert spec.spatial is not None
        conf = render_chain_conf(spec)
        assert "type = sofa" not in conf
