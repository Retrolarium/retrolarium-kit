from __future__ import annotations

import unittest

from retrolarium_kit.games.castle_excellent.mml import (
    CastleExcellentMmlError,
    decode_music,
    encode_music,
    validate_mml_text,
)


PARAMETERS = {
    "track_table_file_offset": 8,
    "track_count": 1,
    "track_names": ["Room music"],
    "channel_names": ["Pulse 1", "Pulse 2", "Triangle"],
    "playback_prefix_file_offset": 32,
    "playback_prefix_cpu_address": 0xBBD7,
    "playback_prefix_size": 16,
    "mml_file_offset": 64,
    "mml_cpu_address": 0xBBE7,
    "mml_capacity": 32,
    "stream_count": 2,
}


def fixture() -> bytes:
    image = bytearray(128)
    first = 0xBBE7
    second = first + len(b"t1l8c4") + 3
    image[8:16] = (
        first.to_bytes(2, "little")
        + second.to_bytes(2, "little")
        + bytes(2)
        + bytes((0xBF, 0xBB))
    )
    image[32:48] = bytes(range(16))
    arena = (
        b"t1l8c4\0" + first.to_bytes(2, "little")
        + b"o3r8\0\0\0"
    )
    image[64:64 + len(arena)] = arena
    return bytes(image)


class CastleExcellentMmlTests(unittest.TestCase):
    def test_decode_encode_and_relocate_streams(self) -> None:
        source = fixture()
        document = decode_music(source, PARAMETERS)
        self.assertEqual(document["used_bytes"], 16)
        self.assertEqual(document["streams"][0], {
            "id": "stream_00", "text": "t1l8c4", "restart": "stream_00"})
        self.assertEqual(
            document["track_sets"][0]["streams"],
            ["stream_00", "stream_01", None],
        )
        rebuilt = bytearray(source)
        for offset, payload in encode_music(document, PARAMETERS):
            rebuilt[offset:offset + len(payload)] = payload
        self.assertEqual(bytes(rebuilt), source)

        document["streams"][0]["text"] += "d8"
        document["used_bytes"] += 2
        rebuilt = bytearray(source)
        spans = encode_music(document, PARAMETERS)
        for offset, payload in spans:
            rebuilt[offset:offset + len(payload)] = payload
        second = 0xBBE7 + len(b"t1l8c4d8") + 3
        self.assertEqual(
            rebuilt[10:12], second.to_bytes(2, "little"))
        self.assertEqual(rebuilt[64:72], b"t1l8c4d8")

    def test_grammar_capacity_and_identity_are_strict(self) -> None:
        self.assertEqual(validate_mml_text("t5vfl@o4c+8.r4")["note"], 1)
        with self.assertRaisesRegex(CastleExcellentMmlError, "invalid byte"):
            validate_mml_text("c#4")
        document = decode_music(fixture(), PARAMETERS)
        document["streams"][0]["text"] = "c" * 30
        document["used_bytes"] = 40
        with self.assertRaisesRegex(CastleExcellentMmlError, "capacity"):
            encode_music(document, PARAMETERS)
        document = decode_music(fixture(), PARAMETERS)
        document["track_sets"][0]["name"] = "Renamed"
        with self.assertRaisesRegex(CastleExcellentMmlError, "identity"):
            encode_music(document, PARAMETERS)


if __name__ == "__main__":
    unittest.main()
