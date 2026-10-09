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
    parse_resource_document,
)


def encoding() -> dict[str, object]:
    characters = " ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.;"
    return {
        "characters": characters,
        "codes": [ord(character) for character in characters],
        "case_fold": "upper",
        "padding": 0x20,
    }


def fixture() -> tuple[bytes, GameProfile, RevisionSpec]:
    image = bytearray(b"NES\x1a" + bytes((1, 1, 0, 0)) + bytes(8)
                      + bytes(0x4000) + bytes(0x2000))
    image[20:26] = b"HELLO "
    image[40:44] = b"1 UP"
    source = bytes(image)
    revision = RevisionSpec(
        "probe-v1", "text probe", len(source), hashlib.sha1(source).hexdigest(),
        hashlib.sha256(source).hexdigest(), "ines", 0,
    )
    profile = GameProfile(
        "text-probe", 1, "text probe", "nes", ("text",), (revision,),
        (
            ResourceSpec(
                "bundle", "resource-bundle", "nes.resource-bundle.v1", "fixed"
            ),
            ResourceSpec(
                "messages", "text", "nes.fixed-text-fields.v1", "fixed",
                {
                    "encoding": encoding(),
                    "fields": [
                        {"key": "hello", "label": "Greeting",
                         "file_offset": 20, "width": 6},
                        {"key": "players", "label": "Players",
                         "file_offset": 40, "width": 4},
                    ],
                },
            ),
        ),
    )
    return source, profile, revision


class NesTextTests(unittest.TestCase):
    def test_disjoint_fields_round_trip_and_edit_transactionally(self) -> None:
        source, profile, revision = fixture()
        document = decode_resources(source, profile, revision)
        messages = document.as_dict()["resources"][0]["messages"]
        self.assertEqual([item["text"] for item in messages], ["HELLO", "1 UP"])
        exact, report = build_resources(source, document, profile, revision)
        self.assertEqual(exact, source)
        self.assertEqual(report["changed_bytes"], 0)
        self.assertEqual(len(report["resources"][0]["ranges"]), 2)

        raw = copy.deepcopy(document.as_dict())
        raw["resources"][0]["messages"][0]["text"] = "bye"
        edited, report = build_resources(
            source, parse_resource_document(raw, profile, revision), profile, revision
        )
        self.assertEqual(edited[20:26], b"BYE   ")
        self.assertEqual(edited[40:44], b"1 UP")
        self.assertEqual(report["changed_bytes"], 5)

    def test_rejects_unsupported_text_and_changed_metadata(self) -> None:
        source, profile, revision = fixture()
        raw = decode_resources(source, profile, revision).as_dict()
        raw["resources"][0]["messages"][0]["text"] = "HELLO!"
        with self.assertRaisesRegex(Exception, "unsupported characters"):
            parse_resource_document(raw, profile, revision)
        raw = decode_resources(source, profile, revision).as_dict()
        raw["resources"][0]["messages"][0]["width"] = 7
        with self.assertRaisesRegex(Exception, "metadata differs"):
            parse_resource_document(raw, profile, revision)

    def test_rejects_overlapping_profile_fields(self) -> None:
        source, profile, revision = fixture()
        parameters = {
            "encoding": dict(profile.resources[1].parameters["encoding"]),
            "fields": [
                dict(item) for item in profile.resources[1].parameters["fields"]
            ],
        }
        parameters["fields"][1]["file_offset"] = 24
        invalid = GameProfile(
            profile.game_id, profile.profile_version, profile.name, profile.platform,
            profile.capabilities, profile.revisions,
            (profile.resources[0], ResourceSpec(
                "messages", "text", "nes.fixed-text-fields.v1", "fixed", parameters
            )),
        )
        with self.assertRaisesRegex(Exception, "overlaps"):
            decode_resources(source, invalid, revision)


if __name__ == "__main__":
    unittest.main()
