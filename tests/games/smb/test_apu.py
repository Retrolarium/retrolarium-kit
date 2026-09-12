from __future__ import annotations

import copy
import hashlib
import unittest

from retrolarium_kit.authoring import GameProfile, ResourceSpec, RevisionSpec
from retrolarium_kit.nes_resources import (
    build_resources,
    decode_resources,
    inspection_details,
)
from retrolarium_kit.smb_apu import (
    SmbApuError,
    decode_channel_byte,
    decode_music,
    encode_channel_command,
    encode_music,
    music_channel_spans,
    parse_layout,
)


def layout_parameters() -> dict[str, object]:
    return {
        "cpu_address": 0x9000,
        "selector_names": ["event", "area"],
        "ground_schedule_count": 2,
        "headers": [
            {"name": "alpha", "size": 5},
            {"name": "beta", "size": 6},
            {"name": "silence", "size": 4},
        ],
        "shared_stream_size": 16,
        "note_period_count": 2,
        "note_length_count": 4,
        "envelopes": [
            {"name": "area", "size": 2},
            {"name": "water", "size": 3},
        ],
    }


def synthetic_payload() -> bytes:
    parameters = layout_parameters()
    layout = parse_layout(parameters)
    arena_address = layout.cpu_address + layout.shared_stream_offset
    return bytes([
        4, 9,
        15, 4,
        0x18, arena_address & 0xFF, arena_address >> 8, 3, 2,
        0x20, (arena_address + 4) & 0xFF, (arena_address + 4) >> 8, 4, 2, 7,
        0x08, (arena_address + 12) & 0xFF, (arena_address + 12) >> 8, 0,
        *range(16),
        0x01, 0x23, 0x04, 0x56,
        1, 2, 3, 4,
        0x90, 0x91,
        0x80, 0x81, 0x82,
    ])


