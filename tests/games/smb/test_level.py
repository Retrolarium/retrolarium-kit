from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from retrolarium_kit.authoring import GameProfile, ResourceSpec, RevisionSpec
from retrolarium_kit.game_profiles import packaged_game_profiles
from retrolarium_kit.nes_resources import build_resources, decode_resources, parse_resource_document
from retrolarium_kit.smb_level import decode_levels, encode_levels, positioned_area_objects, positioned_enemy_records
from retrolarium_kit.smb_level_render import render_level_scene


PARAMETERS = {
    "area_file_offset": 32,
    "area_size": 12,
    "enemy_file_offset": 48,
    "enemy_size": 6,
    "stream_names": ["ground_1", "ground_2"],
    "area_capacities": [7, 5],
    "enemy_capacities": [5, 1],
}


def fixture() -> tuple[bytes, GameProfile, RevisionSpec]:
    header = b"NES\x1a" + bytes((1, 1, 0, 0)) + bytes(8)
    body = bytearray(0x6000)
    body[16:28] = bytes((0x6B, 0x92, 0x34, 0x85, 0xFD, 0xAA, 0xBB,
                         0x10, 0x23, 0xDF, 0x01, 0xFD))
    body[32:38] = bytes((0x31, 0xC5, 0xFF, 0xCC, 0xDD, 0xFF))
    image = header + bytes(body)
    revision = RevisionSpec(
        "probe", "SMB level probe", len(image), hashlib.sha1(image).hexdigest(),
        hashlib.sha256(image).hexdigest(), "ines", 0,
    )
    profile = GameProfile(
        "smb-level-probe", 1, "SMB level probe", "nes", ("levels",), (revision,),
        (
            ResourceSpec("bundle", "resource-bundle", "nes.resource-bundle.v1", "fixed"),
            ResourceSpec("levels", "level", "smb.levels.v1", "fixed", PARAMETERS),
        ),
    )
    return image, profile, revision


