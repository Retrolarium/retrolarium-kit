from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from retrolarium_kit.studio import repository
from retrolarium_kit.studio.repository import (
    GameRepositoryError, check_release, check_repository, fetch_repository, identify_rom, ips_patch, load_descriptor, open_rom, prepare_revision,
    profile_views, reset_revision, revision_statuses,
)
from retrolarium_kit.studio.project import build_studio_project, load_studio_project, verify_studio_project
from retrolarium_kit.core.game_profiles import game_profiles, repository_profile
from helpers import GAME_REPOSITORIES, game_repository


def apply_ips(original: bytes, patch: bytes) -> bytes:
    assert patch[:5] == b"PATCH"
    out = bytearray(original)
    at = 5
    while patch[at:at + 3] != b"EOF":
        offset = int.from_bytes(patch[at:at + 3], "big")
        size = int.from_bytes(patch[at + 3:at + 5], "big")
        data = patch[at + 5:at + 5 + size]
        if len(out) < offset + size:
            out.extend(bytes(offset + size - len(out)))
        out[offset:offset + size] = data
        at += 5 + size
    return bytes(out)


# The ROMs are not part of the repository; the descriptor tests run where
# the Pac-Man disassembly sits beside this checkout.
PACMAN = game_repository("pacman_src")
V10 = PACMAN / "Pac-Man (J) (V1.0) [!].nes"
V11 = PACMAN / "Pac-Man (J) (V1.1) [!].nes"
# The game's profile, which a repository keeps beside its descriptor.
PROFILE = PACMAN / "config" / "game_profile.json"

DESCRIPTOR = """schema = "retrolarium.game"
schema_version = 1

[game]
id = "pacman"
name = "Pac-Man"
platform = "nes"
profile = "pacman.v1"

[[revisions]]
id = "japan-v10"
label = "Japan · v1.0"
rom = "pacman10.nes"

[[revisions]]
id = "japan-v11"
label = "Japan · v1.1"
rom = "pacman11.nes"

"""


# A revision the repository builds itself (the expanded NROM-256 image of
# Source Reconstruction 3.0): the built-revision tests need one, and 3.1 keeps
# none, so the profile copy gains it when the repository's lacks it.
BUILT_REVISION = {
    "id": "japan-v10-nrom256",
    "name": "Pac-Man Japan v1.0 expanded (NROM-256)",
    "size": 40976,
    "sha1": "1a351d963c3e644aa8c59cb28406fb0d3029dbb2",
    "sha256": "6ea0a5ce61c410aa77b48fb208b2793dcac18f6f9e6eef5b490cced630a61512",
    "container": "ines",
    "mapper": 0,
}


def write_repository(root: Path, descriptor: str = DESCRIPTOR) -> Path:
    """A Pac-Man repository: the descriptor and the game's profile."""
    (root / "config").mkdir(parents=True, exist_ok=True)
    profile = json.loads(PROFILE.read_text(encoding="utf-8"))
    if BUILT_REVISION["id"] not in [item["id"] for item in profile["revisions"]]:
        profile["revisions"].append(BUILT_REVISION)
    (root / "config" / "game_profile.json").write_text(json.dumps(profile, indent=2), encoding="utf-8")
    (root / "pacman.rlj").write_text(descriptor, encoding="utf-8")
    return root / "pacman.rlj"


