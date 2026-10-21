"""Game profiles: found in the game repository the files a command names lie in."""

from __future__ import annotations

import contextlib
import io
from pathlib import Path
import shutil
import tempfile
import unittest

from helpers import HONKEYPONG_PROFILE
from retrolarium_kit.core import game_profiles
from retrolarium_kit.core.authoring import AuthoringError
from retrolarium_kit.core.game_profiles import discover_profiles


class DiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.opened = dict(game_profiles._OPENED)
        game_profiles._OPENED.pop("honkeypong", None)
        self.folder = tempfile.TemporaryDirectory()
        self.repository = Path(self.folder.name) / "honkeypong_src"
        (self.repository / "config").mkdir(parents=True)
        shutil.copyfile(HONKEYPONG_PROFILE, self.repository / "config" / "game_profile.json")
        self.document = self.repository / "hack" / "main" / "resources.json"
        self.document.parent.mkdir(parents=True)
        self.document.write_text("{}", encoding="utf-8")

    def tearDown(self) -> None:
        game_profiles._OPENED.clear()
        game_profiles._OPENED.update(self.opened)
        self.folder.cleanup()

    def test_a_document_inside_a_repository_brings_its_profile(self) -> None:
        with self.assertRaises(AuthoringError):
            game_profiles.game_profiles().get("honkeypong")
        discover_profiles(self.document)
        self.assertEqual("honkeypong", game_profiles.game_profiles().get("honkeypong").game_id)

    def test_a_place_outside_any_repository_brings_none(self) -> None:
        discover_profiles(Path(self.folder.name))
        self.assertNotIn("honkeypong", game_profiles.game_profiles().identifiers())

    def test_the_cli_finds_the_profile_of_the_files_it_is_given(self) -> None:
        from retrolarium_kit.cli import main

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            main(["game", "profiles", str(self.document)])
        self.assertIn("honkeypong", output.getvalue())


if __name__ == "__main__":
    unittest.main()
