from __future__ import annotations

import ast
import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from retrolarium_kit.cli import main
from retrolarium_kit.core.errors import KitError
from retrolarium_kit.games.alien_soldier.lzss import LzssError
from retrolarium_kit.games.alien_soldier.tile_codec import TileCodecError
from retrolarium_kit.studio.repository import GameRepositoryError

PACKAGE = Path(__file__).resolve().parents[2] / "src" / "retrolarium_kit"


class KitErrorTests(unittest.TestCase):
    def test_every_kit_error_class_is_a_kit_error(self) -> None:
        # By the source: a class named *Error derives from KitError or from
        # another *Error of the kit (which does).
        offenders = []
        for path in sorted(PACKAGE.rglob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not isinstance(node, ast.ClassDef) or not node.name.endswith("Error") or node.name == "KitError":
                    continue
                bases = [base.id if isinstance(base, ast.Name) else getattr(base, "attr", "") for base in node.bases]
                if "KitError" not in bases and not any(
                    base.endswith("Error") and base not in ("ValueError", "RuntimeError") for base in bases
                ):
                    offenders.append(f"{path.relative_to(PACKAGE)}:{node.lineno} {node.name}")
        self.assertEqual([], offenders)

    def test_the_older_bases_are_kept(self) -> None:
        for error in (LzssError, TileCodecError, GameRepositoryError):
            with self.subTest(error=error.__name__):
                self.assertTrue(issubclass(error, KitError))
                self.assertTrue(issubclass(error, ValueError))

    def test_a_kit_error_from_any_module_is_one_line_without_a_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            rom = Path(folder) / "game.bin"
            rom.write_bytes(b"rom")
            for error in (LzssError("the stream ends inside a copy"), TileCodecError("too many tiles")):
                output, errors = io.StringIO(), io.StringIO()
                with (
                    self.subTest(error=type(error).__name__),
                    patch("retrolarium_kit.studio.repository.identify_rom", side_effect=error),
                    contextlib.redirect_stdout(output),
                    contextlib.redirect_stderr(errors),
                ):
                    status = main(["studio", "identify", str(rom)])
                self.assertEqual(1, status)
                self.assertEqual("", output.getvalue())
                self.assertEqual(f"error: {error}\n", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
