from __future__ import annotations

import hashlib
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from retrolarium_kit.authoring import GameProfile, ResourceSpec, RevisionSpec
from retrolarium_kit.game_profiles import packaged_game_profiles
from retrolarium_kit.games import authoring_adapters
from retrolarium_kit.solomon_animations import (
    DEFINITION_SIZE, FRAME_COUNT, GROUP_COUNTS, GROUP_OFFSETS, POINTER_COUNT,
    SELECTOR_OFFSETS, SolomonAnimationError, animation_frame_for_action,
    build_animations,
    decode_animations, parse_animation_document,
)


def fixture(pointer_offset: int) -> tuple[bytes, GameProfile, RevisionSpec]:
    header = b"NES\x1a" + bytes((2, 4, 0x30, 0)) + bytes(8)
    prg = bytearray(0x8000)
    prg[0x100:0x100 + 27] = bytes((8,)) * 27
    definition = 0x8000 + pointer_offset + POINTER_COUNT * 2
    frame_start = definition + DEFINITION_SIZE
    for index in range(POINTER_COUNT):
        group = definition + (
            GROUP_OFFSETS[0] if index == 0
            else GROUP_OFFSETS[index % len(GROUP_OFFSETS)]
        )
        prg[pointer_offset + index * 2:pointer_offset + index * 2 + 2] = (
            group.to_bytes(2, "little")
        )
    for group_offset, count in zip(GROUP_OFFSETS, GROUP_COUNTS):
        for action in range(count):
            offset = definition - 0x8000 + group_offset + action * 4
            target = frame_start + ((group_offset + action) % FRAME_COUNT) * 3
            prg[offset:offset + 4] = bytes((0x10, 4)) + target.to_bytes(2, "little")
    for selector_offset in SELECTOR_OFFSETS:
        offset = definition - 0x8000 + selector_offset
        for variant in range(4):
            target = frame_start + variant * 3
            prg[offset + variant * 2:offset + variant * 2 + 2] = (
                target.to_bytes(2, "little")
            )
    frame_offset = frame_start - 0x8000
    for index in range(FRAME_COUNT):
        prg[frame_offset + index * 3:frame_offset + index * 3 + 3] = (
            bytes((index & 255, (index + 1) & 255, (index + 2) & 255))
        )
    source = header + bytes(prg) + bytes(0x8000)
    revision = RevisionSpec(
        "probe", "Solomon animation probe", len(source),
        hashlib.sha1(source).hexdigest(),
        hashlib.sha256(source).hexdigest(), "ines", 3,
    )
    resource = ResourceSpec(
        "animations", "animations", "solomon.animations.v1", "fixed",
        {"pointer_table_prg": pointer_offset,
         "enemy_configuration_prg": 0x100},
    )
    profile = GameProfile(
        "solomons-key", 1, "Solomon animation probe", "nes",
        ("graphics",), (revision,), (resource,),
    )
    return source, profile, revision


