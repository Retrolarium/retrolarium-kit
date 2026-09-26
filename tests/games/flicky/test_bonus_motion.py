"""Bonus table inventory, fixed-size staging and safety checks."""

from pathlib import Path
import tempfile
import unittest

from retrolarium_kit.flicky_bonus_motion import (
    BONUS_SOURCE, TABLE_SPECS, export_bonus_motion,
    stage_bonus_motion, validate_bonus_motion,
)


def pointer_lines() -> list[str]:
    groups = (
        ("Bonus_ChickDelayPointers", "Bonus_ChickDelays",
         [0, 0, 0, 1, *([2] * 8)]),
        ("Bonus_ChickVelocityPointers", "Bonus_ChickVelocities",
         [0, 1, 2, 3, 4, 5, 6, 5, 0, 7, 8, 5]),
        ("Bonus_ChickTrajIndexPointers", "Bonus_ChickTrajIndex",
         [0, 0, 0, 0, 1, 2, 3, 4, 5, 6, 7, 8]),
    )
    return [f"{label}: dc.l " + ", ".join(f"{prefix}{index}"
            for index in targets) for label, prefix, targets in groups]


class FlickyBonusMotionTests(unittest.TestCase):
    def test_word_edit_is_staged_without_repacking_neighbors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "src" / BONUS_SOURCE
            staged_path = root / "stage" / BONUS_SOURCE
            source_path.parent.mkdir(parents=True)
            staged_path.parent.mkdir(parents=True)
            lines = ["; synthetic fixed-width source", *pointer_lines()]
            for identifier, kind in TABLE_SPECS:
                words = [0xFFFF] if kind == "trajectory" else [0, 1, 2]
                lines.append(f"{identifier}: dc.w    " + ", ".join(
                    f"${word:X}" for word in words))
            original = "\n".join(lines) + "\n"
            source_path.write_text(original, encoding="utf-8")
            staged_path.write_text(original, encoding="utf-8")
            baseline = export_bonus_motion(root)
            self.assertEqual(len(baseline), 30)
            self.assertEqual(baseline[0]["rounds"], [3, 7, 11])
            self.assertEqual(next(item for item in baseline
                                  if item["id"] == "Bonus_ChickVelocities8")[
                                      "rounds"], [43])
            document = {"bonus_motion": baseline}
            stage_bonus_motion(document, root, root / "stage")
            self.assertEqual(staged_path.read_text(encoding="utf-8"), original)
            baseline[0]["words"][1] = 15
            stage_bonus_motion(document, root, root / "stage")
            edited = staged_path.read_text(encoding="utf-8")
            self.assertIn("Bonus_ChickDelays0: dc.w    $0, $F, $2", edited)
            self.assertEqual(export_bonus_motion(root)[0]["words"], [0, 1, 2])
            self.assertEqual(len(export_bonus_motion(root)), 30)
            self.assertIn("Bonus_ChickVelocities8: dc.w    $0, $1, $2", edited)
            source_path.write_text(original.replace(
                "Bonus_ChickDelayPointers: dc.l Bonus_ChickDelays0",
                "Bonus_ChickDelayPointers: dc.l Bonus_ChickVelocities0", 1),
                encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "invalid Flicky bonus round aliases"):
                export_bonus_motion(root)

    def test_shape_and_pointer_indices_are_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "src" / BONUS_SOURCE
            source_path.parent.mkdir(parents=True)
            lines = pointer_lines()
            for identifier, kind in TABLE_SPECS:
                words = [0xFFFF] if kind == "trajectory" else [0, 1, 2]
                lines.append(f"{identifier}: dc.w " + ", ".join(
                    f"${word:X}" for word in words))
            source_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            records = export_bonus_motion(root)
            records[0]["rounds"] = [47]
            with self.assertRaisesRegex(ValueError, "round aliases changed"):
                validate_bonus_motion({"bonus_motion": records}, root)
            records = export_bonus_motion(root)
            del records[0]["rounds"]
            validate_bonus_motion({"bonus_motion": records}, root)
            records = export_bonus_motion(root)
            records[0]["words"].append(3)
            with self.assertRaisesRegex(ValueError, "invalid Flicky bonus words"):
                validate_bonus_motion({"bonus_motion": records}, root)
            records = export_bonus_motion(root)
            next(item for item in records if item["kind"] == "trajectory_index")[
                "words"][0] = 0x0900
            with self.assertRaisesRegex(ValueError, "outside 0..8"):
                validate_bonus_motion({"bonus_motion": records}, root)
            records = export_bonus_motion(root)
            next(item for item in records if item["kind"] == "trajectory")[
                "words"][-1] = 0
            with self.assertRaisesRegex(ValueError, "sentinel changed"):
                validate_bonus_motion({"bonus_motion": records}, root)


if __name__ == "__main__":
    unittest.main()
