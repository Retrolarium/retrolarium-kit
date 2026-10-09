from __future__ import annotations

import hashlib
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from retrolarium_kit.core.authoring import GameProfile, ResourceSpec, RevisionSpec
from retrolarium_kit.games.castle.demo import CastleDemoError, build_demo, decode_demo, parse_demo_document


def fixture() -> tuple[bytes, GameProfile, RevisionSpec]:
    script, init, reader = 0xB82C, 0xB7FD, 0xB80A
    prg = bytearray(0x8000)

    def put(address: int, payload: bytes) -> None:
        prg[address - 0x8000:address - 0x8000 + len(payload)] = payload

    put(init, bytes((0xA9, script & 0xFF, 0x85, 0xFD, 0xA9, script >> 8, 0x85, 0xFE)))
    put(reader, bytes((0xE6, 0xFF, 0xA5, 0xFF, 0xC9, 0x0C)))
    put(script, bytes((0x80, 0x80, 0x01, 0x41, 0x41, 0xFF)) + bytes(8))
    header = b"NES\x1a" + bytes((2, 1, 0x31, 0)) + bytes(8)
    source = header + bytes(prg) + bytes(0x2000)
    revision = RevisionSpec(
        "probe", "Castle probe", len(source), hashlib.sha1(source).hexdigest(),
        hashlib.sha256(source).hexdigest(), "ines", 3,
    )
    profile = GameProfile(
        "castle-excellent", 1, "Castle probe", "nes", ("demo",), (revision,),
        (ResourceSpec("title-demo", "demo", "castle.title-demo.v1", "fixed", {
            "script_cpu": script, "init_cpu": init, "reader_cpu": reader, "capacity": 8,
        }),),
    )
    return source, profile, revision


class CastleDemoTests(unittest.TestCase):
    def test_round_trip_and_edit(self) -> None:
        source, profile, revision = fixture()
        document = decode_demo(source, profile, revision)
        self.assertEqual([(2, ["RIGHT"]), (1, ["A"]), (2, ["A", "LEFT"])],
                         [(s["commands"], s["buttons"]) for s in document.steps])
        self.assertEqual(source, build_demo(source, document, profile, revision)[0])
        value = document.as_dict()
        value["steps"] = [{"index": 0, "commands": 3, "buttons": ["B", "DOWN"]},
                          {"index": 1, "commands": 5, "buttons": []}]
        output, report = build_demo(source, parse_demo_document(value, profile, revision), profile, revision)
        self.assertEqual(value, decode_demo(output, profile, revision, _verify_identity=False).as_dict())
        self.assertEqual(8, report["commands"])
        # Equal neighbours are allowed and decode as one step.
        value["steps"] = [{"index": 0, "commands": 1, "buttons": []},
                          {"index": 1, "commands": 1, "buttons": []}]
        output, _ = build_demo(source, parse_demo_document(value, profile, revision), profile, revision)
        self.assertEqual([2], [s["commands"] for s in decode_demo(
            output, profile, revision, _verify_identity=False).steps])

    def test_limits(self) -> None:
        source, profile, revision = fixture()
        value = decode_demo(source, profile, revision).as_dict()
        value["steps"][0]["commands"] = 7
        with self.assertRaisesRegex(CastleDemoError, "8 commands of 12 frames"):
            parse_demo_document(value, profile, revision)
        value = decode_demo(source, profile, revision).as_dict()
        value["steps"][0]["buttons"] = ["START"]
        with self.assertRaisesRegex(CastleDemoError, "unknown or unordered"):
            parse_demo_document(value, profile, revision)


if __name__ == "__main__":
    unittest.main()
