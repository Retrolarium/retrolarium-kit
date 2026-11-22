from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from retrolarium_kit.core.project import ProjectError
from retrolarium_kit.studio import repository


class CheckRepositoryErrorTests(unittest.TestCase):
    """One revision that fails with any kit error, or a failed child process,
    is marked failed; the other revisions are still checked."""

    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name) / "probe_src"
        self.root.mkdir()
        (self.root / "game.rlj").write_text("descriptor\n", encoding="utf-8")
        revisions = []
        for name in ("first", "second"):
            image = f"{name} cartridge".encode()
            (self.root / f"{name}.bin").write_bytes(image)
            revisions.append(repository.Revision(name, name.title(), f"{name}.bin", hashlib.sha256(image).hexdigest()))
        self.descriptor = repository.GameDescriptor(
            self.root / "game.rlj", self.root, "probe", "Probe", "megadrive", "probe.v1", tuple(revisions), None, {}
        )

    def check(self, failure: BaseException) -> dict[str, object]:
        def shadow(path: Path) -> repository.GameDescriptor:
            return repository.GameDescriptor(**{**vars(self.descriptor), "path": path, "root": path.parent})

        def prepare(descriptor: repository.GameDescriptor, revision_id: str) -> dict[str, object]:
            project = descriptor.root / ".retrolarium" / revision_id / "probe.rlj"
            project.parent.mkdir(parents=True, exist_ok=True)
            return {"status": "ready", "project": str(project)}

        def build(project: Path, **_options: object) -> dict[str, object]:
            if project.parent.name == "first":
                raise failure
            built = project.parent / "built.bin"
            built.write_bytes((project.parents[2] / "second.bin").read_bytes())
            return {"artifact": {"path": "built.bin"}}

        with (
            mock.patch.object(repository, "load_descriptor", side_effect=shadow),
            mock.patch.object(repository, "prepare_revision", side_effect=prepare),
            mock.patch.object(repository, "_edited_documents", return_value=[]),
            mock.patch("retrolarium_kit.studio.project.validate_studio_project"),
            mock.patch("retrolarium_kit.studio.project.build_studio_project", side_effect=build),
        ):
            return repository.check_repository(self.descriptor)

    def test_a_project_error_fails_only_its_revision(self) -> None:
        report = self.check(ProjectError("project build failed with exit code 2"))
        self.assertEqual("failed", report["status"])
        first, second = report["revisions"]
        self.assertEqual(
            ("first", "failed", "project build failed with exit code 2"),
            (first["revision"], first["status"], first["reason"]),
        )
        self.assertEqual(("second", "ok"), (second["revision"], second["status"]))

    def test_a_failed_child_process_reports_its_code_and_last_lines(self) -> None:
        output = "".join(f"line {number}\n" for number in range(1, 9))
        report = self.check(subprocess.CalledProcessError(3, ["writer", "levels.json"], output=output))
        first, second = report["revisions"]
        self.assertEqual("failed", first["status"])
        self.assertEqual("writer failed with exit code 3:\nline 4\nline 5\nline 6\nline 7\nline 8", first["reason"])
        self.assertEqual("ok", second["status"])

    def test_a_file_error_fails_only_its_revision(self) -> None:
        report = self.check(FileNotFoundError("no such file: build/game.bin"))
        self.assertEqual(["failed", "ok"], [entry["status"] for entry in report["revisions"]])


if __name__ == "__main__":
    unittest.main()
