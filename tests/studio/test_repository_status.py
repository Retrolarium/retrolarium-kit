from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from retrolarium_kit.studio import repository


class LegacySplitStatusTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        image = b"synthetic cartridge"
        (self.root / "image.bin").write_bytes(image)
        revision = repository.Revision("base", "Base", "image.bin", hashlib.sha256(image).hexdigest())
        self.descriptor = repository.GameDescriptor(
            self.root / "game.rlj",
            self.root,
            "probe",
            "Probe",
            "megadrive",
            "probe.v1",
            (revision,),
            {"revision": "base", "check": ["check.py"], "command": ["split.py"]},
            {},
        )

    def test_reading_status_does_not_run_a_legacy_check_or_split(self):
        with mock.patch.object(repository, "_run", return_value=subprocess.CompletedProcess([], 0)) as run:
            result = repository._split_status(self.descriptor, run=False)
        run.assert_not_called()
        self.assertEqual("needed", result["status"])

    def test_explicit_preparation_still_runs_a_successful_check(self):
        with mock.patch.object(repository, "_run", return_value=subprocess.CompletedProcess([], 0)) as run:
            result = repository._split_status(self.descriptor, run=True)
        self.assertEqual("ok", result["status"])
        self.assertEqual(["check.py"], run.call_args.args[1])
        self.assertEqual(1, run.call_count)

    def test_explicit_preparation_splits_after_a_failed_check(self):
        with mock.patch.object(
            repository, "_run", side_effect=[subprocess.CompletedProcess([], 1), subprocess.CompletedProcess([], 0)]
        ) as run:
            result = repository._split_status(self.descriptor, run=True)
        self.assertEqual("made", result["status"])
        self.assertEqual([["check.py"], ["split.py"]], [call.args[1] for call in run.call_args_list])

    def test_a_missing_rom_remains_unavailable_without_running_commands(self):
        (self.root / "image.bin").unlink()
        with mock.patch.object(repository, "_run") as run:
            result = repository._split_status(self.descriptor, run=False)
        run.assert_not_called()
        self.assertEqual("unavailable", result["status"])


class ArchiveUrlTests(unittest.TestCase):
    def test_a_missing_or_invalid_ref_is_a_repository_error(self):
        for ref in ({}, {"ref": None}, {"ref": ""}, {"ref": 42}):
            with self.subTest(ref=ref), self.assertRaises(repository.GameRepositoryError):
                repository._archive_url(
                    {"organization": "https://example.invalid/lab"}, {"repository": "probe", **ref}, None
                )

    def test_an_immutable_commit_and_a_tag_keep_their_url_layouts(self):
        catalog = {"organization": "https://example.invalid/lab"}
        with mock.patch.dict("os.environ", {"RETROLARIUM_GAMES_SOURCE": ""}):
            for ref, suffix in (("1" * 40, "archive/" + "1" * 40 + ".zip"), ("tags/v1", "archive/refs/tags/v1.zip")):
                self.assertEqual(
                    "https://example.invalid/lab/probe/" + suffix,
                    repository._archive_url(catalog, {"repository": "probe", "ref": ref}, None),
                )

    def test_unknown_template_fields_remain_repository_errors(self):
        with self.assertRaises(repository.GameRepositoryError):
            repository._archive_url(
                {"organization": "https://example.invalid/lab"}, {"repository": "probe", "ref": "tags/v1"}, "{unknown}"
            )
