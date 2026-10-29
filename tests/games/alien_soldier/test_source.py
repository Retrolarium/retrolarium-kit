from __future__ import annotations

from pathlib import Path
import shutil
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from helpers import game_repository
from retrolarium_kit.core import game_names
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
        for path in (objects.ART, game_names.PATH):
            if (ALIEN_SOLDIER / path).is_file():
                (cls.root / path).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ALIEN_SOLDIER / path, cls.root / path)
        cls.documents = workspace.export(cls.root, "jp")

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.root, True)

    def test_unedited_documents_change_no_source_file(self) -> None:
        documents = {module.DOCUMENT: self.documents[module.DOCUMENT] for module in workspace.DOCUMENTS}
        self.assertEqual(workspace.apply(self.root, "jp", documents), [])

    def test_a_project_saved_with_maps_as_mappings_still_applies(self) -> None:
        # Kits before this one read the maps an asset list loads as sprite
        # mappings; a project saved then keeps those blocks.
        document = dict(self.documents["graphics/mappings.json"])
        document["blocks"] = [
            {"label": "Stage1BaseMappingData4000", "kind": "static", "mappings": []},
            *document["blocks"],
        ]
        documents = {module.DOCUMENT: self.documents[module.DOCUMENT] for module in workspace.DOCUMENTS}
        documents["graphics/mappings.json"] = document
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

    def test_the_sequencer_writes_what_the_driver_writes(self) -> None:
        # Each case was checked write for write against the game's own
        # driver in MAME (every song and effect matches it frame by frame).
        tree = workspace.tree(self.root, workspace.source_profile(self.root), "jp")
        sequencer = Sequencer(
            tree,
            self.documents["sound/sound.json"],
            self.documents["sound/envelopes.json"],
            self.documents["sound/pcm.json"],
        )

        def frames(label: str, seconds: float = 2) -> dict[int, list]:
            writes, _ = sequencer.render(label, seconds=seconds)
            by_frame: dict[int, list] = {}
            for write in writes:
                if write.chip == "ym2612" and write.address != 0x2A:
                    by_frame.setdefault(write.sample * 60 // 44100, []).append(write)
            return by_frame

        # A pan animation stepped past its end reads on into the next one:
        # FM5 of the song $8E pans to both sides, not to the left alone.
        song = frames("Sound_BGM_8E")
        self.assertEqual({w.value for f in range(19, 23) for w in song[f] if (w.port, w.address) == (1, 0xB5)}, {0xC0})
        # Pan mode 4 runs past both jump tables into the restart: FM1 of
        # the song $83 writes its pan every frame of a note.
        song = frames("Sound_BGM_83")
        self.assertTrue(all(any((w.port, w.address) == (0, 0xB4) for w in song[f]) for f in range(55, 61)))
        # $E9 sets AM over the effect's first voice bytes ($31 for FM5's
        # third operator).
        effect = frames("Sound_SFX_DD")
        self.assertIn((1, 0x65, 0xB1), [(w.port, w.address, w.value) for w in effect[1]])
        # An effect's channels run in the driver's record order, FM4 first.
        effect = frames("Sound_SFX_A1")
        voices = [w.address for w in effect[0] if w.port == 1 and w.address in (0xB0, 0xB1)]
        self.assertEqual(voices, [0xB0, 0xB1])
        # The writes of a frame take time: a key-on comes after its key-off.
        writes, _ = sequencer.render("Sound_BGM_84", seconds=10)
        offs: dict[int, int] = {}
        for write in writes:
            if write.chip == "ym2612" and write.address == 0x28:
                if write.value & 0xF0:
                    self.assertGreater(write.sample, offs.get(write.value & 7, -1))
                else:
                    offs[write.value & 7] = write.sample

    def test_stages_form_levels_by_block_set(self) -> None:
        exported = self.documents["levels/stages.json"]
        sets = {item["id"]: item["stages"] for item in exported["block_sets"]}
        self.assertEqual(sets["Stage1BaseMappingData6000"], [0, 1, 2, 3])
        self.assertEqual(sets["Stage1Phase3AndStage8MappingData6000"], [7, 8])
        scenes = exported["scenes"]
        records = {item["label"]: item for item in self.documents["levels/levels.json"]["records"]}
        for index in (0, 1, 2, 3):
            area = scenes[index]["area"]
            # Each stage starts at its camera and ends a screen past where it
            # pins the camera for its boss.
            self.assertEqual(area["x"], records[scenes[index]["record"]]["camera_x"])
            self.assertEqual(area["height"], 224)
        self.assertEqual(scenes[0]["area"]["x"] + scenes[0]["area"]["width"], 0x6E8 + 320)
        # Xi-Tiger's handlers move the camera but never draw the map: the
        # picture stays where the entry drew it, at the record's X.
        self.assertEqual((scenes[8]["area"]["x"], scenes[8]["area"]["width"]), (0x800, 0xA0 + 320))
        # Sunset Sting's stage climbs before its boss.
        self.assertGreater(scenes[14]["area"]["height"], 224)
        self.assertEqual(len(scenes[14]["area"]["parts"]), 2)
        # After Viblack the fight moves to Back Stringer's web, apart.
        (web,) = scenes[15]["arenas"]
        # The 256 lines streamed down from row 0, coloured by the boss
        # Viblack brings in.
        self.assertEqual(
            {key: web[key] for key in ("x", "y", "width", "height")},
            {"x": 0x1060, "y": 0, "width": 320, "height": 256},
        )
        # The Destroyer Prototype fights in the empty space the asteroid field
        # climbs to (camera Y $F600); the moons and surface streamed from row
        # $B00 once it is gone are Shield Viper's (stage 22 restarts there).
        (space,) = scenes[20]["arenas"]
        self.assertEqual((space["x"], space["y"], space["height"]), (0, 0xA00, 224))
        (backdrop,) = scenes[21]["arenas"]
        self.assertEqual((backdrop["x"], backdrop["y"], backdrop["height"]), (0, 0xB00, 256))
        self.assertEqual(len(scenes[7]["arenas"]), 1)
        self.assertEqual(
            (web["list"], web["palettes"]),
            ("Boss_BackStringerGraphicsLoadList", [["Boss_BackStringerPaletteCommand", 0]]),
        )
        # Epsilon 1's parallax works the camera Y out as $28 - $18: the rows
        # sit 16 pixels lower, the floor under the player.
        self.assertEqual(scenes[16]["area"]["y"], -0x10)
        # Its floor is a raster split: rows of blocks the initializer writes
        # into plane B's other name table, shown from line 200 down.
        self.assertEqual(
            scenes[16]["floor"],
            {"ram": 0xFFFF4000, "blocks": list(range(0x29, 0x33)), "per_row": 10, "line": 200},
        )
        self.assertNotIn("floor", scenes[15])
        # Z-Leo moves his fight to (0, $100) and rises to $240, with his own
        # maps; that screen is shown above the stage.
        (fight,) = scenes[24]["arenas"]
        self.assertEqual((fight["x"], fight["y"], fight["height"], fight["inline"]), (0, -0x240, 0x240 + 224, True))
        # It is as wide as his core room (the camera follows him across it)
        # and shown over the room the corridor ends in, where the stage's
        # plane B reads the same rows.
        self.assertEqual((fight["width"], fight["shown"]), (384, {"x": 0x400, "y": -0x40}))
        self.assertIn({"x": 0x400, "y": -0x40, "width": 384, "height": fight["height"]}, scenes[24]["area"]["parts"])
        # His rise streams plane B with plane A's patterns ($4000).
        self.assertEqual((fight["planes"][0], fight["planes"][1]["patterns"]), (None, 0xFFFF4000))
        # ... the patterns his battle entry loads at $4020, in his colours.
        self.assertEqual(fight["lists"], ["Boss_ZLeoBattleEntryEffectDescriptor"])
        self.assertEqual(fight["palettes"], [["Boss_ZLeoPaletteCommands", 0]])
        # The first level's stages scroll plane B at a quarter speed; the
        # train's rows run on their own.
        self.assertEqual([scenes[index].get("plane_b_quarter") for index in (0, 3, 7)], [True, True, False])
        # The train streams plane B (the train) with plane A's camera X and
        # leaves plane A as the entry drew it at $C00.
        self.assertTrue(scenes[7]["plane_b_follows"])
        self.assertEqual(scenes[7]["planes"][0]["fixed_x"], 0xC00)
        # Flying-Neo widens the pinned camera by $A0 and swings it from Y $60
        # to $80, over plane B streamed $910 rows lower.
        (neo,) = scenes[7]["arenas"]
        self.assertEqual((neo["x"], neo["y"], neo["width"], neo["height"]), (0xE60, -0x80, 0xA0 + 320, 0x20 + 224))
        self.assertEqual(neo["planes"][1]["y_offset"], 0x910)
        # Xi-Tiger's screen is apart: the art its handlers load turns the
        # train into clouds, and plane B is blanked.
        self.assertTrue(scenes[8]["area"]["apart"])
        self.assertIsNone(scenes[8]["planes"][1])
        self.assertEqual(
            [load["list"] for load in scenes[8]["later"]],
            ["Stage9_CaterpillarTileAssetLoadList", "Stage9_XiTigerEntranceTileAssetLoadList"],
        )
        # Stage 7's first state loads the counter alien's art at once.
        self.assertIn("Stage7_InitialTileAssetLoadList", scenes[6]["lists"])
        # Stage 16 pins its camera between two X bounds: a column.
        column = scenes[15]["area"]
        self.assertEqual((column["x"], column["width"]), (0x620, 0x6A0 + 320 - 0x620))
        self.assertGreater(column["height"], 224)

    def test_stage_initializers_rewrite_their_maps(self) -> None:
        scenes = self.documents["levels/stages.json"]["scenes"]
        snow = {"routine": "update-indices", "ram": "0xFFFF2000", "groups": 0x80, "add": 0, "tiles": 0x150}
        # Stage 19 reaches the call through a branch into stage 18's code.
        self.assertEqual(scenes[17]["transforms"], [snow])
        self.assertEqual(scenes[18]["transforms"], [snow])
        self.assertEqual(
            scenes[19]["transforms"],
            [{"routine": "update-indices", "ram": "0xFFFF4000", "groups": 0xC0, "add": 0x8000, "tiles": 0x180}],
        )
        self.assertEqual(scenes[0]["transforms"], [])

    def test_a_stage_loads_more_maps_as_its_camera_moves(self) -> None:
        scenes = self.documents["levels/stages.json"]["scenes"]
        # Stage 19 starts on stage 18's snow and swaps in the factory at $D80.
        (factory,) = scenes[18]["later"]
        self.assertEqual((factory["camera_x"], factory["list"]), (0xD80, "Stage19_InitialTileAssetLoadList"))
        self.assertIn("Stage19InitialMappingData6000", [write["source"] for write in factory["map_writes"]])
        self.assertEqual(scenes[17]["later"], [])

    def test_a_loader_plays_the_shared_tail_it_falls_into(self) -> None:
        scenes = self.documents["levels/stages.json"]["scenes"]
        for index in (20, 21, 22):
            self.assertIn("Stage3Phase12SharedAssetLoadList", scenes[index]["lists"])
            self.assertIsNotNone(scenes[index]["planes"][0])

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

    def test_bosses_draw_with_their_asset_sets(self) -> None:
        exported = self.documents["graphics/objects.json"]
        contexts = exported["contexts"]
        # Joker was never recorded: his art, at $6000 (tile $300), in the
        # colours of his stage and his palette command.
        joker = contexts["Boss_JokerRotationMappingA0"]
        self.assertTrue(joker["derived"])
        self.assertEqual(joker["attribute"] & 0x7FF, 0x300)
        self.assertIn({"source": "Boss_JokerTileArt", "command": 7, "vram": 0x6000}, joker["loads"])
        self.assertEqual(joker["palettes"][-1], ["Boss_JokerPaletteCommand", 0])
        self.assertIn("Back Stringer", [group["title"] for group in exported["groups"]])
        # Blocks drawn from a boss's art take its colours, whatever play
        # matched (the Seven Force).
        self.assertEqual(
            contexts["SevenForcesRotationSpriteFrame29"]["palettes"][-1], ["SevenForcesValkiriePaletteCommand", 0]
        )
        # A borrowed block a stage spawns draws with that stage's art: stage
        # 7's counter alien from the art its first state loads.
        alien = [load["source"] for load in contexts["IndexedObjectSpriteMapping09"]["loads"]]
        self.assertIn("Stage7InitialTileArt", alien)
        # A screen's code draws the ending planet's burst in that screen's art.
        burst = contexts["EndingPlanetBurstSpriteMappingB"]
        self.assertIn("EndingSequenceTileArt2000", [load["source"] for load in burst["loads"]])
        # Maps an asset list loads are no sprite mappings.
        blocks = {block["label"] for block in self.documents["graphics/mappings.json"]["blocks"]}
        self.assertNotIn("Stage1BaseMappingData4000", blocks)
        # A boss's art shows in its palette command's line.
        art = {item["label"]: item for item in self.documents["graphics/art.json"]["payloads"]}
        self.assertEqual(
            (art["Boss_JetsripperTileArt"]["colours"], art["Boss_JetsripperTileArt"]["line"]),
            ({"commands": [["Boss_JetsripperPaletteCommand", 0]]}, 3),
        )

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
        # Most placements of the stages' spawn lists record what they draw.
        spawns = self.documents["graphics/objects.json"]["spawns"]
        self.assertGreater(len(spawns), 250)
        first = next(item for item in spawns if item["list"] == "Stage1_ObjectSpawnList" and item["index"] == 0)
        self.assertEqual((first["type"], first["parameter"]), (0x1C, 0x10))

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

    def test_jointed_bosses_read_as_skeletons_with_poses_and_animations(self) -> None:
        document = self.documents["graphics/bosses.json"]
        titles = [boss["title"] for boss in document["bosses"]]
        for name in ("Antroid", "Terobuster", "Shellshogun", "Joker", "Xi-Tiger", "Z-Leo", "Madam Barbar"):
            self.assertIn(name, titles)
        # The Seven Force's forms read each from its own file and what it
        # calls; Sirene's tentacles follow a history of the frames before.
        checked = {boss["title"] for boss in document["bosses"] if boss["checked"]}
        for name in ("Valkirie battle", "Medusa", "Sylpheed", "Artemis", "Sirene"):
            self.assertIn(name, checked)
        # Sharpsteel's animations are written as bytes; Deep Strider's code
        # alone turns it (fields its setup gives, four-frame tables).
        self.assertIn("Sharpsteel", checked)
        strider = next(boss for boss in document["bosses"] if boss["title"] == "Deep Strider")
        self.assertTrue(strider["checked"])
        self.assertEqual((strider["poses"], strider["sequences"], strider["channels"]), (None, [], 0))
        self.assertTrue(all(len(part["frames"]) == 4 for part in strider["parts"] if part["kind"] == "rotation"))
        sirene = next(boss for boss in document["bosses"] if boss["title"] == "Sirene")
        self.assertGreater(sirene["settle"], 30)
        antroid = next(boss for boss in document["bosses"] if boss["title"] == "Antroid")
        self.assertEqual((len(antroid["parts"]), antroid["channels"], antroid["timing"]), (26, 15, "set"))
        self.assertEqual(antroid["asset_set"], "Boss_AntroidAssetSet")
        self.assertTrue(antroid["checked"])
        # The poses are the table offsets its animations name: all rows but one.
        self.assertEqual(
            [row["offset"] for row in document["poses"][antroid["poses"]]][:9], [0, 16, 32, 48, 64, 80, 96, 112, 144]
        )
        self.assertEqual(len(antroid["sequences"]), 16)
        self.assertIsNone(antroid["parts"][0]["parent"])
        # A foot hangs from the leg chain and turns with the last leg channel;
        # the side the pose code tests is field 6.
        self.assertEqual(antroid["parts"][25]["parent"], 24)
        self.assertEqual(antroid["joints"][25], 14)
        self.assertEqual(antroid["side_field"], 6)
        self.assertEqual(antroid["parts"][14]["angle"], 0x1C0)
        self.assertEqual(antroid["facing"]["256"]["set"], [2])
        self.assertEqual(len(document["cosine"]), 256)
        sequences = {item["label"]: item for item in document["sequences"]}
        self.assertEqual(
            sequences["Boss_AntroidIdlePoseCommands"]["commands"],
            [{"pose": 6, "frames": 32, "steps": 32}, {"pose": 7, "frames": 32, "steps": 32}],
        )
        retry = sequences["Boss_AntroidJumpSlamRetryPosePrefix"]
        self.assertEqual((retry["end"], retry["next"]), ("next", "Boss_AntroidJumpSlamFirstArcPoseCommands"))
        self.assertIn({"sound": 0xD0}, sequences["Boss_AntroidJumpAttackPoseCommands"]["commands"])
        # Terobuster adds a signed count to its steps; Joker's hands and feet
        # are anchors its renderer's code places.
        terobuster = next(boss for boss in document["bosses"] if boss["title"] == "Terobuster")
        self.assertEqual(terobuster["timing"], "add")
        joker = next(boss for boss in document["bosses"] if boss["title"] == "Joker")
        self.assertTrue(joker["anchor_code"])
        self.assertEqual([index for index, part in enumerate(joker["parts"]) if part.get("anchor")], [19, 20, 21, 22])
        # Flying-Neo's renderer walks from its third slot, eight parts; its
        # code puts the second beside the boss.
        neo = next(boss for boss in document["bosses"] if boss["title"] == "Flying-Neo")
        self.assertEqual(neo["traversed"], [2, 8])
        self.assertTrue(neo["anchor_code"] and neo["checked"])
        # Back Stringer's arms hang in threes from the points half a bone
        # past the part above; its arms pick their mappings from a table.
        stringer = next(boss for boss in document["bosses"] if boss["title"] == "Back Stringer")
        self.assertEqual(
            [(part.get("reach", False), part.get("from_reach", False)) for part in stringer["parts"][3:6]],
            [(True, False), (True, True), (False, True)],
        )
        table = stringer["symbols"]["Boss_BackStringerPartAngleFrameTable"]
        self.assertEqual(
            stringer["tables"][f"{table:06X}"][:8], f"{stringer['symbols']['Boss_BackStringerPartAngleMapping0']:08x}"
        )
        self.assertTrue(stringer["anchor_code"] and stringer["checked"])
        # Each Seven Force form draws in its own colours, its set's command;
        # the alternate Valkirie steps twelve channels of its own.
        forms = {boss["title"]: boss for boss in document["bosses"]}
        self.assertEqual(forms["Medusa"]["palettes"][-1], ["SevenForcesMedusaPaletteCommand", 0])
        self.assertEqual(forms["Sylpheed"]["palettes"][-1], ["SevenForcesSylpheedPaletteCommand", 0])
        self.assertEqual(forms["Valkirie alternate"]["channels"], 12)
        # A debug viewer builds a boss the game cut, in the $3EC set's art:
        # one animation between two poses. No text names it: the game
        # repository's name for its entity type does.
        from retrolarium_kit.core.game_names import GameNames

        viewer = next(boss for boss in document["bosses"] if boss["id"] == "Debug_ValkirieViewerInitialize")
        self.assertEqual((viewer["asset_set"], len(document["poses"][viewer["poses"]])), ("EntityType3ECAssetSet", 2))
        self.assertEqual(viewer["title"], GameNames.load(ALIEN_SOLDIER).name("bosses", 0x3EC))
        # Wolf Garopa's horse's head and neck and its rider's head and arm are
        # records its setup fills by hand, placed by the code after its
        # renderer by their pixel places.
        wolf = forms["Wolf Garopa"]
        self.assertEqual(
            [(index, part["source"]) for index, part in enumerate(wolf["parts"]) if part["kind"] == "mapping"][-4:],
            [
                (26, "Boss_WolfGaropaInitialPartMapping"),
                (27, "Boss_WolfGaropaOrbDirectionMapping0"),
                (28, "Boss_WolfGaropaNearPlayerMapping"),
                (29, "Boss_WolfGaropaOrbAlternateMapping"),
            ],
        )
        self.assertTrue(wolf["post_code"])
        # Flying-Neo's head is a mapping, its tail sprites of their own that
        # swing on a sine table its code builds in RAM.
        self.assertEqual(neo["parts"][10]["source"], "Boss_FlyingNeoAuxiliaryPartMappingA")
        self.assertEqual(
            [(part["kind"], part["size"]) for part in neo["parts"][11:14]],
            [("sprite", 10), ("sprite", 10), ("sprite", 10)],
        )
        self.assertIn("FF9900", neo["tables"])

    def test_big_bosses_have_their_plane_b_bodies(self) -> None:
        # Where MAME's plane B and scroll put them: Xi-Tiger's patterns
        # moved down $80 tiles by its setup, Shellshogun's as loaded.
        bodies = {boss["title"]: boss["body"] for boss in self.documents["graphics/bosses.json"]["bosses"]}
        self.assertEqual((bodies["Xi-Tiger"]["x"], bodies["Xi-Tiger"]["y"]), (-64, -32))
        self.assertEqual(bodies["Xi-Tiger"]["cells"][0][3:6], [0xE280, 0xE281, 0xE282])
        self.assertEqual((bodies["Shellshogun"]["x"], bodies["Shellshogun"]["y"]), (-60, -32))
        self.assertEqual(len(bodies["Shellshogun"]["cells"]), 8)
        self.assertEqual((bodies["Terobuster"]["x"], bodies["Terobuster"]["y"]), (-76, -50))
        # Joker's scrolls down line by line; at rest it sits as the others do.
        self.assertEqual((bodies["Joker"]["x"], bodies["Joker"]["y"]), (-64, -32))
        self.assertIsNone(bodies["Antroid"])
        # Wolf Garopa scrolls by fields of its own; Z-Leo's body is plane A's,
        # six rows its code queues as it rises, its tile indices moved $280.
        self.assertEqual((bodies["Wolf Garopa"]["x"], bodies["Wolf Garopa"]["y"]), (-48, -80))
        self.assertEqual((bodies["Z-Leo"]["plane"], bodies["Z-Leo"]["x"], bodies["Z-Leo"]["y"]), ("A", -96, -68))
        self.assertEqual(len(bodies["Z-Leo"]["cells"]), 24)
        self.assertEqual(bodies["Z-Leo"]["cells"][8][:2], [0x62C1, 0x62C2])
        # Flying-Neo's line and column scroll follow it; its set has no
        # palette but still loads the patterns. Its tiles carry the sky.
        self.assertEqual((bodies["Flying-Neo"]["x"], bodies["Flying-Neo"]["y"]), (-48, -24))
        self.assertTrue(bodies["Flying-Neo"]["scene"])
        # A boss shows facing as its setup builds its body.
        faces = {boss["title"]: boss.get("faces") for boss in self.documents["graphics/bosses.json"]["bosses"]}
        self.assertEqual((faces["Wolf Garopa"], faces["Shellshogun"], faces["Antroid"]), (0, 0x100, None))

    def test_chain_bosses_bring_the_code_that_places_their_records(self) -> None:
        from retrolarium_kit.games.alien_soldier import bosses, pose_code

        found = {boss["title"]: boss for boss in self.documents["graphics/bosses.json"]["bosses"]}
        for title in ("Jetsripper", "Gusthead", "Caterpillar", "Sunset Sting"):
            self.assertIn("position_field", found[title], title)
            self.assertTrue(found[title]["checked"], title)
        # Bosses whose records other code places come as plane bodies, their
        # records where they stand.
        self.assertEqual((found["Jampan"]["record_code"], found["Jampan"]["body"]["plane"]), ({}, "B"))
        # Jetsripper's chain hangs on its body ($40/$44); Sunset Sting's
        # record draws nothing, so its ring is shown around its middle.
        self.assertEqual((found["Jetsripper"]["position_field"], found["Jetsripper"]["centre"]), (0x40, [0, 0]))
        self.assertTrue(all(abs(value) <= 32 for value in found["Sunset Sting"]["centre"]))
        # Sunset Sting's segments dispatch on a state table; settled, they
        # orbit at their full radius ($80) around the core, each on the
        # mapping its angle picks.
        tree = workspace.tree(self.root, workspace.source_profile(self.root), "jp")
        self.assertEqual(len(pose_code.state_table(tree, "Boss_SunsetStingSegmentStates")), 7)
        sting = found["Sunset Sting"]
        self.assertTrue(any(step[0] == "switch" for step in sting["record_code"]["1F0"]))
        machine = pose_code.Machine({}, {"a5": bosses.SLOT})
        for at, data in sting["tables"].items():
            for offset, value in enumerate(bytes.fromhex(data)):
                machine.memory[int(at, 16) + offset] = value
        segment = bosses.SLOT + 2 * bosses.PART_SIZE
        self.assertEqual((machine.read(segment, 2), machine.read(segment + 0x44, 2)), (0x1F0, 0x80))
        machine.a[5] = segment
        pose_code.run(sting["record_code"]["1F0"], machine)
        named = {address: name for name, address in sting["symbols"].items()}
        self.assertTrue(named.get(machine.read(segment + 8, 4), "").startswith("Boss_SunsetStingSegmentMapping"))
        core = machine.read(bosses.SLOT + 0x10, 2), machine.read(bosses.SLOT + 0x14, 2)
        reach = machine.read(segment + 0x10, 2) - core[0], machine.read(segment + 0x14, 2) - core[1]
        self.assertGreater(abs(reach[0]) + abs(reach[1]), 16)

    def test_plane_bosses_bring_the_bodies_their_code_queues(self) -> None:
        # As Gens's states of the fights hold them: the name-table cells
        # the boss's code queues, and their corner from the boss as the
        # scroll shows it.
        found = {boss["title"]: boss for boss in self.documents["graphics/bosses.json"]["bosses"]}
        bodies = {
            title: (found[title]["body"]["plane"], found[title]["body"]["x"], found[title]["body"]["y"])
            for title in ("Missiray", "Destroyer-Mk2", "Jampan", "Bugmax")
        }
        self.assertEqual(
            bodies,
            {
                "Missiray": ("B", -160, -32),
                "Destroyer-Mk2": ("A", -64, -80),
                "Jampan": ("B", -36, -36),
                "Bugmax": ("B", -40, -40),
            },
        )
        missiray = found["Missiray"]["body"]
        self.assertEqual((len(missiray["cells"]), len(missiray["cells"][0])), (12, 40))
        # Its segments lift the plane's columns: the middle (its head) stays.
        self.assertEqual(missiray["columns"][8:12], [0, 0, 0, 0])
        self.assertEqual(set(missiray["columns"][:8]), {-16})
        destroyer = found["Destroyer-Mk2"]
        self.assertEqual((len(destroyer["body"]["cells"]), len(destroyer["body"]["cells"][0])), (20, 16))
        self.assertTrue(any(part["kind"] == "mapping" for part in destroyer["parts"]))
        # Epsilon 1 as it waits to fight: four bands of its body and the
        # wings its animated columns draw, across all of plane A, the wings
        # eight lines below the body under its column scroll.
        epsilon = found["Epsilon 1"]["body"]
        self.assertEqual((epsilon["plane"], epsilon["x"], epsilon["y"]), ("A", -256, -56))
        self.assertEqual((len(epsilon["cells"]), len(epsilon["cells"][0])), (16, 64))
        self.assertEqual((set(epsilon["columns"][12:20]), set(epsilon["columns"][:12])), ({0}, {8}))

    def test_the_caterpillar_stands_on_the_ship_its_stage_streams(self) -> None:
        from retrolarium_kit.games.alien_soldier import bosses, stages

        tree = workspace.tree(self.root, workspace.source_profile(self.root), "jp")
        # Stage 9 spawns the Caterpillar ($128), then streams plane B from
        # the secondary camera, the map read from row $700.
        stream = stages.secondary_streams(tree)[0x128]
        self.assertEqual(
            (stream["descriptor"], stream["camera_y"]), ("Stage9_CaterpillarShipColumnTransferDescriptor", 0xF900)
        )
        caterpillar = next(
            boss for boss in self.documents["graphics/bosses.json"]["bosses"] if boss["title"] == "Caterpillar"
        )
        body = caterpillar["body"]
        # Map X $210 to $99F, ten rows, all on line 3 over the sprites.
        self.assertEqual((body["plane"], len(body["cells"]), len(body["cells"][0])), ("B", 10, 242))
        self.assertEqual({word & 0xE000 for row in body["cells"] for word in row if word & 0x7FF}, {0xE000})
        # As in Gens's states of the fight: a segment stands at map X $58 -
        # $80 of the plane, 24 lines below the ship's top on its column.
        machine = bytearray(0x10000)
        for at, data in caterpillar["tables"].items():
            if int(at, 16) >= 0xFF0000:
                low = int(at, 16) - 0xFF0000
                machine[low : low + len(data) // 2] = bytes.fromhex(data)
        segment = bosses.SLOT + 9 * bosses.PART_SIZE - 0xFF0000
        word = lambda at: int.from_bytes(machine[at : at + 2], "big")
        x = word(segment + 0x10) - caterpillar["centre"][0]
        y = word(segment + 0x14) - caterpillar["centre"][1]
        self.assertEqual(x - body["x"], word(segment + 0x58) - 0x80 - 0x210)
        self.assertEqual(y - body["y"], 24)

    def test_victor_and_viblack_come_with_their_planes_and_own_sprites(self) -> None:
        from retrolarium_kit.games.alien_soldier import stages

        found = {boss["title"]: boss for boss in self.documents["graphics/bosses.json"]["bosses"]}
        # As in Gens's states of their fights: Victor 32 frames in, flying
        # in at $1C4 with its velocity; its core, eyes and ring are sprites
        # the records hold themselves.
        victor = found["Victor"]
        self.assertEqual((victor["body"]["plane"], victor["body"]["x"], victor["body"]["y"]), ("B", -40, -40))
        core = victor["parts"][0]
        self.assertEqual(
            (core["kind"], core["size"], core["x"], core["y"], core["attribute"]), ("sprite", 14, -16, -8, 0x632C)
        )
        records = bytes.fromhex(victor["tables"][f"{victor['slot']:06X}"])
        word = lambda index, field: int.from_bytes(records[index * 0x60 + field : index * 0x60 + field + 2], "big")
        self.assertEqual((word(0, 0x10), word(0, 0x14)), (452, 256))
        ring = [
            (
                (word(index, 0x10) - 452 + 0x8000) % 0x10000 - 0x8000,
                (word(index, 0x14) - 256 + 0x8000) % 0x10000 - 0x8000,
            )
            for index in range(4, 12)
        ]
        self.assertEqual(ring, [(-26, 31), (3, 40), (31, 25), (40, -4), (25, -32), (-4, -41), (-32, -26), (-41, 3)])
        # Viblack lifts its stage's blocks into plane B (stage 16 holds the
        # camera at $660 for it), shown level as it stands between rocks.
        tree = workspace.tree(self.root, workspace.source_profile(self.root), "jp")
        self.assertEqual(stages.spawning_scenes(tree)[0x2B8]["camera_x"], 0x660)
        viblack = found["Viblack"]
        body = viblack["body"]
        self.assertEqual(
            (body["plane"], body["x"], body["y"], len(body["cells"]), len(body["cells"][0])), ("B", -160, -24, 8, 40)
        )
        self.assertEqual(set(body["columns"]), {0})
        self.assertIn("Stage16TileArt0000", [load["source"] for load in viblack["loads"]])
        self.assertEqual(viblack["palettes"][-1], ["Boss_ViblackIntroPaletteCommand", 0])

    def test_love_penguin_waves_its_tentacles_as_it_comes_in(self) -> None:
        # As MAME runs the cut boss in Antroid's place, 141 frames in: its
        # face in plane B, 24 tentacle segments placed down their tree
        # (pointers flagged in their top byte), its message on screen.
        penguin = next(
            boss for boss in self.documents["graphics/bosses.json"]["bosses"] if boss["title"] == "Love Penguin"
        )
        body = penguin["body"]
        self.assertEqual(
            (body["plane"], body["x"], body["y"], len(body["cells"]), len(body["cells"][0])), ("B", -36, -36, 12, 12)
        )
        self.assertEqual(sum(1 for part in penguin["parts"] if part["kind"] == "mapping"), 25)
        self.assertEqual(penguin["parts"][1]["source"], "EntityType1C0_EarlyFormPartMapping")
        records = bytes.fromhex(penguin["tables"][f"{penguin['slot']:06X}"])
        word = lambda index, field: int.from_bytes(records[index * 0x60 + field : index * 0x60 + field + 2], "big")
        self.assertEqual((word(0, 4), word(0, 0x10), word(0, 0x14)), (6, 385, 271))
        self.assertEqual(
            {
                index: tuple(
                    (word(index, field) - word(0, field) + 0x8000) % 0x10000 - 0x8000 for field in (0x10, 0x14)
                )
                for index in (5, 10, 15, 20, 24)
            },
            {5: (88, 75), 10: (110, 35), 15: (115, -10), 20: (102, -53), 24: (-42, -83)},
        )

    def test_bosses_bring_what_their_setup_leaves_in_their_records(self) -> None:
        # Wolf Garopa's hind legs hang from anchors its builder turns back,
        # Shellshogun draws its arms over its belly and turns its sword with
        # its hand before its renderer, Flying-Neo's tail has waved a while,
        # and the debug viewer's body rides on its first part.
        from retrolarium_kit.games.alien_soldier import pose_code

        found = {boss["title"]: boss for boss in self.documents["graphics/bosses.json"]["bosses"]}

        def word(boss: dict, address: int) -> int:
            memory = {
                int(at, 16) + index: byte
                for at, data in boss["ram"].items()
                for index, byte in enumerate(bytes.fromhex(data))
            }
            return memory.get(address, 0) << 8 | memory.get(address + 1, 0)

        def angle(title: str, index: int) -> int:
            boss = found[title]
            return word(boss, boss["slot"] + index * 0x60 + 0x56)

        self.assertEqual([angle("Wolf Garopa", index) for index in range(1, 5)], [0x1F8, 0x1F8, 0x120, 0x120])
        shellshogun = found["Shellshogun"]
        slot = shellshogun["slot"]
        machine = pose_code.Machine({}, {"a5": slot})
        machine.write(slot + 6 * 0x60 + 0x56, 2, 0x210)
        pose_code.run(shellshogun["anchor_code"], machine)
        self.assertEqual((machine.read(slot + 0x23E, 2), machine.read(slot + 0x29C, 2)), (0xC860, 0x190))
        self.assertEqual(
            [shellshogun["parts"][index]["attribute"] for index in (1, 6, 8, 13)], [0x8B00, 0x8300, 0x300, 0x8300]
        )
        self.assertEqual(len({angle("Flying-Neo", index) for index in range(12, 20)}), 8)
        body = found["Lambda Bunny"]["body"]
        self.assertEqual(
            (body["command"], body["x"], body["y"], body["part"]), ("Debug_ValkirieViewerLeftTileTransfer", -39, -52, 1)
        )

    def test_shield_viper_lays_its_body_down_its_heads_path(self) -> None:
        from retrolarium_kit.games.alien_soldier import bosses, pose_code

        viper = next(
            boss for boss in self.documents["graphics/bosses.json"]["bosses"] if boss["title"] == "Shield Viper"
        )
        self.assertTrue(viper["checked"])
        self.assertEqual(
            (viper["chain"], viper["position_field"], viper["centre"]), ("Boss_ShieldViperUpdate", 0x10, [0, 0])
        )
        # The head and 24 body records drawn; the orbit shot waits hidden.
        self.assertEqual(sum(1 for part in viper["parts"] if part["kind"] == "mapping"), 25)
        # Run as the studio runs it, it is Gens's state of the fight 472
        # frames in: the head circling while its message is on screen, its
        # body on the path behind it.
        machine = pose_code.Machine({}, {"a5": bosses.SLOT})
        for at, data in viper["tables"].items():
            for offset, value in enumerate(bytes.fromhex(data)):
                machine.memory[int(at, 16) + offset] = value
        pose_code.run(viper["pose_code"], machine)
        for index in range(1, len(viper["parts"])):
            code = viper["record_code"].get(f"{machine.read(bosses.SLOT + index * bosses.PART_SIZE, 2):X}")
            if code:
                machine.a[5] = bosses.SLOT + index * bosses.PART_SIZE
                pose_code.run(code, machine)
        head = [machine.read(bosses.SLOT + field, 2) for field in (4, 0x10, 0x14, 0x56)]
        self.assertEqual(head, [8, 310, 242, 0xFD80])

        def place(index: int) -> tuple[int, int]:
            record = bosses.SLOT + index * bosses.PART_SIZE
            return tuple(
                (machine.read(record + field, 2) - machine.read(bosses.SLOT + field, 2) + 0x8000) % 0x10000 - 0x8000
                for field in (0x10, 0x14)
            )

        self.assertEqual(
            {index: place(index) for index in (1, 6, 12, 18, 24)},
            {1: (5, -27), 6: (64, -47), 12: (27, 34), 18: (18, -44), 24: (74, -16)},
        )

    def test_a_capture_knows_the_object_records_and_the_bosses(self) -> None:
        from retrolarium_kit.games.alien_soldier import capture

        tree = workspace.tree(self.root, workspace.source_profile(self.root), "jp")
        # Sixty $60-byte records from $FFC620, the end the walker compares
        # with; the bosses are the types their asset sets install.
        self.assertEqual(capture._pool(tree), (0xFFC620, 0xFFDCA0))
        types = capture.boss_types(tree)
        self.assertIn(0x30, types)
        self.assertIn(0x430, types)

    def test_pose_code_runs_as_the_game_runs_it(self) -> None:
        from retrolarium_kit.games.alien_soldier import bosses

        antroid = next(boss for boss in self.documents["graphics/bosses.json"]["bosses"] if boss["title"] == "Antroid")
        words = [value << 8 for value in antroid["neutral_pose"]]
        words[14] = 0x2000
        angles = bosses._run_angles(antroid, words)
        # The foot's angle is the sum of its leg's channels and the body's
        # (14, 13, 12 and 2), each byte doubled.
        expected = (
            2 * (0x20 + antroid["neutral_pose"][13] + antroid["neutral_pose"][12] + antroid["neutral_pose"][2])
        ) & 0x1FE
        self.assertEqual(angles[25] & 0x1FE, expected)
        swapped = bosses._run_angles(antroid, words, side=2)
        self.assertNotEqual(swapped[25] & 0x1FE, expected)
        # Back Stringer's arms take a mapping from a table by angle (an
        # eighth of a turn later), four per arm.
        from retrolarium_kit.games.alien_soldier import pose_code

        stringer = next(
            boss for boss in self.documents["graphics/bosses.json"]["bosses"] if boss["title"] == "Back Stringer"
        )
        machine = pose_code.Machine({}, {"a5": bosses.SLOT})
        for at, data in stringer["tables"].items():
            for offset, value in enumerate(bytes.fromhex(data)):
                machine.memory[int(at, 16) + offset] = value
        machine.write(bosses.SLOT + 0x60 + 0x56, 2, 0x40)
        pose_code.run(stringer["anchor_code"], machine)
        named = {address: name for name, address in stringer["symbols"].items()}
        self.assertEqual(
            [named.get(machine.read(bosses.SLOT + index * 0x60 + 8, 4)) for index in (1, 2)],
            ["Boss_BackStringerPartAngleMapping1", "Boss_BackStringerPartAngleMapping4"],
        )

    def test_poses_and_animations_write_back(self) -> None:
        from retrolarium_kit.games.alien_soldier import bosses

        document = self.documents["graphics/bosses.json"]
        label = next(boss["poses"] for boss in document["bosses"] if boss["title"] == "Antroid")
        rows = [{**row, "values": list(row["values"])} for row in document["poses"][label]]
        rows[6]["values"][0] = 0x55
        edited = {**document, "poses": {label: rows}}
        sequences = [dict(item) for item in document["sequences"]]
        idle = next(item for item in sequences if item["label"] == "Boss_AntroidIdlePoseCommands")
        idle["commands"] = [{**command, "frames": 20} for command in idle["commands"]]
        edited["sequences"] = sequences
        tree = workspace.tree(self.root, workspace.source_profile(self.root), "jp")
        bosses.apply(tree, edited)
        self.assertEqual(tree.data(label)[rows[6]["offset"]], 0x55)
        self.assertEqual(tree.data("Boss_AntroidIdlePoseCommands")[:2], bytes((20, 32)))
        again = bosses.export(tree)
        self.assertEqual(again["poses"][label][6]["values"][0], 0x55)
        # A step count of zero would divide by zero; an end and the offsets stay.
        for change in ({"steps": 0}, {"frames": 0x80}):
            broken = [dict(item) for item in document["sequences"]]
            target = next(item for item in broken if item["label"] == "Boss_AntroidIdlePoseCommands")
            target["commands"] = [{**target["commands"][0], **change}, *target["commands"][1:]]
            with self.assertRaises(bosses.BossError):
                bosses.apply(tree, {**document, "sequences": broken})
        broken = [{**item, "end": "hold"} if item["end"] == "loop" else item for item in document["sequences"]]
        with self.assertRaises(bosses.BossError):
            bosses.apply(tree, {**document, "sequences": broken})
        moved = [{**row, "offset": row["offset"] + 1} for row in document["poses"][label]]
        with self.assertRaises(bosses.BossError):
            bosses.apply(tree, {**document, "poses": {label: moved}})


if __name__ == "__main__":
    unittest.main()
