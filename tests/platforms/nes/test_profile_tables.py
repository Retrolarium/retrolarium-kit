"""rltool profile tables: the table places a game's builds give, compared
with its config/game_profile.json (Pac-Man's repository; skipped without
it or without its japan-v10 build)."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from helpers import game_repository
from retrolarium_kit.platform.nes import profile_tables


class ProfileTablesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.game = game_repository("pacman_src")
        if not (self.game / "build" / "revisions" / "japan-v10" / "pacman.dbg").is_file():
            missing = "pacman_src with its japan-v10 build is not present"
            if os.environ.get("RETROLARIUM_REQUIRE_GAMES"):
                raise AssertionError(missing)
            self.skipTest(missing)

    def test_the_check_finds_a_table_the_profile_misplaces(self) -> None:
        spec = profile_tables.load_spec(self.game)
        self.assertEqual([], profile_tables.check(self.game, spec, {"japan-v10"}))
        with tempfile.TemporaryDirectory() as directory:
            copy = Path(directory)
            shutil.copytree(self.game / "src", copy / "src")
            shutil.copytree(self.game / "config", copy / "config")
            dbg = Path("build/revisions/japan-v10/pacman.dbg")
            (copy / dbg).parent.mkdir(parents=True)
            shutil.copyfile(self.game / dbg, copy / dbg)
            path = copy / "config" / "game_profile.json"
            profile = json.loads(path.read_text(encoding="utf-8"))
            resource = next(r for r in profile["resources"] if r.get("codec") == profile_tables.CODEC)
            name, place = next(iter(resource["parameters"]["tables"].items()))
            place["file_offset"] += 1
            path.write_text(json.dumps(profile), encoding="utf-8")
            problems = profile_tables.check(copy, spec, {"japan-v10"})
        self.assertEqual(1, len(problems))
        self.assertIn(f"japan-v10 {name}", problems[0])

    def test_a_revision_not_built_is_named(self) -> None:
        spec = {"revisions": {"x": {"engine": "e", "dbg": "build/none.dbg", "offsets": "image"}},
                "engines": {"e": []}}
        with self.assertRaisesRegex(profile_tables.ProfileTablesError, "build the revision first"):
            profile_tables.resolve_all(self.game, spec)


if __name__ == "__main__":
    unittest.main()
