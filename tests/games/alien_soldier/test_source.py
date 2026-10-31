from __future__ import annotations

import copy
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from helpers import game_repository
from retrolarium_kit.core import game_names
from retrolarium_kit.games.alien_soldier import mappings, objects, workspace
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
        fixture_path = ALIEN_SOLDIER / "tests/fixtures/retrolarium.json"
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        if fixture.get("schema") != "alien-soldier.retrolarium-tests" or fixture.get("schema_version") != 1:
            raise AssertionError(f"{fixture_path} is not a supported test fixture")
        cls.cases = fixture["kit"]
        cls.documents = workspace.export(cls.root, "jp")

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.root, True)

    def fixture(self, key: str):
        """An independent reference fact kept by the game, with its value type."""

        def decode(value):
            if isinstance(value, list):
                return [decode(item) for item in value]
            if isinstance(value, dict):
                if set(value) == {"tuple"}:
                    return tuple(decode(item) for item in value["tuple"])
                if set(value) == {"set"}:
                    return {decode(item) for item in value["set"]}
                if set(value) == {"bytes"}:
                    return bytes.fromhex(value["bytes"])
                if set(value) == {"mapping"}:
                    return {decode(k): decode(v) for k, v in value["mapping"]}
                return {k: decode(v) for k, v in value.items()}
            return value

        return decode(self.cases[self._testMethodName.removeprefix("test_")]["values"][key])

    def test_unedited_documents_change_no_source_file(self) -> None:
        documents = {module.DOCUMENT: self.documents[module.DOCUMENT] for module in workspace.DOCUMENTS}
        self.assertEqual(workspace.apply(self.root, "jp", documents), [])

    def test_a_project_saved_with_maps_as_mappings_still_applies(self) -> None:
        # Kits before this one read the maps an asset list loads as sprite
        # mappings; a project saved then keeps those blocks.
        document = dict(self.documents["graphics/mappings.json"])
        document["blocks"] = [
            {"label": self.fixture("source_symbol"), "kind": "static", "mappings": []},
            *document["blocks"],
        ]
        documents = {module.DOCUMENT: self.documents[module.DOCUMENT] for module in workspace.DOCUMENTS}
        documents["graphics/mappings.json"] = document
        self.assertEqual(workspace.apply(self.root, "jp", documents), [])

    def test_a_dynamic_piece_takes_art_of_its_length(self) -> None:
        tree = workspace.tree(self.root, workspace.source_profile(self.root), "jp")
        document = copy.deepcopy(self.documents["graphics/mappings.json"])
        dynamic = [block for block in document["blocks"] if block["kind"] == "dynamic"]
        pieces = [piece for block in dynamic for mapping in block["mappings"] for piece in mapping["pieces"]]
        block = dynamic[0]
        piece = block["mappings"][0]["pieces"][0]
        length = len(tree.data(piece["art"]))
        other = next(
            item["art"] for item in pieces if item["art"] != piece["art"] and len(tree.data(item["art"])) == length
        )
        longer = next(item["art"] for item in pieces if len(tree.data(item["art"])) != length)
        piece["art"] = longer
        with self.assertRaises(mappings.MappingError):
            mappings.apply(tree, document)
        piece["art"] = other
        mappings.apply(tree, document)
        self.assertEqual(tree.block(block["label"]).operands[1].text.split("+")[0], other)

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
        song = frames(self.fixture("source_symbol"))
        self.assertEqual(
            {w.value for f in range(19, 23) for w in song[f] if (w.port, w.address) == (1, 0xB5)},
            self.fixture("expected_w_value_for_f_in_range_19_23_for_w_in_song_f_if_w_port_w_address_1_181"),
        )
        # Pan mode 4 runs past both jump tables into the restart: FM1 of
        # The song $83 writes its pan every frame of a note.
        song = frames(self.fixture("source_symbol_2"))
        self.assertTrue(all(any((w.port, w.address) == (0, 0xB4) for w in song[f]) for f in range(55, 61)))
        # $E9 sets AM over the effect's first voice bytes ($31 for FM5's
        # third operator).
        effect = frames(self.fixture("source_symbol_3"))
        self.assertIn(
            self.fixture("expected_w_port_w_address_w_value_for_w_in_effect_1"),
            [(w.port, w.address, w.value) for w in effect[1]],
        )
        # An effect's channels run in the driver's record order, FM4 first.
        effect = frames(self.fixture("source_symbol_4"))
        voices = [w.address for w in effect[0] if w.port == 1 and w.address in (0xB0, 0xB1)]
        self.assertEqual(voices, self.fixture("expected_voices"))
        # The writes of a frame take time: a key-on comes after its key-off.
        writes, _ = sequencer.render(self.fixture("source_symbol_5"), seconds=10)
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
        self.assertEqual(sets[self.fixture("sets_key")], self.fixture("expected_sets_stage1to4mapping_data6000"))
        self.assertEqual(sets[self.fixture("sets_key_2")], self.fixture("expected_sets_stage8and9mapping_data6000"))
        scenes = exported["scenes"]
        records = {item["label"]: item for item in self.documents["levels/levels.json"]["records"]}
        for index in (0, 1, 2, 3):
            area = scenes[index]["area"]
            # Each stage starts at its camera and ends a screen past where it
            # pins the camera for its boss.
            self.assertEqual(area["x"], records[scenes[index]["record"]]["camera_x"])
            self.assertEqual(area["height"], self.fixture("expected_scenes_14_area_height"))
        self.assertEqual(scenes[0]["area"]["x"] + scenes[0]["area"]["width"], 0x6E8 + 320)
        # The camera-only encounter's handlers move the camera but never draw the map: the
        # picture stays where the entry drew it, at the record's X.
        self.assertEqual((scenes[8]["area"]["x"], scenes[8]["area"]["width"]), (0x800, 0xA0 + 320))
        # The radial-chain boss's stage climbs before its boss.
        self.assertGreater(scenes[14]["area"]["height"], self.fixture("expected_scenes_14_area_height"))
        self.assertEqual(len(scenes[14]["area"]["parts"]), self.fixture("expected_len_scenes_14_area_parts"))
        # After the stage-backed boss the fight moves to the extended-reach boss's web, apart.
        (web,) = scenes[15]["arenas"]
        # The 256 lines streamed down from row 0, coloured by the boss
        # The stage-backed boss brings in.
        self.assertEqual(
            {key: web[key] for key in ("x", "y", "width", "height")},
            self.fixture("expected_key_web_key_for_key_in_x_y_width_height"),
        )
        # The ascent encounter fights in the empty space the asteroid field
        # climbs to (camera Y $F600); the moons and surface streamed from row
        # $B00 once it is gone are the path-following boss's (stage 22 restarts there).
        (space,) = scenes[20]["arenas"]
        self.assertEqual(
            (space["x"], space["y"], space["height"]), self.fixture("expected_space_x_space_y_space_height")
        )
        (backdrop,) = scenes[21]["arenas"]
        self.assertEqual(
            (backdrop["x"], backdrop["y"], backdrop["height"]),
            self.fixture("expected_backdrop_x_backdrop_y_backdrop_height"),
        )
        self.assertEqual(len(scenes[7]["arenas"]), self.fixture("expected_len_scenes_7_arenas"))
        self.assertEqual(
            (web["list"], web["palettes"]),
            self.fixture("expected_web_list_web_palettes"),
        )
        # Epsilon 1's parallax works the camera Y out as $28 - $18: the rows
        # sit 16 pixels lower, the floor under the player.
        self.assertEqual(scenes[16]["area"]["y"], self.fixture("expected_scenes_16_area_y"))
        # Its floor is a raster split: rows of blocks the initializer writes
        # into plane B's other name table, shown from line 200 down.
        self.assertEqual(
            scenes[16]["floor"],
            {"ram": 0xFFFF4000, "blocks": list(range(0x29, 0x33)), "per_row": 10, "line": 200},
        )
        self.assertNotIn(self.fixture("expected_scenes_15"), scenes[15])
        # The rising boss moves his fight to (0, $100) and rises to $240, with his own
        # maps; that screen is shown above the stage.
        (fight,) = scenes[24]["arenas"]
        self.assertEqual((fight["x"], fight["y"], fight["height"], fight["inline"]), (0, -0x240, 0x240 + 224, True))
        # It is as wide as his core room (the camera follows him across it)
        # and shown over the room the corridor ends in, where the stage's
        # plane B reads the same rows.
        self.assertEqual((fight["width"], fight["shown"]), self.fixture("expected_fight_width_fight_shown"))
        self.assertIn({"x": 0x400, "y": -0x40, "width": 384, "height": fight["height"]}, scenes[24]["area"]["parts"])
        # His rise streams plane B with plane A's patterns ($4000).
        self.assertEqual(
            (fight["planes"][0], fight["planes"][1]["patterns"]),
            self.fixture("expected_fight_planes_0_fight_planes_1_patterns"),
        )
        # ... The patterns his battle entry loads at $4020, in his colours.
        self.assertEqual(fight["lists"], self.fixture("expected_fight_lists"))
        self.assertEqual(fight["palettes"], self.fixture("expected_fight_palettes"))
        # The first level's stages scroll plane B at a quarter speed; the
        # train's rows run on their own.
        self.assertEqual(
            [scenes[index].get("plane_b_quarter") for index in (0, 3, 7)],
            self.fixture("expected_scenes_index_get_plane_b_quarter_for_index_in_0_3_7"),
        )
        # The train streams plane B (the train) with plane A's camera X and
        # leaves plane A as the entry drew it at $C00.
        self.assertTrue(scenes[7]["plane_b_follows"])
        self.assertEqual(scenes[7]["planes"][0]["fixed_x"], self.fixture("expected_scenes_7_planes_0_fixed_x"))
        # The airborne boss widens the pinned camera by $A0 and swings it from Y $60
        # to $80, over plane B streamed $910 rows lower.
        (airborne,) = scenes[7]["arenas"]
        self.assertEqual(
            (airborne["x"], airborne["y"], airborne["width"], airborne["height"]),
            (0xE60, -0x80, 0xA0 + 320, 0x20 + 224),
        )
        self.assertEqual(airborne["planes"][1]["y_offset"], self.fixture("expected_airborne_planes_1_y_offset"))
        # The camera-only encounter's screen is apart: the art its handlers load turns the
        # train into clouds, and plane B is blanked.
        self.assertTrue(scenes[8]["area"]["apart"])
        self.assertIsNone(scenes[8]["planes"][1])
        self.assertEqual(
            [load["list"] for load in scenes[8]["later"]],
            self.fixture("expected_load_list_for_load_in_scenes_8_later"),
        )
        # Stage 7's first state loads the counter alien's art at once.
        self.assertIn(self.fixture("expected_scenes_6_lists"), scenes[6]["lists"])
        # Stage 16 pins its camera between two X bounds: a column.
        column = scenes[15]["area"]
        self.assertEqual((column["x"], column["width"]), (0x620, 0x6A0 + 320 - 0x620))
        self.assertGreater(column["height"], self.fixture("expected_scenes_14_area_height"))

    def test_stage_initializers_rewrite_their_maps(self) -> None:
        scenes = self.documents["levels/stages.json"]["scenes"]
        snow = {"routine": "update-indices", "ram": "0xFFFF2000", "groups": 0x80, "add": 0, "tiles": 0x150}
        # Stage 19 reaches the call through a branch into stage 18's code.
        self.assertEqual(scenes[17]["transforms"], [snow])
        self.assertEqual(scenes[18]["transforms"], [snow])
        self.assertEqual(
            scenes[19]["transforms"],
            self.fixture("expected_scenes_19_transforms"),
        )
        self.assertEqual(scenes[0]["transforms"], self.fixture("expected_scenes_0_transforms"))

    def test_a_stage_loads_more_maps_as_its_camera_moves(self) -> None:
        scenes = self.documents["levels/stages.json"]["scenes"]
        # Stage 19 starts on stage 18's snow and swaps in the factory at $D80.
        (factory,) = scenes[18]["later"]
        self.assertEqual((factory["camera_x"], factory["list"]), self.fixture("expected_factory_camera_x_factory_list"))
        self.assertIn(
            self.fixture("expected_write_source_for_write_in_factory_map_writes"),
            [write["source"] for write in factory["map_writes"]],
        )
        self.assertEqual(scenes[17]["later"], self.fixture("expected_scenes_17_later"))

    def test_a_loader_plays_the_shared_tail_it_falls_into(self) -> None:
        scenes = self.documents["levels/stages.json"]["scenes"]
        for index in (20, 21, 22):
            self.assertIn(self.fixture("expected_scenes_index_lists"), scenes[index]["lists"])
            self.assertIsNotNone(scenes[index]["planes"][0])

    def test_a_staging_buffer_is_no_map(self) -> None:
        coarse = [item for item in self.documents["levels/maps.json"]["payloads"] if item["role"] == "coarse"]
        self.assertTrue(coarse)
        self.assertTrue(all(len(item["values"]) <= 32 * 32 for item in coarse))
        tiles = {item["label"] for item in self.documents["graphics/compressed_tiles.json"]["payloads"]}
        self.assertLessEqual(self.fixture("expected_tiles"), tiles)

    def test_objects_group_every_mapping_block_once(self) -> None:
        exported = self.documents["graphics/objects.json"]
        grouped = [label for group in exported["groups"] for label in group["blocks"]]
        blocks = [block["label"] for block in self.documents["graphics/mappings.json"]["blocks"]]
        self.assertEqual(sorted(grouped), sorted(blocks))
        self.assertLessEqual(set(exported["contexts"]), set(blocks))

    def test_bosses_draw_with_their_asset_sets(self) -> None:
        exported = self.documents["graphics/objects.json"]
        contexts = exported["contexts"]
        # Joker, as the tool-assisted run draws him: his art at $6000 (tile
        # $300), in his palette command's colours.
        anchored = contexts[self.fixture("contexts_key")]
        self.assertFalse(anchored["borrowed"])
        self.assertEqual(anchored["attribute"] & 0x7FF, self.fixture("expected_anchored_attribute_2047"))
        self.assertIn(self.fixture("expected_anchored_loads"), anchored["loads"])
        self.assertEqual(anchored["palettes"][-1], self.fixture("expected_anchored_palettes_1"))
        # A boss's file play never drew takes its context from the source:
        # The art its asset set loads, ending in the set's palette command.
        derived = [context for context in contexts.values() if context.get("derived")]
        self.assertTrue(derived)
        self.assertTrue(all(context["loads"] and context["palettes"] for context in derived))
        self.assertIn(
            self.fixture("expected_group_title_for_group_in_exported_groups"),
            [group["title"] for group in exported["groups"]],
        )
        # Blocks drawn from a boss's art take its colours, whatever play
        # matched (the multi-form encounter).
        self.assertEqual(
            contexts[self.fixture("contexts_key_3")]["palettes"][-1],
            self.fixture("expected_contexts_multi_form_rotation_sprite_frame29_palettes_1"),
        )
        # A borrowed block a stage spawns draws with that stage's art: stage
        # 7's counter alien from the art its first state loads.
        alien = [load["source"] for load in contexts[self.fixture("contexts_key_4")]["loads"]]
        self.assertIn(self.fixture("expected_alien"), alien)
        # A screen's code draws the ending planet's burst in that screen's art.
        burst = contexts[self.fixture("contexts_key_2")]
        self.assertIn(
            self.fixture("expected_load_source_for_load_in_burst_loads"), [load["source"] for load in burst["loads"]]
        )
        # Maps an asset list loads are no sprite mappings.
        blocks = {block["label"] for block in self.documents["graphics/mappings.json"]["blocks"]}
        self.assertNotIn(self.fixture("expected_blocks"), blocks)
        # A boss's art shows in its palette command's line.
        art = {item["label"]: item for item in self.documents["graphics/art.json"]["payloads"]}
        self.assertEqual(
            (art[self.fixture("art_key")]["colours"], art[self.fixture("art_key")]["line"]),
            self.fixture("reference"),
        )

    def test_the_recorded_object_art_fits_the_source(self) -> None:
        if not (self.root / objects.ART).is_file():
            self.skipTest("the repository records no object art")
        contexts = self.documents["graphics/objects.json"]["contexts"]
        seen = [label for label, context in contexts.items() if not context["borrowed"]]
        self.assertGreater(len(seen), self.fixture("expected_len_seen"))
        # The shared explosion frames draw from the art every stage loads at $9000.
        frame = contexts[self.fixture("contexts_key")]
        self.assertEqual(frame["attribute"] & 0x7FF, self.fixture("expected_frame_attribute_2047"))
        self.assertIn(self.fixture("expected_frame_loads"), frame["loads"])
        # Most placements of the stages' spawn lists record what they draw.
        spawns = self.documents["graphics/objects.json"]["spawns"]
        self.assertGreater(len(spawns), self.fixture("expected_len_spawns"))
        first = next(
            item for item in spawns if item["list"] == self.fixture("item_list_reference") and item["index"] == 0
        )
        self.assertEqual((first["type"], first["parameter"]), self.fixture("expected_first_type_first_parameter"))
        # The cut boss's second form hangs the game before it draws: an outline.
        self.assertNotIn(self.fixture("expected_contexts"), contexts)
        # The SEGA logo's colours are a table its code copies into line 2.
        sega = contexts[self.fixture("contexts_key_2")]
        self.assertEqual(
            [(table["label"], table["line"]) for table in sega["tables"]],
            self.fixture("expected_table_label_table_line_for_table_in_sega_tables"),
        )
        self.assertEqual(sega["tables"][0]["colours"][:3], self.fixture("expected_sega_tables_0_colours_3"))
        art = {item["label"]: item for item in self.documents["graphics/art.json"]["payloads"]}
        self.assertEqual(art[self.fixture("art_key")]["line"], self.fixture("expected_art_sega_screen_tile_art_line"))
        self.assertEqual(art[self.fixture("art_key")]["colours"], {"colours": sega["tables"][0]["colours"]})

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
        self.assertEqual(
            payloads[self.fixture("payloads_key")]["group"],
            self.fixture("expected_payloads_player_phoenix_dash_attack_sprite_art_piece00_group"),
        )
        self.assertEqual(
            payloads[self.fixture("payloads_key_2")]["group"],
            self.fixture("expected_payloads_credits_scene17mapping_data_group"),
        )
        level = [item for item in payloads.values() if item["group"] == "level"]
        self.assertTrue(level)
        self.assertTrue(all("list" in item["colours"] for item in level))

    def test_the_credits_scenes_come_from_their_table(self) -> None:
        scenes = [item for item in self.documents["screens/screens.json"]["screens"] if item.get("kind") == "credits"]
        self.assertEqual(len(scenes), self.fixture("expected_len_scenes"))
        for scene in scenes:
            table = scene["name_table"]
            self.assertIn(table["vram"], self.fixture("expected_table_vram"))
            self.assertEqual(len(scene["palette"]["colours"]), self.fixture("expected_len_scene_palette_colours"))
            self.assertIn(table["source"], [load["source"] for load in scene["vram"]])

    def test_jointed_bosses_read_as_skeletons_with_poses_and_animations(self) -> None:
        document = self.documents["graphics/bosses.json"]
        titles = [boss["title"] for boss in document["bosses"]]
        for name in self.fixture("name_references"):
            self.assertIn(name, titles)
        # The multi-form encounter's forms read each from its own file and what it
        # calls; Sirene's tentacles follow a history of the frames before.
        checked = {boss["title"] for boss in document["bosses"] if boss["checked"]}
        for name in self.fixture("name_references_2"):
            self.assertIn(name, checked)
        # The byte-script boss's animations are written as bytes; the directly driven boss's code
        # alone turns it (fields its setup gives, four-frame tables).
        self.assertIn(self.fixture("expected_checked"), checked)
        direct = next(boss for boss in document["bosses"] if boss["title"] == self.fixture("boss_title_reference"))
        self.assertTrue(direct["checked"])
        self.assertEqual(
            (direct["poses"], direct["sequences"], direct["channels"]),
            self.fixture("expected_direct_poses_direct_sequences_direct_channels"),
        )
        self.assertTrue(all(len(part["frames"]) == 4 for part in direct["parts"] if part["kind"] == "rotation"))
        trailing = next(boss for boss in document["bosses"] if boss["title"] == self.fixture("boss_title_reference_2"))
        self.assertGreater(trailing["settle"], self.fixture("expected_trailing_settle"))
        jointed = next(boss for boss in document["bosses"] if boss["title"] == self.fixture("boss_title_reference_3"))
        self.assertEqual(
            (len(jointed["parts"]), jointed["channels"], jointed["timing"]),
            self.fixture("expected_len_jointed_parts_jointed_channels_jointed_timing"),
        )
        self.assertEqual(jointed["asset_set"], self.fixture("expected_jointed_asset_set"))
        self.assertTrue(jointed["checked"])
        # The poses are the table offsets its animations name: all rows but one.
        self.assertEqual(
            [row["offset"] for row in document["poses"][jointed["poses"]]][:9],
            self.fixture("expected_row_offset_for_row_in_document_poses_jointed_poses_9"),
        )
        self.assertEqual(len(jointed["sequences"]), self.fixture("expected_len_jointed_sequences"))
        self.assertIsNone(jointed["parts"][0]["parent"])
        # A foot hangs from the leg chain and turns with the last leg channel;
        # The side the pose code tests is field 6.
        self.assertEqual(jointed["parts"][25]["parent"], self.fixture("expected_jointed_parts_25_parent"))
        self.assertEqual(jointed["joints"][25], self.fixture("expected_jointed_joints_25"))
        self.assertEqual(jointed["side_field"], self.fixture("expected_jointed_side_field"))
        self.assertEqual(jointed["parts"][14]["angle"], self.fixture("expected_jointed_parts_14_angle"))
        self.assertEqual(jointed["facing"]["256"]["set"], self.fixture("expected_jointed_facing_256_set"))
        self.assertEqual(len(document["cosine"]), self.fixture("expected_len_document_cosine"))
        sequences = {item["label"]: item for item in document["sequences"]}
        self.assertEqual(
            sequences[self.fixture("sequences_key_2")]["commands"],
            self.fixture("expected_sequences_boss_jointed_idle_poses_commands"),
        )
        retry = sequences[self.fixture("sequences_key")]
        self.assertEqual((retry["end"], retry["next"]), self.fixture("expected_retry_end_retry_next"))
        self.assertIn(
            self.fixture("expected_sequences_boss_jointed_jump_attack_poses_commands"),
            sequences[self.fixture("sequences_key_3")]["commands"],
        )
        # The additive-timing boss adds a signed count to its steps; Joker's hands and feet
        # are anchors its renderer's code places.
        additive = next(boss for boss in document["bosses"] if boss["title"] == self.fixture("boss_title_reference_4"))
        self.assertEqual(additive["timing"], self.fixture("expected_additive_timing"))
        anchored = next(boss for boss in document["bosses"] if boss["title"] == self.fixture("boss_title_reference_5"))
        self.assertTrue(anchored["anchor_code"])
        self.assertEqual(
            [index for index, part in enumerate(anchored["parts"]) if part.get("anchor")],
            self.fixture("expected_index_for_index_part_in_enumerate_anchored_parts_if_part_get_anchor"),
        )
        # The airborne boss's renderer walks from its third slot, eight parts; its
        # code puts the second beside the boss.
        airborne = next(boss for boss in document["bosses"] if boss["title"] == self.fixture("boss_title_reference_6"))
        self.assertEqual(airborne["traversed"], self.fixture("expected_airborne_traversed"))
        self.assertTrue(airborne["anchor_code"] and airborne["checked"])
        # The extended-reach boss's arms hang in threes from the points half a bone
        # past the part above; its arms pick their mappings from a table.
        extended_reach = next(
            boss for boss in document["bosses"] if boss["title"] == self.fixture("boss_title_reference_7")
        )
        self.assertEqual(
            [(part.get("reach", False), part.get("from_reach", False)) for part in extended_reach["parts"][3:6]],
            self.fixture("expected_part_get_reach_false_part_get_from_reach_false_for_part_in_extended_rea"),
        )
        table = extended_reach["symbols"][self.fixture("extended_reach_symbols_key")]
        self.assertEqual(
            extended_reach["tables"][f"{table:06X}"][:8],
            f"{extended_reach['symbols'][self.fixture('extended_reach_symbols_key_2')]:08x}",
        )
        self.assertTrue(extended_reach["anchor_code"] and extended_reach["checked"])
        # Each the multi-form encounter form draws in its own colours, its set's command;
        # The alternate Valkirie steps twelve channels of its own.
        forms = {boss["title"]: boss for boss in document["bosses"]}
        self.assertEqual(
            forms[self.fixture("forms_key_3")]["palettes"][-1], self.fixture("expected_forms_medusa_palettes_1")
        )
        self.assertEqual(
            forms[self.fixture("forms_key_4")]["palettes"][-1], self.fixture("expected_forms_sylpheed_palettes_1")
        )
        self.assertEqual(
            forms[self.fixture("forms_key_2")]["channels"], self.fixture("expected_forms_valkirie_alternate_channels")
        )
        # A debug viewer builds a boss the game cut, in the $3EC set's art:
        # one animation between two poses. No text names it: the game
        # repository's name for its entity type does.
        from retrolarium_kit.core.game_names import GameNames

        viewer = next(boss for boss in document["bosses"] if boss["id"] == self.fixture("boss_id_reference"))
        self.assertEqual(
            (viewer["asset_set"], len(document["poses"][viewer["poses"]])),
            self.fixture("expected_viewer_asset_set_len_document_poses_viewer_poses"),
        )
        self.assertEqual(viewer["title"], GameNames.load(ALIEN_SOLDIER).name("bosses", 0x3EC))
        # The mounted boss's horse's head and neck and its rider's head and arm are
        # records its setup fills by hand, placed by the code after its
        # renderer by their pixel places.
        wolf = forms[self.fixture("forms_key")]
        self.assertEqual(
            [(index, part["source"]) for index, part in enumerate(wolf["parts"]) if part["kind"] == "mapping"][-4:],
            self.fixture("expected_index_part_source_for_index_part_in_enumerate_wolf_parts_if_part_kind_m"),
        )
        self.assertTrue(wolf["post_code"])
        # The airborne boss's head is a mapping, its tail sprites of their own that
        # swing on a sine table its code builds in RAM.
        self.assertEqual(airborne["parts"][10]["source"], self.fixture("expected_airborne_parts_10_source"))
        self.assertEqual(
            [(part["kind"], part["size"]) for part in airborne["parts"][11:14]],
            self.fixture("expected_part_kind_part_size_for_part_in_airborne_parts_11_14"),
        )
        self.assertIn(self.fixture("expected_airborne_tables"), airborne["tables"])

    def test_big_bosses_have_their_plane_b_bodies(self) -> None:
        # Where MAME's plane B and scroll put them: the camera-only encounter's patterns
        # moved down $80 tiles by its setup, the armoured boss's as loaded.
        bodies = {boss["title"]: boss["body"] for boss in self.documents["graphics/bosses.json"]["bosses"]}
        self.assertEqual(
            (bodies[self.fixture("bodies_key_3")]["x"], bodies[self.fixture("bodies_key_3")]["y"]),
            self.fixture("expected_bodies_camera_only_x_bodies_camera_only_y"),
        )
        self.assertEqual(
            bodies[self.fixture("bodies_key_3")]["cells"][0][3:6],
            self.fixture("expected_bodies_camera_only_cells_0_3_6"),
        )
        self.assertEqual(
            (bodies[self.fixture("faces_key_2")]["x"], bodies[self.fixture("faces_key_2")]["y"]),
            self.fixture("expected_bodies_armoured_x_bodies_armoured_y"),
        )
        self.assertEqual(
            len(bodies[self.fixture("faces_key_2")]["cells"]), self.fixture("expected_len_bodies_armoured_cells")
        )
        self.assertEqual(
            (bodies[self.fixture("bodies_key_4")]["x"], bodies[self.fixture("bodies_key_4")]["y"]),
            self.fixture("expected_bodies_additive_x_bodies_additive_y"),
        )
        # Joker's scrolls down line by line; at rest it sits as the others do.
        self.assertEqual(
            (bodies[self.fixture("bodies_key_5")]["x"], bodies[self.fixture("bodies_key_5")]["y"]),
            self.fixture("expected_bodies_camera_only_x_bodies_camera_only_y"),
        )
        self.assertIsNone(bodies[self.fixture("bodies_key")])
        # The mounted boss scrolls by fields of its own; the rising boss's body is plane A's,
        # six rows its code queues as it rises, its tile indices moved $280.
        self.assertEqual(
            (bodies[self.fixture("faces_key")]["x"], bodies[self.fixture("faces_key")]["y"]),
            self.fixture("expected_bodies_mounted_x_bodies_mounted_y"),
        )
        self.assertEqual(
            (
                bodies[self.fixture("bodies_key_6")]["plane"],
                bodies[self.fixture("bodies_key_6")]["x"],
                bodies[self.fixture("bodies_key_6")]["y"],
            ),
            self.fixture("expected_bodies_rising_plane_bodies_rising_x_bodies_rising_y"),
        )
        self.assertEqual(
            len(bodies[self.fixture("bodies_key_6")]["cells"]), self.fixture("expected_len_bodies_rising_cells")
        )
        self.assertEqual(
            bodies[self.fixture("bodies_key_6")]["cells"][8][:2], self.fixture("expected_bodies_rising_cells_8_2")
        )
        # The airborne boss's line and column scroll follow it; its set has no
        # palette but still loads the patterns. Its tiles carry the sky.
        self.assertEqual(
            (bodies[self.fixture("bodies_key_2")]["x"], bodies[self.fixture("bodies_key_2")]["y"]),
            self.fixture("expected_bodies_flying_airborne_x_bodies_flying_airborne_y"),
        )
        self.assertTrue(bodies[self.fixture("bodies_key_2")]["scene"])
        # A boss shows facing as its setup builds its body.
        faces = {boss["title"]: boss.get("faces") for boss in self.documents["graphics/bosses.json"]["bosses"]}
        self.assertEqual(
            (faces[self.fixture("faces_key")], faces[self.fixture("faces_key_2")], faces[self.fixture("bodies_key")]),
            self.fixture("expected_faces_mounted_faces_armoured_faces_jointed"),
        )

    def test_chain_bosses_bring_the_code_that_places_their_records(self) -> None:
        from retrolarium_kit.games.alien_soldier import bosses, pose_code

        found = {boss["title"]: boss for boss in self.documents["graphics/bosses.json"]["bosses"]}
        for title in self.fixture("title_references"):
            self.assertIn(self.fixture("expected_found_title"), found[title], title)
            self.assertTrue(found[title]["checked"], title)
        # Bosses whose records other code places come as plane bodies, their
        # records where they stand.
        self.assertEqual(
            (found[self.fixture("found_key_2")]["record_code"], found[self.fixture("found_key_2")]["body"]["plane"]),
            self.fixture("reference"),
        )
        # The linked-chain boss's chain hangs on its body ($40/$44); the radial-chain boss's
        # record draws nothing, so its ring is shown around its middle.
        self.assertEqual(
            (found[self.fixture("found_key_3")]["position_field"], found[self.fixture("found_key_3")]["centre"]),
            self.fixture("reference_2"),
        )
        self.assertTrue(all(abs(value) <= 32 for value in found[self.fixture("found_key")]["centre"]))
        # The radial-chain boss's segments dispatch on a state table; settled, they
        # orbit at their full radius ($80) around the core, each on the
        # mapping its angle picks.
        tree = workspace.tree(self.root, workspace.source_profile(self.root), "jp")
        self.assertEqual(
            len(pose_code.state_table(tree, self.fixture("source_symbol_2"))),
            self.fixture("expected_len_pose_code_state_table_tree_boss_radial_segment_states"),
        )
        sting = found[self.fixture("found_key")]
        self.assertTrue(any(step[0] == "switch" for step in sting["record_code"]["1F0"]))
        machine = pose_code.Machine({}, {"a5": bosses.SLOT})
        for at, data in sting["tables"].items():
            for offset, value in enumerate(bytes.fromhex(data)):
                machine.memory[int(at, 16) + offset] = value
        segment = bosses.SLOT + 2 * bosses.PART_SIZE
        self.assertEqual(
            (machine.read(segment, 2), machine.read(segment + 0x44, 2)),
            self.fixture("expected_machine_read_segment_2_machine_read_segment_68_2"),
        )
        machine.a[5] = segment
        pose_code.run(sting["record_code"]["1F0"], machine)
        named = {address: name for name, address in sting["symbols"].items()}
        self.assertTrue(named.get(machine.read(segment + 8, 4), "").startswith(self.fixture("source_symbol")))
        core = machine.read(bosses.SLOT + 0x10, 2), machine.read(bosses.SLOT + 0x14, 2)
        reach = machine.read(segment + 0x10, 2) - core[0], machine.read(segment + 0x14, 2) - core[1]
        self.assertGreater(abs(reach[0]) + abs(reach[1]), self.fixture("expected_abs_reach_0_abs_reach_1"))
        # Its stem and head are plane B's cells its code queues, as the
        # tool-assisted run shows them: centred on it, its line scroll being
        # its X, at the top of the screen.
        body = sting["body"]
        self.assertEqual(
            (body["plane"], len(body["cells"]), len(body["cells"][0])),
            self.fixture("expected_body_plane_len_body_cells_len_body_cells_0"),
        )
        self.assertEqual(body["x"] + 4 * len(body["cells"][0]), -sting["centre"][0])
        # Its visible core, which its intro brings down to the floor under
        # it, stands there, not at the origin its setup leaves it at.
        core_record = bosses.SLOT + bosses.PART_SIZE
        self.assertEqual(machine.read(core_record + 0x10, 2), core[0])
        self.assertEqual(machine.read(core_record + 0x14, 2), self.fixture("expected_machine_read_core_record_20_2"))

    def test_plane_bosses_bring_the_bodies_their_code_queues(self) -> None:
        # As Gens's states of the fights hold them: the name-table cells
        # The boss's code queues, and their corner from the boss as the
        # scroll shows it.
        found = {boss["title"]: boss for boss in self.documents["graphics/bosses.json"]["bosses"]}
        bodies = {
            title: (found[title]["body"]["plane"], found[title]["body"]["x"], found[title]["body"]["y"])
            for title in self.fixture("title_references")
        }
        self.assertEqual(
            bodies,
            self.fixture("expected_bodies"),
        )
        column_body = found[self.fixture("found_key_2")]["body"]
        self.assertEqual(
            (len(column_body["cells"]), len(column_body["cells"][0])),
            self.fixture("expected_len_column_body_cells_len_column_body_cells_0"),
        )
        # Its segments lift the plane's columns: the middle (its head) stays.
        self.assertEqual(column_body["columns"][8:12], self.fixture("expected_column_body_columns_8_12"))
        self.assertEqual(set(column_body["columns"][:8]), self.fixture("expected_set_column_body_columns_8"))
        destroyer = found[self.fixture("found_key")]
        self.assertEqual(
            (len(destroyer["body"]["cells"]), len(destroyer["body"]["cells"][0])),
            self.fixture("expected_len_destroyer_body_cells_len_destroyer_body_cells_0"),
        )
        self.assertTrue(any(part["kind"] == "mapping" for part in destroyer["parts"]))
        # Epsilon 1 as it waits to fight: four bands of its body and the
        # wings its animated columns draw, across all of plane A, the wings
        # eight lines below the body under its column scroll.
        raster_body = found[self.fixture("found_key_3")]["body"]
        self.assertEqual(
            (raster_body["plane"], raster_body["x"], raster_body["y"]),
            self.fixture("expected_raster_body_plane_raster_body_x_raster_body_y"),
        )
        self.assertEqual(
            (len(raster_body["cells"]), len(raster_body["cells"][0])),
            self.fixture("expected_len_raster_body_cells_len_raster_body_cells_0"),
        )
        self.assertEqual(
            (set(raster_body["columns"][12:20]), set(raster_body["columns"][:12])),
            self.fixture("expected_set_raster_body_columns_12_20_set_raster_body_columns_12"),
        )

    def test_the_streamed_stands_on_the_ship_its_stage_streams(self) -> None:
        from retrolarium_kit.games.alien_soldier import bosses, stages

        tree = workspace.tree(self.root, workspace.source_profile(self.root), "jp")
        # Stage 9 spawns the Caterpillar ($128), then streams plane B from
        # The secondary camera, the map read from row $700.
        stream = stages.secondary_streams(tree)[0x128]
        self.assertEqual(
            (stream["descriptor"], stream["camera_y"]), self.fixture("expected_stream_descriptor_stream_camera_y")
        )
        streamed = next(
            boss
            for boss in self.documents["graphics/bosses.json"]["bosses"]
            if boss["title"] == self.fixture("boss_title_reference")
        )
        body = streamed["body"]
        # Map X $210 to $99F, ten rows, all on line 3 over the sprites.
        self.assertEqual(
            (body["plane"], len(body["cells"]), len(body["cells"][0])),
            self.fixture("expected_body_plane_len_body_cells_len_body_cells_0"),
        )
        self.assertEqual(
            {word & 0xE000 for row in body["cells"] for word in row if word & 0x7FF},
            self.fixture("expected_word_57344_for_row_in_body_cells_for_word_in_row_if_word_2047"),
        )
        # As in Gens's states of the fight: a segment stands at map X $58 -
        # $80 of the plane, 24 lines below the ship's top on its column.
        machine = bytearray(0x10000)
        for at, data in streamed["tables"].items():
            if int(at, 16) >= 0xFF0000:
                low = int(at, 16) - 0xFF0000
                machine[low : low + len(data) // 2] = bytes.fromhex(data)
        segment = bosses.SLOT + 9 * bosses.PART_SIZE - 0xFF0000
        word = lambda at: int.from_bytes(machine[at : at + 2], "big")
        x = word(segment + 0x10) - streamed["centre"][0]
        y = word(segment + 0x14) - streamed["centre"][1]
        self.assertEqual(x - body["x"], word(segment + 0x58) - 0x80 - 0x210)
        self.assertEqual(y - body["y"], self.fixture("expected_y_body_y"))

    def test_sprite_body_and_stage_body_come_with_their_planes_and_own_sprites(self) -> None:
        from retrolarium_kit.games.alien_soldier import stages

        found = {boss["title"]: boss for boss in self.documents["graphics/bosses.json"]["bosses"]}
        # As in Gens's states of their fights: Victor 32 frames in, flying
        # in at $1C4 with its velocity; its core, eyes and ring are sprites
        # The records hold themselves.
        sprite_body = found[self.fixture("found_key")]
        self.assertEqual(
            (sprite_body["body"]["plane"], sprite_body["body"]["x"], sprite_body["body"]["y"]),
            self.fixture("expected_sprite_body_body_plane_sprite_body_body_x_sprite_body_body_y"),
        )
        core = sprite_body["parts"][0]
        self.assertEqual(
            (core["kind"], core["size"], core["x"], core["y"], core["attribute"]),
            self.fixture("expected_core_kind_core_size_core_x_core_y_core_attribute"),
        )
        records = bytes.fromhex(sprite_body["tables"][f"{sprite_body['slot']:06X}"])
        word = lambda index, field: int.from_bytes(records[index * 0x60 + field : index * 0x60 + field + 2], "big")
        self.assertEqual((word(0, 0x10), word(0, 0x14)), self.fixture("expected_word_0_16_word_0_20"))
        ring = [
            (
                (word(index, 0x10) - 452 + 0x8000) % 0x10000 - 0x8000,
                (word(index, 0x14) - 256 + 0x8000) % 0x10000 - 0x8000,
            )
            for index in range(4, 12)
        ]
        self.assertEqual(ring, self.fixture("expected_ring"))
        # The stage-backed boss lifts its stage's blocks into plane B (stage 16 holds the
        # camera at $660 for it), shown level as it stands between rocks.
        tree = workspace.tree(self.root, workspace.source_profile(self.root), "jp")
        self.assertEqual(
            stages.spawning_scenes(tree)[0x2B8]["camera_x"],
            self.fixture("expected_stages_spawning_scenes_tree_696_camera_x"),
        )
        stage_body = found[self.fixture("found_key_2")]
        body = stage_body["body"]
        self.assertEqual(
            (body["plane"], body["x"], body["y"], len(body["cells"]), len(body["cells"][0])),
            self.fixture("expected_body_plane_body_x_body_y_len_body_cells_len_body_cells_0"),
        )
        self.assertEqual(set(body["columns"]), self.fixture("expected_set_body_columns"))
        self.assertIn(
            self.fixture("expected_load_source_for_load_in_stage_body_loads"),
            [load["source"] for load in stage_body["loads"]],
        )
        self.assertEqual(stage_body["palettes"][-1], self.fixture("expected_stage_body_palettes_1"))

    def test_cut_jointed_waves_its_tentacles_as_it_comes_in(self) -> None:
        # As MAME runs the cut boss in the jointed boss's place, 141 frames in: its
        # face in plane B, 24 tentacle segments placed down their tree
        # (pointers flagged in their top byte), its message on screen.
        cut_boss = next(
            boss
            for boss in self.documents["graphics/bosses.json"]["bosses"]
            if boss["title"] == self.fixture("boss_title_reference")
        )
        body = cut_boss["body"]
        self.assertEqual(
            (body["plane"], body["x"], body["y"], len(body["cells"]), len(body["cells"][0])),
            self.fixture("expected_body_plane_body_x_body_y_len_body_cells_len_body_cells_0"),
        )
        self.assertEqual(
            sum(1 for part in cut_boss["parts"] if part["kind"] == "mapping"),
            self.fixture("expected_sum_1_for_part_in_cut_boss_parts_if_part_kind_mapping"),
        )
        self.assertEqual(cut_boss["parts"][1]["source"], self.fixture("expected_cut_boss_parts_1_source"))
        records = bytes.fromhex(cut_boss["tables"][f"{cut_boss['slot']:06X}"])
        word = lambda index, field: int.from_bytes(records[index * 0x60 + field : index * 0x60 + field + 2], "big")
        self.assertEqual(
            (word(0, 4), word(0, 0x10), word(0, 0x14)), self.fixture("expected_word_0_4_word_0_16_word_0_20")
        )
        self.assertEqual(
            {
                index: tuple(
                    (word(index, field) - word(0, field) + 0x8000) % 0x10000 - 0x8000 for field in (0x10, 0x14)
                )
                for index in (5, 10, 15, 20, 24)
            },
            self.fixture("expected_index_tuple_word_index_field_word_0_field_32768_65536_32768_for_field_i"),
        )

    def test_bosses_bring_what_their_setup_leaves_in_their_records(self) -> None:
        # The mounted boss's hind legs hang from anchors its builder turns back,
        # The armoured boss draws its arms over its belly and turns its sword with
        # its hand before its renderer, the airborne boss's tail has waved a while,
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

        self.assertEqual(
            [angle(self.fixture("source_symbol"), index) for index in range(1, 5)],
            self.fixture("expected_angle_mounted_index_for_index_in_range_1_5"),
        )
        armoured = found[self.fixture("found_key")]
        slot = armoured["slot"]
        machine = pose_code.Machine({}, {"a5": slot})
        machine.write(slot + 6 * 0x60 + 0x56, 2, 0x210)
        pose_code.run(armoured["anchor_code"], machine)
        self.assertEqual(
            (machine.read(slot + 0x23E, 2), machine.read(slot + 0x29C, 2)),
            self.fixture("expected_machine_read_slot_574_2_machine_read_slot_668_2"),
        )
        self.assertEqual(
            [armoured["parts"][index]["attribute"] for index in (1, 6, 8, 13)],
            self.fixture("expected_armoured_parts_index_attribute_for_index_in_1_6_8_13"),
        )
        self.assertEqual(
            len({angle(self.fixture("source_symbol_2"), index) for index in range(12, 20)}),
            self.fixture("expected_len_angle_flying_airborne_index_for_index_in_range_12_20"),
        )
        body = found[self.fixture("found_key_2")]["body"]
        self.assertEqual(
            (body["command"], body["x"], body["y"], body["part"]),
            self.fixture("expected_body_command_body_x_body_y_body_part"),
        )

    def test_path_following_lays_its_body_down_its_heads_path(self) -> None:
        from retrolarium_kit.games.alien_soldier import bosses, pose_code

        path_boss = next(
            boss
            for boss in self.documents["graphics/bosses.json"]["bosses"]
            if boss["title"] == self.fixture("boss_title_reference")
        )
        self.assertTrue(path_boss["checked"])
        self.assertEqual(
            (path_boss["chain"], path_boss["position_field"], path_boss["centre"]),
            self.fixture("expected_path_boss_chain_path_boss_position_field_path_boss_centre"),
        )
        # The head and 24 body records drawn; the orbit shot waits hidden.
        self.assertEqual(
            sum(1 for part in path_boss["parts"] if part["kind"] == "mapping"),
            self.fixture("expected_sum_1_for_part_in_path_boss_parts_if_part_kind_mapping"),
        )
        # Run as the studio runs it, it is Gens's state of the fight 472
        # frames in: the head circling while its message is on screen, its
        # body on the path behind it.
        machine = pose_code.Machine({}, {"a5": bosses.SLOT})
        for at, data in path_boss["tables"].items():
            for offset, value in enumerate(bytes.fromhex(data)):
                machine.memory[int(at, 16) + offset] = value
        pose_code.run(path_boss["pose_code"], machine)
        for index in range(1, len(path_boss["parts"])):
            code = path_boss["record_code"].get(f"{machine.read(bosses.SLOT + index * bosses.PART_SIZE, 2):X}")
            if code:
                machine.a[5] = bosses.SLOT + index * bosses.PART_SIZE
                pose_code.run(code, machine)
        head = [machine.read(bosses.SLOT + field, 2) for field in (4, 0x10, 0x14, 0x56)]
        self.assertEqual(head, self.fixture("expected_head"))

        def place(index: int) -> tuple[int, int]:
            record = bosses.SLOT + index * bosses.PART_SIZE
            return tuple(
                (machine.read(record + field, 2) - machine.read(bosses.SLOT + field, 2) + 0x8000) % 0x10000 - 0x8000
                for field in (0x10, 0x14)
            )

        self.assertEqual(
            {index: place(index) for index in (1, 6, 12, 18, 24)},
            self.fixture("expected_index_place_index_for_index_in_1_6_12_18_24"),
        )

    def test_a_capture_knows_the_object_records_and_the_bosses(self) -> None:
        from retrolarium_kit.games.alien_soldier import capture

        tree = workspace.tree(self.root, workspace.source_profile(self.root), "jp")
        # Sixty $60-byte records from $FFC620, the end the walker compares
        # with; the bosses are the types their asset sets install.
        self.assertEqual(capture._pool(tree), self.fixture("expected_capture_pool_tree"))
        types = capture.boss_types(tree)
        self.assertIn(self.fixture("expected_types"), types)
        self.assertIn(self.fixture("expected_types_2"), types)

    def test_pose_code_runs_as_the_game_runs_it(self) -> None:
        from retrolarium_kit.games.alien_soldier import bosses

        jointed = next(
            boss
            for boss in self.documents["graphics/bosses.json"]["bosses"]
            if boss["title"] == self.fixture("boss_title_reference")
        )
        words = [value << 8 for value in jointed["neutral_pose"]]
        words[14] = 0x2000
        angles = bosses._run_angles(jointed, words)
        # The foot's angle is the sum of its leg's channels and the body's
        # (14, 13, 12 and 2), each byte doubled.
        expected = (
            2 * (0x20 + jointed["neutral_pose"][13] + jointed["neutral_pose"][12] + jointed["neutral_pose"][2])
        ) & 0x1FE
        self.assertEqual(angles[25] & 0x1FE, expected)
        swapped = bosses._run_angles(jointed, words, side=2)
        self.assertNotEqual(swapped[25] & 0x1FE, expected)
        # The extended-reach boss's arms take a mapping from a table by angle (an
        # eighth of a turn later), four per arm.
        from retrolarium_kit.games.alien_soldier import pose_code

        extended_reach = next(
            boss
            for boss in self.documents["graphics/bosses.json"]["bosses"]
            if boss["title"] == self.fixture("boss_title_reference_2")
        )
        machine = pose_code.Machine({}, {"a5": bosses.SLOT})
        for at, data in extended_reach["tables"].items():
            for offset, value in enumerate(bytes.fromhex(data)):
                machine.memory[int(at, 16) + offset] = value
        machine.write(bosses.SLOT + 0x60 + 0x56, 2, 0x40)
        pose_code.run(extended_reach["anchor_code"], machine)
        named = {address: name for name, address in extended_reach["symbols"].items()}
        self.assertEqual(
            [named.get(machine.read(bosses.SLOT + index * 0x60 + 8, 4)) for index in (1, 2)],
            self.fixture("expected_named_get_machine_read_bosses_slot_index_96_8_4_for_index_in_1_2"),
        )

    def test_poses_and_animations_write_back(self) -> None:
        from retrolarium_kit.games.alien_soldier import bosses

        document = self.documents["graphics/bosses.json"]
        label = next(
            boss["poses"] for boss in document["bosses"] if boss["title"] == self.fixture("boss_title_reference")
        )
        rows = [{**row, "values": list(row["values"])} for row in document["poses"][label]]
        rows[6]["values"][0] = 0x55
        edited = {**document, "poses": {label: rows}}
        sequences = [dict(item) for item in document["sequences"]]
        idle = next(item for item in sequences if item["label"] == self.fixture("source_symbol"))
        idle["commands"] = [{**command, "frames": 20} for command in idle["commands"]]
        edited["sequences"] = sequences
        tree = workspace.tree(self.root, workspace.source_profile(self.root), "jp")
        bosses.apply(tree, edited)
        self.assertEqual(tree.data(label)[rows[6]["offset"]], 0x55)
        self.assertEqual(tree.data(self.fixture("source_symbol"))[:2], bytes((20, 32)))
        again = bosses.export(tree)
        self.assertEqual(again["poses"][label][6]["values"][0], 0x55)
        # A step count of zero would divide by zero; an end and the offsets stay.
        for change in ({"steps": 0}, {"frames": 0x80}):
            broken = [dict(item) for item in document["sequences"]]
            target = next(item for item in broken if item["label"] == self.fixture("source_symbol"))
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
