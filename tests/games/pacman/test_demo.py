from __future__ import annotations

import hashlib
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from retrolarium_kit.core.authoring import GameProfile, ResourceSpec, RevisionSpec
from retrolarium_kit.games.pacman.demo import PacmanDemoError, build_demo, decode_demo, parse_demo_document


def fixture() -> tuple[bytes, GameProfile, RevisionSpec]:
    script, table = 0xD471, 0xD48A
    prg = bytearray(0x4000)

    def put(address: int, payload: bytes) -> None:
        prg[address - 0xC000:address - 0xC000 + len(payload)] = payload

    put(script, bytes((
        0xA4, 0xE4, 0xC6, 0xE3, 0xD0, 0x0B, 0xE6, 0xE4, 0xE6, 0xE4, 0xA4, 0xE4,
        0xB9, 0x8A, 0xD4, 0x85, 0xE3, 0xB9, 0x8B, 0xD4, 0x29, 0x03,
    )))
    put(table, bytes((0x00, 0x01, 0xD0, 0x00, 0x28, 0x01, 0x00, 0x03)))
    header = b"NES\x1a" + bytes((1, 1, 0, 0)) + bytes(8)
    source = header + bytes(prg) + bytes(0x2000)
    revision = RevisionSpec(
        "probe", "Pac-Man probe", len(source), hashlib.sha1(source).hexdigest(),
        hashlib.sha256(source).hexdigest(), "ines", 0,
    )
    profile = GameProfile(
        "pacman", 1, "Pac-Man probe", "nes", ("demo",), (revision,),
        (ResourceSpec("attract-demo", "demo", "pacman.attract-demo.v1", "fixed", {
            "script_cpu": script, "table_cpu": table, "pairs": 4,
        }),),
    )
    return source, profile, revision


class PacmanDemoTests(unittest.TestCase):
    def test_round_trip_and_edit(self) -> None:
        source, profile, revision = fixture()
        document = decode_demo(source, profile, revision)
        self.assertEqual([("left", 255), ("up", 208), ("left", 40), ("right", 256)],
                         [(s["direction"], s["frames"]) for s in document.steps])
        self.assertEqual(source, build_demo(source, document, profile, revision)[0])
        value = document.as_dict()
        value["steps"] = [{"index": 0, "direction": "down", "frames": 255},
                          {"index": 1, "direction": "right", "frames": 17}]
        output, report = build_demo(source, parse_demo_document(value, profile, revision), profile, revision)
        self.assertEqual(2, report["steps"])
        start = 16 + 0xD48A - 0xC000
        # The first pair keeps its unread duration; the rest repeat the last direction.
        self.assertEqual(bytes((0x00, 0x02, 17, 0x03, 0x00, 0x03, 0x00, 0x03)), output[start:start + 8])
        self.assertEqual([("down", 255), ("right", 17), ("right", 256), ("right", 256)],
                         [(s["direction"], s["frames"]) for s in decode_demo(
                             output, profile, revision, _verify_identity=False).steps])

    def test_limits(self) -> None:
        source, profile, revision = fixture()
        value = decode_demo(source, profile, revision).as_dict()
        value["steps"][0]["frames"] = 100
        with self.assertRaisesRegex(PacmanDemoError, "first demo step always lasts 255"):
            parse_demo_document(value, profile, revision)
        value = decode_demo(source, profile, revision).as_dict()
        value["steps"][1]["frames"] = 257
        with self.assertRaisesRegex(PacmanDemoError, "lasts 1-256 frames"):
            parse_demo_document(value, profile, revision)
        value = decode_demo(source, profile, revision).as_dict()
        value["steps"].append({"index": 4, "direction": "up", "frames": 1})
        with self.assertRaisesRegex(PacmanDemoError, "1-4 steps"):
            parse_demo_document(value, profile, revision)
        value = decode_demo(source, profile, revision).as_dict()
        value["steps"][2]["direction"] = "jump"
        with self.assertRaisesRegex(PacmanDemoError, "direction is not one of"):
            parse_demo_document(value, profile, revision)
        broken = bytearray(source)
        broken[16 + 0xD471 - 0xC000 + 13] = 0x00
        with self.assertRaisesRegex(PacmanDemoError, "another table"):
            decode_demo(bytes(broken), profile, revision, _verify_identity=False)


if __name__ == "__main__":
    unittest.main()
