from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from retrolarium_kit.game_repository import (
    GameRepositoryError, identify_rom, ips_patch, load_descriptor, prepare_revision, revision_statuses,
)
from retrolarium_kit.studio_project import build_studio_project, load_studio_project, verify_studio_project


def apply_ips(original: bytes, patch: bytes) -> bytes:
    assert patch[:5] == b"PATCH"
    out = bytearray(original)
    at = 5
    while patch[at:at + 3] != b"EOF":
        offset = int.from_bytes(patch[at:at + 3], "big")
        size = int.from_bytes(patch[at + 3:at + 5], "big")
        data = patch[at + 5:at + 5 + size]
        if len(out) < offset + size:
            out.extend(bytes(offset + size - len(out)))
        out[offset:offset + size] = data
        at += 5 + size
    return bytes(out)


# The ROMs are not part of the repository; the descriptor tests run where
# the Pac-Man disassembly sits beside this checkout.
PACMAN = Path("D:/Projects/oranguthang/pacman_src")
V10 = PACMAN / "Pac-Man (J) (V1.0) [!].nes"
V11 = PACMAN / "Pac-Man (J) (V1.1) [!].nes"

DESCRIPTOR = """schema = "retrolarium.game"
schema_version = 1

[game]
id = "pacman"
name = "Pac-Man"
platform = "nes"
profile = "pacman.v1"

[[revisions]]
id = "japan-v10"
label = "Japan · v1.0"
rom = "pacman10.nes"

[[revisions]]
id = "japan-v11"
label = "Japan · v1.1"
rom = "pacman11.nes"

[workspace]
layout = "auto"

[[workspace.tabs]]
id = "maze"
title = "Maze"
kind = "level.tile-grid"
group = "content"
document = "resources.json"
codec = "pacman.maze-rle.v1"
capabilities = ["paint", "undo"]

[[workspace.tabs]]
id = "demo"
title = "Demo"
kind = "pacman.demo"
group = "content"
document = "demo.json"
resource = "attract-demo"
codec = "pacman.demo@1"
capabilities = ["edit", "undo"]
"""


class IpsTests(unittest.TestCase):
    def test_patch_round_trips_changes_and_growth(self) -> None:
        original = bytes(range(256)) * 4
        edited = bytearray(original)
        edited[3] = 0xFF
        edited[500:520] = bytes(20)
        edited += b"tail"
        patch = ips_patch(original, bytes(edited))
        self.assertEqual(bytes(edited), apply_ips(original, patch))
        self.assertEqual(b"PATCHEOF", ips_patch(original, original))

    def test_a_change_at_eof_offset_starts_a_byte_early(self) -> None:
        original = bytes(0x454F50)
        edited = bytearray(original)
        edited[0x454F46] = 1
        patch = ips_patch(original, bytes(edited))
        self.assertEqual(b"\x45\x4F\x45", patch[5:8])
        self.assertEqual(bytes(edited), apply_ips(original, patch))

    def test_shrinking_is_refused(self) -> None:
        with self.assertRaises(GameRepositoryError):
            ips_patch(bytes(4), bytes(2))


@unittest.skipUnless(V10.is_file() and V11.is_file(), "the Pac-Man ROMs are not available")
class DescriptorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        (self.root / "pacman.rlj").write_text(DESCRIPTOR, encoding="utf-8")
        self.descriptor = load_descriptor(self.root / "pacman.rlj")

    def tearDown(self) -> None:
        shutil.rmtree(self.root)

    def test_a_missing_rom_is_asked_for_and_a_wrong_one_is_named(self) -> None:
        self.assertEqual("missing", revision_statuses(self.descriptor)["revisions"][0]["rom_status"])
        self.assertEqual("needs-rom", prepare_revision(self.descriptor, "japan-v10")["status"])
        wrong = prepare_revision(self.descriptor, "japan-v10", rom=V11)
        self.assertEqual("rom-mismatch", wrong["status"])
        self.assertEqual("japan-v11", wrong["identified"][0]["revision"])
        self.assertFalse((self.root / "pacman10.nes").exists())
        self.assertEqual("japan-v10", identify_rom(V10)[0]["revision"])

    def test_prepare_build_and_patch(self) -> None:
        ready = prepare_revision(self.descriptor, "japan-v10", rom=V10)
        self.assertEqual("ready", ready["status"])
        self.assertEqual(V10.read_bytes(), (self.root / "pacman10.nes").read_bytes())
        self.assertEqual({"hack/japan-v10/resources.json", "hack/japan-v10/demo.json"},
                         set(ready["created_documents"]))
        project = Path(ready["project"])
        self.assertEqual(self.root / ".retrolarium" / "japan-v10", project.parent)
        inspection = load_studio_project(project)
        self.assertEqual("rom", inspection["project"]["source_mode"])
        self.assertEqual("Pac-Man [Japan · v1.0]", inspection["project"]["name"])
        # Unedited, the build is the ROM itself.
        report = build_studio_project(project)
        built = project.parent / report["artifact"]["path"]
        self.assertEqual(V10.read_bytes(), built.read_bytes())
        self.assertEqual("current", verify_studio_project(project)["status"])
        self.assertFalse(revision_statuses(self.descriptor)["revisions"][0]["edited"])
        # An edit in hack/ lands in the build; the ROM copy stays original.
        bundle_path = self.root / "hack" / "japan-v10" / "resources.json"
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        palette = next(r for r in bundle["resources"] if r["id"] == "round-gameplay")
        palette["values"][1] = (palette["values"][1] + 1) % 0x40
        bundle_path.write_text(json.dumps(bundle, indent=2), encoding="utf-8")
        self.assertEqual("mismatch", verify_studio_project(project)["status"])
        report = build_studio_project(project)
        edited = (project.parent / report["artifact"]["path"]).read_bytes()
        self.assertNotEqual(V10.read_bytes(), edited)
        self.assertEqual(V10.read_bytes(), (self.root / "pacman10.nes").read_bytes())
        self.assertEqual(edited, apply_ips(V10.read_bytes(), ips_patch(V10.read_bytes(), edited)))
        # Preparing again keeps the edits and the project.
        again = prepare_revision(self.descriptor, "japan-v10")
        self.assertEqual([], again["created_documents"])
        self.assertTrue(revision_statuses(self.descriptor)["revisions"][0]["edited"])


if __name__ == "__main__":
    unittest.main()
