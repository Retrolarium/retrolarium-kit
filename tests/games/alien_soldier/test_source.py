from __future__ import annotations

from pathlib import Path
import shutil
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from helpers import game_repository
from retrolarium_kit.games.alien_soldier import workspace
from retrolarium_kit.games.alien_soldier.sequencer import Sequencer


# The Alien Soldier reconstruction is not part of the repository; the tests
# run where it sits beside this checkout (with its Retrolarium profile).
ALIEN_SOLDIER = game_repository("alien_soldier_src")
AVAILABLE = (ALIEN_SOLDIER / workspace.PROFILE).is_file()


@unittest.skipUnless(AVAILABLE, "the Alien Soldier source is not available")
class AlienSoldierSourceTests(unittest.TestCase):
    """Every document the studio edits, read from the source and written
    back unchanged, and every song and effect played by the sequencer."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(tempfile.mkdtemp())
        for folder in workspace.source_profile(ALIEN_SOLDIER)["copy"]:
            shutil.copytree(ALIEN_SOLDIER / folder, cls.root / folder)
        (cls.root / workspace.PROFILE).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ALIEN_SOLDIER / workspace.PROFILE, cls.root / workspace.PROFILE)
        cls.documents = workspace.export(cls.root, "jp")

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.root, True)

    def test_unedited_documents_change_no_source_file(self) -> None:
        documents = {module.DOCUMENT: self.documents[module.DOCUMENT] for module in workspace.DOCUMENTS}
        self.assertEqual(workspace.apply(self.root, "jp", documents), [])

    def test_every_payload_plays(self) -> None:
        tree = workspace.tree(self.root, workspace.source_profile(self.root), "jp")
        sequencer = Sequencer(
            tree,
            self.documents["sound/sound.json"],
            self.documents["sound/envelopes.json"],
            self.documents["sound/pcm.json"],
        )
        for item in self.documents["sound/sound.json"]["payloads"]:
            with self.subTest(item["label"]):
                writes, total = sequencer.render(item["label"], seconds=2)
                self.assertTrue(writes)
                self.assertTrue(all(write.sample < total for write in writes))


if __name__ == "__main__":
    unittest.main()
