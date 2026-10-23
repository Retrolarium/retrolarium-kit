from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from retrolarium_kit.core.authoring import AuthoringError, CodePatch
from retrolarium_kit.core.game_profiles import _code_patches
from retrolarium_kit.studio.playtest import invincible_image
from retrolarium_kit.studio.project import StudioProjectError


class Revision:
    def __init__(self, patches: tuple[CodePatch, ...]) -> None:
        self.invincible = patches


class InvinciblePlaytestTests(unittest.TestCase):
    def test_the_profile_lists_patches_by_file_offset(self) -> None:
        patches = _code_patches([{"offset": 4, "expect": "a931", "write": "eaea"}], "invincible")
        self.assertEqual((CodePatch(4, b"\xa9\x31", b"\xea\xea"),), patches)
        for bad in ([{"offset": 4, "expect": "a9", "write": "eaea"}], [{"offset": 4, "expect": "zz", "write": "ea"}]):
            with self.assertRaises(AuthoringError):
                _code_patches(bad, "invincible")

    def test_the_playtest_runs_a_patched_copy_beside_the_build(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            build = Path(directory) / "game.nes"
            build.write_bytes(bytes(range(16)))
            self.assertEqual(build, invincible_image(build, Revision(())))
            copy = invincible_image(build, Revision((CodePatch(4, b"\x04\x05", b"\xea\xea"),)))
            self.assertEqual(Path(directory) / "game-playtest.nes", copy)
            self.assertEqual(bytes((0, 1, 2, 3, 0xEA, 0xEA, 6)), copy.read_bytes()[:7])
            self.assertEqual(bytes(range(16)), build.read_bytes())
            with self.assertRaisesRegex(StudioProjectError, "code moved"):
                invincible_image(build, Revision((CodePatch(4, b"\x99", b"\xea"),)))


if __name__ == "__main__":
    unittest.main()
