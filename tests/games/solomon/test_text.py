from __future__ import annotations

import hashlib
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from retrolarium_kit.core.authoring import GameProfile, ResourceSpec, RevisionSpec
from retrolarium_kit.games.solomon.text import (
    SolomonTextError, build_text, decode_text, parse_text_document, static_order,
)


def text(value: str) -> list[int]:
    return [int(c) if c.isdigit() else 0x24 if c == " " else ord(c) - 55 for c in value]


def program(address: int, tiles: list[int]) -> bytes:
    return bytes((address >> 8, address & 0xFF, 0x40 | (len(tiles) - 1))) + bytes(tiles) + b"\0"


def message(tiles: list[int], *addresses: int) -> bytes:
    return bytes((0x40 | (len(tiles) - 1),)) + bytes(tiles) + b"\0" + b"".join(
        bytes((a & 0xFF, a >> 8)) for a in addresses
    )


def fixture() -> tuple[bytes, GameProfile, RevisionSpec]:
    prg = bytearray(0x8000)

    def put(address: int, payload: bytes) -> None:
        prg[address - 0x8000:address - 0x8000 + len(payload)] = payload

    programs = {
        index: program(0x2040 + index * 0x20, text(f"LINE {index:02d}")) for index in range(18)
    }
    programs[5] = program(0x3F00, list(range(16)))
    cursor = 0xE024
    pointers = [0] * 18
    for index in static_order(18):
        pointers[index] = cursor
        put(cursor, programs[index])
        cursor += len(programs[index])
    static_end = cursor
    put(0xE000, bytes(p & 0xFF for p in pointers))
    put(0xE012, bytes(p >> 8 for p in pointers))
    put(0xF000, bytes((pointers[5] & 0xFF, pointers[5] >> 8)))

    first = message(text("FAIRIES"), 0x2864, 0x22A4)
    second = message(text("FLEW"), 0x28A2, 0x22E2)
    put(0xF010, first + second)
    put(0xF002, bytes((len(first) - 1,)))
    initial_end = 0xF010 + len(first) + len(second)

    data = bytearray()
    offsets = bytearray()
    for index in range(10):
        offsets.append((len(data) - 1) & 0xFF)
        if index == 4:
            put(0xF003, bytes(((len(data) - 1) & 0xFF,)))
        addresses = (0x2900 + index, 0x2300 + index) if index < 4 else (0x2900 + index,)
        data += message(text(f"MESSAGE {chr(65 + index)}"), *addresses)
    put(0xF100, bytes(offsets))
    put(0xF110, bytes(data))
    data_end = 0xF110 + len(data) + 6     # room to grow

    intro = program(0x216B, text("SHRINE"))[:-1] + program(0x21CB, text(" ROOM 00"))[:-1] \
        + program(0x226F, [0x98, 0, 0])
    put(0xF300, intro)
    put(0xF320, bytes(text("PRINSESS") + text(" SOLOMON") + text(" HIDDEN ")))

    header = b"NES\x1a" + bytes((2, 4, 0x30, 0)) + bytes(8)
    source = header + bytes(prg) + bytes(0x8000)
    revision = RevisionSpec(
        "probe", "Solomon text probe", len(source),
        hashlib.sha1(source).hexdigest(), hashlib.sha256(source).hexdigest(), "ines", 3,
    )
    parameters = {
        "static_pointer_low_cpu": 0xE000, "static_pointer_high_cpu": 0xE012,
        "static_region_cpu": 0xE024, "static_region_end_cpu": static_end,
        "static_05_reference_cpu": 0xF000,
        "ending_initial_cpu": 0xF010, "ending_initial_end_cpu": initial_end,
        "ending_initial_second_operand_cpu": 0xF002,
        "ending_offsets_cpu": 0xF100, "ending_data_cpu": 0xF110, "ending_data_end_cpu": data_end,
        "ending_single_address_operand_cpu": 0xF003,
        "room_intro_cpu": 0xF300, "special_names_cpu": 0xF320,
    }
    profile = GameProfile(
        "solomons-key", 1, "Solomon text probe", "nes", ("text",), (revision,),
        (ResourceSpec("text", "text", "solomon.text.v1", "fixed", parameters),),
    )
    return source, profile, revision


