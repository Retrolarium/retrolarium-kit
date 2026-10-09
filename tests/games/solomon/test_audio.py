from __future__ import annotations

import hashlib
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from retrolarium_kit.core.authoring import GameProfile, ResourceSpec, RevisionSpec
from retrolarium_kit.games.solomon.audio import (
    SolomonAudioError, build_audio, decode_audio, parse_audio_document,
)


def fixture() -> tuple[bytes, GameProfile, RevisionSpec]:
    addresses = {
        "period_cpu": 0xE000,
        "duration_cpu": 0xE018,
        "envelope_pointer_cpu": 0xE032,
        "envelope_data_cpu": 0xE042,
        "effect_pointer_cpu": 0xE052,
        "effect_data_cpu": 0xE086,
        "stream_cpu": 0xE0D5,
        "stream_end_cpu": 0xE299,
    }
    prg = bytearray(0x8000)

    def put(address: int, data: bytes) -> None:
        prg[address - 0x8000:address - 0x8000 + len(data)] = data

    for index in range(12):
        put(0xE000 + 2 * index, (index + 1).to_bytes(2, "little"))
    put(0xE018, bytes(range(26)))
    for index in range(8):
        put(0xE032 + 2 * index, (0xE042 + 2 * index).to_bytes(2, "little"))
        put(0xE042 + 2 * index, bytes((index + 1, 15 - index)))
    for index in range(26):
        put(0xE052 + 2 * index, (0xE086 + 3 * index).to_bytes(2, "little"))
        put(0xE086 + 3 * index, b"\x80\xd5\xe0")
    put(0xE0D4, b"\xff")
    for index in range(113):
        next_address = 0xE0D5 + 4 * (index + 1)
        put(0xE0D5 + 4 * index,
            b"\xf3" + next_address.to_bytes(2, "little") + b"\xf9")
    put(0xE299, b"\xf9")
    header = b"NES\x1a" + bytes((2, 4, 0x30, 0)) + bytes(8)
    source = header + bytes(prg) + bytes(0x8000)
    revision = RevisionSpec(
        "probe", "Solomon audio probe", len(source),
        hashlib.sha1(source).hexdigest(),
        hashlib.sha256(source).hexdigest(), "ines", 3,
    )
    profile = GameProfile(
        "solomons-key", 1, "Solomon audio probe", "nes",
        ("audio", "exact-round-trip"), (revision,),
        (ResourceSpec("audio", "audio", "solomon.audio.v1", "fixed", addresses),),
    )
    return source, profile, revision


class SolomonAudioTests(unittest.TestCase):
    def test_exact_roundtrip_and_edit_timing_envelope_effect(self) -> None:
        source, profile, revision = fixture()
        document = decode_audio(source, profile, revision)
        self.assertEqual(114, sum("entry" in item for item in document.commands))
        self.assertEqual(source, build_audio(source, document, profile, revision)[0])
        value = document.as_dict()
        value["periods"][2] = 0x1234
        value["durations"][3] = 0x77
        value["envelopes"][0]["steps"][0]["volume"] = 7
        value["effects"][0]["channels"][0]["selector"] = 0x81
        edited = parse_audio_document(value, profile, revision)
        output, report = build_audio(source, edited, profile, revision)
        self.assertEqual(5, report["changed_bytes"])
        self.assertEqual(0x34, output[16 + 0x6004])
        self.assertEqual(0x12, output[16 + 0x6005])
        self.assertEqual(7, output[16 + 0x6043])
        self.assertEqual(0x81, output[16 + 0x6086])

    def test_rejects_overflow_and_unreachable_stream(self) -> None:
        source, profile, revision = fixture()
        value = decode_audio(source, profile, revision).as_dict()
        value["commands"].append({"kind": "stop"})
        with self.assertRaisesRegex(SolomonAudioError, "allocation"):
            parse_audio_document(value, profile, revision)
        value = decode_audio(source, profile, revision).as_dict()
        value["commands"][0]["target"] = "stream_002"
        parsed = parse_audio_document(value, profile, revision)
        with self.assertRaisesRegex(SolomonAudioError, "entry count"):
            build_audio(source, parsed, profile, revision)

    def test_rejects_invalid_channel_and_source_identity(self) -> None:
        source, profile, revision = fixture()
        value = decode_audio(source, profile, revision).as_dict()
        value["effects"][0]["channels"][0]["selector"] = 0x88
        with self.assertRaisesRegex(SolomonAudioError, "selector"):
            parse_audio_document(value, profile, revision)
        with self.assertRaisesRegex(SolomonAudioError, "identity"):
            decode_audio(source[:-1] + b"\x01", profile, revision)


if __name__ == "__main__":
    unittest.main()
