from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from retrolarium_kit.authoring import GameProfile, ResourceSpec, RevisionSpec
from retrolarium_kit.game_profiles import packaged_game_profiles
from retrolarium_kit.nes_resources import (
    build_resources,
    decode_resources,
    parse_resource_document,
)
from retrolarium_kit.smb_world import decode_world, encode_world


PARAMETERS = {
    "routing_file_offset": 32,
    "routing_size": 4,
    "world_lengths": [2, 2],
    "physics_file_offset": 64,
    "physics_size": 5,
    "physics_tables": [
        {"name": "gravity", "length": 2, "signed": False},
        {"name": "speed", "length": 3, "signed": True},
    ],
}


def fixture() -> tuple[bytes, GameProfile, RevisionSpec]:
    header = b"NES\x1a" + bytes((2, 1, 0, 0)) + bytes(8)
    prg = bytearray(0x8000)
    prg[16:20] = bytes((0x25, 0xC1, 0x43, 0x7F))
    prg[48:53] = bytes((0x20, 0x70, 0xFC, 0x00, 0x28))
    image = header + bytes(prg) + bytes(0x2000)
    revision = RevisionSpec(
        "probe-v1", "SMB world probe", len(image),
        hashlib.sha1(image).hexdigest(), hashlib.sha256(image).hexdigest(),
        "ines", 0,
    )
    profile = GameProfile(
        "smb-world-probe", 1, "SMB world probe", "nes", ("world",),
        (revision,),
        (
            ResourceSpec(
                "bundle", "resource-bundle", "nes.resource-bundle.v1", "fixed"
            ),
            ResourceSpec("world", "world", "smb.world.v1", "fixed", PARAMETERS),
        ),
    )
    return image, profile, revision


class SmbWorldTests(unittest.TestCase):
    def test_codec_round_trips_and_exposes_signed_values(self) -> None:
        value = decode_world(
            bytes((0x25, 0xC1, 0x43, 0x7F)),
            bytes((0x20, 0x70, 0xFC, 0x00, 0x28)),
            PARAMETERS,
        )
        self.assertEqual(value["area_types"], ["water", "ground", "underground", "castle"])
        self.assertEqual(value["worlds"][0]["areas"][0]["area_type"], 1)
        self.assertTrue(value["worlds"][0]["areas"][1]["alternate_bit"])
        self.assertEqual(value["physics_tables"][1]["values"], [-4, 0, 40])
        self.assertEqual(
            encode_world(value, PARAMETERS),
            (bytes((0x25, 0xC1, 0x43, 0x7F)), bytes((0x20, 0x70, 0xFC, 0x00, 0x28))),
        )

    def test_bundle_updates_both_ranges_transactionally(self) -> None:
        image, profile, revision = fixture()
        document = decode_resources(image, profile, revision)
        exact, report = build_resources(image, document, profile, revision)
        self.assertEqual(exact, image)
        self.assertEqual(report["changed_bytes"], 0)
        self.assertEqual(report["resources"][0]["size"], 9)

        raw = copy.deepcopy(document.as_dict())
        world = raw["resources"][0]
        world["worlds"][0]["areas"][0]["area_index"] = 7
        world["physics_tables"][1]["values"][0] = -5
        parsed = parse_resource_document(raw, profile, revision)
        edited, edited_report = build_resources(image, parsed, profile, revision)
        self.assertEqual(edited[32], 0x27)
        self.assertEqual(edited[64 + 2], 0xFB)
        self.assertEqual(edited_report["changed_bytes"], 2)

    def test_invalid_shape_and_ranges_are_rejected(self) -> None:
        image, profile, revision = fixture()
        raw = decode_resources(image, profile, revision).as_dict()
        raw["resources"][0]["physics_tables"][1]["values"][0] = -129
        with self.assertRaisesRegex(Exception, "-128..127"):
            parse_resource_document(raw, profile, revision)

        raw = decode_resources(image, profile, revision).as_dict()
        raw["resources"][0]["worlds"][0]["areas"].pop()
        with self.assertRaisesRegex(Exception, "must contain 2 routes"):
            parse_resource_document(raw, profile, revision)

    def test_packaged_ju_profile_matches_open_source_build_identity(self) -> None:
        profile = packaged_game_profiles().get("smb1")
        self.assertEqual(profile.revisions[0].revision_id, "ju")
        self.assertEqual(
            profile.revisions[0].sha256,
            "f61548fdf1670cffefcc4f0b7bdcdd9eaba0c226e3b74f8666071496988248de",
        )
        world = next(item for item in profile.resources if item.kind == "world")
        self.assertEqual(world.codec, "smb.world.v1")
        self.assertEqual(world.parameters["world_lengths"], [5, 5, 4, 5, 4, 4, 5, 4])


if __name__ == "__main__":
    unittest.main()
