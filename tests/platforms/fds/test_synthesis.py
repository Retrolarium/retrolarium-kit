from __future__ import annotations

import copy
import hashlib
import unittest

from retrolarium_kit.core.authoring import (
    GameProfile,
    PayloadSpec,
    ResourceSpec,
    RevisionSpec,
)
from retrolarium_core.fds import fds_payload
from retrolarium_kit.platform.fds.synthesis import (
    FdsSynthesisError,
    decode_synthesis,
    encode_synthesis,
)
from retrolarium_kit.platform.nes.resources import (
    build_resources,
    decode_resources,
    parse_resource_document,
)


def parameters() -> dict[str, object]:
    return {
        "size": 80,
        "waveforms": [
            {"name": "wave-a", "offset": 0, "count": 32},
            {"name": "wave-b", "offset": 32, "count": 32},
        ],
        "volume_programs": [
            {"name": "wave-a", "offset": 64, "count": 2},
            {"name": "wave-b", "offset": 68, "count": 3},
        ],
    }


def payload() -> bytes:
    result = bytearray(range(64)) + bytearray([
        0xA0, 4, 0x52, 5,
        0x12, 6, 0x9F, 7, 0x43, 8,
        0xDE, 0xAD, 0xBE, 0xEF, 0x55, 0xAA,
    ])
    return bytes(result)


def fds_fixture(data: bytes) -> bytes:
    image = bytearray(65_500)
    image[0] = 1
    image[56:58] = bytes((2, 2))
    position = 58
    for number, identifier, name, address, chunk in (
        (0, 3, b"SYNTHA  ", 0xC000, data[:40]),
        (1, 4, b"SYNTHB  ", 0xC028, data[40:]),
    ):
        header = bytearray(16)
        header[0:3] = bytes((3, number, identifier))
        header[3:11] = name
        header[11:13] = address.to_bytes(2, "little")
        header[13:15] = len(chunk).to_bytes(2, "little")
        image[position:position + 16] = header
        position += 16
        image[position] = 4
        position += 1
        image[position:position + len(chunk)] = chunk
        position += len(chunk)
    return bytes(image)


class FdsSynthesisTests(unittest.TestCase):
    def test_fds_resource_pipeline_round_trip_and_known_edit(self) -> None:
        data = payload()
        image = fds_fixture(data)
        revision = RevisionSpec(
            "fds", "FDS synthesis probe", len(image),
            hashlib.sha1(image).hexdigest(), hashlib.sha256(image).hexdigest(),
            "fds",
            payloads=(PayloadSpec(
                "ending", "fds-records", len(data), hashlib.sha1(data).hexdigest(),
                record_ids=(3, 4),
            ),),
        )
        profile = GameProfile(
            "fds-synthesis-probe", 1, "FDS synthesis probe", "nes",
            ("audio",), (revision,),
            (
                ResourceSpec(
                    "bundle", "resource-bundle", "nes.resource-bundle.v1", "fixed"
                ),
                ResourceSpec(
                    "synthesis", "audio", "fds.synthesis.v1", "fixed",
                    {**parameters(), "file_offset": 0}, payload="ending",
                ),
            ),
        )
        document = decode_resources(image, profile, revision)
        rebuilt, report = build_resources(image, document, profile, revision)
        self.assertEqual(image, rebuilt)
        self.assertEqual(0, report["changed_bytes"])

        edited = copy.deepcopy(document.as_dict())
        synthesis = edited["resources"][0]
        synthesis["waveforms"][1]["samples"][3] = 1
        synthesis["volume_programs"][0]["steps"][1]["frames"] = 9
        parsed = parse_resource_document(edited, profile, revision)
        rebuilt, report = build_resources(image, parsed, profile, revision)
        expected = bytearray(data)
        expected[35] = 1
        expected[67] = 9
        self.assertEqual(bytes(expected), fds_payload(rebuilt, (3, 4)))
        self.assertEqual(2, report["changed_bytes"])

    def test_round_trip_preserves_arena_and_decodes_controls(self) -> None:
        source = payload()
        document = decode_synthesis(source, parameters())
        self.assertEqual(list(range(32)), document["waveforms"][0]["samples"])
        self.assertEqual(list(range(32, 64)), document["waveforms"][1]["samples"])
        self.assertEqual(
            {"step": 0, "mode": "direct", "value": 32, "frames": 4},
            document["volume_programs"][0]["steps"][0],
        )
        self.assertEqual(
            {"step": 0, "mode": "decrease", "value": 18, "frames": 6},
            document["volume_programs"][1]["steps"][0],
        )
        self.assertEqual(source, encode_synthesis(document, parameters()))

    def test_known_edits_change_only_owned_bytes(self) -> None:
        source = payload()
        document = decode_synthesis(source, parameters())
        document["waveforms"][1]["samples"][3] = 1
        document["volume_programs"][0]["steps"][1] = {
            "step": 1, "mode": "increase", "value": 7, "frames": 9,
        }
        encoded = encode_synthesis(document, parameters())
        changed = [
            index for index, (before, after) in enumerate(zip(source, encoded))
            if before != after
        ]
        self.assertEqual([35, 66, 67], changed)
        self.assertEqual([1, 0x47, 9], [encoded[index] for index in changed])
        self.assertEqual(source[74:], encoded[74:])

    def test_rejects_invalid_layout_metadata_and_values(self) -> None:
        invalid_layout = parameters()
        invalid_layout["volume_programs"][0]["offset"] = 31
        with self.assertRaisesRegex(FdsSynthesisError, "spans overlap"):
            decode_synthesis(payload(), invalid_layout)

        document = decode_synthesis(payload(), parameters())
        renamed = copy.deepcopy(document)
        renamed["waveforms"][0]["name"] = "other"
        with self.assertRaisesRegex(FdsSynthesisError, "differs from profile"):
            encode_synthesis(renamed, parameters())

        bad_sample = copy.deepcopy(document)
        bad_sample["waveforms"][0]["samples"][0] = 64
        with self.assertRaisesRegex(FdsSynthesisError, "0..63"):
            encode_synthesis(bad_sample, parameters())

        bad_frames = copy.deepcopy(document)
        bad_frames["volume_programs"][0]["steps"][0]["frames"] = 0
        with self.assertRaisesRegex(FdsSynthesisError, "1..255"):
            encode_synthesis(bad_frames, parameters())

        malformed_sample = bytearray(payload())
        malformed_sample[0] = 64
        with self.assertRaisesRegex(FdsSynthesisError, "0..63"):
            decode_synthesis(bytes(malformed_sample), parameters())

        malformed_frames = bytearray(payload())
        malformed_frames[65] = 0
        with self.assertRaisesRegex(FdsSynthesisError, "1..255"):
            decode_synthesis(bytes(malformed_frames), parameters())


if __name__ == "__main__":
    unittest.main()
