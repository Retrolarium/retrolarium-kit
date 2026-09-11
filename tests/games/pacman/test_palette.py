from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from retrolarium_kit.authoring import GameProfile, ResourceSpec, RevisionSpec
from retrolarium_kit.nes_resources import build_resources, decode_resources, parse_resource_document


def fixture() -> tuple[bytes, GameProfile, RevisionSpec]:
    values = bytearray(range(32))
    for left, right in ((0, 16), (4, 20), (8, 24), (12, 28)):
        values[right] = values[left]
    image = b"NES\x1a" + bytes((1, 1, 0, 0)) + bytes(8) \
        + bytes(values) + bytes(0x4000 - 32) + bytes(0x2000)
    revision = RevisionSpec(
        "probe-v1", "palette probe", len(image), hashlib.sha1(image).hexdigest(),
        hashlib.sha256(image).hexdigest(), "ines", 0,
    )
    pairs = [[0, 16], [4, 20], [8, 24], [12, 28]]
    profile = GameProfile(
        "palette-probe", 1, "palette probe", "nes", ("palettes",),
        (revision,),
        (
            ResourceSpec("bundle", "resource-bundle", "nes.resource-bundle.v1", "fixed"),
            ResourceSpec("palette", "palette", "pacman.palette-table.v1", "fixed", {
                "file_offset": 16, "size": 32, "entries": 32,
                "mirror_pairs": pairs,
            }),
        ),
    )
    return image, profile, revision


class PacmanPaletteTests(unittest.TestCase):
    def test_mirrored_palette_round_trips_and_edits_both_bytes(self) -> None:
        image, profile, revision = fixture()
        document = decode_resources(image, profile, revision)
        exact, report = build_resources(image, document, profile, revision)
        self.assertEqual(exact, image)
        self.assertEqual(report["changed_bytes"], 0)
        raw = copy.deepcopy(document.as_dict())
        raw["resources"][0]["values"][0] = 0x2A
        raw["resources"][0]["values"][16] = 0x2A
        edited, report = build_resources(
            image, parse_resource_document(raw, profile, revision), profile, revision
        )
        self.assertEqual((edited[16], edited[32]), (0x2A, 0x2A))
        self.assertEqual(report["changed_bytes"], 2)

    def test_unsynchronized_pair_and_changed_pair_contract_are_rejected(self) -> None:
        image, profile, revision = fixture()
        raw = decode_resources(image, profile, revision).as_dict()
        raw["resources"][0]["values"][0] = 2
        with self.assertRaisesRegex(Exception, "mirrored colors"):
            parse_resource_document(raw, profile, revision)
        raw = decode_resources(image, profile, revision).as_dict()
        raw["resources"][0]["mirror_pairs"] = []
        with self.assertRaisesRegex(Exception, "differ from the profile"):
            parse_resource_document(raw, profile, revision)

    def test_replicated_palette_values_decode_and_write_every_copy(self) -> None:
        image = bytearray(b"NES\x1a" + bytes((1, 0, 0, 0)) + bytes(8) + bytes(0x4000))
        image[20] = image[40] = 0x12
        image[60] = 0x23
        image = bytes(image)
        revision = RevisionSpec(
            "probe-v1", "replicated palette probe", len(image),
            hashlib.sha1(image).hexdigest(), hashlib.sha256(image).hexdigest(),
            "ines", 0,
        )
        profile = GameProfile(
            "palette-probe", 1, "palette probe", "nes", ("palettes",),
            (revision,), (
                ResourceSpec("bundle", "resource-bundle", "nes.resource-bundle.v1", "fixed"),
                ResourceSpec("dynamic", "palette", "pacman.palette-table.v1", "fixed", {
                    "entries": 2,
                    "replicated_file_offsets": [[20, 40], [60]],
                    "mirror_pairs": [],
                }),
            ),
        )
        document = decode_resources(image, profile, revision)
        self.assertEqual(document.resources[0]["values"], [0x12, 0x23])
        raw = document.as_dict()
        raw["resources"][0]["values"] = [0x2A, 0x31]
        edited, report = build_resources(
            image, parse_resource_document(raw, profile, revision), profile, revision
        )
        self.assertEqual((edited[20], edited[40], edited[60]), (0x2A, 0x2A, 0x31))
        self.assertEqual((report["resources"][0]["size"], report["changed_bytes"]), (3, 3))

        mismatched = bytearray(image)
        mismatched[40] = 0x13
        bad_revision = RevisionSpec(
            "bad", "bad", len(mismatched), hashlib.sha1(mismatched).hexdigest(),
            hashlib.sha256(mismatched).hexdigest(), "ines", 0,
        )
        with self.assertRaisesRegex(Exception, "replicas differ"):
            decode_resources(bytes(mismatched), profile, bad_revision)


if __name__ == "__main__":
    unittest.main()
