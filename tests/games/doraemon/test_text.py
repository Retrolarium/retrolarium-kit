from __future__ import annotations

import hashlib
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from retrolarium_kit.core.authoring import GameProfile, ResourceSpec, RevisionSpec
from retrolarium_kit.games.doraemon.text import DoraemonTextError, build_text, decode_text, parse_text_document


MANIFEST = {
    "bank": 3,
    "game_over": {"address": "0x8000", "size": 9},
    "title_stream": {
        "address": "0x8010", "end_address": "0x8021", "record_count": 2,
        "text_records": [{"id": "push", "ppu_address": "0x21E7"}],
    },
    "ending_nametable": {"address": "0x8030", "row_width": 4, "row_count": 2},
    "chapter_help_screens": {
        "address": "0x8040", "size": 16, "screen_size": 16, "count": 1,
        "screens": [{"id": "world1", "address": "0x8040",
                     "text_spans": [{"id": "candy", "address": "0x8044", "size": 5}]}],
    },
    "ending_credits": {"address": "0x8050", "row_width": 8, "row_count": 2},
    "signatures": [{"address": "0x8060", "bytes": "A9 48"}],
}


def fixture() -> tuple[bytes, GameProfile, RevisionSpec]:
    prg = bytearray(0x20000)

    def put(address: int, payload: bytes) -> None:
        at = 3 * 0x8000 + address - 0x8000
        prg[at:at + len(payload)] = payload

    put(0x8000, b"GAME\x00OVER")
    # A graphics record, the text record and the terminator.
    put(0x8010, bytes((0x20, 0x84, 3, 0x80, 0x81, 0x82)) + bytes((0x21, 0xE7, 8)) + b"PUSH\x00GO\x5b" + b"\x00")
    put(0x8030, bytes(range(1, 9)))
    put(0x8040, b"\xff\xff\xff\xffCANDY" + b"\xff" * 7)
    put(0x8050, b" HUDSON THE END ")
    put(0x8060, b"\xa9\x48")
    header = b"NES\x1a" + bytes((8, 4, 0x20, 0x40)) + bytes(8)
    source = header + bytes(prg) + bytes(0x8000)
    revision = RevisionSpec("probe", "Doraemon probe", len(source), hashlib.sha1(source).hexdigest(),
                            hashlib.sha256(source).hexdigest(), "ines", 66)
    profile = GameProfile("doraemon", 1, "Doraemon probe", "nes", ("text",), (revision,),
                          (ResourceSpec("text", "text", "doraemon.shell-text.v1", "fixed", MANIFEST),))
    return source, profile, revision


class DoraemonTextTests(unittest.TestCase):
    def test_round_trip_and_edit(self) -> None:
        source, profile, revision = fixture()
        document = decode_text(source, profile, revision)
        value = document.as_dict()
        self.assertEqual("GAME OVER", value["game_over"])
        self.assertEqual("PUSH GO.", value["title_records"][1]["text"])
        self.assertEqual(["01 02 03 04", "05 06 07 08"], value["ending_nametable_rows"])
        self.assertEqual("CANDY", value["chapter_help_screens"][0]["segments"][1]["text"])
        self.assertEqual([" HUDSON ", "THE END "], value["ending_credit_rows"])
        self.assertEqual(source, build_text(source, document, profile, revision)[0])

        value["game_over"] = "THE  END "
        value["title_records"][1]["text"] = "START.  "
        value["chapter_help_screens"][0]["segments"][1]["text"] = "SWEET"
        value["ending_credit_rows"][1] = "FIN.    "
        output, report = build_text(source, parse_text_document(value, profile, revision), profile, revision)
        self.assertEqual(value, decode_text(output, profile, revision, _verify_identity=False).as_dict() | {
            "source_sha256": revision.sha256})
        base = 16 + 3 * 0x8000 - 0x8000
        self.assertEqual(b"START\x5b\x00\x00", output[base + 0x8019:base + 0x8021])
        self.assertGreater(report["changed_bytes"], 0)

    def test_fixed_sizes(self) -> None:
        source, profile, revision = fixture()
        value = decode_text(source, profile, revision).as_dict()
        value["game_over"] = "GAME  OVER"
        with self.assertRaisesRegex(DoraemonTextError, "keeps 9 places"):
            parse_text_document(value, profile, revision)
        value = decode_text(source, profile, revision).as_dict()
        value["chapter_help_screens"][0]["segments"][1]["text"] = "SUGAR!"
        with self.assertRaisesRegex(DoraemonTextError, "candy keeps 5 places"):
            parse_text_document(value, profile, revision)
        value = decode_text(source, profile, revision).as_dict()
        value["ending_credit_rows"][0] = "TOO LONG ROW"
        with self.assertRaisesRegex(DoraemonTextError, "keeps 8 places"):
            parse_text_document(value, profile, revision)
        broken = bytearray(source)
        broken[16 + 3 * 0x8000 + 0x60] = 0
        with self.assertRaisesRegex(DoraemonTextError, "differs from the profile"):
            decode_text(bytes(broken), profile, revision, _verify_identity=False)


if __name__ == "__main__":
    unittest.main()
