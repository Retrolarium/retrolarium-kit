from __future__ import annotations

import hashlib
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from retrolarium_kit.authoring import GameProfile, ResourceSpec, RevisionSpec
from retrolarium_kit.solomon_presentation import (
    SolomonPresentationError, build_presentation, decode_presentation,
    parse_presentation_document, project_title_surface,
)


def fixture() -> tuple[bytes, GameProfile, RevisionSpec]:
    addresses = {
        "record_cpu": 0xE000,
        "logo_cpu": 0xE09B,
        "demo_duration_cpu": 0xE192,
        "demo_input_cpu": 0xE1B4,
    }
    prg = bytearray(0x8000)
    prg[0x6000:0x609B] = bytes([0x50] + [0x80] * 153 + [0x7F])
    prg[0x609B:0x6192] = bytes([0x50] + [0x81] * 245 + [0x7F])
    prg[0x6192:0x61B4] = bytes(range(34))
    prg[0x61B4:0x61D6] = bytes([0x80] * 34)
    header = b"NES\x1a" + bytes((2, 4, 0x30, 0)) + bytes(8)
    source = header + bytes(prg) + bytes(0x8000)
    revision = RevisionSpec(
        "probe", "Solomon presentation probe", len(source),
        hashlib.sha1(source).hexdigest(),
        hashlib.sha256(source).hexdigest(), "ines", 3,
    )
    profile = GameProfile(
        "solomons-key", 1, "Solomon presentation probe", "nes",
        ("presentation", "exact-round-trip"), (revision,),
        (ResourceSpec(
            "presentation", "presentation", "solomon.presentation.v1",
            "fixed", addresses,
        ),),
    )
    return source, profile, revision


class SolomonPresentationTests(unittest.TestCase):
    def test_exact_roundtrip_and_title_demo_edits(self) -> None:
        source, profile, revision = fixture()
        document = decode_presentation(source, profile, revision)
        self.assertEqual(2, len(document.streams))
        self.assertEqual(34, len(document.demo_steps))
        self.assertEqual(0x80, project_title_surface(document, 0)[0][0])
        self.assertEqual(
            source, build_presentation(source, document, profile, revision)[0]
        )
        value = document.as_dict()
        value["streams"][0]["tokens"][1]["tiles"][0] = 0x82
        value["demo_steps"][1]["duration"] = 47
        value["demo_steps"][2]["buttons"] = ["A", "RIGHT"]
        edited = parse_presentation_document(value, profile, revision)
        output, report = build_presentation(source, edited, profile, revision)
        self.assertEqual(3, report["changed_bytes"])
        self.assertEqual(0x82, output[16 + 0x6001])
        self.assertEqual(47, output[16 + 0x6193])
        self.assertEqual(0x81, output[16 + 0x61B6])

    def test_rejects_overflow_bad_buttons_and_source(self) -> None:
        source, profile, revision = fixture()
        value = decode_presentation(source, profile, revision).as_dict()
        value["streams"][0]["tokens"][1]["tiles"].append(0x80)
        with self.assertRaisesRegex(SolomonPresentationError, "allocation"):
            parse_presentation_document(value, profile, revision)
        value = decode_presentation(source, profile, revision).as_dict()
        value["demo_steps"][0]["buttons"] = ["RIGHT", "A"]
        with self.assertRaisesRegex(SolomonPresentationError, "buttons"):
            parse_presentation_document(value, profile, revision)
        with self.assertRaisesRegex(SolomonPresentationError, "identity"):
            decode_presentation(source[:-1] + b"\x01", profile, revision)


if __name__ == "__main__":
    unittest.main()
