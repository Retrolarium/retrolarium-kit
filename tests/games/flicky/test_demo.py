from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from retrolarium_kit.flicky_demo import (
    DEMO_SOURCE, encode_steps, export_attract_demos, stage_attract_demos,
    validate_attract_demos,
)


SOURCE = """Demo_ReadInput:
                rts

Demo_InputStream0:  binclude "data/other/stream0.bin"
Demo_InputStream0_End:
Demo_InputStream1:  dc.w    $12, $801, $A00
Demo_InputStream2:  binclude "data/other/stream2.bin"
Demo_InputStream2_End:
Demo_InputStream3:  binclude "data/other/stream3.bin"
Demo_InputStream3_End:
; Player main object
Player_Main:
                rts
"""


def fixture(root: Path) -> None:
    (root / "src/game/screens").mkdir(parents=True)
    (root / "data/other").mkdir(parents=True)
    (root / "src" / DEMO_SOURCE).write_text(SOURCE, encoding="utf-8")
    (root / "data/other/stream0.bin").write_bytes(bytes((0x00, 0x12, 0x48, 0x05, 0x00, 0x00)))
    (root / "data/other/stream2.bin").write_bytes(bytes((0x04, 0x2C)))
    (root / "data/other/stream3.bin").write_bytes(bytes((0x80, 0x01, 0x00, 0x00)))


class FlickyDemoTests(unittest.TestCase):
    def test_export_decodes_pairs(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            fixture(root)
            demos = export_attract_demos(root)
            self.assertEqual([1, 10, 20, 24], [demo["round"] for demo in demos])
            self.assertEqual([{"frames": 18, "buttons": []},
                              {"frames": 5, "buttons": ["A", "RIGHT"]},
                              {"frames": 256, "buttons": []}], demos[0]["steps"])
            self.assertEqual([(0x12, []), (1, ["RIGHT"]), (256, ["DOWN", "RIGHT"])],
                             [(s["frames"], s["buttons"]) for s in demos[1]["steps"]])
            for demo in demos:
                self.assertEqual(bytes(demo["baseline_bytes"]), encode_steps(demo["steps"]))

    def test_unchanged_demos_leave_the_source(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            fixture(root)
            staged = root / "staged"
            (staged / "game/screens").mkdir(parents=True)
            (staged / DEMO_SOURCE).write_text(SOURCE, encoding="utf-8")
            stage_attract_demos({"attract_demos": export_attract_demos(root)}, root, staged)
            self.assertEqual(SOURCE, (staged / DEMO_SOURCE).read_text(encoding="utf-8"))

    def test_edits_pack_the_block_and_keep_its_size(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            fixture(root)
            staged = root / "staged"
            (staged / "game/screens").mkdir(parents=True)
            (staged / DEMO_SOURCE).write_text(SOURCE, encoding="utf-8")
            demos = export_attract_demos(root)
            demos[3]["steps"] = [{"frames": 3, "buttons": ["B"]}]
            stage_attract_demos({"attract_demos": demos}, root, staged)
            text = (staged / DEMO_SOURCE).read_text(encoding="utf-8")
            self.assertIn("Demo_InputStream3:  dc.b    $10, $03\n", text)
            self.assertIn("Demo_InputStream3_End:\n                dc.b    $00, $00\n", text)
            self.assertTrue(text.endswith("; Player main object\nPlayer_Main:\n                rts\n"))
            restaged = [bytes(demo["baseline_bytes"]) for demo in demos]
            self.assertEqual(sum(map(len, restaged)), 6 + 6 + 2 + 4)

    def test_capacity_and_limits(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            fixture(root)
            demos = export_attract_demos(root)
            demos[2]["steps"] = demos[2]["steps"] * 4
            with self.assertRaisesRegex(ValueError, "block holds 18"):
                validate_attract_demos({"attract_demos": demos}, root)
            demos = export_attract_demos(root)
            demos[0]["steps"][0]["frames"] = 257
            with self.assertRaisesRegex(ValueError, "1-256 frames"):
                validate_attract_demos({"attract_demos": demos}, root)
            demos = export_attract_demos(root)
            demos[0]["steps"][0]["buttons"] = ["X"]
            with self.assertRaisesRegex(ValueError, "buttons are"):
                validate_attract_demos({"attract_demos": demos}, root)
            demos = export_attract_demos(root)
            demos[1]["round"] = 2
            with self.assertRaisesRegex(ValueError, "identity"):
                validate_attract_demos({"attract_demos": demos}, root)
            validate_attract_demos({}, root)


if __name__ == "__main__":
    unittest.main()
