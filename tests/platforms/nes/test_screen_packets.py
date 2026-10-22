from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from retrolarium_kit.core.authoring import GameProfile, ResourceSpec, RevisionSpec
from retrolarium_kit.platform.nes.resources import (
    build_resources,
    decode_resources,
    inspection_details,
    parse_resource_document,
)
from retrolarium_kit.platform.nes.screen_packets import (
    decode_screen_packets,
    encode_screen_packets,
)


PARAMETERS = {
    "terminator": 0,
    "width": 32,
    "height": 30,
    "base_address": 0x2000,
    "attribute_address": 0x23C0,
    "attribute_bytes": 64,
    "fill_tile": 0x24,
    "fill_attribute": 0,
    "encoding": {
        "characters": " ABC",
        "codes": [0x24, 1, 2, 3],
        "case_fold": "upper",
        "padding": 0x24,
    },
    "text_fields": [
        {
            "key": "logo",
            "label": "Logo",
            "screen": "title",
            "address": 0x2080,
            "width": 3,
        },
    ],
    "screens": [
        {"key": "title", "label": "Title", "file_offset": 16, "size": 13},
        {"key": "menu", "label": "Menu", "file_offset": 40, "size": 6},
    ],
}
TITLE = bytes(
    (
        0x20,
        0x80,
        0x03,
        1,
        2,
        3,
        0x23,
        0xC0,
        0x42,
        0xAA,
        0,
        0xFF,
        0xEE,
    )
)
MENU = bytes((0x20, 0x85, 0x82, 4, 5, 0))


def fixture() -> tuple[bytes, GameProfile, RevisionSpec]:
    image = bytearray(b"NES\x1a" + bytes((1, 1, 0, 0)) + bytes(8) + bytes(0x6000))
    image[16 : 16 + len(TITLE)] = TITLE
    image[40 : 40 + len(MENU)] = MENU
    source = bytes(image)
    revision = RevisionSpec(
        "probe",
        "PPU screen probe",
        len(source),
        hashlib.sha1(source).hexdigest(),
        hashlib.sha256(source).hexdigest(),
        "ines",
        0,
    )
    profile = GameProfile(
        "ppu-screen-probe",
        1,
        "PPU screen probe",
        "nes",
        ("screens",),
        (revision,),
        (
            ResourceSpec("bundle", "resource-bundle", "nes.resource-bundle.v1", "fixed"),
            ResourceSpec(
                "screens",
                "screen",
                "nes.ppu-screen-packets.v1",
                "fixed",
                PARAMETERS,
            ),
        ),
    )
    return source, profile, revision


POOL = {
    "offset_table": 60,
    "offset_base": 16,
    "entries": [
        {"screen": "title", "command": 0},
        {"screen": "menu", "command": 0},
        {"screen": "title", "command": 1},
    ],
    "growable_fields": ["logo"],
}


def pooled_fixture() -> tuple[bytes, GameProfile, RevisionSpec]:
    """Both screens back to back behind an engine offset table."""
    _source, profile, _revision = fixture()
    image = bytearray(b"NES" + bytes((1, 1, 0, 0)) + bytes(8) + bytes(0x6000))
    image[16 : 16 + len(TITLE)] = TITLE
    image[29 : 29 + len(MENU)] = MENU
    image[60:63] = bytes((0, 13, 6))
    source = bytes(image)
    revision = RevisionSpec(
        "probe",
        "PPU screen probe",
        len(source),
        hashlib.sha1(source).hexdigest(),
        hashlib.sha256(source).hexdigest(),
        "ines",
        0,
    )
    parameters = copy.deepcopy(PARAMETERS)
    parameters["screens"][1]["file_offset"] = 29
    parameters["packet_pool"] = POOL
    profile = GameProfile(
        profile.game_id,
        profile.profile_version,
        profile.name,
        profile.platform,
        profile.capabilities,
        (revision,),
        (
            profile.resources[0],
            ResourceSpec("screens", "screen", "nes.ppu-screen-packets.v1", "fixed", parameters),
        ),
    )
    return source, profile, revision


