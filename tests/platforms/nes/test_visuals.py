from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from retrolarium_kit.core.authoring import GameProfile, ResourceSpec, RevisionSpec
from retrolarium_kit.core.game_profiles import packaged_game_profiles
from retrolarium_kit.platform.nes.resources import (
    build_resources,
    decode_resources,
    parse_resource_document,
)
from retrolarium_kit.platform.nes.visuals import (
    decode_metatiles,
    decode_palette_packets,
    encode_metatiles,
    encode_palette_packets,
)


METATILE_PARAMETERS = {
    "file_offset": 16,
    "size": 8,
    "group_names": ["background", "foreground"],
    "group_sizes": [1, 1],
    "tile_base": 256,
}
PALETTE_PARAMETERS = {
    "file_offset": 24,
    "size": 16,
    "palette_model": "nes",
    "packet_names": ["ground", "night"],
    "packet_capacities": [8, 8],
    "terminator": 0,
    "day_background": 0x22,
    "night_background": 0x0F,
}


def fixture() -> tuple[bytes, GameProfile, RevisionSpec]:
    header = b"NES\x1a" + bytes((1, 1, 0, 0)) + bytes(8)
    body = bytearray(0x6000)
    body[0:8] = bytes((1, 2, 3, 4, 5, 6, 7, 8))
    body[8:24] = bytes((0x3F, 0x00, 0x04, 0x0F, 0x16, 0x27, 0x18, 0x00,
                        0x3F, 0x00, 0x04, 0x0F, 0x01, 0x11, 0x21, 0x00))
    image = header + bytes(body)
    revision = RevisionSpec(
        "probe", "NES visual probe", len(image), hashlib.sha1(image).hexdigest(),
        hashlib.sha256(image).hexdigest(), "ines", 0,
    )
    profile = GameProfile(
        "nes-visual-probe", 1, "NES visual probe", "nes", ("graphics",),
        (revision,),
        (
            ResourceSpec("bundle", "resource-bundle", "nes.resource-bundle.v1", "fixed"),
            ResourceSpec("metatiles", "metatile", "nes.metatile-2x2.v1", "fixed",
                         METATILE_PARAMETERS),
            ResourceSpec("palettes", "palette", "nes.ppu-palette-packets.v1", "fixed",
                         PALETTE_PARAMETERS),
        ),
    )
    return image, profile, revision


class NesVisualTests(unittest.TestCase):
    def test_metatile_codec_preserves_grouped_four_tile_records(self) -> None:
        payload = bytes((1, 2, 3, 4, 5, 6, 7, 8))
        value = decode_metatiles(payload, METATILE_PARAMETERS)
        self.assertEqual(value["group_names"], ["background", "foreground"])
        self.assertEqual(value["group_sizes"], [1, 1])
        self.assertEqual(value["tile_base"], 256)
        self.assertEqual(value["records"], [[1, 2, 3, 4], [5, 6, 7, 8]])
        self.assertEqual(encode_metatiles(value, METATILE_PARAMETERS), payload)

    def test_palette_packet_codec_preserves_ppu_headers_and_names(self) -> None:
        payload = bytes((0x3F, 0x00, 0x04, 0x0F, 0x16, 0x27, 0x18, 0x00,
                         0x3F, 0x00, 0x04, 0x0F, 0x01, 0x11, 0x21, 0x00))
        value = decode_palette_packets(payload, PALETTE_PARAMETERS)
        self.assertEqual(value["day_background"], 0x22)
        self.assertEqual(value["night_background"], 0x0F)
        self.assertEqual(value["packets"][0], {
            "name": "ground", "address": 0x3F00, "vertical": False,
            "repeat": False, "length": 4, "values": [0x0F, 0x16, 0x27, 0x18],
        })
        self.assertEqual(encode_palette_packets(value, PALETTE_PARAMETERS), payload)

    def test_bundle_round_trips_and_edits_both_visual_ranges(self) -> None:
        image, profile, revision = fixture()
        document = decode_resources(image, profile, revision)
        raw = document.as_dict()
        self.assertEqual(raw["resources"][0]["records"][1], [5, 6, 7, 8])
        self.assertEqual(raw["resources"][1]["packets"][0]["name"], "ground")
        self.assertEqual(raw["resources"][1]["palette_model"], "nes")
        exact, report = build_resources(image, document, profile, revision)
        self.assertEqual(exact, image)
        self.assertEqual(report["changed_bytes"], 0)

        edited = copy.deepcopy(raw)
        edited["resources"][0]["records"][1][2] = 0x44
        edited["resources"][1]["packets"][0]["values"][1] = 0x2A
        parsed = parse_resource_document(edited, profile, revision)
        rebuilt, report = build_resources(image, parsed, profile, revision)
        self.assertEqual(rebuilt[22], 0x44)
        self.assertEqual(rebuilt[28], 0x2A)
        self.assertEqual(report["changed_bytes"], 2)

    def test_invalid_record_and_palette_color_are_rejected(self) -> None:
        image, profile, revision = fixture()
        raw = decode_resources(image, profile, revision).as_dict()
        invalid = copy.deepcopy(raw)
        invalid["resources"][0]["records"][0].pop()
        with self.assertRaisesRegex(Exception, "four tile indexes"):
            parse_resource_document(invalid, profile, revision)
        invalid = copy.deepcopy(raw)
        invalid["resources"][1]["packets"][0]["values"][0] = 0x40
        with self.assertRaisesRegex(Exception, "integer in 0..63"):
            parse_resource_document(invalid, profile, revision)
        invalid = copy.deepcopy(raw)
        invalid["resources"][1]["palette_model"] = "rp2c04_0004"
        with self.assertRaisesRegex(Exception, "palette_model differs"):
            parse_resource_document(invalid, profile, revision)

    def test_packaged_smb_profile_declares_exact_ju_visual_layout(self) -> None:
        profile = packaged_game_profiles().get("smb1")
        metatiles = profile.resource("background-metatiles")
        palettes = profile.resource("area-palette-packets")
        self.assertEqual(metatiles.parameters["group_sizes"], [39, 46, 10, 6])
        self.assertEqual(metatiles.parameters["size"], 404)
        self.assertEqual(palettes.parameters["packet_capacities"], [36, 36, 36, 36, 8, 8, 8, 8])
        self.assertEqual(palettes.parameters["size"], 176)


if __name__ == "__main__":
    unittest.main()
