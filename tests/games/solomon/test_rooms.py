from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from retrolarium_kit.core.authoring import GameProfile, ResourceSpec, RevisionSpec
from retrolarium_kit.games.solomon.rooms import (
    SolomonError,
    build_rooms,
    decode_rooms,
    parse_room_document,
)


def fixture() -> tuple[bytes, GameProfile, RevisionSpec]:
    header = b"NES\x1a" + bytes((2, 4, 0x30, 0)) + bytes(8)
    prg = bytearray(0x8000)
    prg[0x100:0x130] = bytes(range(48))
    prg[0x130:0x160] = bytes(reversed(range(48)))
    image = header + bytes(prg) + bytes(0x8000)
    revision = RevisionSpec(
        "probe", "Solomon probe", len(image), hashlib.sha1(image).hexdigest(),
        hashlib.sha256(image).hexdigest(), "ines", 3,
    )
    resource = ResourceSpec(
        "room-blocks", "room-collection", "solomon.block-bitplanes.v1",
        "fixed", {"room_count": 2, "width": 16, "height": 12,
                  "bytes_per_plane": 24, "prg_offset": 0x100},
    )
    profile = GameProfile(
        "solomons-key", 1, "Solomon probe", "nes", ("rooms",),
        (revision,), (resource,),
    )
    return image, profile, revision


def stream_fixture() -> tuple[bytes, GameProfile, RevisionSpec]:
    image, profile, _ = fixture()
    data = bytearray(image)
    base = 16
    # Two contiguous enemy records: lifetime, type, packed position, end.
    data[base + 0x200:base + 0x204] = bytes((0x21, 0x18, 0x11, 0))
    data[base + 0x204:base + 0x208] = bytes((0x22, 0x19, 0x22, 0))
    data[base + 0x1FC:base + 0x200] = bytes((0x00, 0x04, 0x82, 0x82))
    # Ten-byte metadata and one ordinary item per room.
    header = bytes((0, 1, 0, 1, 0, 0x11, 0x12, 0x13, 0x14, 0x15))
    data[base + 0x304:base + 0x311] = header + bytes((24, 0x21, 0))
    data[base + 0x311:base + 0x31E] = header + bytes((25, 0x31, 0))
    data[base + 0x300:base + 0x304] = bytes((0x04, 0x11, 0x83, 0x83))
    image = bytes(data)
    revision = replace(
        profile.revisions[0], sha1=hashlib.sha1(image).hexdigest(),
        sha256=hashlib.sha256(image).hexdigest(),
    )
    enemies = ResourceSpec(
        "enemy-streams", "object-layer", "solomon.enemy-streams.v1", "fixed",
        {"pointer_table_prg": 0x1FC, "arena_start_prg": 0x200,
         "arena_end_prg": 0x208},
    )
    items = ResourceSpec(
        "item-streams", "object-layer", "solomon.item-streams.v1", "fixed",
        {"pointer_table_prg": 0x300, "arena_start_prg": 0x304,
         "arena_end_prg": 0x31E},
    )
    profile = replace(profile, revisions=(revision,),
                      resources=(*profile.resources, enemies, items))
    return image, profile, revision


