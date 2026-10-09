from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from retrolarium_kit.core.authoring import GameProfile, ResourceSpec, RevisionSpec
from retrolarium_kit.games.solomon.room_art import (
    SolomonRoomArtError, build_room_art, decode_room_art, parse_room_art_document,
)


def fixture() -> tuple[bytes, GameProfile, RevisionSpec]:
    header = b"NES\x1a" + bytes((2, 4, 0x30, 0)) + bytes(8)
    prg = bytearray(0x8000)
    prg[0x100] = 0x80
    prg[0x200:0x204] = bytes((0xA3, 0x12, 0x34, 0x56))
    source = header + bytes(prg) + bytes(0x8000)
    revision = RevisionSpec(
        "probe", "Solomon probe", len(source),
        hashlib.sha1(source).hexdigest(),
        hashlib.sha256(source).hexdigest(), "ines", 3,
    )
    resource = ResourceSpec(
        "room-art", "room-art", "solomon.room-art.v1", "fixed",
        {"room_group_colors_prg": 0x100, "room_map_patterns_prg": 0x200},
    )
    profile = GameProfile(
        "solomons-key", 1, "Solomon probe", "nes", ("graphics",),
        (revision,), (resource,),
    )
    return source, profile, revision


class SolomonRoomArtTests(unittest.TestCase):
    def test_exact_roundtrip_and_two_local_edits(self) -> None:
        source, profile, revision = fixture()
        decoded = decode_room_art(source, profile, revision)
        self.assertEqual(0x80, decoded.group_colors[0])
        self.assertEqual(0xA0, decoded.patterns[0].top_left)
        self.assertEqual(3, decoded.patterns[0].palette)
        self.assertEqual(
            source, build_room_art(source, decoded, profile, revision)[0]
        )
        value = decoded.as_dict()
        value["room_group_colors"][0] = 0x3F
        value["room_map_patterns"][0]["palette"] = 2
        edited = parse_room_art_document(value, profile, revision)
        output, report = build_room_art(source, edited, profile, revision)
        self.assertEqual([0x110, 0x210], [
            index for index, (old, new) in enumerate(zip(source, output))
            if old != new
        ])
        self.assertEqual(2, report["changed_bytes"])
        self.assertEqual(0x3F, output[0x110])
        self.assertEqual(0xA2, output[0x210])

    def test_rejects_invalid_packed_record_and_marker(self) -> None:
        source, profile, revision = fixture()
        value = decode_room_art(source, profile, revision).as_dict()
        value["room_map_patterns"][0]["top_left"] = 0xA1
        with self.assertRaisesRegex(SolomonRoomArtError, "palette bits"):
            parse_room_art_document(value, profile, revision)
        value = decode_room_art(source, profile, revision).as_dict()
        value["room_group_colors"][0] = 0x81
        with self.assertRaisesRegex(SolomonRoomArtError, "0..63 or"):
            parse_room_art_document(value, profile, revision)

    def test_rejects_profile_overlap(self) -> None:
        source, profile, revision = fixture()
        overlapping = replace(
            profile.resources[0],
            parameters={
                "room_group_colors_prg": 0x200,
                "room_map_patterns_prg": 0x200,
            },
        )
        profile = replace(profile, resources=(overlapping,))
        with self.assertRaisesRegex(SolomonRoomArtError, "overlap"):
            decode_room_art(source, profile, revision)


if __name__ == "__main__":
    unittest.main()