class IpsTests(unittest.TestCase):
    def test_patch_round_trips_changes_and_growth(self) -> None:
        original = bytes(range(256)) * 4
        edited = bytearray(original)
        edited[3] = 0xFF
        edited[500:520] = bytes(20)
        edited += b"tail"
        patch = ips_patch(original, bytes(edited))
        self.assertEqual(bytes(edited), apply_ips(original, patch))
        self.assertEqual(b"PATCHEOF", ips_patch(original, original))

    def test_a_change_at_eof_offset_starts_a_byte_early(self) -> None:
        original = bytes(0x454F50)
        edited = bytearray(original)
        edited[0x454F46] = 1
        patch = ips_patch(original, bytes(edited))
        self.assertEqual(b"\x45\x4F\x45", patch[5:8])
        self.assertEqual(bytes(edited), apply_ips(original, patch))

    def test_a_longest_run_at_eof_offset_still_fits_a_record(self) -> None:
        original = bytes(0x454F46 + 0x10000)
        edited = bytearray(original)
        edited[0x454F46:0x454F46 + 0xFFFF] = b"\x01" * 0xFFFF
        patch = ips_patch(original, bytes(edited))
        self.assertEqual(bytes(edited), apply_ips(original, patch))

    def test_shrinking_is_refused(self) -> None:
        with self.assertRaises(GameRepositoryError):
            ips_patch(bytes(4), bytes(2))

    def test_a_rom_opened_as_it_is_patches_without_a_build(self) -> None:
        from retrolarium_kit.cli import main
        from retrolarium_kit.studio.project import rom_as_is

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "original").mkdir()
            original = bytes(range(256)) * 2
            edited = bytearray(original)
            edited[10] = 0xEE
            (root / "original" / "game.bin").write_bytes(original)
            (root / "game.bin").write_bytes(bytes(edited))
            project = root / "game.rlj"
            project.write_text(
                'schema = "retrolarium.project"\nschema_version = 1\n\n[project]\nid = "game"\n'
                'name = "game"\nplatform = "unknown"\nprofile = "rom.v1"\nsource_mode = "rom"\n\n'
                '[source]\nrom = "game.bin"\noriginal = "original/game.bin"\nsha256 = "00"\n',
                encoding="utf-8")
            self.assertEqual(((root / "original" / "game.bin").resolve(), (root / "game.bin").resolve()),
                             rom_as_is(project))
            output = root / "game.ips"
            with contextlib.redirect_stdout(io.StringIO()):
                main(["studio", "patch", str(project), "--output", str(output)])
            self.assertEqual(bytes(edited), apply_ips(original, output.read_bytes()))


