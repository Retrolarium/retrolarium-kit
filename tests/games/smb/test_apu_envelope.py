from __future__ import annotations

import copy
import hashlib
import unittest

from retrolarium_kit.authoring import GameProfile, ResourceSpec, RevisionSpec
from retrolarium_kit.nes_resources import build_resources, decode_resources
from retrolarium_kit.smb_apu_envelope import (
    SmbApuEnvelopeError,
    decode_envelope,
    encode_envelope,
)


SOURCE = bytes((
    0x9F, 0x9B, 0x98, 0x96, 0x95, 0x94, 0x92, 0x90,
    0x90, 0x9A, 0x97, 0x95, 0x93, 0x92,
))
PARAMETERS = {"size": len(SOURCE)}


class SmbApuEnvelopeTests(unittest.TestCase):
    def test_resource_pipeline_round_trip_and_known_edit(self) -> None:
        header = b"NES\x1a" + bytes((1, 1, 0, 0)) + bytes(8)
        source = bytearray(header + bytes(0x4000) + bytes(0x2000))
        file_offset = 0x120
        source[file_offset:file_offset + len(SOURCE)] = SOURCE
        image = bytes(source)
        revision = RevisionSpec(
            "probe", "Envelope probe", len(image),
            hashlib.sha1(image).hexdigest(), hashlib.sha256(image).hexdigest(),
            "ines", mapper=0,
        )
        profile = GameProfile(
            "smb-envelope-probe", 1, "SMB envelope probe", "nes",
            ("audio",), (revision,),
            (
                ResourceSpec(
                    "bundle", "resource-bundle", "nes.resource-bundle.v1", "fixed"
                ),
                ResourceSpec(
                    "swim-stomp", "audio", "smb.apu-envelope.v1", "fixed",
                    {"file_offset": file_offset, **PARAMETERS},
                ),
            ),
        )

        document = decode_resources(image, profile, revision)
        rebuilt, report = build_resources(image, document, profile, revision)
        self.assertEqual(image, rebuilt)
        self.assertEqual(0, report["changed_bytes"])

        document.resources[0]["steps"][9]["volume"] = 4
        rebuilt, report = build_resources(image, document, profile, revision)
        self.assertEqual(0x94, rebuilt[file_offset + 9])
        self.assertEqual(1, report["changed_bytes"])

    def test_round_trip_matches_legacy_swim_stomp_envelope(self) -> None:
        document = decode_envelope(SOURCE, PARAMETERS)
        self.assertEqual(
            {"duty": 2, "mode": 1, "volume": 15}, document["steps"][0]
        )
        self.assertEqual(
            {"duty": 2, "mode": 1, "volume": 2}, document["steps"][-1]
        )
        self.assertEqual(SOURCE, encode_envelope(document, PARAMETERS))

    def test_known_edit_changes_only_one_packed_field(self) -> None:
        document = decode_envelope(SOURCE, PARAMETERS)
        document["steps"][4]["volume"] = 3
        encoded = encode_envelope(document, PARAMETERS)
        self.assertEqual(0x93, encoded[4])
        self.assertEqual(SOURCE[:4], encoded[:4])
        self.assertEqual(SOURCE[5:], encoded[5:])

    def test_rejects_wrong_sizes_fields_and_ranges(self) -> None:
        with self.assertRaisesRegex(SmbApuEnvelopeError, "13 bytes, expected 14"):
            decode_envelope(SOURCE[:-1], PARAMETERS)

        document = decode_envelope(SOURCE, PARAMETERS)
        missing = copy.deepcopy(document)
        del missing["steps"][0]["mode"]
        with self.assertRaisesRegex(SmbApuEnvelopeError, "fields differ"):
            encode_envelope(missing, PARAMETERS)

        too_short = copy.deepcopy(document)
        too_short["steps"].pop()
        with self.assertRaisesRegex(SmbApuEnvelopeError, "14 entries"):
            encode_envelope(too_short, PARAMETERS)

        for field, value, maximum in (
            ("duty", 4, 3),
            ("mode", 4, 3),
            ("volume", 16, 15),
        ):
            invalid = copy.deepcopy(document)
            invalid["steps"][0][field] = value
            with self.assertRaisesRegex(
                SmbApuEnvelopeError, f"0..{maximum}"
            ):
                encode_envelope(invalid, PARAMETERS)

        boolean = copy.deepcopy(document)
        boolean["steps"][0]["volume"] = True
        with self.assertRaisesRegex(SmbApuEnvelopeError, "integer"):
            encode_envelope(boolean, PARAMETERS)


if __name__ == "__main__":
    unittest.main()
