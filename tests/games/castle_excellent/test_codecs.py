from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from retrolarium_kit.authoring import (
    AuthoringError,
    GameProfile,
    ResourceSpec,
    RevisionSpec,
)
from retrolarium_kit.castle import (
    CastleError,
    PrefixEntry,
    build_levels,
    decode_levels,
    encode_prefix_stream,
    parse_level_document,
    validate_prefix_codebook,
)
from retrolarium_kit.game_profiles import packaged_game_profiles
from retrolarium_kit.games import build_game, extract_game, inspect_game, validate_game


ORACLE = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "synthetic"
    / "nes"
    / "castle_codec_probe"
    / "oracle.json"
)


def put_cpu(prg: bytearray, address: int, payload: bytes) -> None:
    start = address - 0x8000
    prg[start:start + len(payload)] = payload


def synthetic_image() -> bytes:
    header = b"NES\x1a" + bytes((2, 4, 0x31, 0)) + bytes(8)
    prg = bytearray(0x8000)
    codebook = (
        bytes((0x10,)) + b"0\0"
        + bytes((0x20,)) + b"10\0"
        + bytes((0x30,)) + b"110\0"
        + bytes((0x40,)) + b"111\0"
        + b"\xff"
    )
    put_cpu(prg, 0x8000, codebook)
    put_cpu(prg, 0x8100, b"\x00\x00\x01\x00\x00\x00\x01\x00")
    object_stream = bytes((1, 1, 1, 0xFF, 0xFF, 2, 0, 1, 3, 1, 0, 0xFF, 0xFF))
    put_cpu(prg, 0x8200, object_stream + b"\xff" * (0x20 - len(object_stream)))
    put_cpu(prg, 0x8240, bytes((1, 0, 2, 0)))
    put_cpu(prg, 0x8250, bytes((0, 1, 1, 2, 2)))
    put_cpu(prg, 0x8260, bytes((0, 1, 1, 1, 1)))
    chr_data = bytearray(b"\xff" * 0x8000)
    chr_data[2 * 0x2000:2 * 0x2000 + 2] = bytes((0x59, 0x0F))
    chr_data[3 * 0x2000:3 * 0x2000 + 2] = bytes((0x0F, 0x0F))
    return header + bytes(prg) + bytes(chr_data)


def synthetic_profile(image: bytes) -> tuple[GameProfile, RevisionSpec]:
    revision = RevisionSpec(
        "castle-probe",
        "Castle codec probe",
        len(image),
        hashlib.sha1(image).hexdigest(),
        hashlib.sha256(image).hexdigest(),
        "ines",
        3,
    )
    rooms = ResourceSpec(
        "rooms",
        "room-collection",
        "castle.prefix-rooms.v1",
        "fixed",
        {
            "room_count": 4,
            "width": 2,
            "height": 2,
            "codebook_cpu": 0x8000,
            "pointer_table_cpu": 0x8100,
            "fill_byte": 0xFF,
            "arenas": [
                {"bank": 2, "first_room": 0, "last_room": 2, "start": 0, "end": 2},
                {"bank": 3, "first_room": 2, "last_room": 4, "start": 0, "end": 2},
            ],
        },
    )
    objects = ResourceSpec(
        "objects",
        "object-layer",
        "castle.object-stream.v1",
        "fixed",
        {
            "room_count": 4,
            "width": 2,
            "height": 2,
            "stream_cpu": 0x8200,
            "stream_end_cpu": 0x8220,
            "count_table_cpu": 0x8240,
            "state_offsets_cpu": 0x8250,
            "state_capacity": 4,
            "type_count": 4,
            "maximum_per_room": 3,
            "fill_byte": 0xFF,
        },
    )
    mutable = ResourceSpec(
        "mutable-cell-state",
        "derived-state-layout",
        "castle.mutable-cell-state.v1",
        "fixed",
        {
            "state_offsets_cpu": 0x8260,
            "state_capacity": 4,
            "final_room_minimum_bytes": 0,
            "persistent_cell_values": [0x20],
        },
    )
    profile = GameProfile(
        "castle-probe",
        1,
        "Castle codec probe",
        "nes",
        ("rooms", "objects", "fixed-arena-write"),
        (revision,),
        (rooms, objects, mutable),
    )
    return profile, revision