@unittest.skipUnless(V10.is_file() and V11.is_file() and PROFILE.is_file(), "the Pac-Man ROMs are not available")
class DescriptorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.descriptor = load_descriptor(write_repository(self.root))

    def tearDown(self) -> None:
        shutil.rmtree(self.root)

    def test_a_missing_rom_is_asked_for_and_a_wrong_one_is_named(self) -> None:
        self.assertEqual("missing", revision_statuses(self.descriptor)["revisions"][0]["rom_status"])
        self.assertEqual("needs-rom", prepare_revision(self.descriptor, "japan-v10")["status"])
        wrong = prepare_revision(self.descriptor, "japan-v10", rom=V11)
        self.assertEqual("rom-mismatch", wrong["status"])
        self.assertEqual("japan-v11", wrong["identified"][0]["revision"])
        self.assertFalse((self.root / "pacman10.nes").exists())
        self.assertEqual("japan-v10", identify_rom(V10)[0]["revision"])

    def test_prepare_build_and_patch(self) -> None:
        ready = prepare_revision(self.descriptor, "japan-v10", rom=V10)
        self.assertEqual("ready", ready["status"])
        self.assertEqual(V10.read_bytes(), (self.root / "pacman10.nes").read_bytes())
        self.assertEqual({"hack/japan-v10/resources.json", "hack/japan-v10/demo.json"},
                         set(ready["created_documents"]))
        project = Path(ready["project"])
        self.assertEqual(self.root / ".retrolarium" / "japan-v10", project.parent)
        inspection = load_studio_project(project)
        self.assertEqual("rom", inspection["project"]["source_mode"])
        self.assertEqual("Pac-Man [Japan · v1.0]", inspection["project"]["name"])
        # Unedited, the build is the ROM itself.
        report = build_studio_project(project)
        built = project.parent / report["artifact"]["path"]
        self.assertEqual(V10.read_bytes(), built.read_bytes())
        self.assertEqual("current", verify_studio_project(project)["status"])
        self.assertFalse(revision_statuses(self.descriptor)["revisions"][0]["edited"])
        # An edit in hack/ lands in the build; the ROM copy stays original.
        bundle_path = self.root / "hack" / "japan-v10" / "resources.json"
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        palette = next(r for r in bundle["resources"] if r["id"] == "round-gameplay")
        palette["values"][1] = (palette["values"][1] + 1) % 0x40
        bundle_path.write_text(json.dumps(bundle, indent=2), encoding="utf-8")
        self.assertEqual("mismatch", verify_studio_project(project)["status"])
        report = build_studio_project(project)
        edited = (project.parent / report["artifact"]["path"]).read_bytes()
        self.assertNotEqual(V10.read_bytes(), edited)
        self.assertEqual(V10.read_bytes(), (self.root / "pacman10.nes").read_bytes())
        self.assertEqual(edited, apply_ips(V10.read_bytes(), ips_patch(V10.read_bytes(), edited)))
        # Preparing again keeps the edits and the project.
        again = prepare_revision(self.descriptor, "japan-v10")
        self.assertEqual([], again["created_documents"])
        self.assertTrue(revision_statuses(self.descriptor)["revisions"][0]["edited"])
        # Resetting puts the ROM's documents back.
        self.assertEqual(["resources.json"], reset_revision(self.descriptor, "japan-v10")["restored_documents"])
        self.assertFalse(revision_statuses(self.descriptor)["revisions"][0]["edited"])
        build_studio_project(project)
        self.assertEqual("current", verify_studio_project(project)["status"])
        self.assertEqual(V10.read_bytes(), (project.parent / report["artifact"]["path"]).read_bytes())
        with self.assertRaises(GameRepositoryError):
            reset_revision(self.descriptor, "japan-v11")

    def test_open_rom_downloads_the_repository_once(self) -> None:
        archives = self.root / "archives"
        archives.mkdir()
        with zipfile.ZipFile(archives / "pacman_src.zip", "w") as bundle:
            bundle.writestr("pacman_src-main/pacman.rlj", DESCRIPTOR)
            bundle.write(PROFILE, "pacman_src-main/config/game_profile.json")
        games = self.root / "games"
        source = str(archives / "{repository}.zip")
        first = open_rom(V11, games, source=source)
        self.assertEqual(("ready", "japan-v11", True), (first["status"], first["revision"], first["downloaded"]))
        self.assertTrue((games / "pacman_src" / "hack" / "japan-v11" / "demo.json").is_file())
        again = open_rom(V10, games, source=source)
        self.assertEqual(("ready", "japan-v10", False), (again["status"], again["revision"], again["downloaded"]))
        # A descriptor already opened wins over the download folder.
        known = open_rom(V10, self.root / "elsewhere", known=[self.root / "pacman.rlj"])
        self.assertEqual(str(self.root / "pacman.rlj"), known["descriptor"])
        stranger = self.root / "stranger.nes"
        stranger.write_bytes(b"NES\x1a" + bytes(16))
        self.assertEqual("unknown", open_rom(stranger, games)["status"])

    def test_an_archive_cannot_write_outside_its_folder(self) -> None:
        archives = self.root / "archives"
        archives.mkdir()
        with zipfile.ZipFile(archives / "pacman_src.zip", "w") as bundle:
            bundle.writestr("../escape.txt", "x")
        with self.assertRaises(GameRepositoryError):
            fetch_repository("pacman", self.root / "games", source=str(archives / "{repository}.zip"))
        self.assertFalse((self.root / "escape.txt").exists())

    def test_downloads_name_a_release_tag_or_a_branch(self) -> None:
        catalog = repository.repository_catalog()
        for game, entry in catalog["games"].items():
            self.assertRegex(entry["ref"], r"^(tags|heads)/[\w.-]+$", game)
        self.assertEqual(
            "https://github.com/RetrolariumLab/pacman_src/archive/refs/tags/source-reconstruction-3.1.zip",
            repository._archive_url(catalog, catalog["games"]["pacman"], None))

    def test_a_view_can_belong_to_some_revisions(self) -> None:
        views = profile_views("pacman.v1")
        only_v11 = {**views, "tabs": [
            {**tab, "revisions": ["japan-v11"]} if tab["id"] == "demo" else tab for tab in views["tabs"]
        ]}
        with mock.patch.object(repository, "profile_views", return_value=only_v11):
            descriptor = load_descriptor(self.root / "pacman.rlj")
        ready = prepare_revision(descriptor, "japan-v10", rom=V10)
        self.assertEqual(["hack/japan-v10/resources.json"], ready["created_documents"])
        tabs = load_studio_project(Path(ready["project"]))["workspace"]["tabs"]
        self.assertNotIn("demo", [tab["id"] for tab in tabs])
        only_v12 = {**views, "tabs": [{**views["tabs"][0], "revisions": ["japan-v12"]}]}
        with mock.patch.object(repository, "profile_views", return_value=only_v12), \
                self.assertRaises(GameRepositoryError):
            load_descriptor(self.root / "pacman.rlj")

    def test_a_newer_kit_adds_its_resources_and_keeps_edits(self) -> None:
        ready = prepare_revision(self.descriptor, "japan-v10", rom=V10)
        bundle_path = self.root / "hack" / "japan-v10" / "resources.json"
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        palette = next(r for r in bundle["resources"] if r["id"] == "round-gameplay")
        palette["values"][1] = (palette["values"][1] + 1) % 0x40
        # As an older kit left them: one resource fewer, older originals.
        dropped = bundle["resources"].pop()
        bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
        stamp = Path(ready["project"]).parent / "original" / ".kit-extraction"
        stamp.write_text("0.0.0 old\n", encoding="utf-8")
        prepare_revision(self.descriptor, "japan-v10")
        upgraded = json.loads(bundle_path.read_text(encoding="utf-8"))
        self.assertEqual(dropped["id"], upgraded["resources"][-1]["id"])
        self.assertEqual(palette, next(r for r in upgraded["resources"] if r["id"] == "round-gameplay"))
        self.assertNotEqual("0.0.0 old\n", stamp.read_text(encoding="utf-8"))
        self.assertTrue(revision_statuses(self.descriptor)["revisions"][0]["edited"])

    def test_check_builds_every_revision_in_a_copy(self) -> None:
        shutil.copyfile(V10, self.root / "pacman10.nes")
        shutil.copyfile(V11, self.root / "pacman11.nes")
        report = check_repository(self.descriptor)
        self.assertEqual("ok", report["status"])
        self.assertEqual([("japan-v10", "ok"), ("japan-v11", "ok")],
                         [(entry["revision"], entry["status"]) for entry in report["revisions"]])
        # The check leaves the user's folders alone: it never made them here.
        self.assertFalse((self.root / "hack").exists())
        self.assertFalse((self.root / ".retrolarium").exists())
        (self.root / "pacman11.nes").unlink()
        report = check_repository(self.descriptor)
        self.assertEqual("failed", report["status"])
        self.assertEqual("ok", report["revisions"][0]["status"])
        self.assertIn("needs its ROM", report["revisions"][1]["reason"])
        only = check_repository(self.descriptor, ["japan-v10"])
        self.assertEqual(("ok", 1), (only["status"], len(only["revisions"])))

    def test_views_come_from_the_kit_not_the_descriptor(self) -> None:
        self.assertEqual("maze", self.descriptor.workspace["startup_tab"])
        (self.root / "pacman.rlj").write_text(DESCRIPTOR + '\n[workspace]\nlayout = "auto"\n', encoding="utf-8")
        with self.assertRaisesRegex(GameRepositoryError, "views come from the kit"):
            load_descriptor(self.root / "pacman.rlj")

    def built(self, build: str) -> None:
        (self.root / "pacman.rlj").write_text(DESCRIPTOR + BUILT + build, encoding="utf-8")
        self.descriptor = load_descriptor(self.root / "pacman.rlj")

    def test_a_built_revision_is_made_from_its_base_rom(self) -> None:
        # A stand-in build: it copies the ROM it is given, which is not the
        # expanded image, so the kit names the failed build.
        (self.root / "copy.py").write_text(
            "import shutil, sys\nshutil.copyfile(sys.argv[1], sys.argv[2])\n", encoding="utf-8")
        self.built('from = "japan-v10"\ncommand = ["copy.py", "{rom}", "{output}"]\n')
        expanded = revision_statuses(self.descriptor)["revisions"][2]
        self.assertEqual(("missing", "japan-v10"), (expanded["rom_status"], expanded["built_from"]["id"]))
        needs = prepare_revision(self.descriptor, "japan-v10-nrom256")
        self.assertEqual(("needs-rom", "pacman10.nes"), (needs["status"], needs["rom"]))
        # The ROM given for it is its base's, and lands where the base's goes.
        self.assertEqual("rom-mismatch", prepare_revision(self.descriptor, "japan-v10-nrom256", rom=V11)["status"])
        failed = prepare_revision(self.descriptor, "japan-v10-nrom256", rom=V10)
        self.assertEqual(V10.read_bytes(), (self.root / "pacman10.nes").read_bytes())
        self.assertEqual("build-failed", failed["status"])
        # A stale image is built again when the revision opens.
        self.assertTrue((self.root / "build/expanded/pacman.nes").is_file())
        self.assertEqual("buildable", revision_statuses(self.descriptor)["revisions"][2]["rom_status"])

    def test_a_built_revision_names_a_dumped_one(self) -> None:
        for build, message in (('from = "japan-v12"\ncommand = ["x.py"]\n', "built from a dumped revision"),
                               ('command = ["x.py"]\n', "build needs from and command")):
            with self.subTest(build=build), self.assertRaisesRegex(GameRepositoryError, message):
                self.built(build)

    @unittest.skipUnless((PACMAN / "pacman.rlj").is_file()
                         and "japan-v10-nrom256" in (PACMAN / "pacman.rlj").read_text(encoding="utf-8"),
                         "the Pac-Man repository keeps no expanded revision")
    def test_the_repository_builds_its_expanded_revision(self) -> None:
        report = check_repository(load_descriptor(PACMAN / "pacman.rlj"), ["japan-v10-nrom256"])
        self.assertEqual(("ok", "ok"), (report["status"], report["revisions"][0]["status"]))


