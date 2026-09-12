from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from retrolarium_kit.fds_music import FdsMusicError, decode_music, encode_music
from retrolarium_kit.authoring import GameProfile, ResourceSpec, RevisionSpec
from retrolarium_kit.nes_resources import (
    build_resources,
    decode_resources,
    parse_resource_document,
)


PARAMETERS = {
    "size": 80,
    "cpu_address": 0xD000,
    "sequence_names": ["opening", "finale"],
    "header_names": ["pattern-1", "pattern-2"],
    "stream_offset": 20,
    "synthesis_offset": 40,
    "synthesis_size": 20,
    "note_lengths_offset": 64,
    "note_length_count": 16,
    "waveforms": [
        {"name": "wave-a", "offset": 0, "count": 32},
    ],
    "volume_programs": [
        {"name": "volume-a", "offset": 16, "count": 2},
    ],
}


def fixture() -> tuple[bytes, dict[str, object]]:
    parameters = dict(PARAMETERS)
    parameters["size"] = 112
    parameters["synthesis_offset"] = 40
    parameters["synthesis_size"] = 48
    parameters["note_lengths_offset"] = 96
    parameters["waveforms"] = [
        {"name": "wave-a", "offset": 0, "count": 32},
    ]
    parameters["volume_programs"] = [
        {"name": "volume-a", "offset": 32, "count": 2},
    ]
    payload = bytearray(range(112))
    payload[0:2] = bytes((2, 10))
    payload[2:10] = bytes((0, 0x14, 0xD0, 1, 2, 3, 4, 1))
    payload[10:18] = bytes((8, 0x18, 0xD0, 2, 3, 4, 5, 2))
    payload[40:72] = bytes(range(32))
    payload[72:76] = bytes((0x81, 2, 0x42, 3))
    return bytes(payload), parameters


class FdsMusicTests(unittest.TestCase):
    def test_complete_bank_round_trip_preserves_every_region(self) -> None:
        payload, parameters = fixture()
        value = decode_music(payload, parameters)
        self.assertEqual(
            value["sequence"],
            [
                {"name": "opening", "header": "pattern-1"},
                {"name": "finale", "header": "pattern-2"},
            ],
        )
        self.assertEqual(value["headers"][1]["stream_offset"], 4)
        self.assertEqual(value["synthesis"]["waveforms"][0]["samples"], list(range(32)))
        self.assertEqual(encode_music(value, parameters), payload)

    def test_edits_sequence_stream_synthesis_and_lengths(self) -> None:
        payload, parameters = fixture()
        value = copy.deepcopy(decode_music(payload, parameters))
        value["sequence"][0]["header"] = "pattern-2"
        value["shared_stream_arena"][0] = 0x81
        value["synthesis"]["waveforms"][0]["samples"][0] = 63
        value["note_lengths"][0] = 24
        edited = encode_music(value, parameters)
        self.assertEqual(edited[0], 10)
        self.assertEqual(edited[20], 0x81)
        self.assertEqual(edited[40], 63)
        self.assertEqual(edited[96], 24)

    def test_rejects_unknown_headers_and_out_of_arena_channels(self) -> None:
        payload, parameters = fixture()
        value = copy.deepcopy(decode_music(payload, parameters))
        value["sequence"][0]["header"] = "missing"
        with self.assertRaisesRegex(FdsMusicError, "header is unknown"):
            encode_music(value, parameters)
        value = copy.deepcopy(decode_music(payload, parameters))
        value["headers"][0]["noise_offset"] = 0xFF
        with self.assertRaisesRegex(FdsMusicError, "leaves the stream arena"):
            encode_music(value, parameters)

    def test_resource_pipeline_round_trip_and_known_edit(self) -> None:
        payload, parameters = fixture()
        source = b"NES\x1a" + bytes((1, 0, 0, 0)) + bytes(8) + payload
        source += bytes(16 + 16_384 - len(source))
        revision = RevisionSpec(
            "probe", "FDS music probe", len(source),
            hashlib.sha1(source).hexdigest(), hashlib.sha256(source).hexdigest(),
            "ines", 0,
        )
        profile = GameProfile(
            "fds-music-probe", 1, "FDS music probe", "nes", ("audio",),
            (revision,),
            (
                ResourceSpec(
                    "bundle", "resource-bundle", "nes.resource-bundle.v1", "fixed"
                ),
                ResourceSpec(
                    "music", "audio", "fds.music.v1", "fixed",
                    {**parameters, "file_offset": 16},
                ),
            ),
        )
        document = decode_resources(source, profile, revision)
        rebuilt, report = build_resources(source, document, profile, revision)
        self.assertEqual(rebuilt, source)
        self.assertEqual(report["changed_bytes"], 0)

        edited = copy.deepcopy(document.as_dict())
        music = edited["resources"][0]
        music["sequence"][0]["header"] = "pattern-2"
        music["synthesis"]["waveforms"][0]["samples"][0] = 63
        parsed = parse_resource_document(edited, profile, revision)
        rebuilt, report = build_resources(source, parsed, profile, revision)
        self.assertEqual(rebuilt[16], 10)
        self.assertEqual(rebuilt[56], 63)
        self.assertEqual(report["changed_bytes"], 2)


if __name__ == "__main__":
    unittest.main()