def grow_logo(raw: dict, text: list[int]) -> None:
    resource = raw["resources"][0]
    command = resource["screens"][0]["commands"][0]
    command["length"] = len(text)
    command["values"] = list(text)
    resource["screens"][0]["tiles"][4][0 : len(text)] = list(text)
    resource["text_fields"][0]["width"] = len(text)


class NesScreenPacketTests(unittest.TestCase):
    def test_pooled_screens_grow_text_and_follow_the_offset_table(self) -> None:
        source, profile, revision = pooled_fixture()
        document = decode_resources(source, profile, revision)
        resource = document.resources[0]
        self.assertTrue(resource["capacity_pool"])
        self.assertEqual(resource["growable_fields"], ["logo"])
        exact, report = build_resources(source, document, profile, revision)
        self.assertEqual(exact, source)
        self.assertEqual(report["changed_bytes"], 0)

        # Two more letters take the title's trailing bytes; the menu stays
        # put and the second title entry follows its command.
        raw = copy.deepcopy(document.as_dict())
        grow_logo(raw, [1, 2, 3, 1, 2])
        edited, _report = build_resources(source, parse_resource_document(raw, profile, revision), profile, revision)
        self.assertEqual(
            edited[16:29],
            bytes(
                (
                    0x20,
                    0x80,
                    0x05,
                    1,
                    2,
                    3,
                    1,
                    2,
                    0x23,
                    0xC0,
                    0x42,
                    0xAA,
                    0,
                )
            ),
        )
        self.assertEqual(edited[29:35], MENU)
        self.assertEqual(edited[60:63], bytes((0, 13, 8)))

        # A shorter title frees bytes after the menu.
        raw = copy.deepcopy(document.as_dict())
        grow_logo(raw, [1, 2])
        raw["resources"][0]["screens"][0]["tiles"][4][2] = 0x24
        edited, _report = build_resources(source, parse_resource_document(raw, profile, revision), profile, revision)
        self.assertEqual(edited[60:63], bytes((0, 12, 5)))
        self.assertEqual(edited[28:34], MENU)
        self.assertEqual(edited[34:35], bytes((0,)))

        raw = copy.deepcopy(document.as_dict())
        grow_logo(raw, [1, 2, 3, 1, 2, 3])
        with self.assertRaisesRegex(Exception, "need 20 bytes; the ROM keeps 19"):
            parse_resource_document(raw, profile, revision)

        # A field grows only as the whole write it starts.
        raw = copy.deepcopy(document.as_dict())
        raw["resources"][0]["text_fields"][0]["width"] = 4
        with self.assertRaisesRegex(Exception, "only grow as the whole write"):
            parse_resource_document(raw, profile, revision)

    def test_codec_preserves_commands_flags_and_trailing_bytes(self) -> None:
        source, _profile, _revision = fixture()
        value = decode_screen_packets(source, PARAMETERS)
        self.assertEqual([item["key"] for item in value["screens"]], ["title", "menu"])
        self.assertEqual(
            value["screens"][0]["commands"][0],
            {
                "address": 0x2080,
                "vertical": False,
                "repeat": False,
                "length": 3,
                "values": [1, 2, 3],
            },
        )
        self.assertEqual(
            value["screens"][0]["commands"][1],
            {
                "address": 0x23C0,
                "vertical": False,
                "repeat": True,
                "length": 2,
                "values": [0xAA],
            },
        )
        self.assertEqual(value["screens"][0]["trailing"], [0xFF, 0xEE])
        self.assertEqual(value["screens"][0]["tiles"][4][0:3], [1, 2, 3])
        self.assertEqual(value["screens"][0]["attributes"][0:2], [0xAA, 0xAA])
        self.assertEqual(value["encoding"], PARAMETERS["encoding"])
        self.assertEqual(
            value["text_fields"],
            [
                {
                    "key": "logo",
                    "label": "Logo",
                    "screen": "title",
                    "start": 0x80,
                    "width": 3,
                }
            ],
        )
        self.assertTrue(value["screens"][1]["commands"][0]["vertical"])
        encoded = encode_screen_packets(value, PARAMETERS)
        self.assertEqual(encoded, ((16, TITLE), (40, MENU)))

    def test_resource_pipeline_edits_disjoint_screens_transactionally(self) -> None:
        source, profile, revision = fixture()
        document = decode_resources(source, profile, revision)
        self.assertEqual(inspection_details(document)["screens"], 2)
        self.assertEqual(inspection_details(document)["text_messages"], 1)
        exact, report = build_resources(source, document, profile, revision)
        self.assertEqual(exact, source)
        self.assertEqual(report["changed_bytes"], 0)
        self.assertEqual(len(report["resources"][0]["ranges"]), 2)

        raw = copy.deepcopy(document.as_dict())
        raw["resources"][0]["screens"][1]["tiles"][4][5] = 9
        edited, report = build_resources(source, parse_resource_document(raw, profile, revision), profile, revision)
        self.assertEqual(edited[43], 9)
        self.assertEqual(edited[16 : 16 + len(TITLE)], TITLE)
        self.assertEqual(report["changed_bytes"], 1)

    def test_resource_pipeline_rebuilds_embedded_text_from_screen_tiles(self) -> None:
        source, profile, revision = fixture()
        raw = copy.deepcopy(decode_resources(source, profile, revision).as_dict())
        screen = raw["resources"][0]["screens"][0]
        screen["tiles"][4][0:3] = [3, 1, 2]

        edited, report = build_resources(source, parse_resource_document(raw, profile, revision), profile, revision)

        self.assertEqual(edited[19:22], bytes((3, 1, 2)))
        self.assertEqual(report["changed_bytes"], 3)
        reopened = decode_screen_packets(edited, PARAMETERS)
        self.assertEqual(reopened["screens"][0]["tiles"][4][0:3], [3, 1, 2])
        self.assertEqual(reopened["text_fields"], raw["resources"][0]["text_fields"])

    def test_rejects_malformed_streams_and_profile_layouts(self) -> None:
        source, profile, revision = fixture()
        missing_terminator = bytearray(TITLE)
        missing_terminator[10] = 1
        with self.assertRaisesRegex(Exception, "outside PPU memory|no terminator"):
            decode_screen_packets(
                bytes(missing_terminator),
                {
                    **{key: value for key, value in PARAMETERS.items() if key != "screens"},
                    "screens": [{"key": "title", "label": "Title", "file_offset": 0, "size": len(missing_terminator)}],
                },
            )

        raw = copy.deepcopy(decode_resources(source, profile, revision).as_dict())
        raw["resources"][0]["screens"][0]["commands"][0]["values"].pop()
        with self.assertRaisesRegex(Exception, "values has the wrong length"):
            parse_resource_document(raw, profile, revision)

        raw = copy.deepcopy(decode_resources(source, profile, revision).as_dict())
        raw["resources"][0]["screens"][0]["attributes"][0] = 0x55
        with self.assertRaisesRegex(Exception, "cannot be represented"):
            parse_resource_document(raw, profile, revision)

        raw = copy.deepcopy(decode_resources(source, profile, revision).as_dict())
        raw["resources"][0]["screens"][0]["tiles"][4][0] = 9
        with self.assertRaisesRegex(Exception, "unmapped glyph"):
            parse_resource_document(raw, profile, revision)

        overlapping = copy.deepcopy(PARAMETERS)
        overlapping["screens"][1]["file_offset"] = 20
        invalid_profile = GameProfile(
            profile.game_id,
            profile.profile_version,
            profile.name,
            profile.platform,
            profile.capabilities,
            profile.revisions,
            (
                profile.resources[0],
                ResourceSpec(
                    "screens",
                    "screen",
                    "nes.ppu-screen-packets.v1",
                    "fixed",
                    overlapping,
                ),
            ),
        )
        with self.assertRaisesRegex(Exception, "overlaps"):
            decode_resources(source, invalid_profile, revision)


if __name__ == "__main__":
    unittest.main()