class SolomonRoomsTest(unittest.TestCase):
    def test_rejects_invalid_stream_commands_and_metadata(self) -> None:
        image, profile, revision = stream_fixture()
        document = decode_rooms(image, profile, revision).as_dict()
        document["rooms"][0]["item_stream"]["commands"].pop()
        with self.assertRaisesRegex(ValueError, "no item terminator"):
            parse_room_document(document, profile, revision)
        document = decode_rooms(image, profile, revision).as_dict()
        document["rooms"][0]["item_stream"]["metadata"]["mirror_1_enemy_set"] = 17
        with self.assertRaisesRegex(ValueError, "escapes its table"):
            parse_room_document(document, profile, revision)
        document = decode_rooms(image, profile, revision).as_dict()
        document["rooms"][0]["enemy_stream"]["enemies"][0]["position"]["x"] = 16
        with self.assertRaisesRegex(ValueError, "position.x"):
            parse_room_document(document, profile, revision)

    def test_variable_streams_repack_and_capacity(self) -> None:
        image, profile, revision = stream_fixture()
        decoded = decode_rooms(image, profile, revision)
        self.assertEqual(2, decoded.schema_version)
        document = decoded.as_dict()
        parsed = parse_room_document(document, profile, revision)
        self.assertEqual(image, build_rooms(image, parsed, profile, revision)[0])
        first, second = document["rooms"]
        second["enemy_stream"]["enemies"].append(
            first["enemy_stream"]["enemies"].pop())
        second["item_stream"]["commands"].insert(
            -1, first["item_stream"]["commands"].pop(0))
        output, report = build_rooms(
            image, parse_room_document(document, profile, revision), profile, revision
        )
        self.assertGreater(report["changed_bytes"], 0)
        self.assertEqual(0, report["enemy_streams"]["free_bytes"])
        self.assertEqual(0, report["item_streams"]["free_bytes"])
        revised = replace(revision, sha1=hashlib.sha1(output).hexdigest(),
                          sha256=hashlib.sha256(output).hexdigest())
        self.assertEqual(document["rooms"][1]["item_stream"],
                         decode_rooms(output, profile, revised)
                         .as_dict()["rooms"][1]["item_stream"])
        second["enemy_stream"]["enemies"].append(
            second["enemy_stream"]["enemies"][0].copy())
        with self.assertRaisesRegex(SolomonError, "arena capacity"):
            build_rooms(image, parse_room_document(document, profile, revision),
                        profile, revision)

    def test_round_trip_and_localized_edit(self) -> None:
        image, profile, revision = fixture()
        decoded = decode_rooms(image, profile, revision)
        self.assertEqual((2, 16, 12),
                         (len(decoded.rooms), decoded.width, decoded.height))
        document = decoded.as_dict()
        parsed = parse_room_document(document, profile, revision)
        self.assertEqual(image, build_rooms(image, parsed, profile, revision)[0])
        first = document["rooms"][0]["brown"]
        first[0][0] = 1 - first[0][0]
        edited = parse_room_document(document, profile, revision)
        output, report = build_rooms(image, edited, profile, revision)
        self.assertEqual(1, report["changed_bytes"])
        self.assertEqual(16 + 0x100, report["room_block_file_offset"])
        differences = [index for index, (a, b) in enumerate(zip(image, output))
                       if a != b]
        self.assertEqual([16 + 0x100], differences)
        self.assertEqual(first[0][0],
                         decode_rooms(output, profile,
                                      replace(revision,
                                              sha1=hashlib.sha1(output).hexdigest(),
                                              sha256=hashlib.sha256(output).hexdigest()))
                         .as_dict()["rooms"][0]["brown"][0][0])

    def test_rejects_invalid_geometry_cells_and_identity(self) -> None:
        image, profile, revision = fixture()
        document = decode_rooms(image, profile, revision).as_dict()
        document["rooms"][0]["white"][0][0] = 2
        with self.assertRaisesRegex(SolomonError, "must be 0 or 1"):
            parse_room_document(document, profile, revision)
        document["rooms"][0]["white"][0][0] = 0
        document["source_sha256"] = "0" * 64
        with self.assertRaisesRegex(SolomonError, "source_sha256"):
            parse_room_document(document, profile, revision)
        with self.assertRaisesRegex(SolomonError, "selected revision"):
            decode_rooms(image[:-1] + b"\x01", profile, revision)

    def test_region_offset_is_profile_owned(self) -> None:
        image, profile, revision = fixture()
        moved = bytearray(image)
        moved[16 + 0x180:16 + 0x1E0] = moved[16 + 0x100:16 + 0x160]
        revision2 = replace(revision,
                            sha1=hashlib.sha1(moved).hexdigest(),
                            sha256=hashlib.sha256(moved).hexdigest())
        resource2 = replace(profile.resources[0],
                            parameters={**profile.resources[0].parameters,
                                        "prg_offset": 0x180})
        profile2 = replace(profile, revisions=(revision2,), resources=(resource2,))
        document = decode_rooms(bytes(moved), profile2, revision2).as_dict()
        document["rooms"][0]["brown"][0][0] ^= 1
        output, report = build_rooms(bytes(moved),
                                     parse_room_document(document, profile2, revision2),
                                     profile2, revision2)
        self.assertEqual(16 + 0x180, report["room_block_file_offset"])
        self.assertEqual(moved[16 + 0x100:16 + 0x160],
                         output[16 + 0x100:16 + 0x160])


if __name__ == "__main__":
    unittest.main()
