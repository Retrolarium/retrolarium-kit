"""Every build step the kit starts is bounded and reads any output bytes."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from retrolarium_kit.core import errors, project
from retrolarium_kit.games.flicky.workspace import build as flicky_build
from retrolarium_kit.games.flicky.workspace.content import FlickyWorkspaceError
from retrolarium_kit.platform.megadrive import build as megadrive_build
from retrolarium_kit.studio import repository

SLEEP = [sys.executable, "-c", "import time; time.sleep(10)"]
# Bytes that are not UTF-8 (nor text in most code pages) on stdout.
GARBAGE = [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'\\xff\\xfe\\n'); sys.exit(1)"]


class TimeoutTests(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def test_the_kit_and_the_studio_agree_on_the_bound(self) -> None:
        self.assertEqual(15 * 60, errors.BUILD_TIMEOUT)
        self.assertEqual(errors.BUILD_TIMEOUT, repository.SCRIPT_TIMEOUT)

    def test_a_project_build_that_hangs_is_a_project_error(self) -> None:
        with (
            mock.patch.object(project, "BUILD_TIMEOUT", 0.5),
            mock.patch("sys.stderr"),
            self.assertRaisesRegex(project.ProjectError, "did not finish"),
        ):
            project._run_build_process(SLEEP, project_directory=self.root)

    def test_a_mega_drive_tool_that_hangs_is_a_build_error(self) -> None:
        with (
            mock.patch.object(megadrive_build, "BUILD_TIMEOUT", 0.5),
            self.assertRaisesRegex(megadrive_build.MegaDriveBuildError, "did not finish"),
        ):
            megadrive_build._tool(SLEEP, self.root, dict(os.environ))

    def test_a_flicky_step_that_hangs_is_a_workspace_error(self) -> None:
        with (
            mock.patch.object(flicky_build, "BUILD_TIMEOUT", 0.5),
            self.assertRaisesRegex(FlickyWorkspaceError, "did not finish"),
        ):
            flicky_build._run(SLEEP, self.root, dict(os.environ))


class OutputDecodingTests(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def test_a_mega_drive_tool_may_print_any_bytes(self) -> None:
        result = megadrive_build._tool(GARBAGE, self.root, dict(os.environ))
        self.assertEqual((1, "��"), (result.returncode, result.stdout.strip()))

    def test_a_flicky_step_may_print_any_bytes(self) -> None:
        with self.assertRaisesRegex(FlickyWorkspaceError, "��"):
            flicky_build._run(GARBAGE, self.root, dict(os.environ))

    def test_a_repository_script_may_print_any_bytes(self) -> None:
        (self.root / "split.py").write_text(
            "import sys\nsys.stdout.buffer.write(b'\\xff\\xfe\\n')\nsys.exit(1)\n", encoding="utf-8"
        )
        image = b"image"
        (self.root / "image.bin").write_bytes(image)
        descriptor = repository.GameDescriptor(
            self.root / "game.rlj",
            self.root,
            "probe",
            "Probe",
            "megadrive",
            "probe.v1",
            (repository.Revision("base", "Base", "image.bin", hashlib.sha256(image).hexdigest()),),
            {"revision": "base", "command": ["split.py"]},
            {},
        )
        # A test folder, not a download the kit verified, and consent to run it.
        unverified = {"RETROLARIUM_UNVERIFIED_GAMES": "1", "RETROLARIUM_ALLOW_REPOSITORY_SCRIPTS": "all"}
        with mock.patch.dict("os.environ", unverified), mock.patch("sys.stderr"):
            result = repository._run(descriptor, ["split.py"], self.root / "image.bin")
        self.assertEqual((1, "��"), (result.returncode, result.stdout.strip()))


if __name__ == "__main__":
    unittest.main()