class SmbLevelTests(unittest.TestCase):
    def test_codec_round_trips_headers_records_and_padding(self) -> None:
        area = bytes((0x6B, 0x92, 0x34, 0x85, 0xFD, 0xAA, 0xBB,
                      0x10, 0x23, 0xDF, 0x01, 0xFD))
        enemy = bytes((0x31, 0xC5, 0xFF, 0xCC, 0xDD, 0xFF))
        value = decode_levels(area, enemy, PARAMETERS)
        first = value["areas"][0]
        self.assertEqual(first["header"]["timer_setting"], 1)
        self.assertEqual(first["header"]["entrance_control"], 5)
        self.assertEqual(first["objects"][0], {
            "column": 3, "row": 4, "page_advance": True, "object_control": 5,
        })
        self.assertEqual(first["enemies"][0]["kind"], "enemy")
        self.assertTrue(first["enemies"][0]["hard_mode"])
        self.assertEqual(first["area_padding"], [0xAA, 0xBB])
        self.assertEqual(first["enemy_padding"], [0xCC, 0xDD])
        self.assertEqual(encode_levels(value, PARAMETERS), (area, enemy))

    def test_implicit_terminators_and_page_positioning_are_exact(self) -> None:
        parameters = {"stream_names": ["ground_9"], "area_capacities": [4], "enemy_capacities": [2]}
        value = decode_levels(bytes((0, 0, 0x1D, 0x03)), bytes((0x2F, 0x07)), parameters)
        stream = value["areas"][0]
        self.assertTrue(stream["area_implicit_terminator"])
        self.assertTrue(stream["enemy_implicit_terminator"])
        self.assertEqual(encode_levels(value, parameters),
                         (bytes((0, 0, 0x1D, 0x03)), bytes((0x2F, 0x07))))
        self.assertEqual(positioned_area_objects([
            {"column": 2, "row": 1, "page_advance": True, "object_control": 0},
            {"column": 3, "row": 13, "page_advance": False, "object_control": 5},
        ])[-1]["x"], 83)
        self.assertEqual(positioned_enemy_records([
            {"kind": "page", "column": 0, "row": 15, "page_advance": False, "hard_mode": False, "object_or_page": 4},
            {"kind": "enemy", "column": 3, "row": 2, "page_advance": False, "hard_mode": False, "object_or_page": 1},
        ])[-1]["x"], 67)

    def test_bundle_edits_both_ranges_transactionally(self) -> None:
        image, profile, revision = fixture()
        document = decode_resources(image, profile, revision)
        exact, report = build_resources(image, document, profile, revision)
        self.assertEqual(exact, image)
        self.assertEqual(report["changed_bytes"], 0)
        raw = copy.deepcopy(document.as_dict())
        levels = raw["resources"][0]
        levels["areas"][0]["objects"][0]["object_control"] = 6
        levels["areas"][0]["enemies"][0]["object_or_page"] = 4
        parsed = parse_resource_document(raw, profile, revision)
        edited, report = build_resources(image, parsed, profile, revision)
        self.assertEqual(edited[35], 0x86)
        self.assertEqual(edited[49], 0xC4)
        self.assertEqual(report["changed_bytes"], 2)

    def test_invalid_kind_capacity_and_identity_are_rejected(self) -> None:
        image, profile, revision = fixture()
        raw = decode_resources(image, profile, revision).as_dict()
        raw["resources"][0]["areas"][0]["enemies"][0]["kind"] = "page"
        with self.assertRaisesRegex(Exception, "kind does not match"):
            parse_resource_document(raw, profile, revision)
        raw = decode_resources(image, profile, revision).as_dict()
        raw["resources"][0]["areas"][1]["objects"].append({
            "column": 1, "row": 1, "page_advance": False, "object_control": 1,
        })
        with self.assertRaisesRegex(Exception, "exceeds"):
            parse_resource_document(raw, profile, revision)

    def test_packaged_ju_profile_exposes_all_open_source_streams(self) -> None:
        profile = packaged_game_profiles().get("smb1")
        levels = next(item for item in profile.resources if item.kind == "level")
        self.assertEqual(levels.codec, "smb.levels.v1")
        self.assertEqual(len(levels.parameters["stream_names"]), 34)
        self.assertEqual(sum(levels.parameters["area_capacities"]), 3373)
        self.assertEqual(sum(levels.parameters["enemy_capacities"]), 1087)

    def test_procedural_renderer_reconstructs_terrain_objects_and_pages(self) -> None:
        header = {
            "timer_setting": 0, "entrance_control": 0,
            "foreground_or_color": 0, "area_style": 0,
            "background_scenery": 0, "terrain_control": 4,
        }
        objects = [
            {"column": 3, "row": 4, "page_advance": False, "object_control": 5},
            {"column": 0, "row": 12, "page_advance": True, "object_control": 2},
            {"column": 4, "row": 13, "page_advance": False, "object_control": 0x41},
        ]
        enemies = [
            {"kind": "page", "column": 0, "row": 15, "page_advance": False,
             "hard_mode": False, "object_or_page": 2},
            {"kind": "enemy", "column": 6, "row": 5, "page_advance": False,
             "hard_mode": False, "object_or_page": 6},
        ]
        scene = render_level_scene("ground_1", header, objects, enemies)
        self.assertEqual(scene.width, 42)
        self.assertEqual(scene.metatiles[0][0], 0x54)
        self.assertEqual(scene.metatiles[0][4], 0)
        self.assertEqual(scene.metatiles[3][4], 0x56)
        self.assertEqual(scene.metatiles[16][11], 0)
        self.assertEqual(scene.metatiles[20][0], 0x24)
        self.assertEqual(scene.metatiles[20][10], 0x61)
        self.assertEqual(scene.enemies[0]["x"], 38)


if __name__ == "__main__":
    unittest.main()