class SolomonAnimationTests(unittest.TestCase):
    def test_engine_phase_and_delay_sequence(self) -> None:
        source, profile, revision = fixture(0x50E8)
        value = decode_animations(source, profile, revision).as_dict()
        value["descriptors"][0]["initial_phase"] = 0x43
        value["descriptors"][0]["delay"] = 2
        document = parse_animation_document(value, profile, revision)
        frame_start = value["frames"][0]["cpu_address"]
        for tick, index in (
            (0, 3), (1, 3), (2, 3),
            (3, 2), (5, 2), (6, 1), (9, 0), (12, 3),
            (30000, 3),
        ):
            with self.subTest(tick=tick):
                self.assertEqual(
                    frame_start + index * 3,
                    animation_frame_for_action(document, 0, 0, tick)["cpu_address"],
                )

    def test_packaged_regions_and_adapter_are_registered(self) -> None:
        profile = packaged_game_profiles().get("solomons-key")
        for revision_id, expected in (("usa", 0x50E8), ("europe", 0x5068)):
            revision = next(
                item for item in profile.revisions
                if item.revision_id == revision_id
            )
            resources = [
                item for item in profile.resources_for(revision)
                if item.codec == "solomon.animations.v1"
            ]
            self.assertEqual(1, len(resources))
            self.assertEqual(expected, resources[0].parameters["pointer_table_prg"])
        self.assertEqual(
            "animations",
            authoring_adapters().get("solomon.animations.v1").resource_id,
        )

    def test_roundtrip_and_edit_for_both_region_placements(self) -> None:
        for pointer_offset in (0x50E8, 0x5068):
            with self.subTest(pointer_offset=pointer_offset):
                source, profile, revision = fixture(pointer_offset)
                document = decode_animations(source, profile, revision)
                self.assertEqual(340, len(document.descriptors))
                self.assertEqual(FRAME_COUNT, len(document.frames))
                self.assertEqual(
                    source, build_animations(source, document, profile, revision)[0]
                )
                value = document.as_dict()
                value["frames"][8]["left_tile"] ^= 1
                value["descriptors"][0]["delay"] = 9
                value["descriptors"][0]["uses_variants"] = True
                value["descriptors"][0]["data_pointer"] = (
                    value["selectors"][0]["cpu_address"]
                )
                value["group_pointers"][0] = (
                    value["descriptors"][1]["cpu_address"] - 4
                    + GROUP_OFFSETS[6]
                )
                edited = parse_animation_document(value, profile, revision)
                rebuilt, report = build_animations(
                    source, edited, profile, revision
                )
                self.assertEqual(
                    sum(a != b for a, b in zip(source, rebuilt)),
                    report["changed_bytes"],
                )
                self.assertEqual(
                    value["group_pointers"][0],
                    int.from_bytes(rebuilt[16 + pointer_offset:
                                           18 + pointer_offset], "little"),
                )
                self.assertEqual(
                    edited.frames[8]["left_tile"],
                    rebuilt[16 + pointer_offset + POINTER_COUNT * 2
                            + DEFINITION_SIZE + 8 * 3],
                )

    def test_rejects_misaligned_and_external_pointers(self) -> None:
        source, profile, revision = fixture(0x50E8)
        document = decode_animations(source, profile, revision)
        value = document.as_dict()
        value["descriptors"][0]["data_pointer"] += 1
        with self.assertRaises(SolomonAnimationError):
            parse_animation_document(value, profile, revision)
        value = document.as_dict()
        value["group_pointers"][0] = value["descriptors"][36]["cpu_address"]
        with self.assertRaisesRegex(
            SolomonAnimationError, "Wizard animation group"
        ):
            parse_animation_document(value, profile, revision)
        value = document.as_dict()
        value["enemy_configuration"][1] = 0
        with self.assertRaisesRegex(
            SolomonAnimationError, "action leaves"
        ):
            parse_animation_document(value, profile, revision)
        value = document.as_dict()
        value["selectors"][0]["pointers"][0] = 0xFFFF
        with self.assertRaises(SolomonAnimationError):
            parse_animation_document(value, profile, revision)
        value = document.as_dict()
        value["frames"][0]["cpu_address"] += 3
        with self.assertRaises(SolomonAnimationError):
            parse_animation_document(value, profile, revision)

    def test_source_identity_required(self) -> None:
        source, profile, revision = fixture(0x50E8)
        document = decode_animations(source, profile, revision)
        with self.assertRaises(SolomonAnimationError):
            build_animations(
                source[:-1] + bytes((source[-1] ^ 1,)), document,
                profile, revision,
            )
        value = document.as_dict()
        value["enemy_configuration"][0] ^= 1
        edited = parse_animation_document(value, profile, revision)
        with self.assertRaisesRegex(SolomonAnimationError, "source-owned"):
            build_animations(source, edited, profile, revision)


if __name__ == "__main__":
    unittest.main()
