from __future__ import annotations

from pathlib import Path
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from retrolarium_kit.flicky_title_layout import (
    SOURCE, export_title_layout, stage_title_layout, validate_title_layout,
)


# The Flicky reconstruction is not part of the repository; the test runs
# where it sits beside this checkout.
FLICKY = Path("D:/Projects/oranguthang/flicky_src")


@unittest.skipUnless((FLICKY / "src" / SOURCE).is_file(), "the Flicky source is not available")
class FlickyTitleLayoutTests(unittest.TestCase):
    def test_title_sprites_are_word_pairs_across_both_labels(self) -> None:
        layout = export_title_layout(FLICKY)
        self.assertEqual(["statics", "cast"], [r["id"] for r in layout])
        self.assertEqual([0xD0, 0xC0], layout[0]["positions"][0])
        self.assertEqual([[0xB0, 0xF0], [0x110, 0xEC], [0xB0, 0x108], [0x110, 0x100]], layout[1]["positions"])

    def test_edited_positions_are_staged_and_checked(self) -> None:
        layout = export_title_layout(FLICKY)
        layout[1]["positions"][0] = [0xC0, 0xF8]
        staged = Path(tempfile.mkdtemp())
        try:
            (staged / SOURCE).parent.mkdir(parents=True)
            shutil.copy(FLICKY / "src" / SOURCE, staged / SOURCE)
            stage_title_layout({"title_layout": layout}, FLICKY, staged)
            text = (staged / SOURCE).read_text(encoding="utf-8")
            self.assertIn("dc.w    $C0\n", text)
            self.assertIn("dc.w    $F8, $110, $EC, $B0, $108, $110, $100", text)
        finally:
            shutil.rmtree(staged)
        layout[0]["positions"] = layout[0]["positions"][:5]
        with self.assertRaises(ValueError):
            validate_title_layout({"title_layout": layout}, FLICKY)


if __name__ == "__main__":
    unittest.main()
