from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from retrolarium_kit.cli import main
from retrolarium_kit.studio.project import StudioProjectError


def run(argv: list[str]) -> tuple[int, dict[str, object] | None, str]:
    output, errors = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
        status = main(argv)
    text = output.getvalue()
    return status, json.loads(text) if text.strip() else None, errors.getvalue()


class CliTests(unittest.TestCase):
    def test_help_loads_no_codecs(self) -> None:
        # The Studio calls the kit often; starting it must not import the
        # games, the platform codecs or the Studio project module.
        script = (
            "import contextlib, io, sys\n"
            "from retrolarium_kit.cli import main\n"
            "with contextlib.redirect_stdout(io.StringIO()):\n"
            "    try:\n"
            "        main(['--help'])\n"
            "    except SystemExit:\n"
            "        pass\n"
            "print(sorted(m for m in sys.modules if m.startswith('retrolarium_kit.')\n"
            "             and m.split('.')[1] in ('games', 'platform', 'studio', 'core')))\n"
        )
        source = str(Path(__file__).resolve().parents[2] / "src")
        environment = {"PYTHONPATH": source, "SYSTEMROOT": os.environ.get("SYSTEMROOT", "")}
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                                env=environment, check=True)
        self.assertEqual("[]", result.stdout.strip())

    def test_build_runs_a_plan_in_the_same_process(self) -> None:
        plan = {"program": "consoles.exe", "arguments": ["nes"]}
        with patch("retrolarium_kit.studio.project.build_studio_project",
                   return_value={"status": "built", "artifact": {"path": "build/game.nes"}}), \
                patch("retrolarium_kit.studio.playtest.studio_launch_plan", return_value=plan) as planning:
            status, result, _ = run([
                "studio", "build", "game.rlj", "--plan-json",
                json.dumps(["studio", "launch-plan", "game.rlj", "--mame", "consoles.exe"]),
            ])
        self.assertEqual(0, status)
        self.assertEqual("built", result["status"])
        self.assertEqual(plan, result["plan"])
        self.assertEqual(Path("game.rlj"), planning.call_args.args[0])

    def test_a_failed_plan_keeps_the_build(self) -> None:
        with patch("retrolarium_kit.studio.project.build_studio_project",
                   return_value={"status": "built", "artifact": {}}), \
                patch("retrolarium_kit.studio.playtest.studio_launch_plan",
                      side_effect=StudioProjectError("no ROM to play")):
            status, result, _ = run([
                "studio", "build", "game.rlj", "--plan-json",
                json.dumps(["studio", "launch-plan", "game.rlj", "--mame", "consoles.exe"]),
            ])
        self.assertEqual(0, status)
        self.assertEqual("built", result["status"])
        self.assertEqual("no ROM to play", result["plan_error"])
        self.assertNotIn("plan", result)

    def test_a_plan_that_is_not_a_command_is_reported(self) -> None:
        with patch("retrolarium_kit.studio.project.build_studio_project",
                   return_value={"status": "built", "artifact": {}}):
            status, result, errors = run([
                "studio", "build", "game.rlj", "--plan-json", '["studio", "no-such-plan"]',
            ])
        self.assertEqual(0, status)
        self.assertIn("invalid choice", result["plan_error"])
        self.assertEqual("", errors)

    def test_errors_from_loaded_modules_are_one_line(self) -> None:
        with patch("retrolarium_kit.studio.project.load_studio_project",
                   side_effect=StudioProjectError("broken project")):
            status, result, errors = run(["studio", "inspect", "game.rlj"])
        self.assertEqual(1, status)
        self.assertIsNone(result)
        self.assertEqual("error: broken project\n", errors)

    def test_fds_write_fills_disk_files_with_built_programs(self) -> None:
        import tempfile

        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from helpers import fds_side

        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "side.fds").write_bytes(fds_side())
            (folder / "program.bin").write_bytes(b"xyzuv")
            status, _, _ = run(["fds", "write", "--disk", str(folder / "side.fds"),
                                "--file", f"3,4={folder / 'program.bin'}", "--output", str(folder / "out.fds")])
            written = (folder / "out.fds").read_bytes()
            self.assertEqual(0, status)
            self.assertIn(b"xy", written)
            self.assertIn(b"zuv", written)
            self.assertNotIn(b"CDE", written)
            # The files must be filled exactly, and named once.
            status, _, errors = run(["fds", "write", "--disk", str(folder / "side.fds"),
                                     "--file", f"3={folder / 'program.bin'}", "--output", str(folder / "bad.fds")])
            self.assertEqual((1, False), (status, (folder / "bad.fds").exists()))
            self.assertIn("expected 2", errors)
            (folder / "two.bin").write_bytes(b"ab")
            status, _, errors = run(["fds", "write", "--disk", str(folder / "side.fds"),
                                     "--file", f"3={folder / 'two.bin'}", "--file", f"3={folder / 'two.bin'}",
                                     "--output", str(folder / "bad.fds")])
            self.assertEqual(1, status)
            self.assertIn("named twice", errors)


    def test_assets_path_names_the_file_an_image_is_read_from(self) -> None:
        import tempfile

        kit = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "assets").mkdir()
            (root / "assets" / "manifest.json").write_text(json.dumps({
                "schema": "retrolarium.assets", "schema_version": 1, "output": "assets/generated",
                "images": [{"id": "base", "file": "My Game (U).nes", "size": 1, "sha1": "0" * 40}],
                "assets": []}), encoding="utf-8")
            command = [sys.executable, str(kit / "rltool.py"), "assets", "path"]
            found = subprocess.run(command + ["--image", "base"], cwd=root, capture_output=True, text=True)
            missing = subprocess.run(command + ["--image", "other"], cwd=root, capture_output=True, text=True)
        self.assertEqual(0, found.returncode, found.stderr)
        self.assertEqual(f"{root.resolve().as_posix()}/My Game (U).nes", found.stdout.strip())
        self.assertEqual(1, missing.returncode)
        self.assertIn("no image 'other'", missing.stderr)


if __name__ == "__main__":
    unittest.main()
