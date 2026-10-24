from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from retrolarium_kit.games.pacman.screen import (
    PacmanScreenError,
    decode_screen_collection,
    encode_screen_collection,
    set_text_field,
    text_field_value,
)
from retrolarium_kit.core.authoring import GameProfile, ResourceSpec, RevisionSpec
from retrolarium_kit.platform.nes.resources import (
    build_resources,
    decode_resources,
    inspection_details,
    parse_resource_document,
)


def parameters() -> dict[str, object]:
    return {
        "prg_cpu_base": 0xC000,
        "prg_file_offset": 16,
        "title_logo": {"file_offset": 16, "ppu_address": 0x20E5, "width": 2, "height": 2},
        "title_packets": {"file_offset": 20, "payload_sizes": [2, 1]},
        "title_attributes": {"file_offset": 29, "ppu_address": 0x23C8, "size": 2},
        "fixed_blocks": [
            {"key": "menu", "label": "Menu", "file_offset": 31, "size": 3},
            {"key": "pause", "label": "Pause", "file_offset": 34, "size": 2},
        ],
        "attract": {"pointer_file_offset": 40, "packet_file_offsets": [44, 50], "payload_sizes": [3, 1]},
        "intermission_visual": [
            {"key": "cycle_tiles", "file_offset": 60, "size": 2, "allowed_values": []},
            {"key": "cycle_indexes", "file_offset": 62, "size": 2, "allowed_values": [0, 4, 8]},
        ],
        "read_only_tables": [{"key": "dispatch", "cpu_address": 0xC040, "kind": "words", "count": 2}],
        "encoding": {"characters": " ABC", "codes": [32, 65, 66, 67], "case_fold": "upper", "padding": 32},
        "text_fields": [
            {"key": "title", "label": "Title", "path": ["title_packets", 0, "bytes"], "start": 0, "width": 2},
            {"key": "attract", "label": "Attract", "path": ["attract_packets", 1, "bytes"], "start": 0, "width": 1},
            {"key": "pause", "label": "Pause", "path": ["fixed_blocks", "pause"], "start": 0, "width": 2},
        ],
    }


def fixture() -> bytes:
    source = bytearray(96)
    source[16:20] = bytes((1, 2, 3, 4))
    source[20:25] = bytes((0x20, 0x00, 65, 66, 0xFF))
    source[25:29] = bytes((0x21, 0x00, 67, 0xFF))
    source[29:31] = bytes((0xC0, 0x30))
    source[31:34] = bytes((0x20, 0x00, 0xFF))
    source[34:36] = bytes((65, 66))
    source[40:44] = bytes((0x1C, 0xC0, 0x22, 0xC0))
    source[44:50] = bytes((0x22, 0x00, 65, 66, 67, 0xFF))
    source[50:54] = bytes((0x23, 0x00, 65, 0xFF))
    source[60:64] = bytes((7, 8, 0, 4))
    source[80:84] = bytes((0x34, 0x12, 0x78, 0x56))
    return bytes(source)


class PacmanScreenTests(unittest.TestCase):
    def test_decode_encode_round_trip_and_text_views(self) -> None:
        source = fixture()
        config = parameters()
        document = decode_screen_collection(source, config)
        self.assertEqual(document["title_logo"]["tiles"], [[1, 2], [3, 4]])
        self.assertEqual(text_field_value(document, "title"), "AB")
        self.assertEqual(text_field_value(document, "attract"), "A")
        self.assertEqual(document["read_only_code_tables"][0]["values"], [0x1234, 0x5678])
        rebuilt = bytearray(source)
        for offset, payload in encode_screen_collection(document, config):
            rebuilt[offset : offset + len(payload)] = payload
        self.assertEqual(bytes(rebuilt), source)

        edited = set_text_field(document, "pause", "c")
        self.assertEqual(text_field_value(edited, "pause"), "C")
        spans = encode_screen_collection(edited, config)
        rebuilt = bytearray(source)
        for offset, payload in spans:
            rebuilt[offset : offset + len(payload)] = payload
        self.assertEqual(rebuilt[34:36], bytes((67, 32)))

    def test_rejects_pointer_packet_and_protected_metadata_changes(self) -> None:
        config = parameters()
        source = bytearray(fixture())
        source[40] ^= 1
        with self.assertRaisesRegex(PacmanScreenError, "pointer"):
            decode_screen_collection(bytes(source), config)
        document = decode_screen_collection(fixture(), config)
        invalid = copy.deepcopy(document)
        invalid["title_packets"][0]["address"] = 0x1000
        with self.assertRaisesRegex(PacmanScreenError, "8192"):
            encode_screen_collection(invalid, config)
        invalid = copy.deepcopy(document)
        invalid["attract_packets"][0]["bytes"].append(65)
        with self.assertRaisesRegex(PacmanScreenError, "exactly 3"):
            encode_screen_collection(invalid, config)
        invalid = copy.deepcopy(document)
        invalid["intermission_visual"]["cycle_indexes"][0] = 3
        with self.assertRaisesRegex(PacmanScreenError, "allowlist"):
            encode_screen_collection(invalid, config)
        invalid = copy.deepcopy(document)
        invalid["read_only_code_tables"][0]["cpu_address"] += 1
        with self.assertRaisesRegex(PacmanScreenError, "metadata"):
            encode_screen_collection(invalid, config)

    def test_text_rejects_overflow_and_unmapped_tiles(self) -> None:
        document = decode_screen_collection(fixture(), parameters())
        with self.assertRaisesRegex(PacmanScreenError, "fixed width"):
            set_text_field(document, "title", "ABC")
        with self.assertRaisesRegex(PacmanScreenError, "unsupported"):
            set_text_field(document, "title", "A!")
        # A tile the font lacks reads as no text, but the screen still builds.
        painted = copy.deepcopy(document)
        painted["fixed_blocks"]["pause"][0] = 0
        with self.assertRaisesRegex(PacmanScreenError, "unmapped"):
            text_field_value(painted, "pause")
        encode_screen_collection(painted, parameters())

    def test_nes_bundle_composes_screen_and_embedded_text_transactionally(self) -> None:
        source = bytearray(fixture())
        source[:16] = b"NES\x1a" + bytes((1, 0, 0, 0)) + bytes(8)
        source = bytes(source).ljust(0x4010, b"\0")
        revision = RevisionSpec(
            "probe",
            "Screen probe",
            len(source),
            hashlib.sha1(source).hexdigest(),
            hashlib.sha256(source).hexdigest(),
            "ines",
            0,
        )
        profile = GameProfile(
            "screen-probe",
            1,
            "Screen probe",
            "nes",
            ("screens", "text"),
            (revision,),
            (
                ResourceSpec("bundle", "resource-bundle", "nes.resource-bundle.v1", "fixed"),
                ResourceSpec("screens", "screen", "pacman.screen-collection.v1", "fixed", parameters()),
            ),
        )
        decoded = decode_resources(source, profile, revision)
        details = inspection_details(decoded)
        self.assertEqual((details["screens"], details["text_messages"]), (1, 3))
        raw = decoded.as_dict()
        content = {key: value for key, value in raw["resources"][0].items() if key not in ("id", "kind", "codec")}
        edited_content = set_text_field(content, "pause", "c")
        raw["resources"][0].update(edited_content)
        parsed = parse_resource_document(raw, profile, revision)
        rebuilt, report = build_resources(source, parsed, profile, revision)
        self.assertEqual(rebuilt[34:36], bytes((67, 32)))
        self.assertEqual(len(report["resources"][0]["ranges"]), 9)


if __name__ == "__main__":
    unittest.main()
