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

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

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
        source = str(Path(__file__).resolve().parents[1] / "src")
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


if __name__ == "__main__":
    unittest.main()
