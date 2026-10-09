from __future__ import annotations

from pathlib import Path
import copy
import hashlib
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "retrolarium-core" / "python"))

from helpers import fds_side as fixture
from retrolarium_core.fds import (
    FdsError,
    fds_payload,
    fds_payload_ranges,
    parse_fds_side,
    replace_fds_payload,
)
from retrolarium_kit.core.authoring import (
    GameProfile,
    PayloadSpec,
    ResourceSpec,
    RevisionSpec,
)
from retrolarium_kit.platform.nes.resources import (
    build_resources,
    decode_resources,
    parse_resource_document,
)


class FdsTests(unittest.TestCase):
    def test_parses_records_and_joins_payload_in_requested_order(self) -> None:
        image = fixture()
        records = parse_fds_side(image)
        self.assertEqual([record.file_id for record in records], [3, 4])
        self.assertEqual([record.load_address for record in records], [0x6000, 0xA000])
        self.assertEqual(fds_payload(image, (3, 4)), b"ABCDE")
        self.assertEqual(fds_payload(image, (4, 3)), b"CDEAB")

    def test_replaces_only_selected_payload_bytes(self) -> None:
        image = fixture()
        replaced = replace_fds_payload(image, (3, 4), b"12345")
        self.assertEqual(fds_payload(replaced, (3, 4)), b"12345")
        records = parse_fds_side(image)
        for record in records:
            self.assertEqual(
                replaced[record.header_offset:record.data_offset],
                image[record.header_offset:record.data_offset],
            )
        self.assertEqual(len(replaced), len(image))

    def test_maps_logical_ranges_across_record_boundaries(self) -> None:
        image = fixture()
        ranges = fds_payload_ranges(image, (3, 4), 1, 4)
        records = parse_fds_side(image)
        self.assertEqual(
            [(item.file_offset, item.payload_offset, item.size) for item in ranges],
            [
                (records[0].data_offset + 1, 0, 1),
                (records[1].data_offset, 1, 3),
            ],
        )
        with self.assertRaisesRegex(FdsError, "exceeds 5 bytes"):
            fds_payload_ranges(image, (3, 4), 4, 2)

    def test_rejects_malformed_side_and_payload_contracts(self) -> None:
        image = fixture()
        trailing = bytearray(image)
        trailing[-1] = 1
        with self.assertRaisesRegex(FdsError, "nonzero data"):
            parse_fds_side(bytes(trailing))
        with self.assertRaisesRegex(FdsError, "non-empty and unique"):
            fds_payload(image, (3, 3))
        with self.assertRaisesRegex(FdsError, "not unique"):
            fds_payload(image, (9,))
        with self.assertRaisesRegex(FdsError, "expected 5"):
            replace_fds_payload(image, (3, 4), b"shorter")

    def test_resource_bundle_maps_edits_across_fds_records(self) -> None:
        image = fixture()
        revision = RevisionSpec(
            "fds", "FDS probe", len(image), hashlib.sha1(image).hexdigest(),
            hashlib.sha256(image).hexdigest(), "fds",
            payloads=(
                PayloadSpec(
                    "main", "fds-records", 5,
                    hashlib.sha1(b"ABCDE").hexdigest(), record_ids=(3, 4),
                ),
            ),
        )
        profile = GameProfile(
            "fds-probe", 1, "FDS probe", "nes", ("screens",), (revision,),
            (
                ResourceSpec(
                    "bundle", "resource-bundle", "nes.resource-bundle.v1", "fixed"
                ),
                ResourceSpec(
                    "screen", "screen", "nes.nametable.v1", "fixed",
                    {
                        "file_offset": 1, "size": 2, "width": 1,
                        "height": 1, "attribute_bytes": 1,
                    },
                    payload="main",
                ),
            ),
        )
        document = decode_resources(image, profile, revision)
        self.assertEqual(document.resources[0]["tiles"], [[ord("B")]])
        self.assertEqual(document.resources[0]["attributes"], [ord("C")])
        edited = copy.deepcopy(document.as_dict())
        edited["resources"][0]["tiles"] = [[9]]
        edited["resources"][0]["attributes"] = [8]
        parsed = parse_resource_document(edited, profile, revision)
        rebuilt, report = build_resources(image, parsed, profile, revision)
        self.assertEqual(fds_payload(rebuilt, (3, 4)), b"A\x09\x08DE")
        self.assertEqual(report["changed_bytes"], 2)
        self.assertEqual(len(report["resources"][0]["ranges"]), 2)


if __name__ == "__main__":
    unittest.main()