@unittest.skipUnless(PROFILE.is_file(), "the Pac-Man repository is not available")
class RepositoryProfileTests(unittest.TestCase):
    def test_the_profile_comes_from_the_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "pacman.rlj").write_text(DESCRIPTOR, encoding="utf-8")
            with self.assertRaisesRegex(GameRepositoryError, "no config/game_profile.json"):
                load_descriptor(root / "pacman.rlj")
            write_repository(root, DESCRIPTOR.replace('"pacman.v1"', '"pacman.v2"'))
            with self.assertRaisesRegex(GameRepositoryError, "profile of pacman.v1, not pacman.v2"):
                load_descriptor(root / "pacman.rlj")
            self.assertEqual("pacman.v1", load_descriptor(write_repository(root)).profile)
            self.assertEqual(["japan-v10", "japan-v11", "japan-v10-nrom256"],
                             [item.revision_id for item in game_profiles().get("pacman").revisions])

    def test_open_rom_knows_the_revisions_the_repositories_keep(self) -> None:
        games = repository.repository_catalog()["games"]
        for game_id, folder in GAME_REPOSITORIES.items():
            profile = repository_profile(game_repository(folder))
            if profile is None:
                continue
            with self.subTest(game=game_id):
                self.assertEqual(
                    [(item.revision_id, item.name, item.sha256) for item in profile.revisions],
                    [(item["id"], item["name"], item["sha256"]) for item in games[game_id]["revisions"]])