def prg_byte(output: bytes, address: int) -> int:
    return output[16 + address - 0x8000]


class SolomonTextTests(unittest.TestCase):
    def test_static_order_without_the_licence_screen(self) -> None:
        self.assertEqual((0, 1, 2, 3, 4, 6, 7, 8, 9, 10, 11, 12, 14, 15, 16, 17, 5, 13), static_order(18))
        self.assertEqual((0, 1, 2, 3, 4, 6, 7, 8, 9, 10, 11, 12, 5), static_order(13))

    def test_exact_roundtrip(self) -> None:
        source, profile, revision = fixture()
        document = decode_text(source, profile, revision)
        self.assertEqual(18, len(document.static_programs))
        self.assertEqual(text("LINE 07"), document.static_programs[7]["commands"][0]["bytes"])
        self.assertEqual([0x2864, 0x22A4], document.ending_initial[0]["addresses"])
        self.assertEqual(1, len(document.ending_messages[4]["addresses"]))
        self.assertEqual(text("SHRINE"), document.room_intro[0]["bytes"])
        self.assertEqual(source, build_text(source, document, profile, revision)[0])

    def test_lengths_change_and_references_follow(self) -> None:
        source, profile, revision = fixture()
        value = decode_text(source, profile, revision).as_dict()
        # Program 00 shrinks: every later program, 05 included, moves.
        value["static_programs"][0]["commands"][0]["bytes"] = text("HI")
        value["ending_initial"][0]["tiles"] = text("FAIRIES WE")
        value["ending_initial"][1]["tiles"] = text("A")
        value["ending_messages"][1]["tiles"] = text("LONG MESSAGE B")
        output, report = build_text(source, parse_text_document(value, profile, revision), profile, revision)
        rebuilt = decode_text(output, profile, revision, _verify_identity=False).as_dict()
        self.assertEqual(value, rebuilt)
        layout = profile.resources[0].parameters
        low = prg_byte(output, layout["static_pointer_low_cpu"] + 5)
        high = prg_byte(output, layout["static_pointer_high_cpu"] + 5)
        reference = prg_byte(output, 0xF000) | (prg_byte(output, 0xF001) << 8)
        self.assertEqual(low | (high << 8), reference)
        self.assertEqual(1 + 10 + 1 + 4 - 1, prg_byte(output, 0xF002))
        self.assertGreater(report["changed_bytes"], 0)

    def test_limits_are_refused(self) -> None:
        source, profile, revision = fixture()
        value = decode_text(source, profile, revision).as_dict()
        value["static_programs"][3]["commands"][0]["bytes"] = text("X" * 40)
        with self.assertRaisesRegex(SolomonTextError, "region has"):
            parse_text_document(value, profile, revision)
        value = decode_text(source, profile, revision).as_dict()
        value["ending_messages"][2]["tiles"] = [0x0A, 0x00]
        with self.assertRaisesRegex(SolomonTextError, "tile \\$00"):
            parse_text_document(value, profile, revision)
        value = decode_text(source, profile, revision).as_dict()
        value["room_intro"][0]["bytes"] = text("TEMPLE!")[:-1] + text("S")
        with self.assertRaisesRegex(SolomonTextError, "three runs"):
            parse_text_document(value, profile, revision)
        value = decode_text(source, profile, revision).as_dict()
        value["static_programs"][5]["commands"][0]["address"] = 0x3F10
        with self.assertRaisesRegex(SolomonTextError, "program 05"):
            parse_text_document(value, profile, revision)


if __name__ == "__main__":
    unittest.main()