def write_profile(path: Path, profile: GameProfile) -> None:
    path.write_text(
        json.dumps(
            {
                "schema": "retrolarium.game-profile",
                "schema_version": 1,
                "profile_version": profile.profile_version,
                "id": profile.game_id,
                "name": profile.name,
                "platform": profile.platform,
                "capabilities": list(profile.capabilities),
                "revisions": [
                    {
                        "id": revision.revision_id,
                        "name": revision.name,
                        "size": revision.size,
                        "sha1": revision.sha1,
                        "sha256": revision.sha256,
                        "container": revision.container,
                        "mapper": revision.mapper,
                    }
                    for revision in profile.revisions
                ],
                "resources": [
                    {
                        "id": resource.resource_id,
                        "kind": resource.kind,
                        "codec": resource.codec,
                        "capacity_policy": resource.capacity_policy,
                        "parameters": dict(resource.parameters),
                    }
                    for resource in profile.resources
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


class CastleCodecTests(unittest.TestCase):
    def test_packaged_profile_has_both_known_revisions(self) -> None:
        profile = packaged_game_profiles().get("castle-excellent")
        self.assertEqual(
            [revision.revision_id for revision in profile.revisions],
            ["castle-excellent-japan-hsp-05", "castlequest-usa"],
        )
        self.assertEqual(profile.resource("rooms").codec, "castle.prefix-rooms.v1")

    def test_open_probe_matches_independent_oracle_and_round_trips(self) -> None:
        image = synthetic_image()
        profile, revision = synthetic_profile(image)
        document = decode_levels(image, profile, revision)
        oracle = json.loads(ORACLE.read_text(encoding="utf-8"))
        actual = document.as_dict()
        self.assertEqual(actual["width"], oracle["width"])
        self.assertEqual(actual["height"], oracle["height"])
        self.assertEqual(actual["rooms"], oracle["rooms"])
        rebuilt, report = build_levels(image, document, profile, revision)
        self.assertEqual(rebuilt, image)
        self.assertEqual(
            [item["used_bytes"] for item in report["room_banks"]],
            oracle["room_arena_used_bytes"],
        )
        self.assertEqual(
            report["object_stream"]["used_bytes"],
            oracle["object_stream_used_bytes"],
        )
        self.assertEqual(report["object_state_bytes"], oracle["object_state_bytes"])
        self.assertEqual(
            report["mutable_cell_state_bytes"], oracle["mutable_cell_state_bytes"]
        )

    def test_edit_updates_room_objects_and_side_tables_transactionally(self) -> None:
        image = synthetic_image()
        profile, revision = synthetic_profile(image)
        source_document = decode_levels(image, profile, revision).as_dict()
        source_document["rooms"][1]["cells"][0][0] = 0x20
        source_document["rooms"][2]["objects"].pop()
        document = parse_level_document(source_document, profile, revision)
        rebuilt, report = build_levels(image, document, profile, revision)
        self.assertNotEqual(rebuilt, image)
        self.assertGreater(report["changed_bytes"], 0)
        prg_start = 16
        self.assertEqual(rebuilt[prg_start + 0x240:prg_start + 0x244], bytes((1, 0, 1, 0)))
        self.assertEqual(rebuilt[prg_start + 0x250:prg_start + 0x255], bytes((0, 1, 1, 2, 2)))
        self.assertEqual(rebuilt[prg_start + 0x260:prg_start + 0x265], bytes((0, 1, 2, 2, 2)))

    def test_room_arena_overflow_is_rejected(self) -> None:
        image = synthetic_image()
        profile, revision = synthetic_profile(image)
        raw = decode_levels(image, profile, revision).as_dict()
        raw["rooms"][0]["cells"] = [[0x30, 0x30], [0x30, 0x30]]
        document = parse_level_document(raw, profile, revision)
        with self.assertRaisesRegex(CastleError, "overflows CHR bank 2"):
            build_levels(image, document, profile, revision)

    def test_profile_driven_public_operations_use_atomic_outputs(self) -> None:
        image = synthetic_image()
        profile, _revision = synthetic_profile(image)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image_path = root / "probe.nes"
            profile_path = root / "profile.json"
            document_path = root / "levels.json"
            output_path = root / "rebuilt.nes"
            report_path = root / "report.json"
            image_path.write_bytes(image)
            write_profile(profile_path, profile)

            inspected = inspect_game(image_path, custom_profile=profile_path)
            self.assertEqual(inspected["details"]["rooms"], 4)
            extracted = extract_game(
                image_path, document_path, custom_profile=profile_path
            )
            self.assertEqual(extracted["rooms"], 4)
            validated = validate_game(
                image_path, document_path, custom_profile=profile_path
            )
            self.assertEqual(validated["status"], "valid")
            result = build_game(
                image_path,
                document_path,
                output_path,
                custom_profile=profile_path,
                report_path=report_path,
            )
            self.assertEqual(output_path.read_bytes(), image)
            self.assertEqual(result["changed_bytes"], 0)
            self.assertTrue(report_path.is_file())

            with self.assertRaisesRegex(AuthoringError, "must not overwrite"):
                build_game(
                    image_path,
                    document_path,
                    image_path,
                    custom_profile=profile_path,
                )

    def test_invalid_codebook_and_unknown_cell_are_rejected(self) -> None:
        with self.assertRaisesRegex(CastleError, "prefix-free"):
            validate_prefix_codebook((PrefixEntry(1, "0"), PrefixEntry(2, "01")))
        with self.assertRaisesRegex(CastleError, "no prefix code"):
            encode_prefix_stream((0x99,), (PrefixEntry(1, "0"), PrefixEntry(2, "1")))


if __name__ == "__main__":
    unittest.main()
