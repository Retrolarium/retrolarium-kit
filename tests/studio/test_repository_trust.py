"""Game repositories are pinned to a commit and a content digest, and only
the scripts of a verified download run."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from retrolarium_kit.core import repository_trust
from retrolarium_kit.core.repository_trust import RepositoryTrustError, tree_digest
from retrolarium_kit.studio import repository
from retrolarium_kit.studio.repository import GameRepositoryError, fetch_repository

COMMIT = "c0ffee" + "0" * 34
FILES = {
    "probe.rlj": b'schema = "retrolarium.game"\n',
    "scripts/split.py": b"print('split')\n",
    "scripts/build/__init__.py": b"",
    "src/main.asm": b"; source\n",
}


def write_tree(root: Path, files: dict[str, bytes]) -> Path:
    for name, payload in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(payload)
    return root


def write_archive(path: Path, files: dict[str, bytes], top: str = f"probe_src-{COMMIT}") -> Path:
    with zipfile.ZipFile(path, "w") as bundle:
        for name, payload in files.items():
            bundle.writestr(f"{top}/{name}", payload)
    return path


def expected_digest(files: dict[str, bytes]) -> str:
    lines = "".join(f"{name}\0{hashlib.sha256(files[name]).hexdigest()}\n" for name in sorted(files))
    return hashlib.sha256(lines.encode("utf-8")).hexdigest()


def catalog(content_sha256: str) -> dict[str, object]:
    return {
        "schema": "retrolarium.game-repositories",
        "schema_version": 1,
        "organization": "https://example.invalid/lab",
        "games": {
            "probe": {
                "repository": "probe_src",
                "descriptor": "probe.rlj",
                "ref": "tags/v1",
                "commit": COMMIT,
                "content_sha256": content_sha256,
                "revisions": [],
            }
        },
    }


class TreeDigestTests(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def test_the_digest_is_the_sorted_manifest_of_paths_and_file_hashes(self) -> None:
        write_tree(self.root, FILES)
        (self.root / "empty").mkdir()
        self.assertEqual(expected_digest(FILES), tree_digest(self.root))

    def test_any_change_changes_the_digest(self) -> None:
        write_tree(self.root, FILES)
        before = tree_digest(self.root)
        (self.root / "src" / "main.asm").write_bytes(b"; changed\n")
        self.assertNotEqual(before, tree_digest(self.root))

    @unittest.skipUnless(os.name == "nt", "NTFS junctions are Windows-specific")
    def test_a_junction_cannot_redirect_a_verified_script_tree(self) -> None:
        import _winapi

        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        write_tree(Path(outside.name), {"unexpected.py": b"raise SystemExit('never executed')\n"})
        link = self.root / "link"
        _winapi.CreateJunction(outside.name, str(link))
        self.addCleanup(link.rmdir)
        for read in (tree_digest, repository_trust.script_files):
            with self.subTest(read=read.__name__), self.assertRaisesRegex(RepositoryTrustError, "link is a link"):
                read(self.root)

    @unittest.skipIf(os.name == "nt", "symbolic links need privileges on Windows")
    def test_a_symbolic_link_is_refused(self) -> None:
        write_tree(self.root, FILES)
        (self.root / "link.py").symlink_to(self.root / "scripts" / "split.py")
        with self.assertRaisesRegex(RepositoryTrustError, "link.py is a link"):
            tree_digest(self.root)


class CatalogTests(unittest.TestCase):
    def test_every_game_of_the_kit_is_pinned(self) -> None:
        for game_id, entry in repository.repository_catalog()["games"].items():
            with self.subTest(game=game_id):
                self.assertRegex(entry["commit"], r"^[0-9a-f]{40}$")
                self.assertRegex(entry["content_sha256"], r"^[0-9a-f]{64}$")

    def test_an_entry_without_a_commit_or_a_content_digest_is_refused(self) -> None:
        for key in ("commit", "content_sha256"):
            document = catalog("0" * 64)
            del document["games"]["probe"][key]
            with (
                self.subTest(missing=key),
                self.assertRaisesRegex(GameRepositoryError, key.split("_")[0]),
            ):
                repository.validate_catalog(document)
        document = catalog("0" * 64)
        document["games"]["probe"]["commit"] = "tags/v1"
        with self.assertRaisesRegex(GameRepositoryError, "not pinned to a commit"):
            repository.validate_catalog(document)

    def test_the_archive_is_the_pinned_commit_not_the_tag(self) -> None:
        document = catalog("0" * 64)
        with mock.patch.dict("os.environ", {"RETROLARIUM_GAMES_SOURCE": ""}):
            self.assertEqual(
                f"https://example.invalid/lab/probe_src/archive/{COMMIT}.zip",
                repository._archive_url(document, document["games"]["probe"], None),
            )
        self.assertEqual(
            f"https://example.invalid/lab/probe_src/tree/{COMMIT}",
            repository.repository_url(document, document["games"]["probe"]),
        )


class FetchTests(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.games = self.root / "games"
        environment = mock.patch.dict(
            "os.environ",
            {
                "RETROLARIUM_GAMES_SOURCE": "",
                "RETROLARIUM_UNVERIFIED_GAMES": "",
                "RETROLARIUM_VERIFIED_HOME": str(self.root / "verified"),
            },
        )
        environment.start()
        self.addCleanup(environment.stop)
        self.use_catalog(expected_digest(FILES))

    def use_catalog(self, content_sha256: str) -> None:
        patcher = mock.patch.object(
            repository_trust.catalog, "repository_catalog", return_value=catalog(content_sha256)
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def fetch(self, files: dict[str, bytes], **options: object) -> Path:
        archive = write_archive(self.root / "probe_src.zip", files)
        return fetch_repository("probe", self.games, source=str(archive), **options)

    def test_an_archive_with_the_pinned_content_is_recorded_as_verified(self) -> None:
        descriptor = self.fetch(FILES)
        self.assertEqual(self.games / "probe_src" / "probe.rlj", descriptor)
        marker = repository_trust.read_marker(descriptor.parent)
        self.assertEqual(
            (COMMIT, expected_digest(FILES)),
            (marker["commit"], marker["content_sha256"]),
        )
        self.assertEqual(f"https://example.invalid/lab/probe_src/tree/{COMMIT}", marker["url"])
        self.assertEqual(
            {"scripts/build/__init__.py", "scripts/split.py"},
            set(marker["scripts"]),
        )
        self.assertEqual(
            [],
            [path.name for path in self.games.iterdir() if path.name.startswith(".download-")],
        )

    def test_a_forged_project_record_cannot_reuse_previous_consent(self) -> None:
        target = write_tree(self.games / "probe_src", {**FILES, "scripts/split.py": b"print('changed')\n"})
        record = {
            "schema": repository_trust.MARKER_SCHEMA,
            "schema_version": 1,
            "game": "probe",
            "repository": "probe_src",
            "commit": COMMIT,
            "content_sha256": expected_digest(FILES),
            "url": f"https://example.invalid/lab/probe_src/tree/{COMMIT}",
            "scripts": repository_trust.script_files(target),
        }
        (target / repository_trust.MARKER).write_text(json.dumps(record), encoding="utf-8")
        with mock.patch.dict(os.environ, {repository_trust.CONSENT_VARIABLE: expected_digest(FILES)}):
            with self.assertRaises(RepositoryTrustError):
                repository_trust.authorize_scripts(target, ["scripts/split.py"])
            archive = write_archive(self.root / "pinned.zip", FILES)
            with mock.patch("urllib.request.urlopen", side_effect=self.serving(archive)):
                with self.assertRaisesRegex(GameRepositoryError, "scripts in"):
                    repository.ensure_repository_verified(target)

    def test_verification_is_saved_outside_the_project_and_bound_to_its_path(self) -> None:
        target = self.fetch(FILES).parent
        self.assertFalse((target / repository_trust.MARKER).exists())
        self.assertIsNotNone(repository_trust.read_marker(target))
        copied = self.root / "copied"
        shutil.copytree(target, copied)
        with self.assertRaises(RepositoryTrustError):
            repository_trust.authorize_scripts(copied, ["scripts/split.py"])

    def test_a_record_for_an_old_catalog_identity_is_not_trusted(self) -> None:
        target = self.fetch(FILES).parent
        for key, value in (("commit", "1" * 40), ("content_sha256", "1" * 64)):
            changed = catalog(expected_digest(FILES))
            changed["games"]["probe"][key] = value
            with mock.patch.object(repository_trust.catalog, "repository_catalog", return_value=changed):
                self.assertIsNone(repository_trust.read_marker(target))
                with self.assertRaises(RepositoryTrustError):
                    repository_trust.verify_scripts(target, ["scripts/split.py"])

    def test_own_git_checkout_is_not_downloaded_or_marked(self) -> None:
        target = write_tree(self.games / "probe_src", FILES)
        (target / ".git").mkdir()
        with mock.patch.object(repository, "download_repository") as download:
            repository.ensure_repository_verified(target)
        download.assert_not_called()
        self.assertIsNone(repository_trust.read_marker(target))
        with self.assertRaisesRegex(RepositoryTrustError, "RETROLARIUM_UNVERIFIED_GAMES=1"):
            repository_trust.authorize_scripts(target, ["scripts/split.py"])

    def test_a_changed_file_is_refused_and_nothing_is_kept(self) -> None:
        changed = {**FILES, "scripts/split.py": b"print('something else')\n"}
        with self.assertRaisesRegex(GameRepositoryError, "does not match the catalog"):
            self.fetch(changed)
        self.assertFalse((self.games / "probe_src").exists())
        self.assertEqual([], list(self.games.iterdir()))

    def test_an_unverified_source_is_accepted_only_when_asked(self) -> None:
        changed = {
            **FILES,
            "scripts/split.py": b"print('local work')\n",
            repository_trust.MARKER: b"{}",
        }
        with (
            mock.patch.dict("os.environ", {"RETROLARIUM_UNVERIFIED_GAMES": "1"}),
            mock.patch("sys.stderr"),
        ):
            descriptor = self.fetch(changed)
        # Unverified: no record (not even the one the archive brought).
        self.assertIsNone(repository_trust.read_marker(descriptor.parent))
        self.assertFalse((descriptor.parent / repository_trust.MARKER).exists())

    def test_the_default_download_is_never_accepted_unverified(self) -> None:
        archive = write_archive(self.root / "probe_src.zip", {**FILES, "src/main.asm": b"; other\n"})

        def download(url: str, timeout: float) -> io.BytesIO:
            self.assertEqual(f"https://example.invalid/lab/probe_src/archive/{COMMIT}.zip", url)
            return contextlib.closing(io.BytesIO(archive.read_bytes()))

        with (
            mock.patch.dict("os.environ", {"RETROLARIUM_UNVERIFIED_GAMES": "1"}),
            mock.patch("urllib.request.urlopen", side_effect=download),
            self.assertRaisesRegex(GameRepositoryError, "does not match the catalog"),
        ):
            fetch_repository("probe", self.games)

    def test_a_symbolic_link_in_the_archive_is_refused(self) -> None:
        archive = self.root / "probe_src.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr(f"probe_src-{COMMIT}/probe.rlj", FILES["probe.rlj"])
            link = zipfile.ZipInfo(f"probe_src-{COMMIT}/scripts/split.py")
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            bundle.writestr(link, "/etc/passwd")
        with self.assertRaisesRegex(GameRepositoryError, "symbolic link"):
            fetch_repository("probe", self.games, source=str(archive))
        self.assertFalse((self.games / "probe_src").exists())

    def test_an_archive_with_too_many_entries_is_refused(self) -> None:
        self.assertEqual(20_000, repository.ARCHIVE_ENTRIES)
        many = {**FILES, **{f"data/{index}.bin": b"" for index in range(8)}}
        with (
            mock.patch.object(repository, "ARCHIVE_ENTRIES", len(many) - 1),
            self.assertRaisesRegex(GameRepositoryError, "more than"),
        ):
            self.fetch(many)

    def test_an_archive_that_unpacks_too_large_is_refused(self) -> None:
        self.assertEqual(2 << 30, repository.ARCHIVE_BYTES)
        with (
            mock.patch.object(repository, "ARCHIVE_BYTES", 16),
            self.assertRaisesRegex(GameRepositoryError, "unpacks to more than"),
        ):
            self.fetch(FILES)

    def test_download_is_bounded_before_unpacking(self) -> None:
        archive = write_archive(self.root / "incoming.zip", FILES)
        with mock.patch.object(repository, "ARCHIVE_DOWNLOAD_BYTES", 16):
            for source in (str(archive), archive.as_uri()):
                with self.subTest(source=source), self.assertRaisesRegex(GameRepositoryError, "download exceeds"):
                    fetch_repository("probe", self.games, source=source)
        self.assertFalse((self.games / "probe_src").exists())

    def test_download_has_an_overall_deadline(self) -> None:
        archive = write_archive(self.root / "incoming.zip", FILES)
        with (
            mock.patch("urllib.request.urlopen", side_effect=self.serving(archive)),
            mock.patch.object(repository.time, "monotonic", side_effect=[0, repository.DOWNLOAD_SECONDS + 1]),
            self.assertRaisesRegex(GameRepositoryError, "time limit"),
        ):
            fetch_repository("probe", self.games)

    def test_a_local_archive_path_may_contain_literal_braces(self) -> None:
        archive = write_archive(self.root / "archive{literal}.zip", FILES)
        descriptor = fetch_repository("probe", self.games, source=str(archive))
        self.assertTrue(descriptor.is_file())

    @staticmethod
    def serving(archive: Path):
        return lambda url, timeout: contextlib.closing(io.BytesIO(archive.read_bytes()))

    def test_an_older_download_is_verified_again_from_the_pinned_commit(self) -> None:
        target = write_tree(self.games / "probe_src", {**FILES, "hack/v1/edits.json": b"{}"})
        archive = write_archive(self.root / "pinned.zip", FILES)
        opened = mock.patch("urllib.request.urlopen", side_effect=self.serving(archive))
        with opened:
            fetch_repository("probe", self.games)
        self.assertEqual(COMMIT, repository_trust.read_marker(target)["commit"])

    def test_an_older_download_with_bytecode_is_verified_and_clears_its_old_refusal(self) -> None:
        target = write_tree(
            self.games / "probe_src",
            {
                **FILES,
                "scripts/build/__pycache__/helper.pyc": b"old cache",
                "hack/v1/edits.json": b"edited",
            },
        )
        entry = catalog(expected_digest(FILES))["games"]["probe"]
        scripts = repository_trust.scripts_digest(repository_trust.script_files(target))
        repository._record_refusal(target, entry, scripts, "old cache was not in the archive")
        archive = write_archive(self.root / "pinned.zip", FILES)
        with mock.patch("urllib.request.urlopen", side_effect=self.serving(archive)) as download:
            repository.ensure_repository_verified(target)
            repository.ensure_repository_verified(target)
        self.assertEqual(1, download.call_count)
        self.assertIsNotNone(repository_trust.read_marker(target))
        self.assertFalse((target / "scripts/build/__pycache__").exists())
        self.assertFalse((target / repository.REFUSAL).exists())
        self.assertEqual(b"edited", (target / "hack/v1/edits.json").read_bytes())

    def test_an_older_download_with_other_scripts_stays_unverified(self) -> None:
        target = write_tree(
            self.games / "probe_src",
            {**FILES, "scripts/split.py": b"print('edited')\n"},
        )
        archive = write_archive(self.root / "pinned.zip", FILES)
        opened = mock.patch("urllib.request.urlopen", side_effect=self.serving(archive))
        errors = io.StringIO()
        with opened, contextlib.redirect_stderr(errors):
            fetch_repository("probe", self.games)
        self.assertIsNone(repository_trust.read_marker(target))
        self.assertIn("stays unverified", errors.getvalue())

    def test_a_saved_source_project_upgrades_without_reopening_its_rom(self) -> None:
        target = write_tree(self.games / "probe_src", {**FILES, "hack/v1/edits.json": b"edited"})
        snapshot = write_tree(
            target / ".retrolarium/v1/source",
            {k: v for k, v in FILES.items() if k != "probe.rlj"},
        )
        archive = write_archive(self.root / "pinned.zip", FILES)
        with mock.patch("urllib.request.urlopen", side_effect=self.serving(archive)) as download:
            repository.ensure_repository_verified(snapshot)
            repository.ensure_repository_verified(snapshot)
        self.assertEqual(1, download.call_count)
        self.assertEqual(b"edited", (target / "hack/v1/edits.json").read_bytes())
        with mock.patch.dict(os.environ, {repository_trust.CONSENT_VARIABLE: ""}):
            with self.assertRaises(repository_trust.ConsentNeeded):
                repository_trust.authorize_scripts(snapshot, ["scripts/split.py"])

    def test_flicky_build_upgrades_a_saved_project_before_running_its_builder(
        self,
    ) -> None:
        from retrolarium_kit.games.flicky import content
        from retrolarium_kit.studio.source_games import source_game

        target = write_tree(self.games / "probe_src", FILES)
        project = target / ".retrolarium/v1"
        project.mkdir(parents=True)
        archive = write_archive(self.root / "pinned.zip", FILES)

        def build(*args, **options):
            self.assertIsNotNone(repository_trust.read_marker(target))
            return {"status": "built"}

        with (
            mock.patch("urllib.request.urlopen", side_effect=self.serving(archive)),
            mock.patch.object(content, "build_flicky_source_snapshot", side_effect=build),
        ):
            self.assertEqual({"status": "built"}, source_game("flicky-ue.v1").build(project))

    def test_a_saved_project_with_changed_scripts_cannot_gain_a_record(self) -> None:
        target = write_tree(self.games / "probe_src", {**FILES, "scripts/split.py": b"edited"})
        archive = write_archive(self.root / "pinned.zip", FILES)
        with mock.patch("urllib.request.urlopen", side_effect=self.serving(archive)):
            with self.assertRaisesRegex(GameRepositoryError, "scripts in"):
                repository.ensure_repository_verified(target)
        self.assertIsNone(repository_trust.read_marker(target))

    def test_changed_scripts_are_refused_again_without_another_download(self) -> None:
        target = write_tree(self.games / "probe_src", {**FILES, "scripts/split.py": b"edited"})
        archive = write_archive(self.root / "pinned.zip", FILES)
        with mock.patch("urllib.request.urlopen", side_effect=self.serving(archive)) as download:
            for _ in range(2):
                with self.assertRaisesRegex(GameRepositoryError, "scripts in"):
                    repository.ensure_repository_verified(target)
        self.assertEqual(1, download.call_count)
        self.assertTrue((target / repository.REFUSAL).is_file())
        # Restoring the pinned scripts lifts the refusal and gives a record.
        (target / "scripts/split.py").write_bytes(FILES["scripts/split.py"])
        with mock.patch("urllib.request.urlopen", side_effect=self.serving(archive)):
            repository.ensure_repository_verified(target)
        self.assertEqual(COMMIT, repository_trust.read_marker(target)["commit"])
        self.assertFalse((target / repository.REFUSAL).exists())

    def test_an_offline_check_explains_itself_and_is_tried_again(self) -> None:
        target = write_tree(self.games / "probe_src", FILES)
        archive = write_archive(self.root / "pinned.zip", FILES)
        with mock.patch("urllib.request.urlopen", side_effect=OSError("network is unreachable")):
            with self.assertRaisesRegex(GameRepositoryError, "no verification record.*network is unreachable"):
                repository.ensure_repository_verified(target)
        self.assertFalse((target / repository.REFUSAL).exists())
        with mock.patch("urllib.request.urlopen", side_effect=self.serving(archive)):
            repository.ensure_repository_verified(target)
        self.assertIsNotNone(repository_trust.read_marker(target))

    def test_the_comparison_copy_is_made_outside_the_project_tree(self) -> None:
        target = write_tree(self.games / "probe_src", FILES)
        archive = write_archive(self.root / "pinned.zip", FILES)
        made: list[str] = []
        real = tempfile.mkdtemp

        def mkdtemp(*args, **options):
            folder = real(*args, **options)
            made.append(folder)
            return folder

        with (
            mock.patch("urllib.request.urlopen", side_effect=self.serving(archive)),
            mock.patch.object(repository.tempfile, "mkdtemp", side_effect=mkdtemp),
        ):
            repository.ensure_repository_verified(target)
        self.assertEqual(1, len(made))
        self.assertFalse(Path(made[0]).resolve().is_relative_to(self.games.resolve()))
        self.assertFalse(Path(made[0]).exists())

    def test_a_cache_that_cannot_be_removed_does_not_undo_verification(self) -> None:
        target = write_tree(self.games / "probe_src", {**FILES, "scripts/split.py": b"edited"})
        archive = write_archive(self.root / "pinned.zip", FILES)
        with mock.patch("urllib.request.urlopen", side_effect=self.serving(archive)):
            with self.assertRaises(GameRepositoryError):
                repository.ensure_repository_verified(target)
        (target / "scripts/split.py").write_bytes(FILES["scripts/split.py"])
        # Exercise two spellings of the same file on every host. Windows
        # TEMP may similarly use an 8.3 alias, while verification resolves it.
        cache = target / "src" / ".." / repository.REFUSAL
        actual = Path.unlink
        denied = []

        def unlink(path, *args, **options):
            if path.resolve() == cache.resolve():
                denied.append(path)
                raise PermissionError("the cache is read-only")
            return actual(path, *args, **options)

        with (
            mock.patch("urllib.request.urlopen", side_effect=self.serving(archive)) as download,
            mock.patch.object(Path, "unlink", unlink),
        ):
            repository.ensure_repository_verified(target)
            repository.ensure_repository_verified(target)
        self.assertEqual(1, download.call_count)
        self.assertEqual(COMMIT, repository_trust.read_marker(target)["commit"])
        self.assertEqual(1, len(denied))
        self.assertTrue(cache.is_file())

    def test_a_changed_snapshot_still_cannot_run_after_its_root_is_verified(
        self,
    ) -> None:
        target = write_tree(self.games / "probe_src", FILES)
        snapshot = write_tree(
            target / ".retrolarium/v1/source",
            {k: (b"edited" if k == "scripts/split.py" else v) for k, v in FILES.items() if k != "probe.rlj"},
        )
        archive = write_archive(self.root / "pinned.zip", FILES)
        with mock.patch("urllib.request.urlopen", side_effect=self.serving(archive)):
            repository.ensure_repository_verified(snapshot)
        with self.assertRaisesRegex(RepositoryTrustError, "changed since"):
            repository_trust.authorize_scripts(snapshot, ["scripts/split.py"])


class ScriptVerificationTests(unittest.TestCase):
    """Before a script of a repository runs, its scripts are checked against
    the verified download."""

    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = write_tree(Path(folder.name) / "probe_src", FILES)
        image = b"cartridge"
        (self.root / "image.bin").write_bytes(image)
        self.descriptor = repository.GameDescriptor(
            self.root / "probe.rlj",
            self.root,
            "probe",
            "Probe",
            "megadrive",
            "probe.v1",
            (repository.Revision("base", "Base", "image.bin", hashlib.sha256(image).hexdigest()),),
            {"revision": "base", "command": ["scripts/split.py"]},
            {},
        )
        for patcher in (
            mock.patch.dict(os.environ, {repository_trust.VERIFIED_HOME_VARIABLE: str(Path(folder.name) / "verified")}),
            mock.patch.object(repository_trust.catalog, "repository_catalog", return_value=catalog("0" * 64)),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        repository_trust.write_marker(
            self.root,
            game="probe",
            repository="probe_src",
            commit=COMMIT,
            content_sha256="0" * 64,
            url=f"https://example.invalid/lab/probe_src/tree/{COMMIT}",
            expected_scripts=repository_trust.script_files(self.root),
        )
        # The user agreed to run this download's scripts.
        environment = mock.patch.dict(
            "os.environ",
            {
                "RETROLARIUM_UNVERIFIED_GAMES": "",
                "RETROLARIUM_ALLOW_REPOSITORY_SCRIPTS": "0" * 64,
            },
        )
        environment.start()
        self.addCleanup(environment.stop)

    def run_split(self) -> mock.MagicMock:
        with mock.patch.object(
            repository.subprocess,
            "run",
            return_value=subprocess.CompletedProcess([], 0, "", ""),
        ) as run:
            repository._run(self.descriptor, ["scripts/split.py"], self.root / "image.bin")
        return run

    def test_an_unchanged_script_of_a_verified_download_runs(self) -> None:
        # Edited documents, ROM copies and the kit's own folders do not count.
        write_tree(
            self.root,
            {
                "src/main.asm": b"; edited\n",
                ".retrolarium/base/x.py": b"",
                "hack/base/a.json": b"",
            },
        )
        self.assertEqual(1, self.run_split().call_count)

    def test_a_manual_python_run_does_not_prevent_the_next_authorized_run(self) -> None:
        environment = dict(os.environ)
        environment.pop("PYTHONDONTWRITEBYTECODE", None)
        environment.pop("PYTHONPYCACHEPREFIX", None)
        subprocess.run(
            [sys.executable, "-c", "import sys; sys.path.insert(0, 'scripts'); import build"],
            cwd=self.root,
            env=environment,
            check=True,
            capture_output=True,
        )
        cache = self.root / "scripts/build/__pycache__"
        self.assertTrue(list(cache.glob("*.pyc")))
        self.assertEqual(1, self.run_split().call_count)
        self.assertFalse(cache.exists())
        self.assertEqual(1, self.run_split().call_count)

    def test_snapshot_bytecode_is_removed_before_authorization(self) -> None:
        snapshot = write_tree(
            self.root / ".retrolarium/base/source",
            {
                "scripts/split.py": FILES["scripts/split.py"],
                "scripts/__pycache__/split.pyc": b"old cache",
                "__pycache__/nested/extra.pyc": b"old cache",
            },
        )
        repository_trust.authorize_scripts(snapshot, ["scripts/split.py"])
        self.assertFalse((snapshot / "scripts/__pycache__").exists())
        self.assertFalse((snapshot / "__pycache__").exists())

    def test_a_bytecode_cache_that_cannot_be_cleared_stops_the_run(self) -> None:
        write_tree(self.root, {"scripts/__pycache__/split.pyc": b"old cache"})
        with (
            mock.patch.object(Path, "unlink", side_effect=PermissionError("cache is read-only")),
            mock.patch.object(repository.subprocess, "run") as run,
            self.assertRaisesRegex(RepositoryTrustError, "cache.*read-only"),
        ):
            repository._run(self.descriptor, ["scripts/split.py"], self.root / "image.bin")
        run.assert_not_called()

    def test_only_disposable_bytecode_caches_are_removed(self) -> None:
        kept = {f"{folder}/__pycache__/kept.pyc": b"kept" for folder in (".git", "hack", "build", ".retrolarium")}
        kept["notes/__pycache__"] = b"an ordinary file, not a cache folder"
        write_tree(self.root, {**kept, "scripts/__pycache__/old.pyc": b"old cache"})
        repository_trust.authorize_scripts(self.root, ["scripts/split.py"])
        self.assertFalse((self.root / "scripts/__pycache__").exists())
        for name, content in kept.items():
            self.assertEqual(content, (self.root / name).read_bytes())

    def test_bytecode_outside_a_cache_still_prevents_authorization(self) -> None:
        path = write_tree(self.root, {"tools/helper.pyc": b"standalone bytecode"}) / "tools/helper.pyc"
        with self.assertRaisesRegex(RepositoryTrustError, "is not part of"):
            repository_trust.authorize_scripts(self.root, ["scripts/split.py"])
        self.assertEqual(b"standalone bytecode", path.read_bytes())

    def test_a_linked_bytecode_cache_is_refused_without_touching_its_target(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            outside = Path(folder)
            sentinel = outside / "keep.txt"
            sentinel.write_bytes(b"kept")
            cache = self.root / "scripts/__pycache__"
            if os.name == "nt":
                import _winapi

                _winapi.CreateJunction(str(outside), str(cache))
                self.addCleanup(cache.rmdir)
            else:
                cache.symlink_to(outside, target_is_directory=True)
                self.addCleanup(cache.unlink)
            with self.assertRaisesRegex(RepositoryTrustError, "link|special file"):
                repository_trust.authorize_scripts(self.root, ["scripts/split.py"])
            self.assertEqual(b"kept", sentinel.read_bytes())

    def test_flicky_build_steps_can_run_twice_without_creating_bytecode(self) -> None:
        from retrolarium_kit.games.flicky.workspace import build

        write_tree(self.root, {"scripts/run.py": b"import build\n"})
        marker = repository_trust.read_marker(self.root)
        repository_trust.write_marker(
            self.root,
            game="probe",
            repository="probe_src",
            commit=COMMIT,
            content_sha256=marker["content_sha256"],
            url=marker["url"],
            expected_scripts=repository_trust.script_files(self.root),
        )
        staged = self.root / "build/content/src"
        staged.mkdir(parents=True)
        levels = self.root / "levels.json"
        levels.write_text("{}", encoding="utf-8")
        writer = self.root / "writer"
        actual_process = build._process

        def process(command, root, env=None):
            if command[0] == str(writer):
                return subprocess.CompletedProcess(command, 0, "")
            self.assertEqual("1", env.get("PYTHONDONTWRITEBYTECODE"))
            self.assertEqual(str(self.root), env.get("AS_MSGPATH"))
            return actual_process(command, root, env)

        with contextlib.ExitStack() as patches:
            patches.enter_context(mock.patch.dict(os.environ, {"PYTHONDONTWRITEBYTECODE": "0"}))
            patches.enter_context(mock.patch.object(build, "_process", side_effect=process))
            patches.enter_context(mock.patch.object(build, "validate_workspace"))
            patches.enter_context(mock.patch.object(build, "stage_sources", return_value=staged / "main.s"))
            patches.enter_context(mock.patch.object(build, "workspace_path", return_value=levels))
            for module, names in (
                (build, ("stage_attract_demos", "stage_title_layout", "stage_bonus_motion")),
                (build.graphics, ("load_document", "build_assets", "redirect_includes")),
                (build.semantics, ("load_document", "apply_document")),
                (build.sequences, ("load_document", "apply_document")),
            ):
                for name in names:
                    patches.enter_context(mock.patch.object(module, name))
            for _ in range(2):
                repository_trust.authorize_scripts(self.root, ["scripts/run.py"])
                build.build_content(self.root, as_bin=self.root / "asl", p2bin=self.root / "p2bin", level_writer=writer)
                self.assertFalse((self.root / "scripts/build/__pycache__").exists())

    def test_a_changed_script_does_not_run(self) -> None:
        (self.root / "scripts" / "build" / "__init__.py").write_text("import os\n", encoding="utf-8")
        with (
            mock.patch.object(repository.subprocess, "run") as run,
            self.assertRaisesRegex(
                RepositoryTrustError,
                "scripts/build/__init__.py changed since the kit downloaded probe_src",
            ),
        ):
            repository._run(self.descriptor, ["scripts/split.py"], self.root / "image.bin")
        run.assert_not_called()

    def test_a_script_the_download_did_not_have_does_not_run(self) -> None:
        for name in ("tools/extra.py", "scripts/data.txt"):
            with self.subTest(name=name):
                write_tree(self.root, {name: b"x = 1\n"})
                with (
                    mock.patch.object(repository.subprocess, "run") as run,
                    self.assertRaisesRegex(RepositoryTrustError, "is not part of probe_src"),
                ):
                    repository._run(self.descriptor, ["scripts/split.py"], self.root / "image.bin")
                run.assert_not_called()
                (self.root / name).unlink()

    def test_a_command_outside_the_scripts_must_be_part_of_the_download(self) -> None:
        write_tree(self.root, {"data/evil.txt": b"print('x')\n"})
        with (
            mock.patch.object(repository.subprocess, "run") as run,
            self.assertRaises(RepositoryTrustError),
        ):
            repository._run(self.descriptor, ["data/evil.txt"], self.root / "image.bin")
        run.assert_not_called()

    def test_a_repository_the_kit_did_not_verify_runs_nothing_unless_accepted(
        self,
    ) -> None:
        repository_trust.record_path(self.root).unlink()
        (self.root / ".git").mkdir()  # An intentionally unverified developer checkout.
        with (
            mock.patch.object(repository.subprocess, "run") as run,
            self.assertRaisesRegex(RepositoryTrustError, "RETROLARIUM_UNVERIFIED_GAMES=1"),
        ):
            repository._run(self.descriptor, ["scripts/split.py"], self.root / "image.bin")
        run.assert_not_called()
        errors = io.StringIO()
        accepted = {
            "RETROLARIUM_UNVERIFIED_GAMES": "1",
            "RETROLARIUM_ALLOW_REPOSITORY_SCRIPTS": "all",
        }
        with (
            mock.patch.dict("os.environ", accepted),
            contextlib.redirect_stderr(errors),
        ):
            self.assertEqual(1, self.run_split().call_count)
        self.assertIn("unverified repository", errors.getvalue())

    def test_a_snapshot_inside_the_download_is_checked_against_its_record(self) -> None:
        snapshot = write_tree(
            self.root / ".retrolarium" / "base" / "probe-source",
            {
                "scripts/split.py": FILES["scripts/split.py"],
                "content/levels.json": b"{}",
            },
        )
        self.assertEqual(
            "probe_src",
            repository_trust.verify_scripts(snapshot, ["scripts/split.py"])["repository"],
        )
        (snapshot / "scripts" / "split.py").write_text("print('edited')\n", encoding="utf-8")
        with self.assertRaisesRegex(RepositoryTrustError, "changed since"):
            repository_trust.verify_scripts(snapshot, ["scripts/split.py"])

    def test_new_bytecode_and_import_archives_are_not_ignored(self) -> None:
        for name in ("tools/extra.pyc", "__pycache__/helper.cpython-311.pyc", "tools/extra.pyz", "extra.zip"):
            with self.subTest(name=name):
                write_tree(self.root, {name: b"synthetic executable bytes"})
                with self.assertRaisesRegex(RepositoryTrustError, "is not part of"):
                    repository_trust.verify_scripts(self.root, ["scripts/split.py"])
                (self.root / name).unlink()


class FlickyBuildVerificationTests(unittest.TestCase):
    def test_a_flicky_snapshot_outside_a_verified_download_is_not_built(self) -> None:
        from retrolarium_kit.games.flicky import content

        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            (project / content.SOURCE_DIRECTORY / "scripts").mkdir(parents=True)
            (project / content.SOURCE_DIRECTORY / "scripts" / "run.py").write_text("", encoding="utf-8")
            writer = project / "writer"
            writer.write_bytes(b"")
            tools = mock.MagicMock()
            with (
                mock.patch.dict("os.environ", {"RETROLARIUM_UNVERIFIED_GAMES": ""}),
                mock.patch.object(content, "validate_flicky_source_snapshot", return_value={}),
                mock.patch.object(content, "materialize_build_toolchain", return_value=tools),
                mock.patch.object(content, "build_content") as build,
                self.assertRaises(RepositoryTrustError),
            ):
                content.build_flicky_source_snapshot(project, native_writer=writer)
            build.assert_not_called()


class ReleaseManifestShapeTests(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        (self.root / "probe.rlj").write_text("", encoding="utf-8")
        self.descriptor = repository.GameDescriptor(
            self.root / "probe.rlj",
            self.root,
            "probe",
            "Probe",
            "nes",
            "probe.v1",
            (),
            None,
            {},
        )

    def test_a_manifest_that_is_not_an_object_is_refused(self) -> None:
        manifest = self.root / "release.json"
        manifest.write_text("[1, 2]", encoding="utf-8")
        with self.assertRaisesRegex(GameRepositoryError, "not a JSON object"):
            repository.check_release(self.descriptor, manifest)

    def test_a_removed_editor_must_be_a_path_inside_the_repository(self) -> None:
        manifest = self.root / "release.json"
        for path in ("/etc", "../outside", "C:/Windows"):
            manifest.write_text(
                json.dumps(
                    {
                        "release_line": "3.x",
                        "release_kind": "retrolarium",
                        "status": "development",
                        "retrolarium": {
                            "descriptor": "probe.rlj",
                            "profile": "probe.v1",
                            "revisions": [],
                            "removed_authoring": [{"path": path, "replaced_by": "rltool"}],
                        },
                    }
                ),
                encoding="utf-8",
            )
            with self.subTest(path=path):
                report = repository.check_release(self.descriptor, manifest)
                self.assertEqual("failed", report["status"])
                self.assertIn(
                    "removed_authoring[0].path must be a relative path",
                    " ".join(report["problems"]),
                )


if __name__ == "__main__":
    unittest.main()
