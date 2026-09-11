from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from retrolarium_kit.authoring import GameProfile, ResourceSpec, RevisionSpec
from retrolarium_kit.game_profiles import packaged_game_profiles
from retrolarium_kit.games import build_game, extract_game, inspect_game, validate_game
from retrolarium_kit.nes_resources import (
    NesResourceError,
    build_resources,
    decode_resources,
    parse_resource_document,
)


def fixture_image() -> bytes:
    header = b"NES\x1a" + bytes((1, 1, 0, 0)) + bytes(8)
    prg = bytearray(0x4000)
    prg[:4] = bytes((0x0F, 0x01, 0x11, 0x21))
    prg[4:10] = bytes((1, 2, 3, 4, 0x55, 0xAA))
    chr_data = bytes((0x80,)) + bytes(15)
    return header + bytes(prg) + chr_data + bytes(0x2000 - 16)


def fixture_profile(image: bytes) -> tuple[GameProfile, RevisionSpec]:
    revision = RevisionSpec(
        "probe-v1",
        "NES resource probe",
        len(image),
        hashlib.sha1(image).hexdigest(),
        hashlib.sha256(image).hexdigest(),
        "ines",
        0,
    )
    resources = (
        ResourceSpec(
            "authoring-bundle", "resource-bundle", "nes.resource-bundle.v1", "fixed"
        ),
        ResourceSpec(
            "palette",
            "palette",
            "nes.palette-table.v1",
            "fixed",
            {"file_offset": 16, "size": 4, "entries": 4},
        ),
        ResourceSpec(
            "screen",
            "screen",
            "nes.nametable.v1",
            "fixed",
            {
                "file_offset": 20,
                "size": 6,
                "width": 2,
                "height": 2,
                "attribute_bytes": 2,
            },
        ),
        ResourceSpec(
            "graphics",
            "graphics",
            "nes.chr-2bpp.v1",
            "fixed",
            {"file_offset": 0x4010, "size": 16},
        ),
    )
    return (
        GameProfile(
            "nes-resource-probe",
            1,
            "NES resource probe",
            "nes",
            ("screens", "palettes", "graphics"),
            (revision,),
            resources,
        ),
        revision,
    )


def write_profile(path: Path, profile: GameProfile) -> None:
    path.write_text(
        json.dumps(
            {
                "schema": "retrolarium.game-profile",
                "schema_version": 1,
                "id": profile.game_id,
                "profile_version": profile.profile_version,
                "name": profile.name,
                "platform": profile.platform,
                "capabilities": list(profile.capabilities),
                "revisions": [
                    {
                        "id": item.revision_id,
                        "name": item.name,
                        "size": item.size,
                        "sha1": item.sha1,
                        "sha256": item.sha256,
                        "container": item.container,
                        "mapper": item.mapper,
                    }
                    for item in profile.revisions
                ],
                "resources": [
                    {
                        "id": item.resource_id,
                        "kind": item.kind,
                        "codec": item.codec,
                        "capacity_policy": item.capacity_policy,
                        "parameters": dict(item.parameters),
                    }
                    for item in profile.resources
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )


class NesResourceTests(unittest.TestCase):
    def test_packaged_honkeypong_profile_has_open_fixture_identity(self) -> None:
        profile = packaged_game_profiles().get("honkeypong")
        self.assertEqual(profile.revisions[0].sha256, "cce7bf2f71eb9e63de0dba15098679dde06a1da9d7553f2319aa60ea57a1193b")
        self.assertEqual(profile.revisions[0].mapper, 0)
        self.assertEqual(
            [item.codec for item in profile.resources],
            [
                "nes.resource-bundle.v1",
                "nes.palette-table.v1",
                "nes.palette-table.v1",
                "nes.palette-table.v1",
                "nes.nametable.v1",
                "nes.chr-2bpp.v1",
            ],
        )

    def test_typed_resources_round_trip_and_edit_transactionally(self) -> None:
        image = fixture_image()
        profile, revision = fixture_profile(image)
        document = decode_resources(image, profile, revision)
        raw = document.as_dict()
        self.assertEqual(raw["resources"][0]["values"], [0x0F, 1, 0x11, 0x21])
        self.assertEqual(raw["resources"][1]["tiles"], [[1, 2], [3, 4]])
        self.assertEqual(raw["resources"][2]["tiles"][0][0][0], 1)

        exact, exact_report = build_resources(image, document, profile, revision)
        self.assertEqual(exact, image)
        self.assertEqual(exact_report["changed_bytes"], 0)

        edited = copy.deepcopy(raw)
        edited["resources"][0]["values"][1] = 0x02
        edited["resources"][1]["tiles"][1][1] = 9
        edited["resources"][2]["tiles"][0][0][0] = 3
        parsed = parse_resource_document(edited, profile, revision)
        rebuilt, report = build_resources(image, parsed, profile, revision)
        self.assertEqual(rebuilt[17], 0x02)
        self.assertEqual(rebuilt[23], 9)
        self.assertEqual(rebuilt[0x4010], 0x80)
        self.assertEqual(rebuilt[0x4018], 0x80)
        self.assertEqual(report["changed_bytes"], 3)

    def test_document_rejects_missing_resources_and_invalid_chr_pixels(self) -> None:
        image = fixture_image()
        profile, revision = fixture_profile(image)
        raw = decode_resources(image, profile, revision).as_dict()
        missing = copy.deepcopy(raw)
        missing["resources"].pop()
        with self.assertRaisesRegex(NesResourceError, "wrong resource count"):
            parse_resource_document(missing, profile, revision)
        invalid = copy.deepcopy(raw)
        invalid["resources"][2]["tiles"][0][0][0] = 4
        with self.assertRaisesRegex(NesResourceError, "must be <= 3"):
            parse_resource_document(invalid, profile, revision)
        extra = copy.deepcopy(raw)
        extra["resources"][0]["undocumented"] = True
        with self.assertRaisesRegex(NesResourceError, "fields differ"):
            parse_resource_document(extra, profile, revision)

    def test_public_operations_route_through_second_adapter(self) -> None:
        image = fixture_image()
        profile, _revision = fixture_profile(image)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rom = root / "probe.nes"
            profile_path = root / "profile.json"
            document_path = root / "resources.json"
            output = root / "edited.nes"
            rom.write_bytes(image)
            write_profile(profile_path, profile)
            inspected = inspect_game(rom, custom_profile=profile_path)
            self.assertEqual(inspected["details"]["screens"], 1)
            self.assertEqual(inspected["details"]["chr_tiles"], 1)
            extracted = extract_game(
                rom, document_path, custom_profile=profile_path
            )
            self.assertEqual(extracted["resource"], "resource-bundle")
            self.assertEqual(extracted["resource_count"], 3)
            self.assertEqual(
                validate_game(rom, document_path, custom_profile=profile_path)[
                    "status"
                ],
                "valid",
            )
            result = build_game(
                rom, document_path, output, custom_profile=profile_path
            )
            self.assertEqual(result["schema"], "retrolarium.nes-resource-build")
            self.assertEqual(output.read_bytes(), image)


if __name__ == "__main__":
    unittest.main()
