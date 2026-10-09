from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from retrolarium_kit.core.authoring import GameProfile, ResourceSpec, RevisionSpec
from retrolarium_kit.games.solomon.sprite_frames import (
    SolomonSpriteFrameError, build_sprite_frames, decode_sprite_frames,
    parse_sprite_frame_document,
)


def fixture() -> tuple[bytes, GameProfile, RevisionSpec]:
    header = b"NES\x1a" + bytes((2, 4, 0x30, 0)) + bytes(8)
    prg = bytearray(0x8000)
    for index in range(33):
        prg[0x100 + index * 2:0x102 + index * 2] = bytes((0, 0x82))
    for action in range(0x16, 0x1C):
        descriptor = 0x200 + action * 4
        address = 0x8300 + (action - 0x16) * 3
        prg[descriptor:descriptor + 4] = bytes(
            (0x10, 0, address & 0xFF, address >> 8)
        )
        offset = address - 0x8000
        prg[offset:offset + 3] = bytes((action, action + 1, action + 2))
    source = header + bytes(prg) + bytes(0x8000)
    revision = RevisionSpec(
        "probe", "Solomon sprite probe", len(source),
        hashlib.sha1(source).hexdigest(),
        hashlib.sha256(source).hexdigest(), "ines", 3,
    )
    resource = ResourceSpec(
        "sprite-frames", "sprite-frames", "solomon.sprite-frames.v1", "fixed",
        {"animation_pointers_prg": 0x100, "enemy_configuration_prg": 0x180},
    )
    profile = GameProfile(
        "solomons-key", 1, "Solomon sprite probe", "nes", ("graphics",),
        (revision,), (resource,),
    )
    return source, profile, revision


class SolomonSpriteFrameTests(unittest.TestCase):
    def test_exact_roundtrip_and_shared_frame_edit(self) -> None:
        source, profile, revision = fixture()
        decoded = decode_sprite_frames(source, profile, revision)
        self.assertEqual(6, len(decoded.frames))
        self.assertEqual(("right",), decoded.frames[0].wizard_facings)
        self.assertEqual(("left",), decoded.frames[1].wizard_facings)
        self.assertEqual(27, len(decoded.frames[2].enemy_types))
        self.assertEqual(
            source, build_sprite_frames(source, decoded, profile, revision)[0]
        )
        edited_frame = replace(
            decoded.frames[2], left_tile=decoded.frames[2].left_tile ^ 1
        )
        edited = replace(
            decoded, frames=decoded.frames[:2] + (edited_frame,) + decoded.frames[3:]
        )
        output, report = build_sprite_frames(source, edited, profile, revision)
        self.assertEqual(1, report["changed_bytes"])
        self.assertEqual(
            [16 + edited_frame.prg_offset],
            [index for index, (a, b) in enumerate(zip(source, output)) if a != b],
        )
        self.assertEqual(
            edited_frame.left_tile, output[16 + edited_frame.prg_offset]
        )

    def test_rejects_invalid_bytes_and_binding_edits(self) -> None:
        source, profile, revision = fixture()
        document = decode_sprite_frames(source, profile, revision).as_dict()
        document["frames"][0]["flags"] = 256
        with self.assertRaisesRegex(SolomonSpriteFrameError, "byte"):
            parse_sprite_frame_document(document, profile, revision)
        document = decode_sprite_frames(source, profile, revision).as_dict()
        moved = document["frames"][2]["enemy_types"].pop()
        document["frames"][3]["enemy_types"].append(moved)
        document["frames"][3]["enemy_types"].sort()
        parsed = parse_sprite_frame_document(document, profile, revision)
        with self.assertRaisesRegex(SolomonSpriteFrameError, "binding differs"):
            build_sprite_frames(source, parsed, profile, revision)

    def test_rejects_bad_profile_table(self) -> None:
        source, profile, revision = fixture()
        bad = replace(
            profile.resources[0],
            parameters={
                "animation_pointers_prg": 0x8000,
                "enemy_configuration_prg": 0x180,
            },
        )
        with self.assertRaisesRegex(SolomonSpriteFrameError, "escapes"):
            decode_sprite_frames(
                source, replace(profile, resources=(bad,)), revision
            )


if __name__ == "__main__":
    unittest.main()
