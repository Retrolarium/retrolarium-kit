from __future__ import annotations

from pathlib import Path
import shutil
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from helpers import game_repository
from retrolarium_kit.games.alien_soldier import objects, workspace
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
        if (ALIEN_SOLDIER / objects.ART).is_file():
            shutil.copyfile(ALIEN_SOLDIER / objects.ART, cls.root / objects.ART)
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

    def test_stages_form_levels_by_block_set(self) -> None:
        exported = self.documents["levels/stages.json"]
        sets = {item["id"]: item["stages"] for item in exported["block_sets"]}
        self.assertEqual(sets["Stage1BaseMappingData6000"], [0, 1, 2, 3])
        self.assertEqual(sets["Stage1Phase3AndStage8MappingData6000"], [7, 8])
        scenes = exported["scenes"]
        records = {item["label"]: item for item in self.documents["levels/levels.json"]["records"]}
        for index in (0, 1, 2, 3):
            area = scenes[index]["area"]
            # Each stage starts at its camera and ends a screen past the next.
            self.assertEqual(area["x"], records[scenes[index]["record"]]["camera_x"])
            self.assertEqual(area["height"], 224)
        self.assertEqual(scenes[0]["area"]["x"] + scenes[0]["area"]["width"], scenes[1]["area"]["x"] + 320)
        # Stage 16 pins its camera between two X bounds: a column.
        column = scenes[15]["area"]
        self.assertEqual((column["x"], column["width"]), (0x620, 0x6A0 + 320 - 0x620))
        self.assertGreater(column["height"], 224)

    def test_a_staging_buffer_is_no_map(self) -> None:
        coarse = [item for item in self.documents["levels/maps.json"]["payloads"] if item["role"] == "coarse"]
        self.assertTrue(coarse)
        self.assertTrue(all(len(item["values"]) <= 32 * 32 for item in coarse))
        tiles = {item["label"] for item in self.documents["graphics/compressed_tiles.json"]["payloads"]}
        self.assertLessEqual({"Boss_BackStringerTileArt2", "Stage22And24TileArt2"}, tiles)

    def test_objects_group_every_mapping_block_once(self) -> None:
        exported = self.documents["graphics/objects.json"]
        grouped = [label for group in exported["groups"] for label in group["blocks"]]
        blocks = [block["label"] for block in self.documents["graphics/mappings.json"]["blocks"]]
        self.assertEqual(sorted(grouped), sorted(blocks))
        self.assertLessEqual(set(exported["contexts"]), set(blocks))

    def test_the_recorded_object_art_fits_the_source(self) -> None:
        if not (self.root / objects.ART).is_file():
            self.skipTest("the repository records no object art")
        contexts = self.documents["graphics/objects.json"]["contexts"]
        seen = [label for label, context in contexts.items() if not context["borrowed"]]
        self.assertGreater(len(seen), 100)
        # The shared explosion frames draw from the art every stage loads at $9000.
        frame = contexts["SharedCombatSpriteFrame00"]
        self.assertEqual(frame["attribute"] & 0x7FF, 0x480)
        self.assertIn({"source": "SharedSceneAndStageTileArt9000", "command": 7, "vram": 0x9000}, frame["loads"])

    def test_object_art_naming_what_the_source_lacks_is_refused(self) -> None:
        path = self.root / objects.ART
        before = path.read_bytes() if path.is_file() else None
        tree = workspace.tree(self.root, workspace.source_profile(self.root), "jp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                '{"schema": "alien-soldier.object-art", "schema_version": 1, '
                '"mappings": {"NoSuchMapping": {"attribute": "$0480"}}}',
                encoding="utf-8",
            )
            with self.assertRaises(objects.ObjectError):
                objects.recorded(tree)
        finally:
            if before is None:
                path.unlink()
            else:
                path.write_bytes(before)

    def test_art_is_grouped_by_what_draws_with_it(self) -> None:
        payloads = {item["label"]: item for item in self.documents["graphics/art.json"]["payloads"]}
        compressed = {item["label"] for item in self.documents["graphics/compressed_tiles.json"]["payloads"]}
        plain = {item["label"] for item in self.documents["graphics/tiles.json"]["payloads"]}
        self.assertEqual(set(payloads), compressed | plain)
        # The player's pieces, the credits' screen maps and a stage's art.
        self.assertEqual(payloads["Player_PhoenixDashAttackSpriteArtPiece00"]["group"], "hero")
        self.assertEqual(payloads["Credits_Scene17MappingData"]["group"], "map")
        level = [item for item in payloads.values() if item["group"] == "level"]
        self.assertTrue(level)
        self.assertTrue(all("list" in item["colours"] for item in level))

    def test_the_credits_scenes_come_from_their_table(self) -> None:
        scenes = [item for item in self.documents["screens/screens.json"]["screens"] if item.get("kind") == "credits"]
        self.assertEqual(len(scenes), 20)
        for scene in scenes:
            table = scene["name_table"]
            self.assertIn(table["vram"], (0xC000, 0xE000))
            self.assertEqual(len(scene["palette"]["colours"]), 16)
            self.assertIn(table["source"], [load["source"] for load in scene["vram"]])


if __name__ == "__main__":
    unittest.main()