@unittest.skipUnless(PROFILE.is_file(), "the Pac-Man repository is not available")
class ReleaseManifestTests(unittest.TestCase):
    """The Retrolarium part of a 3.0 manifest; no ROM is needed."""

    def setUp(self) -> None:
        from retrolarium_kit import __version__
        self.version = __version__
        self.root = Path(tempfile.mkdtemp())
        self.descriptor = load_descriptor(write_repository(self.root))
        self.manifest = self.root / "release.json"

    def tearDown(self) -> None:
        shutil.rmtree(self.root)

    def check(self, **changes) -> dict:
        document = {"release_line": "3.0", "release_kind": "retrolarium", "status": "development",
                    "retrolarium": {"descriptor": "pacman.rlj", "profile": "pacman.v1",
                                    "revisions": ["japan-v10", "japan-v11"],
                                    "kit": {"version": None, "commit": None},
                                    "removed_authoring": [{"path": "scripts/authoring/maze_studio.py",
                                                           "replaced_by": "Levels view"}]}}
        for key, value in changes.items():
            target = document["retrolarium"] if key in document["retrolarium"] else document
            target[key] = value
        self.manifest.write_text(json.dumps(document), encoding="utf-8")
        return check_release(self.descriptor, self.manifest)

    def test_a_development_draft_may_leave_the_kit_unpinned(self) -> None:
        self.assertEqual(("ok", []), (self.check()["status"], self.check()["problems"]))

    def test_a_tag_ready_release_pins_this_kit(self) -> None:
        self.assertIn("must pin the kit version", self.check(status="tag-ready")["problems"][0])
        pinned = {"version": self.version, "commit": "0" * 40}
        self.assertEqual("ok", self.check(status="tag-ready", kit=pinned)["status"])
        other = self.check(status="tag-ready", kit={"version": "0.0.1", "commit": None})
        self.assertIn("this is kit", other["problems"][0])

    def test_a_minor_release_of_the_line_is_accepted(self) -> None:
        self.assertEqual("ok", self.check(release_line="3.x")["status"])
        self.assertIn("are not 3.0 or 3.x", self.check(release_line="4.0")["problems"][0])
        self.assertIn("are not 3.0 or 3.x", self.check(release_kind="advanced")["problems"][0])

    def test_the_manifest_matches_the_descriptor_and_the_tree(self) -> None:
        self.assertIn("differ from the descriptor", self.check(revisions=["japan-v10"])["problems"][0])
        self.assertIn("profile", self.check(profile="smb1.v1")["problems"][0])
        (self.root / "scripts/authoring").mkdir(parents=True)
        (self.root / "scripts/authoring/maze_studio.py").write_text("", encoding="utf-8")
        problems = self.check()["problems"]
        self.assertTrue(any("still in the repository" in item for item in problems))
        self.assertTrue(any("scripts/authoring/ still holds tools" in item for item in problems))


