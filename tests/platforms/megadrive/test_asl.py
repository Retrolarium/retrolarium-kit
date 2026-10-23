from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from retrolarium_kit.platform.megadrive.asl import AslSourceError, SourceTree, format_like, parse_number


MAIN = """; entry
                include "src/macros.inc"
                include "src/data.s"
"""
MACROS = """org0            macro   address
.diff           :=      address - *
                    if      .diff < 0
                    error   "too far"
                    while   .diff > 1024
                    dc.b    [1024]0
                endm
                endif
                endm
"""
DATA = """Table:          dc.b    1, $0A, %0011   ; three bytes
                dc.b    -2
Words:          dc.w    $1234,Table-Words
                if      Europe
Region:         dc.b    'E'
                else
Region:         dc.b    $4A
                endif
Art:            binclude "data/art.bin"
Code:           rts
"""


class SourceTreeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        (self.root / "src").mkdir()
        (self.root / "data").mkdir()
        (self.root / "src/main.s").write_text(MAIN, encoding="utf-8")
        (self.root / "src/macros.inc").write_text(MACROS, encoding="utf-8")
        (self.root / "src/data.s").write_text(DATA, encoding="utf-8")
        (self.root / "data/art.bin").write_bytes(b"\x01\x02\x03")

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_numbers_keep_their_notation(self) -> None:
        self.assertEqual([1, 10, 3, -2], [parse_number(text) for text in ("1", "$0A", "%0011", "-2")])
        self.assertIsNone(parse_number("Table-Words"))
        self.assertEqual("$0B", format_like("$0A", 11))
        self.assertEqual("$1FF", format_like("$A", 0x1FF))
        self.assertEqual("%0101", format_like("%0011", 5))

    def test_blocks_follow_the_conditionals_and_skip_macro_bodies(self) -> None:
        japan = SourceTree(self.root, defines={"Europe": 0})
        self.assertEqual(["Table", "Words", "Region", "Art", "Code"], japan.order)
        self.assertEqual(b"\x01\x0a\x03\xfe", japan.data("Table"))
        self.assertEqual(b"\x4a", japan.data("Region"))
        self.assertEqual(b"\x01\x02\x03", japan.data("Art"))
        self.assertIsNone(japan.block("Code").kind)
        europe = SourceTree(self.root, defines={"Europe": 1})
        self.assertEqual("'E'", europe.block("Region").operands[0].text)
        with self.assertRaises(AslSourceError):
            SourceTree(self.root)

    def test_writes_change_only_the_values_that_differ(self) -> None:
        tree = SourceTree(self.root, defines={"Europe": 0})
        self.assertFalse(tree.set_data("Table", b"\x01\x0a\x03\xfe"))
        self.assertTrue(tree.set_values("Table", [1, 11, 3, -3]))
        self.assertTrue(tree.set_data("Art", b"\x09\x09\x09"))
        self.assertEqual(["data/art.bin", "src/data.s"], tree.save())
        text = (self.root / "src/data.s").read_text(encoding="utf-8")
        self.assertIn("Table:          dc.b    1, $0B, %0011   ; three bytes", text)
        self.assertIn("                dc.b    -3", text)
        self.assertEqual(b"\x09\x09\x09", (self.root / "data/art.bin").read_bytes())
        self.assertEqual(b"\x01\x0b\x03\xfd", SourceTree(self.root, defines={"Europe": 0}).data("Table"))

    def test_symbolic_operands_and_widths_are_kept(self) -> None:
        tree = SourceTree(self.root, defines={"Europe": 0})
        with self.assertRaises(AslSourceError):
            tree.set_values("Words", [0x1234, 6])
        with self.assertRaises(AslSourceError):
            tree.set_values("Table", [256, 10, 3, -2])
        with self.assertRaises(AslSourceError):
            tree.set_data("Art", b"\x00")


if __name__ == "__main__":
    unittest.main()
