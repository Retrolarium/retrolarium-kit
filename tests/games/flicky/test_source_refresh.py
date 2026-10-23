from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from helpers import game_repository
from retrolarium_kit.games.flicky.content import (
    ROM_NAME,
    SNAPSHOT_RECORD,
    SOURCE_DIRECTORY,
    WORKSPACE_FILES,
    FlickyContentError,
    import_flicky_source,
    refresh_flicky_source,
)


# The Flicky reconstruction and its ROM are not part of the repository; the
# test runs where they sit beside this checkout.
FLICKY = game_repository("flicky_src")
LEVELS = WORKSPACE_FILES["level_layouts"]


@unittest.skipUnless((FLICKY / ROM_NAME).is_file(), "the Flicky source and ROM are not available")
class FlickySourceRefreshTests(unittest.TestCase):
    """A project imported from an older Flicky source moves to the current one
    with its edits: what an older import lacks (config/names.json, scripts that
    newer kits call differently) no longer stops the build."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.project = self.root / "ue"
        import_flicky_source(FLICKY, FLICKY / ROM_NAME, self.project)
        self.snapshot = self.project / SOURCE_DIRECTORY
        self.original = self.project / "original"
        for relative in WORKSPACE_FILES.values():
            (self.original / relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.snapshot / relative, self.original / relative)

    def make_older(self) -> None:
        # An older source: a file the import holds that the repository no
        # longer has in that form.
        record_path = self.snapshot / SNAPSHOT_RECORD
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record["files"]["config/names.json"] = "0" * 64
        record_path.write_text(json.dumps(record), encoding="utf-8")

    def edit_levels(self, path: Path, key: str = "edited_by_the_test") -> object:
        levels = json.loads(path.read_text(encoding="utf-8"))
        levels[key] = True
        path.write_text(json.dumps(levels), encoding="utf-8")
        return levels

    def test_a_current_import_stays(self) -> None:
        self.assertIsNone(refresh_flicky_source(FLICKY, self.project, self.original))
        self.assertFalse((self.project / f"{SOURCE_DIRECTORY}.before-refresh").exists())

    def test_an_older_import_moves_with_its_edits(self) -> None:
        self.make_older()
        levels = self.edit_levels(self.snapshot / LEVELS)
        self.assertEqual([LEVELS], refresh_flicky_source(FLICKY, self.project, self.original))
        self.assertEqual(levels, json.loads((self.snapshot / LEVELS).read_text(encoding="utf-8")))
        self.assertTrue((self.project / f"{SOURCE_DIRECTORY}.before-refresh" / LEVELS).is_file())
        self.assertIsNone(refresh_flicky_source(FLICKY, self.project, self.original))

    def test_an_edit_on_a_changed_document_stops_the_refresh(self) -> None:
        self.make_older()
        # The edited document's first version is not the current source's.
        self.edit_levels(self.original / LEVELS, "an_older_first_version")
        levels = self.edit_levels(self.snapshot / LEVELS)
        with self.assertRaisesRegex(FlickyContentError, "changed under edited documents"):
            refresh_flicky_source(FLICKY, self.project, self.original)
        self.assertEqual(levels, json.loads((self.snapshot / LEVELS).read_text(encoding="utf-8")))
        self.assertFalse((self.project / f"{SOURCE_DIRECTORY}.before-refresh").exists())


if __name__ == "__main__":
    unittest.main()