# Pac-Man's expanded NROM-256 revision, built rather than dumped; the test
# adds its build table.
BUILT = """[[revisions]]
id = "japan-v10-nrom256"
label = "Japan · v1.0 expanded"
rom = "build/expanded/pacman.nes"

[revisions.build]
"""


FLICKY = game_repository("flicky_src")


@unittest.skipUnless((FLICKY / "Flicky (UE) [!].bin").is_file() and (FLICKY / "flicky.rlj").is_file(),
                     "the Flicky repository and ROM are not available")
class FlickyDescriptorTests(unittest.TestCase):
    def test_a_downloaded_repository_opens_through_its_snapshot(self) -> None:
        listed = subprocess.run(["git", "ls-files", "-z"], cwd=FLICKY, capture_output=True, check=True).stdout
        with tempfile.TemporaryDirectory() as directory:
            # The repository as an archive gives it: tracked files, no .git.
            root = Path(directory) / "flicky_src"
            for name in filter(None, listed.decode().split("\0")):
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(FLICKY / name, root / name)
            shutil.copyfile(FLICKY / "flicky.rlj", root / "flicky.rlj")
            descriptor = load_descriptor(root / "flicky.rlj")
            self.assertEqual("needs-rom", prepare_revision(descriptor, "ue")["status"])
            ready = prepare_revision(descriptor, "ue", rom=FLICKY / "Flicky (UE) [!].bin")
            self.assertEqual("ready", ready["status"])
            inspection = load_studio_project(Path(ready["project"]))
            self.assertEqual("flicky-content", inspection["project"]["source_mode"])
            self.assertFalse(revision_statuses(descriptor)["revisions"][0]["edited"])
            levels = root / ".retrolarium" / "ue" / "flicky-source" / "content" / "workspace" / "level" / "levels.json"
            levels.write_text(levels.read_text(encoding="utf-8").replace("\n", "\n ", 1) + " ", encoding="utf-8")
            self.assertFalse(revision_statuses(descriptor)["revisions"][0]["edited"])
            value = json.loads(levels.read_text(encoding="utf-8"))
            value["edited_by_test"] = True
            levels.write_text(json.dumps(value), encoding="utf-8")
            self.assertTrue(revision_statuses(descriptor)["revisions"][0]["edited"])


if __name__ == "__main__":
    unittest.main()
