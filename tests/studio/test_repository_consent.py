"""The user's consent before a repository's scripts run (rltool exit code 3),
and status reads that trust the record of a split instead of running it."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from retrolarium_kit.cli import main
from retrolarium_kit.core import repository_trust
from retrolarium_kit.core.repository_trust import ConsentNeeded, RepositoryTrustError
from retrolarium_kit.studio import repository

COMMIT = "ab" * 20
CONTENT = "cd" * 32
CHECK = """import pathlib, sys
pathlib.Path("trace.txt").open("a").write("check\\n")
sys.exit(0 if pathlib.Path("split.out").is_file() else 1)
"""
SPLIT = """import pathlib, sys
pathlib.Path("trace.txt").open("a").write("split\\n")
pathlib.Path("split.out").write_bytes(pathlib.Path(sys.argv[1]).read_bytes()[:4])
"""


class ConsentFixture(unittest.TestCase):
    """A verified download with a legacy split (a check and a command)."""

    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name) / "probe_src"
        (self.root / "scripts").mkdir(parents=True)
        (self.root / "scripts" / "check.py").write_text(CHECK, encoding="utf-8")
        (self.root / "scripts" / "split.py").write_text(SPLIT, encoding="utf-8")
        self.image = b"synthetic cartridge"
        (self.root / "image.bin").write_bytes(self.image)
        revision = repository.Revision("base", "Base", "image.bin", hashlib.sha256(self.image).hexdigest())
        self.descriptor = repository.GameDescriptor(
            self.root / "probe.rlj",
            self.root,
            "probe",
            "Probe",
            "megadrive",
            "probe.v1",
            (revision,),
            {"revision": "base", "check": ["scripts/check.py", "{rom}"], "command": ["scripts/split.py", "{rom}"]},
            {"tabs": []},
        )
        repository_trust.write_marker(
            self.root,
            game="probe",
            repository="probe_src",
            commit=COMMIT,
            content_sha256=CONTENT,
            url=f"https://github.com/ExampleLab/probe_src/tree/{COMMIT}",
        )
        self.environment({"RETROLARIUM_UNVERIFIED_GAMES": "", "RETROLARIUM_ALLOW_REPOSITORY_SCRIPTS": ""})

    def environment(self, values: dict[str, str]) -> None:
        patcher = mock.patch.dict("os.environ", values)
        patcher.start()
        self.addCleanup(patcher.stop)

    def trace(self) -> list[str]:
        path = self.root / "trace.txt"
        return path.read_text(encoding="utf-8").split() if path.is_file() else []


class ConsentTests(ConsentFixture):
    def test_consent_names_content_digests_or_all(self) -> None:
        for value, expected in (
            ("", False),
            (CONTENT, True),
            (f"{'0' * 64}, {CONTENT.upper()}", True),
            (f"{'0' * 64}\n{'1' * 64}", False),
            ("all", True),
        ):
            with self.subTest(value=value), mock.patch.dict("os.environ", {repository_trust.CONSENT_VARIABLE: value}):
                self.assertEqual(expected, repository_trust.consented(CONTENT))
        with mock.patch.dict("os.environ", {repository_trust.CONSENT_VARIABLE: CONTENT}):
            self.assertFalse(repository_trust.consented(None))

    def test_without_consent_no_script_runs(self) -> None:
        with (
            mock.patch.object(repository.subprocess, "run") as run,
            self.assertRaises(ConsentNeeded) as raised,
        ):
            repository._split_status(self.descriptor, run=True)
        run.assert_not_called()
        self.assertEqual(
            {
                "schema": "retrolarium.consent-needed",
                "schema_version": 1,
                "repository": "probe_src",
                "commit": COMMIT,
                "content_sha256": CONTENT,
                "url": f"https://github.com/ExampleLab/probe_src/tree/{COMMIT}",
            },
            raised.exception.document,
        )

    def test_consent_for_another_repository_does_not_count(self) -> None:
        self.environment({"RETROLARIUM_ALLOW_REPOSITORY_SCRIPTS": "0" * 64})
        with self.assertRaises(ConsentNeeded):
            repository._split_status(self.descriptor, run=True)
        self.assertEqual([], self.trace())

    def test_an_unverified_repository_needs_consent_for_all(self) -> None:
        (self.root / repository_trust.MARKER).unlink()
        self.environment({"RETROLARIUM_UNVERIFIED_GAMES": "1", "RETROLARIUM_ALLOW_REPOSITORY_SCRIPTS": CONTENT})
        with (
            mock.patch("sys.stderr"),
            self.assertRaisesRegex(RepositoryTrustError, "RETROLARIUM_ALLOW_REPOSITORY_SCRIPTS=all"),
        ):
            repository._split_status(self.descriptor, run=True)
        self.assertEqual([], self.trace())
        self.environment({"RETROLARIUM_ALLOW_REPOSITORY_SCRIPTS": "all"})
        with mock.patch("sys.stderr"):
            self.assertEqual("made", repository._split_status(self.descriptor, run=True)["status"])

    def test_a_check_cannot_swallow_the_question(self) -> None:
        # studio check marks failed steps; a question for the user is not one.
        self.assertFalse(issubclass(ConsentNeeded, (ValueError, repository.KitError)))


class CliConsentTests(ConsentFixture):
    def run_cli(self, argv: list[str]) -> tuple[int, str, str]:
        output, errors = io.StringIO(), io.StringIO()
        with (
            mock.patch.object(repository, "load_descriptor", return_value=self.descriptor),
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(errors),
        ):
            status = main(argv)
        return status, output.getvalue(), errors.getvalue()

    def test_rltool_answers_consent_needed_with_exit_code_3_and_one_json_object(self) -> None:
        status, output, errors = self.run_cli(["studio", "prepare", str(self.descriptor.path), "--revision", "base"])
        self.assertEqual(3, status)
        document = json.loads(output)
        self.assertEqual(("retrolarium.consent-needed", 1), (document["schema"], document["schema_version"]))
        self.assertEqual({"schema", "schema_version", "repository", "commit", "content_sha256", "url"}, set(document))
        self.assertEqual((COMMIT, CONTENT), (document["commit"], document["content_sha256"]))
        self.assertIn("needs consent", errors)
        self.assertEqual([], self.trace())

    def test_the_flag_consents_anywhere_on_the_command_line_and_only_for_the_command(self) -> None:
        def identify(rom: Path) -> list[object]:
            self.assertEqual("all", os.environ[repository_trust.CONSENT_VARIABLE])
            return []

        rom = self.root / "image.bin"
        for argv in (
            ["--allow-repository-scripts", "studio", "identify", str(rom)],
            ["studio", "identify", str(rom), "--allow-repository-scripts"],
        ):
            with (
                self.subTest(argv=argv),
                mock.patch.object(repository, "identify_rom", side_effect=identify) as called,
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(0, main(argv))
            called.assert_called_once()
            self.assertEqual("", os.environ[repository_trust.CONSENT_VARIABLE])


class SplitStatusMarkerTests(ConsentFixture):
    """A status read never runs the check; the record of the last successful
    check or split makes it say "ok"."""

    def setUp(self) -> None:
        super().setUp()
        self.environment({"RETROLARIUM_ALLOW_REPOSITORY_SCRIPTS": CONTENT})
        self.marker = self.root / ".retrolarium" / "split-ok.json"

    def test_a_status_read_runs_nothing_and_trusts_the_record(self) -> None:
        self.assertEqual("needed", repository._split_status(self.descriptor, run=False)["status"])
        self.assertEqual([], self.trace())
        self.assertEqual("made", repository._split_status(self.descriptor, run=True)["status"])
        self.assertEqual(["check", "split"], self.trace())
        recorded = json.loads(self.marker.read_text(encoding="utf-8"))
        self.assertEqual({"rom_sha256", "scripts_digest", "kit_version"}, set(recorded))
        self.assertEqual(hashlib.sha256(self.image).hexdigest(), recorded["rom_sha256"])
        self.assertEqual("ok", repository._split_status(self.descriptor, run=False)["status"])
        self.assertEqual(["check", "split"], self.trace())
        # Preparing again: the check passes and the record stays.
        self.assertEqual("ok", repository._split_status(self.descriptor, run=True)["status"])
        self.assertEqual(["check", "split", "check"], self.trace())
        self.assertEqual("ok", repository.revision_statuses(self.descriptor)["split"]["status"])

    def test_another_rom_scripts_or_kit_make_the_split_needed_again(self) -> None:
        repository._split_status(self.descriptor, run=True)
        self.assertEqual("ok", repository._split_status(self.descriptor, run=False)["status"])
        with mock.patch("retrolarium_kit.__version__", "0.0.0"):
            self.assertEqual("needed", repository._split_status(self.descriptor, run=False)["status"])
        other = repository.Revision("base", "Base", "image.bin", hashlib.sha256(b"other cartridge").hexdigest())
        (self.root / "image.bin").write_bytes(b"other cartridge")
        changed_rom = repository.GameDescriptor(**{**vars(self.descriptor), "revisions": (other,)})
        self.assertEqual("needed", repository._split_status(changed_rom, run=False)["status"])
        (self.root / "image.bin").write_bytes(self.image)
        (self.root / "scripts" / "split.py").write_text(SPLIT + "# edited\n", encoding="utf-8")
        self.assertEqual("needed", repository._split_status(self.descriptor, run=False)["status"])

    def test_a_failed_split_leaves_no_record(self) -> None:
        (self.root / "scripts" / "split.py").write_text("import sys\nsys.exit(2)\n", encoding="utf-8")
        repository_trust.write_marker(
            self.root,
            game="probe",
            repository="probe_src",
            commit=COMMIT,
            content_sha256=CONTENT,
            url=f"https://github.com/ExampleLab/probe_src/tree/{COMMIT}",
        )
        self.assertEqual("failed", repository._split_status(self.descriptor, run=True)["status"])
        self.assertFalse(self.marker.exists())
        self.assertEqual("needed", repository._split_status(self.descriptor, run=False)["status"])

    def test_the_record_does_not_run_a_check_through_subprocess(self) -> None:
        repository._split_status(self.descriptor, run=True)
        with mock.patch.object(repository.subprocess, "run", side_effect=AssertionError("ran")):
            self.assertEqual("ok", repository._split_status(self.descriptor, run=False)["status"])


if __name__ == "__main__":
    unittest.main()
