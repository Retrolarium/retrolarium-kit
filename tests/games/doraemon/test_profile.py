from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from retrolarium_kit.game_profiles import packaged_game_profiles
from retrolarium_kit.nes_resources import (
    build_resources,
    decode_resources,
    parse_resource_document,
)
from retrolarium_kit.studio_project import create_studio_project


class DoraemonProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = packaged_game_profiles().get("doraemon")

    def test_revision_identity_and_gnrom_chr_spans(self) -> None:
        self.assertEqual(
            [(revision.revision_id, revision.sha256, revision.mapper)
             for revision in self.profile.revisions],
            [
                ("original", "6ed579c9c98a1f2db52fd3d2488a491953073e8ace1e3c1dc5884669cecca274", 66),
                ("rev_a", "c070fa0e07a4b8be107ecc2f514e4c64f5bbb2a15754b6c6214e7259a1b30920", 66),
            ],
        )
        for revision in self.profile.revisions:
            self.assertEqual(revision.size, 16 + 8 * 16384 + 4 * 8192)
            resources = self.profile.resources_for(revision)
            self.assertEqual(resources[0].parameters["resource_ids"], [
                "chr-bank-0", "chr-bank-1", "chr-bank-2", "chr-bank-3"
            ])
            self.assertEqual(
                [(resource.parameters["file_offset"], resource.parameters["size"])
                 for resource in resources[1:]],
                [(16 + 8 * 16384 + bank * 8192, 8192) for bank in range(4)],
            )

    def test_all_banks_round_trip_and_isolated_pixel_edit(self) -> None:
        # A private retail ROM is not required to verify the profile's physical
        # bank layout and the generic GUI graphics document contract.
        header = b"NES\x1a" + bytes((8, 4, 0x20, 0x40)) + bytes(8)
        prg = bytes(8 * 16384)
        chr_data = bytes((index * 17 + index // 8192) & 0xFF
                         for index in range(4 * 8192))
        image = header + prg + chr_data
        revision = replace(
            self.profile.revisions[0],
            sha1=hashlib.sha1(image).hexdigest(),
            sha256=hashlib.sha256(image).hexdigest(),
        )
        document = decode_resources(image, self.profile, revision)
        self.assertEqual([len(item["tiles"]) for item in document.resources],
                         [512, 512, 512, 512])
        rebuilt, report = build_resources(image, document, self.profile, revision)
        self.assertEqual(rebuilt, image)
        self.assertEqual(report["changed_bytes"], 0)

        edited = document.as_dict()
        for bank, resource in enumerate(edited["resources"]):
            pixel = resource["tiles"][bank][0][0]
            resource["tiles"][bank][0][0] = (pixel + 1) % 4
        parsed = parse_resource_document(edited, self.profile, revision)
        rebuilt, report = build_resources(image, parsed, self.profile, revision)
        self.assertGreater(report["changed_bytes"], 0)
        self.assertEqual(rebuilt[:16 + 8 * 16384], image[:16 + 8 * 16384])
        changed_banks = {
            (offset - (16 + 8 * 16384)) // 8192
            for offset, (before, after) in enumerate(zip(image, rebuilt))
            if before != after
        }
        self.assertEqual(changed_banks, {0, 1, 2, 3})

    def test_graphics_document_creates_studio_pixel_editor(self) -> None:
        revision = self.profile.revisions[0]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = root / "disassembly" / "manifest.json"
            manifest_path.parent.mkdir()
            manifest_path.write_text("{}", encoding="utf-8")
            document_path = root / "graphics.json"
            resource_document = {
                "schema": "retrolarium.nes-resource-bundle",
                "schema_version": 1,
                "game": "doraemon",
                "revision": "original",
                "source_sha256": revision.sha256,
                "resources": [
                    {"id": f"chr-bank-{bank}", "kind": "graphics",
                     "codec": "nes.chr-2bpp.v1",
                     "tiles": [[[0] * 8 for _ in range(8)] for _ in range(512)]}
                    for bank in range(4)
                ],
            }
            parse_resource_document(resource_document, self.profile, revision)
            document_path.write_text(json.dumps(resource_document), encoding="utf-8")
            with patch(
                "retrolarium_kit.studio_project.load_disassembly_manifest",
                return_value={
                    "schema": "retrolarium.nes.disassembly",
                    "schema_version": 1,
                    "input": {"sha256": revision.sha256, "size": revision.size},
                },
            ):
                inspection = create_studio_project(
                    manifest_path, root / "doraemon.rlj",
                    project_id="doraemon", name="Doraemon",
                    profile="doraemon.v1", resource_document=document_path,
                )
            graphics = next(tab for tab in inspection["workspace"]["tabs"]
                            if tab["id"] == "graphics")
            self.assertEqual(graphics["kind"], "graphics.tile-set")
            self.assertEqual(graphics["codec"], "nes.chr-2bpp.v1")
            self.assertIn("pixel-paint", graphics["capabilities"])


if __name__ == "__main__":
    unittest.main()
