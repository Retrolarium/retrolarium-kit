"""rltool lint: a game's config/lint.json, the text contract and the command."""

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401  (src/ on sys.path)
from retrolarium_kit.cli import main
from retrolarium_kit.core.lint import LintError, load_config, run
from retrolarium_kit.core.lint.runner import parse_config
from retrolarium_kit.core.lint import text

CONFIG = {
    "schema": "retrolarium.lint",
    "schema_version": 1,
    "assembly": {"dialect": "ca65", "paths": ["src"]},
    "text": {"json": "indent", "skip": ["references"]},
}


def repository(root: Path, config: dict = CONFIG) -> None:
    (root / "config").mkdir()
    (root / "config" / "lint.json").write_bytes((json.dumps(config, indent=2) + "\n").encode())
    (root / "src").mkdir()
    (root / "src" / "main.asm").write_bytes(b"Start:\n    RTS\n")
    (root / "notes.md").write_bytes(b"# Notes\n")


class ConfigTests(unittest.TestCase):
    def test_sections_and_settings_are_read(self) -> None:
        config = parse_config(CONFIG)
        self.assertEqual(config.sections, ("assembly", "text"))
        self.assertEqual((config.dialect, config.paths, config.json_layout), ("ca65", ("src",), "indent"))
        asl = parse_config({"schema": "retrolarium.lint", "schema_version": 1,
                            "assembly": {"dialect": "asl", "comment_column": 48}})
        self.assertEqual(asl.style.comment_column, 48)

    def test_unknown_settings_are_refused(self) -> None:
        for document in (
            {**CONFIG, "schema_version": 2},
            {**CONFIG, "extra": {}},
            {**CONFIG, "assembly": {"dialect": "nasm"}},
            {**CONFIG, "assembly": {"dialect": "ca65", "comment_column": 40}},
            {**CONFIG, "text": {"json": "pretty"}},
        ):
            with self.assertRaises(LintError):
                parse_config(document)


class RunTests(unittest.TestCase):
    def test_a_clean_tree_passes_and_fix_repairs_a_dirty_one(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository(root)
            config = load_config(root)
            self.assertEqual(run(root, config, report=lambda line: None), [])
            (root / "src" / "main.asm").write_bytes(b"start: rts\n")
            (root / "data.json").write_bytes(b'{"b": 1, "a": [1,2]}')
            (root / "references").mkdir()
            (root / "references" / "loose.txt").write_bytes(b"left alone  \r\n")
            codes = {issue.code for issue in run(root, config, report=lambda line: None)}
            self.assertEqual(codes, {"label-line", "text-format"})
            self.assertEqual(run(root, config, fix=True, report=lambda line: None), [])
            self.assertEqual((root / "data.json").read_text(encoding="utf-8"),
                             '{\n  "b": 1,\n  "a": [\n    1,\n    2\n  ]\n}\n')
            self.assertEqual((root / "references" / "loose.txt").read_bytes(), b"left alone  \r\n")

    def test_valid_json_keeps_its_layout(self) -> None:
        path_config = {**CONFIG, "text": {"json": "valid"}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository(root, path_config)
            (root / "data.json").write_bytes(b'{"b": 1, "a": [1,2]}\n')
            (root / "empty.py").write_bytes(b"")
            self.assertEqual(run(root, load_config(root), report=lambda line: None), [])
            (root / "broken.json").write_bytes(b"{\n")
            codes = {issue.code for issue in run(root, load_config(root), report=lambda line: None)}
            self.assertEqual(codes, {"json"})

    def test_ignored_files_and_studio_folders_are_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository(root)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            (root / ".gitignore").write_bytes(b"tmp/\n")
            for folder in ("tmp", "hack", ".retrolarium", "build"):
                (root / folder).mkdir()
                (root / folder / "x.json").write_bytes(b"{bad")
            names = {path.relative_to(root).as_posix() for path in text.project_files(root)}
            self.assertEqual(names, {".gitignore", "config/lint.json", "notes.md", "src/main.asm"})

    def test_the_command_reports_and_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository(root)
            (root / "src" / "main.asm").write_bytes(b"start: rts\n")
            errors = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(errors):
                self.assertEqual(main(["lint", "--project", str(root)]), 1)
            self.assertIn("src/main.asm:1: [label-line]", errors.getvalue())
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["lint", "--project", str(root), "--fix", "--only", "assembly",
                                       "src/main.asm"]), 0)
            self.assertEqual((root / "src" / "main.asm").read_bytes(), b"start:\n    RTS\n")


class HooksCommandTests(unittest.TestCase):
    def test_the_hooks_reach_this_kit_through_rltool(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(["hooks", "install", "--project", str(root)]), 0)
            hook = (root / ".git" / "hooks" / "pre-commit").read_text(encoding="utf-8")
            self.assertIn("export RLTOOL=", hook)
            self.assertIn("rltool.py", hook)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["hooks", "uninstall", "--project", str(root)]), 0)
            self.assertFalse((root / ".git" / "hooks" / "pre-commit").exists())


if __name__ == "__main__":
    unittest.main()
