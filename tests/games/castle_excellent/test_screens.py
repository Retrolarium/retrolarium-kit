from __future__ import annotations

import hashlib
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from retrolarium_kit.core.authoring import GameProfile, ResourceSpec, RevisionSpec
from retrolarium_kit.games.castle.screens import CastleScreensError, build_screens, decode_screens, parse_screens_document


def fixture() -> tuple[bytes, GameProfile, RevisionSpec]:
    prg = bytearray(0x8000)
    message = bytes((0x77, 0x71, 0x7D, 0x75)) + b"\x00"
    prg[0x7E84:0x7E84 + len(message)] = message
    chr_data = bytearray(0x8000)
    for page in (0x14, 0x18):
        start = 0x6000 + page * 256
        chr_data[start:start + 1024] = bytes((page + i) & 0xFF for i in range(1024))
    header = b"NES\x1a" + bytes((2, 4, 0x31, 0)) + bytes(8)
    source = header + bytes(prg) + bytes(chr_data)
    revision = RevisionSpec(
        "probe", "Castle probe", len(source), hashlib.sha1(source).hexdigest(),
        hashlib.sha256(source).hexdigest(), "ines", 3,
    )
    profile = GameProfile(
        "castle-excellent", 1, "Castle probe", "nes", ("screens",), (revision,),
        (ResourceSpec("screens", "screens", "castle.screens.v1", "fixed", {
            "chr_file_offset": 16 + 0x8000 + 0x6000, "pages": [0x14, 0x18],
            "page_names": ["completion", "title"], "chr_banks": [0, 1],
            "game_over_cpu": 0xFE84, "game_over_length": 4,
        }),),
    )
    return source, profile, revision


class CastleScreensTests(unittest.TestCase):
    def test_round_trip_and_edit(self) -> None:
        source, profile, revision = fixture()
        document = decode_screens(source, profile, revision)
        self.assertEqual(["completion", "title"], [s["id"] for s in document.screens])
        self.assertEqual((0x77, 0x71, 0x7D, 0x75), document.game_over)
        self.assertEqual(0x14, document.screens[0]["tiles"][0])
        self.assertEqual(64, len(document.screens[1]["attributes"]))
        self.assertEqual(source, build_screens(source, document, profile, revision)[0])
        value = document.as_dict()
        value["screens"][1]["tiles"][5] = 0xAA
        value["screens"][0]["attributes"][0] = 0x55
        value["game_over"] = [0x78, 0x71, 0x7D, 0x75]
        output, report = build_screens(source, parse_screens_document(value, profile, revision), profile, revision)
        self.assertEqual(3, report["changed_bytes"])
        self.assertEqual(value, decode_screens(output, profile, revision, _verify_identity=False).as_dict())

    def test_limits(self) -> None:
        source, profile, revision = fixture()
        value = decode_screens(source, profile, revision).as_dict()
        value["game_over"] = [0x78, 0, 0x7D, 0x75]
        with self.assertRaisesRegex(CastleScreensError, "no zero"):
            parse_screens_document(value, profile, revision)
        value = decode_screens(source, profile, revision).as_dict()
        value["screens"][0]["tiles"] = value["screens"][0]["tiles"][:-1]
        with self.assertRaisesRegex(CastleScreensError, "960 byte values"):
            parse_screens_document(value, profile, revision)


if __name__ == "__main__":
    unittest.main()
