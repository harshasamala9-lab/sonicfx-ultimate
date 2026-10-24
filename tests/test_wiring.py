"""Unit tests for explicit playback → hardware wiring (no live PipeWire)."""
from __future__ import annotations

import pytest

from sonicfx.pipewire.wiring import (
    WiringError,
    link_playback,
    parse_link_list,
    select_link_pairs,
    sink_infos_from_dump,
)


def _node(
    name: str,
    description: str = "",
    *,
    media_class: str = "Audio/Sink",
    serial: int = 1,
    node_id: int = 10,
) -> dict:
    return {
        "id": node_id,
        "type": "PipeWire:Interface:Node",
        "info": {
            "props": {
                "node.name": name,
                "node.description": description or name,
                "media.class": media_class,
                "object.serial": serial,
            }
        },
    }


class TestLinkListParser:
    def test_parses_forward_and_reverse_links(self) -> None:
        text = (
            "sonicfx_out:output_FL\n"
            "  |-> alsa_output.pci:playback_FL\n"
            "sonicfx_out:output_FR\n"
            "  |-> alsa_output.pci:playback_FR\n"
            "alsa_output.pci:monitor_FL\n"
            "  |<- sonicfx_out:output_FL\n"
        )
        assert parse_link_list(text) == [
            ("sonicfx_out:output_FL", "alsa_output.pci:playback_FL"),
            ("sonicfx_out:output_FR", "alsa_output.pci:playback_FR"),
            ("sonicfx_out:output_FL", "alsa_output.pci:monitor_FL"),
        ]

    def test_ignores_orphan_links_and_blank_lines(self) -> None:
        assert parse_link_list("\n  |-> orphan:port\n") == []


class TestSinkDiscovery:
    def test_extracts_sinks_and_excludes_sonicfx(self) -> None:
        objects = [
            _node("alsa_output.usb", "USB Speakers", serial=42, node_id=58),
            _node("sonicfx_sink", "SonicFX Ultimate", serial=99, node_id=59),
            _node("alsa_input.mic", "Mic", media_class="Audio/Source", serial=7),
        ]
        sinks = sink_infos_from_dump(objects, default_name="alsa_output.usb")
        assert [(sink.name, sink.serial) for sink in sinks] == [
            ("alsa_output.usb", 42)
        ]
        assert sinks[0].is_default is True
        assert sinks[0].description == "USB Speakers"

    def test_default_sink_is_listed_first(self) -> None:
        objects = [
            _node("alsa_card.a", "Alpha", serial=1, node_id=1),
            _node("alsa_card.z", "Zulu", serial=2, node_id=2),
        ]
        sinks = sink_infos_from_dump(objects, default_name="alsa_card.z")
        assert [sink.name for sink in sinks] == ["alsa_card.z", "alsa_card.a"]
        assert sinks[0].is_default is True

    def test_missing_serial_falls_back_to_node_id(self) -> None:
        node = _node("alsa_output.x", node_id=77)
        del node["info"]["props"]["object.serial"]
        sinks = sink_infos_from_dump([node])
        assert sinks[0].serial == 77


class TestPortSelection:
    def test_selects_fl_fr_pairs(self) -> None:
        playback = "sonicfx_out"
        outputs = {"sonicfx_out:output_FL", "sonicfx_out:output_FR"}
        inputs = {
            "alsa:playback_FL",
            "alsa:playback_FR",
            "alsa:monitor_FL",
            "alsa:monitor_FR",
        }
        assert select_link_pairs(playback, "alsa", outputs, inputs) == (
            ("sonicfx_out:output_FL", "alsa:playback_FL"),
            ("sonicfx_out:output_FR", "alsa:playback_FR"),
        )

    def test_missing_playback_port_is_an_error(self) -> None:
        with pytest.raises(WiringError, match="does not exist"):
            select_link_pairs(
                "sonicfx_out", "alsa", {"sonicfx_out:output_FL"}, {"alsa:playback_FL"}
            )

    def test_missing_target_channel_is_an_error(self) -> None:
        with pytest.raises(WiringError, match="no FR input"):
            select_link_pairs(
                "sonicfx_out",
                "alsa",
                {"sonicfx_out:output_FL", "sonicfx_out:output_FR"},
                {"alsa:playback_FL"},
            )


class TestLoopGuard:
    def test_refuses_sonicfx_target_by_name(self) -> None:
        with pytest.raises(WiringError, match="itself"):
            link_playback("sonicfx_out", "sonicfx_sink")

    def test_refuses_non_positive_serial(self) -> None:
        with pytest.raises(WiringError, match="positive"):
            link_playback("sonicfx_out", 0)
