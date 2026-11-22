from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from retrolarium_kit.games.flicky.workspace import build
from retrolarium_kit.games.flicky.workspace.content import FlickyWorkspaceError


class LevelWriterTests(unittest.TestCase):
    """The native level writer runs between the snapshot's own build steps;
    a failure is a FlickyWorkspaceError with its output, not a
    CalledProcessError, and its output does not reach the kit's stdout."""

    def test_a_failing_level_writer_is_a_workspace_error(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            staged = root / "build" / "content" / "src"
            staged.mkdir(parents=True)
            failed = subprocess.CompletedProcess(["writer"], 2, stdout="level 3 does not fit\n")
            with (
                mock.patch.object(build, "validate_workspace"),
                mock.patch.object(build, "_run"),
                mock.patch.object(build, "stage_sources", return_value=staged / "main.s"),
                mock.patch.object(build.subprocess, "run", return_value=failed) as run,
                self.assertRaisesRegex(FlickyWorkspaceError, r"level writer failed \(2\):\nlevel 3 does not fit"),
            ):
                build.build_content(root, as_bin=root / "asl", p2bin=root / "p2bin", level_writer=root / "writer")
            self.assertEqual(subprocess.PIPE, run.call_args.kwargs["stdout"])


if __name__ == "__main__":
    unittest.main()
