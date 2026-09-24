"""Portable Flicky source snapshot safety checks (no private ROM required)."""

from pathlib import Path
import tempfile
import unittest

from retrolarium_kit.flicky_content import (
    CANONICAL_SIZE,
    FlickyContentError,
    import_flicky_source,
)


class FlickyContentTests(unittest.TestCase):
    def test_wrong_revision_is_rejected_before_project_creation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rom = root / "other.bin"
            rom.write_bytes(b"\0" * CANONICAL_SIZE)
            project = root / "project"
            with self.assertRaisesRegex(FlickyContentError, "exact canonical"):
                import_flicky_source(root, rom, project)
            self.assertFalse(project.exists())

    def test_missing_rom_is_rejected_before_git_probe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(FlickyContentError, "exact canonical"):
                import_flicky_source(root, root / "missing.bin", root / "project")


if __name__ == "__main__":
    unittest.main()
