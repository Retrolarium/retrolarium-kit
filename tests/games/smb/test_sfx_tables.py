from __future__ import annotations

import hashlib
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from retrolarium_kit.core.game_profiles import packaged_game_profiles
from retrolarium_kit.platform.nes.resources import (
    NesResourceBundleDocument, NesResourceError, _decode_profile_resource, _resource_specs, build_resources,
    decode_resources,
)
from retrolarium_kit.games.smb.sfx_tables import SmbSfxTableError, encode
from helpers import game_repository


# The images are not part of the repository; the test runs where the
# disassembly sits beside this checkout.
ROMS = game_repository("smb1_src")


def _images() -> dict[str, bytes]:
    profile = packaged_game_profiles().get("smb1")
    by_sha = {revision.sha256: revision.revision_id for revision in profile.revisions}
    images = {}
    if ROMS.is_dir():
        for path in [*ROMS.glob("*.nes"), *ROMS.glob("*.fds")]:
            data = path.read_bytes()
            revision = by_sha.get(hashlib.sha256(data).hexdigest())
            if revision:
                images[revision] = data
    return images


def _tables(document: NesResourceBundleDocument) -> dict:
    return next(item for item in document.resources if item["id"] == "sfx-tables")


class SmbSfxTableTests(unittest.TestCase):
    def test_note_tables_hold_even_period_offsets(self) -> None:
        parameters = {"records": [{"id": "t", "file_offset": 0, "size": 2, "kind": "note"}]}
        self.assertEqual([(0, bytes((2, 4)))], encode([{"id": "t", "values": [2, 4]}], parameters))
        with self.assertRaises(SmbSfxTableError):
            encode([{"id": "t", "values": [3, 4]}], parameters)

    def test_every_revision_decodes_its_tables(self) -> None:
        images = _images()
        if not images:
            self.skipTest("no Super Mario Bros. images are available")
        profile = packaged_game_profiles().get("smb1")
        for revision_id, data in images.items():
            revision = next(r for r in profile.revisions if r.revision_id == revision_id)
            spec = next(item for item in _resource_specs(profile, revision) if item.resource_id == "sfx-tables")
            decoded = _decode_profile_resource(data, profile, revision, spec)
            tables = {r["id"]: r["values"] for r in decoded["records"]}
            self.assertEqual([0x58, 0x02, 0x54, 0x56, 0x4E, 0x44], tables["extra_life"], revision_id)
            self.assertEqual([0x4C, 0x52, 0x4C], tables["power_up_grab"][:3], revision_id)
            self.assertEqual([0x14, 0x04, 0x22, 0x24], tables["power_up_reveal_and_vine"][:4], revision_id)
            self.assertEqual([0x01, 0x0E, 0x0E, 0x0D], tables["brick_shatter_noise"][:4], revision_id)

    def test_an_edited_byte_lands_in_place(self) -> None:
        data = _images().get("ju")
        if data is None:
            self.skipTest("the Super Mario Bros. (JU) image is not available")
        profile = packaged_game_profiles().get("smb1")
        revision = next(r for r in profile.revisions if r.revision_id == "ju")
        document = decode_resources(data, profile, revision)
        resources = []
        for item in document.resources:
            item = dict(item)
            if item["id"] == "sfx-tables":
                records = [dict(r, values=list(r["values"])) for r in item["records"]]
                records[0]["values"][0] = 0x5A
                item["records"] = records
            resources.append(item)
        edited = NesResourceBundleDocument(document.game_id, document.revision_id, document.source_sha256, tuple(resources))
        output, _report = build_resources(data, edited, profile, revision)
        self.assertEqual(0x5A, output[29924])
        self.assertEqual(1, sum(a != b for a, b in zip(data, output)))
        records = [dict(r) for r in _tables(edited)["records"]]
        records[3] = {"id": "brick_shatter_noise", "values": [0] * 15}
        broken = tuple(dict(item, records=records) if item["id"] == "sfx-tables" else item for item in resources)
        with self.assertRaises(NesResourceError):
            build_resources(data, NesResourceBundleDocument(document.game_id, document.revision_id, document.source_sha256, broken),
                            profile, revision)


if __name__ == "__main__":
    unittest.main()