class SmbApuTests(unittest.TestCase):
    def test_channel_byte_grammar_preserves_engine_fields(self) -> None:
        self.assertEqual({"kind": "end", "raw": 0},
                         decode_channel_byte(0, "square2"))
        self.assertEqual({"kind": "rest", "raw": 0},
                         decode_channel_byte(0, "triangle"))
        self.assertEqual(
            {"kind": "length", "raw": 0x86, "length_index": 6},
            decode_channel_byte(0x86, "square2"),
        )
        self.assertEqual(
            {"kind": "note", "raw": 0x34, "period_index": 26},
            decode_channel_byte(0x34, "triangle"),
        )
        self.assertEqual({"kind": "control", "raw": 0},
                         decode_channel_byte(0, "square1"))
        self.assertEqual(
            {"kind": "note", "raw": 0xD5, "period_index": 10,
             "length_index": 7},
            decode_channel_byte(0xD5, "square1"),
        )
        self.assertEqual({"kind": "loop", "raw": 0},
                         decode_channel_byte(0, "noise"))
        self.assertEqual(
            {"kind": "beat", "raw": 0xE1, "beat": "strong",
             "length_index": 7},
            decode_channel_byte(0xE1, "noise"),
        )

    def test_channel_byte_grammar_rejects_unknown_inputs(self) -> None:
        with self.assertRaisesRegex(SmbApuError, "channel byte"):
            decode_channel_byte(0x100, "square2")
        with self.assertRaisesRegex(SmbApuError, "unknown SMB music channel"):
            decode_channel_byte(0, "dmc")

    def test_channel_commands_lower_to_canonical_bytes(self) -> None:
        cases = (
            ("square2", "end", {}, 0),
            ("triangle", "rest", {}, 0),
            ("square2", "length", {"length_index": 6}, 0x86),
            ("triangle", "note", {"period_index": 26}, 0x34),
            ("square1", "control", {}, 0),
            ("square1", "note", {"period_index": 10, "length_index": 7}, 0xD5),
            ("noise", "loop", {}, 0),
            ("noise", "beat", {"beat": "strong", "length_index": 7}, 0xE1),
        )
        for channel, kind, arguments, expected in cases:
            with self.subTest(channel=channel, kind=kind):
                encoded = encode_channel_command(channel, kind, **arguments)
                self.assertEqual(expected, encoded)
                self.assertEqual(kind, decode_channel_byte(encoded, channel)["kind"])

    def test_channel_commands_reject_ambiguous_or_foreign_forms(self) -> None:
        with self.assertRaisesRegex(SmbApuError, "reserved zero-byte"):
            encode_channel_command("square2", "note", period_index=0)
        with self.assertRaisesRegex(SmbApuError, "control marker"):
            encode_channel_command(
                "square1", "note", period_index=0, length_index=0
            )
        with self.assertRaisesRegex(SmbApuError, "loop marker"):
            encode_channel_command("noise", "beat", beat="rest", length_index=0)
        with self.assertRaisesRegex(SmbApuError, "not valid"):
            encode_channel_command("noise", "note", period_index=1)
        with self.assertRaisesRegex(SmbApuError, "does not accept"):
            encode_channel_command("triangle", "rest", period_index=1)

    def test_round_trip_models_shared_stream_ownership(self) -> None:
        payload = synthetic_payload()
        document = decode_music(payload, layout_parameters())
        self.assertEqual("alpha", document["selectors"][0]["header"])
        self.assertEqual(["silence", "alpha"], document["ground_schedule"])
        self.assertEqual(4, document["headers"][1]["stream_offset"])
        self.assertEqual(7, document["headers"][1]["noise_offset"])
        self.assertEqual([0x0123, 0x0456], document["note_periods"])
        self.assertEqual(payload, encode_music(document, layout_parameters()))

    def test_channel_spans_partition_aliases_without_copying_arena(self) -> None:
        document = decode_music(synthetic_payload(), layout_parameters())
        spans = music_channel_spans(document, layout_parameters())
        self.assertEqual(
            [(0, 2), (2, 1), (3, 1), (4, 2), (6, 2), (8, 3),
             (11, 1), (12, 4)],
            [(span["offset"], span["size"]) for span in spans],
        )
        self.assertEqual(
            [
                {"header": "silence", "channel": "square2"},
                {"header": "silence", "channel": "triangle"},
            ],
            spans[-1]["owners"],
        )
        self.assertEqual(
            document["shared_stream_arena"],
            list(range(16)),
        )

    def test_known_arena_edit_changes_one_shared_byte(self) -> None:
        original = synthetic_payload()
        document = decode_music(original, layout_parameters())
        edited = copy.deepcopy(document)
        edited["shared_stream_arena"][5] = 0xEA
        payload = encode_music(edited, layout_parameters())
        changed = [
            index for index, values in enumerate(zip(original, payload))
            if values[0] != values[1]
        ]
        self.assertEqual(
            [parse_layout(layout_parameters()).shared_stream_offset + 5], changed
        )

    def test_selector_edit_retargets_one_header_offset(self) -> None:
        original = synthetic_payload()
        document = decode_music(original, layout_parameters())
        document["selectors"][0]["header"] = "silence"
        payload = encode_music(document, layout_parameters())
        self.assertEqual([15], list(payload[:1]))
        self.assertEqual(original[1:], payload[1:])

    def test_rejects_reference_to_middle_of_header(self) -> None:
        payload = bytearray(synthetic_payload())
        payload[0] = 5
        with self.assertRaisesRegex(SmbApuError, "unknown header offset"):
            decode_music(bytes(payload), layout_parameters())

    def test_rejects_channel_outside_shared_arena(self) -> None:
        document = decode_music(synthetic_payload(), layout_parameters())
        document["headers"][1]["noise_offset"] = 12
        with self.assertRaisesRegex(SmbApuError, "leaves the shared stream arena"):
            encode_music(document, layout_parameters())

    def test_rejects_layout_beyond_cpu_address_space(self) -> None:
        parameters = layout_parameters()
        parameters["cpu_address"] = 0xFFF0
        with self.assertRaisesRegex(SmbApuError, "CPU address space"):
            parse_layout(parameters)

    def test_leading_envelopes_precede_the_header_offset_base(self) -> None:
        base_parameters = layout_parameters()
        document = decode_music(synthetic_payload(), base_parameters)
        parameters = copy.deepcopy(base_parameters)
        parameters["envelopes"] = [
            {"name": "area", "size": 2, "location": "leading"},
            {"name": "water", "size": 3, "location": "trailing"},
        ]
        payload = encode_music(document, parameters)
        layout = parse_layout(parameters)
        self.assertEqual(bytes((0x90, 0x91)), payload[:2])
        self.assertEqual(bytes((4, 9)), payload[layout.selector_offset:][:2])
        self.assertEqual(document, decode_music(payload, parameters))

    def test_nes_bundle_round_trip_and_known_edit(self) -> None:
        header = b"NES\x1a" + bytes((1, 1, 0, 0)) + bytes(8)
        body = bytearray(0x6000)
        payload = synthetic_payload()
        file_offset = 32
        body[file_offset - 16 : file_offset - 16 + len(payload)] = payload
        image = header + bytes(body)
        revision = RevisionSpec(
            "probe", "SMB APU probe", len(image),
            hashlib.sha1(image).hexdigest(), hashlib.sha256(image).hexdigest(),
            "ines", 0,
        )
        parameters = {
            **layout_parameters(),
            "file_offset": file_offset,
            "size": len(payload),
        }
        profile = GameProfile(
            "smb-apu-probe", 1, "SMB APU probe", "nes", ("audio",),
            (revision,),
            (
                ResourceSpec(
                    "bundle", "resource-bundle", "nes.resource-bundle.v1",
                    "fixed",
                ),
                ResourceSpec(
                    "music", "audio", "smb.apu-music.v1", "fixed", parameters
                ),
            ),
        )
        document = decode_resources(image, profile, revision)
        self.assertEqual(3, inspection_details(document)["smb_music_headers"])
        self.assertEqual(16, inspection_details(document)["smb_music_arena_bytes"])
        exact, report = build_resources(image, document, profile, revision)
        self.assertEqual(image, exact)
        self.assertEqual(0, report["changed_bytes"])

        document.resources[0]["shared_stream_arena"][5] = 0xEA
        edited, report = build_resources(image, document, profile, revision)
        changed = [
            index for index, values in enumerate(zip(image, edited))
            if values[0] != values[1]
        ]
        self.assertEqual(
            [file_offset + parse_layout(parameters).shared_stream_offset + 5],
            changed,
        )
        self.assertEqual(1, report["changed_bytes"])


if __name__ == "__main__":
    unittest.main()
