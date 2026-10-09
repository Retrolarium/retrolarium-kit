from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from helpers import patched_manifest
from retrolarium_kit.core.game_profiles import packaged_game_profiles
from retrolarium_kit.games.doraemon.hierarchy import (
    DoraemonHierarchyError,
    decode_hierarchy,
    encode_hierarchy,
    payload_size,
)
from retrolarium_kit.games.doraemon.palettes import (
    DoraemonPaletteError,
    decode_room_selectors,
    encode_room_selectors,
)
from retrolarium_kit.games.doraemon.world2.metatiles import (
    DoraemonWorld2MetatileError,
    decode_metatiles as decode_world2_metatiles,
    encode_metatiles as encode_world2_metatiles,
)
from retrolarium_kit.games.doraemon.world2.streams import (
    DoraemonWorld2StreamError,
    decode_screens as decode_world2_screens,
    encode_screens as encode_world2_screens,
)
from retrolarium_kit.games.doraemon.world2.stage import (
    DoraemonWorld2StageError,
    decode_stage as decode_world2_stage,
    encode_stage as encode_world2_stage,
    token_command,
)
from retrolarium_kit.games.doraemon.world2.branches import (
    DoraemonWorld2BranchError,
    decode_branches as decode_world2_branches,
    encode_branches as encode_world2_branches,
)
from retrolarium_kit.games.doraemon.world2.chapters import (
    DoraemonWorld2ChapterError,
    decode_chapters as decode_world2_chapters,
    encode_chapters as encode_world2_chapters,
)
from retrolarium_kit.games.doraemon.world2.projection import project_screen
from retrolarium_kit.games.doraemon.world2.routes import (
    Placement, Route, paint_route_cell,
)
from retrolarium_kit.platform.nes.resources import (
    build_resources,
    decode_resources,
    parse_resource_document,
)
from retrolarium_kit.games.doraemon.metasprites import (
    DoraemonMetaspriteError,
    decode_variable as decode_doraemon_metasprites,
    decode_world2 as decode_doraemon_world2_metasprites,
    encode_variable as encode_doraemon_metasprites,
)
from retrolarium_kit.studio.create import create_studio_project


HELP_SPRITE_STREAMS = ((100064, 89), (100153, 57), (100210, 105))
OBJECT_LISTS = ((22937, 337), (22837, 100))     # city, underground placements


def synthetic_world2_payload() -> bytes:
    return (0xBFCC).to_bytes(2, "little") * 119 + bytes(16431)


def synthetic_metasprite_records(size: int) -> bytes:
    """Records of up to 64 pieces filling `size` bytes (a multiple of 3)."""
    units = size // 3
    output = bytearray()
    while units:
        count = min(64, units - 1)
        output += bytes((count, 16, 16)) + bytes(3 * count)
        units -= count + 1
    return bytes(output)


def with_world2_screens(image: bytes) -> bytes:
    edited = bytearray(image)
    edited[48878:48878 + 16669] = synthetic_world2_payload()
    edited[35766:35772] = bytes((1, 4, 5, 16, 32, 48))
    # The help screens' OAM streams: whole sprites, then the zero Y.
    for offset, size in HELP_SPRITE_STREAMS:
        edited[offset:offset + size] = bytes((8, 1, 0, 8)) * (size // 4) + bytes(1)
    # World 1 placement lists: whole records, then the zero X.
    for offset, size in OBJECT_LISTS:
        edited[offset:offset + size] = bytes((1, 1, 0)) * (size // 3) + bytes(1)
    # Metasprite catalogs: every index entry points at the first record.
    for index, size, records, records_size, address in (
        (7366, 230, 7596, 1509, 0x9D9C), (79591, 376, 79967, 1119, 0xB84F),
    ):
        edited[index:index + size] = address.to_bytes(2, "little") * (size // 2)
        edited[records:records + records_size] = synthetic_metasprite_records(records_size)
    return bytes(edited)


class DoraemonProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = packaged_game_profiles().get("doraemon")

    def test_revision_identity_and_gnrom_chr_spans(self) -> None:
        self.assertEqual(
            [(revision.revision_id, revision.sha256, revision.mapper)
             for revision in self.profile.revisions],
            [
                ("original", "6ed579c9c98a1f2db52fd3d2488a491953073e8ace1e3c1dc5884669cecca274", 66),
                ("rev_a", "c070fa0e07a4b8be107ecc2f514e4c64f5bbb2a15754b6c6214e7259a1b30920", 66),
            ],
        )
        for revision in self.profile.revisions:
            self.assertEqual(revision.size, 16 + 8 * 16384 + 4 * 8192)
            # The bundle's resources; the text, music and demo have their own documents.
            resources = [resource for resource in self.profile.resources_for(revision)
                         if resource.codec not in ("doraemon.shell-text.v1", "doraemon.music-streams.v1",
                                                  "doraemon.world1-demo.v1")]
            self.assertEqual(resources[0].parameters["resource_ids"],
                             [resource.resource_id for resource in resources[1:]])
            self.assertEqual(
                [(resource.parameters["world"], resource.parameters["file_offset"],
                  resource.parameters["size"]) for resource in resources[1:3]],
                [("world1", 10751, 8000), ("world3", 89602, 6400)],
            )
            self.assertEqual(
                [(resource.parameters["file_offset"], resource.parameters["size"])
                 for resource in resources[4:8]],
                [(16 + 8 * 16384 + bank * 8192, 8192) for bank in range(4)],
            )
            self.assertEqual(
                [(resource.parameters["file_offset"], resource.parameters["size"])
                 for resource in resources[8:31]],
                [(22453 + index * 32, 32) for index in range(12)]
                + [(81086 + index * 32, 32) for index in range(11)],
            )
            self.assertEqual(resources[31].parameters,
                             {"file_offset": 47583, "size": 1040,
                              "collision_file_offset": 48852, "collision_size": 26})
            self.assertEqual(
                [(resource.parameters["file_offset"], resource.parameters["size"])
                 for resource in resources[32:41]],
                [(34737 + index * 16, 16) for index in range(9)],
            )
            self.assertEqual(resources[3].parameters,
                             {"file_offset": 77282, "size": 64})
            self.assertEqual(resources[-4].parameters,
                             {"file_offset": 48878, "size": 16669})
            self.assertEqual(resources[-3].parameters,
                             {"file_offset": 48623, "size": 229,
                              "start_file_offset": 35656, "start_size": 3})
            self.assertEqual(resources[-2].parameters,
                             {"file_offset": 42783, "size": 68})
            self.assertEqual(resources[-1].parameters,
                             {"file_offset": 35766, "size": 6})

    def test_all_banks_round_trip_and_isolated_pixel_edit(self) -> None:
        # A private retail ROM is not required to verify the profile's physical
        # bank layout and the generic GUI graphics document contract.
        header = b"NES\x1a" + bytes((8, 4, 0x20, 0x40)) + bytes(8)
        prg = bytes(8 * 16384)
        chr_data = bytes((index * 17 + index // 8192) & 0xFF
                         for index in range(4 * 8192))
        image = with_world2_screens(header + prg + chr_data)
        revision = replace(
            self.profile.revisions[0],
            sha1=hashlib.sha1(image).hexdigest(),
            sha256=hashlib.sha256(image).hexdigest(),
        )
        document = decode_resources(image, self.profile, revision)
        self.assertEqual([len(item["tiles"]) for item in document.resources[3:7]],
                         [512, 512, 512, 512])
        rebuilt, report = build_resources(image, document, self.profile, revision)
        self.assertEqual(rebuilt, image)
        self.assertEqual(report["changed_bytes"], 0)

        edited = document.as_dict()
        for bank, resource in enumerate(edited["resources"][3:7]):
            pixel = resource["tiles"][bank][0][0]
            resource["tiles"][bank][0][0] = (pixel + 1) % 4
        parsed = parse_resource_document(edited, self.profile, revision)
        rebuilt, report = build_resources(image, parsed, self.profile, revision)
        self.assertGreater(report["changed_bytes"], 0)
        self.assertEqual(rebuilt[:16 + 8 * 16384], image[:16 + 8 * 16384])
        changed_banks = {
            (offset - (16 + 8 * 16384)) // 8192
            for offset, (before, after) in enumerate(zip(image, rebuilt))
            if before != after
        }
        self.assertEqual(changed_banks, {0, 1, 2, 3})

    def test_world_hierarchies_round_trip_and_validate_geometry(self) -> None:
        for world, expected_maps in (
            ("world1", [("city", 64, 64), ("underground", 64, 25)]),
            ("world3", [("underwater", 64, 64)]),
        ):
            payload = bytes((index * 37 + 3) & 255
                            for index in range(payload_size(world)))
            document = decode_hierarchy(payload, world)
            self.assertEqual(encode_hierarchy(document, world), payload)
            self.assertEqual(
                [(item["id"], item["width"], item["height"])
                 for item in document["maps"]], expected_maps
            )
            document["maps"][0]["rows"][0][0] ^= 1
            document["attributes"][0]["palette"] ^= 1
            document["small_blocks"][0][0] ^= 1
            document["big_blocks"][0][0] ^= 1
            edited = encode_hierarchy(document, world)
            self.assertEqual(
                [index for index, (before, after) in enumerate(zip(payload, edited))
                 if before != after],
                [0, 256, 1280, 2304],
            )
            document["maps"][0]["rows"][0].pop()
            with self.assertRaisesRegex(DoraemonHierarchyError, "wrong width"):
                encode_hierarchy(document, world)

    def test_combined_level_and_chr_build_has_disjoint_owners(self) -> None:
        header = b"NES\x1a" + bytes((8, 4, 0x20, 0x40)) + bytes(8)
        image = with_world2_screens(header + bytes(8 * 16384 + 4 * 8192))
        for official_revision in self.profile.revisions:
            revision = replace(
                official_revision,
                sha1=hashlib.sha1(image).hexdigest(),
                sha256=hashlib.sha256(image).hexdigest(),
            )
            document = decode_resources(image, self.profile, revision).as_dict()
            document["resources"][0]["attributes"][0]["palette"] = 1
            document["resources"][0]["small_blocks"][0][0] = 1
            document["resources"][0]["big_blocks"][0][0] = 1
            document["resources"][0]["maps"][0]["rows"][0][0] = 1
            document["resources"][0]["maps"][1]["rows"][0][0] = 2
            document["resources"][1]["attributes"][0]["properties"] = 1
            document["resources"][1]["maps"][0]["rows"][0][0] = 3
            document["resources"][2]["selectors"][0] = 1
            document["resources"][3]["tiles"][0][0][0] = 1
            document["resources"][7]["values"][0] = 2
            document["resources"][19]["values"][0] = 3
            document["resources"][30]["palettes"][0] = 2
            document["resources"][30]["tiles"][0][0] = 1
            document["resources"][30]["solid"][0] = True
            document["resources"][31]["values"][0] = 4
            rebuilt, report = build_resources(
                image, parse_resource_document(document, self.profile, revision),
                self.profile, revision,
            )
            changed = {index for index, (before, after) in
                       enumerate(zip(image, rebuilt)) if before != after}
            self.assertEqual(changed, {
                10751,
                10751 + 256,
                10751 + 1280,
                10751 + 2304,
                10751 + 2304 + 64 * 64,
                89602,
                89602 + 2304,
                77282,
                16 + 8 * 16384,
                22453,
                81086,
                47583,
                47583 + 208,
                48852,
                34737,
            })
            self.assertEqual(report["changed_bytes"], 15)

    def test_world2_metatile_tables_round_trip_and_domain(self) -> None:
        visual = bytes([0, 1, 2, 3] * 52) + bytes(range(256)) * 3 + bytes(64)
        collision = bytes((index * 13) & 255 for index in range(26))
        decoded = decode_world2_metatiles(visual, collision)
        self.assertEqual(encode_world2_metatiles(decoded), (visual, collision))
        decoded["palettes"][0] = 4
        with self.assertRaisesRegex(DoraemonWorld2MetatileError, "outside 0..3"):
            encode_world2_metatiles(decoded)

    def test_world2_selector_aliases_and_fixed_capacity_round_trip(self) -> None:
        payload = synthetic_world2_payload()
        decoded = decode_world2_screens(payload)
        self.assertEqual(encode_world2_screens(decoded), payload)
        decoded["screens"][0][0][0] = 1
        with self.assertRaisesRegex(DoraemonWorld2StreamError, "physical alias"):
            encode_world2_screens(decoded)
        for screen in decoded["screens"]:
            screen[0][0] = 1
        rebuilt = encode_world2_screens(decoded)
        self.assertEqual(len(rebuilt), len(payload))
        self.assertEqual(
            [screen[0][0] for screen in decode_world2_screens(rebuilt)["screens"]],
            [1] * 119,
        )

    def test_world2_route_projection_paints_shared_physical_selector(self) -> None:
        resource = decode_world2_screens(synthetic_world2_payload())
        route = Route("fixture", (Placement(0, 0, 0, 0, 0, 0),))
        projected = project_screen(resource, 0, 0)
        self.assertEqual((len(projected.cells), len(projected.cells[0])), (15, 16))
        self.assertEqual(projected.raw_coordinate(0, 0), (0, 0))
        self.assertEqual(route.locate(0, 0), (route.placements[0], 0, 0))
        self.assertEqual(paint_route_cell(resource, route, 0, 0, 1),
                         (0, 0, 0))
        self.assertEqual(project_screen(resource, 118, 0).cells[0][0], 1)
        self.assertEqual(paint_route_cell(resource, route, 0, 0, 1), None)
        self.assertEqual(paint_route_cell(resource, route, 16, 0, 1), None)
        with self.assertRaisesRegex(ValueError, "brush"):
            paint_route_cell(resource, route, 0, 0, 208)

    def test_world2_screen_repack_stays_in_its_rom_owner(self) -> None:
        header = b"NES\x1a" + bytes((8, 4, 0x20, 0x40)) + bytes(8)
        image = with_world2_screens(header + bytes(8 * 16384 + 4 * 8192))
        revision = replace(
            self.profile.revisions[0],
            sha1=hashlib.sha1(image).hexdigest(),
            sha256=hashlib.sha256(image).hexdigest(),
        )
        document = decode_resources(image, self.profile, revision).as_dict()
        for screen in document["resources"][-4]["screens"]:
            screen[0][0] = 1
        rebuilt, report = build_resources(
            image, parse_resource_document(document, self.profile, revision),
            self.profile, revision,
        )
        changed = [index for index, (before, after) in
                   enumerate(zip(image, rebuilt)) if before != after]
        self.assertTrue(changed)
        self.assertTrue(all(48878 <= index < 48878 + 16669 for index in changed))
        self.assertEqual(report["changed_bytes"], len(changed))
        self.assertEqual(
            decode_resources(rebuilt, self.profile, replace(
                revision, sha1=hashlib.sha1(rebuilt).hexdigest(),
                sha256=hashlib.sha256(rebuilt).hexdigest(),
            )).resources[-4]["screens"][118][0][0], 1,
        )

    def test_world2_stage_sequence_owns_disjoint_bytecode_and_starts(self) -> None:
        sequence = bytes(range(229))
        starts = bytes((0, 37, 92))
        decoded = decode_world2_stage(sequence, starts)
        self.assertEqual(encode_world2_stage(decoded), (sequence, starts))
        self.assertEqual(token_command(0x42), "select_screen")
        self.assertEqual(token_command(0xF2), "set_pending_direction")
        self.assertEqual(token_command(0xF7), "restore_saved_offset")
        self.assertEqual(token_command(0xF8), "stop_scroll")
        self.assertEqual(token_command(0xF9), "set_background_palette")
        decoded["start_offsets"][0] = 229
        with self.assertRaisesRegex(DoraemonWorld2StageError, "starts are invalid"):
            encode_world2_stage(decoded)

        header = b"NES\x1a" + bytes((8, 4, 0x20, 0x40)) + bytes(8)
        image = with_world2_screens(header + bytes(8 * 16384 + 4 * 8192))
        revision = replace(
            self.profile.revisions[0],
            sha1=hashlib.sha1(image).hexdigest(),
            sha256=hashlib.sha256(image).hexdigest(),
        )
        document = decode_resources(image, self.profile, revision).as_dict()
        stage = document["resources"][-3]
        self.assertEqual(stage["start_offsets"], [0, 0, 0])
        self.assertEqual(stage["tokens"], [0] * 229)
        stage["start_offsets"][1] = 37
        stage["tokens"][4] = 0xF2
        rebuilt, report = build_resources(
            image, parse_resource_document(document, self.profile, revision),
            self.profile, revision,
        )
        changed = [index for index, (before, after) in
                   enumerate(zip(image, rebuilt)) if before != after]
        self.assertEqual(changed, [35657, 48627])
        self.assertEqual(report["changed_bytes"], 2)

    def test_world2_branch_tables_have_one_disjoint_owner(self) -> None:
        original = bytes(68)
        decoded = decode_world2_branches(original)
        self.assertEqual(encode_world2_branches(decoded), original)
        decoded["branches"][0]["condition_code"] = 4
        with self.assertRaisesRegex(DoraemonWorld2BranchError, "condition"):
            encode_world2_branches(decoded)

        header = b"NES\x1a" + bytes((8, 4, 0x20, 0x40)) + bytes(8)
        image = with_world2_screens(header + bytes(8 * 16384 + 4 * 8192))
        revision = replace(
            self.profile.revisions[0],
            sha1=hashlib.sha1(image).hexdigest(),
            sha256=hashlib.sha256(image).hexdigest(),
        )
        document = decode_resources(image, self.profile, revision).as_dict()
        document["resources"][-2]["branches"][0]["condition_code"] = 3
        rebuilt, report = build_resources(
            image, parse_resource_document(document, self.profile, revision),
            self.profile, revision,
        )
        changed = [index for index, (before, after) in
                   enumerate(zip(image, rebuilt)) if before != after]
        self.assertEqual(changed, [42783 + 34])
        self.assertEqual(report["changed_bytes"], 1)

    def test_world2_chapter_palette_selectors_have_one_owner(self) -> None:
        payload = bytes((1, 4, 5, 16, 32, 48))
        decoded = decode_world2_chapters(payload)
        self.assertEqual(encode_world2_chapters(decoded), payload)
        decoded["sprite_offsets"][0] = 17
        with self.assertRaisesRegex(DoraemonWorld2ChapterError, "catalog-aligned"):
            encode_world2_chapters(decoded)

        header = b"NES\x1a" + bytes((8, 4, 0x20, 0x40)) + bytes(8)
        image = with_world2_screens(header + bytes(8 * 16384 + 4 * 8192))
        revision = replace(
            self.profile.revisions[0],
            sha1=hashlib.sha1(image).hexdigest(),
            sha256=hashlib.sha256(image).hexdigest(),
        )
        document = decode_resources(image, self.profile, revision).as_dict()
        chapters = document["resources"][-1]
        chapters["background_palette_ids"][0] = 2
        chapters["sprite_offsets"][0] = 32
        rebuilt, report = build_resources(
            image, parse_resource_document(document, self.profile, revision),
            self.profile, revision,
        )
        changed = [index for index, (before, after) in
                   enumerate(zip(image, rebuilt)) if before != after]
        self.assertEqual(changed, [35766, 35769])
        self.assertEqual(report["changed_bytes"], 2)

    def test_metasprite_catalog_keeps_its_size_and_recomputes_pointers(self) -> None:
        index = bytes((0x00, 0x90, 0x00, 0x01, 0x06, 0x90))
        records = bytes((1, 8, 8, 0x00, 0x00, 0x10)) + bytes((1, 8, 8, 0x80, 0x00, 0x11))
        catalog = decode_doraemon_metasprites(index, records, 0x9000)
        self.assertEqual([{"record": 0}, {"alias": 0, "flip": 1}, {"record": 1}], catalog["entries"])
        self.assertEqual([[0x80, 0, 0x11]], catalog["records"][1]["pieces"])
        # A piece moves from the second record to the first: the second
        # record's pointer follows.
        catalog["records"] = [
            {"x_extent": 8, "y_extent": 8, "pieces": [[0, 0, 0x10], [0, 8, 0x12]]},
            {"x_extent": 8, "y_extent": 8, "pieces": []},
        ]
        with self.assertRaisesRegex(DoraemonMetaspriteError, "1-64 pieces"):
            encode_doraemon_metasprites(catalog, 0x9000, 6, 12)
        catalog["records"] = [{"x_extent": 8, "y_extent": 8, "pieces": [[0, 0, 0x10], [0, 8, 0x12], [8, 0, 0x13]]}]
        catalog["entries"] = [{"record": 0}, {"alias": 0, "flip": 1}, {"record": 0}]
        new_index, new_records = encode_doraemon_metasprites(catalog, 0x9000, 6, 12)
        self.assertEqual(bytes((0x00, 0x90, 0x00, 0x01, 0x00, 0x90)), new_index)
        self.assertEqual(bytes((3, 8, 8, 0, 0, 0x10, 0, 8, 0x12, 8, 0, 0x13)), new_records)
        catalog["records"][0]["pieces"].pop()
        with self.assertRaisesRegex(DoraemonMetaspriteError, "keeps 12 bytes"):
            encode_doraemon_metasprites(catalog, 0x9000, 6, 12)

    def test_world3_room_palette_selector_domain(self) -> None:
        selectors = [index % 11 for index in range(64)]
        self.assertEqual(decode_room_selectors(encode_room_selectors(selectors)),
                         selectors)
        selectors[8] = 11
        with self.assertRaisesRegex(DoraemonPaletteError, "outside 0..10"):
            encode_room_selectors(selectors)

    def test_graphics_document_creates_studio_pixel_editor(self) -> None:
        revision = self.profile.revisions[0]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = root / "disassembly" / "manifest.json"
            manifest_path.parent.mkdir()
            manifest_path.write_text("{}", encoding="utf-8")
            document_path = root / "graphics.json"
            resource_document = {
                "schema": "retrolarium.nes-resource-bundle",
                "schema_version": 1,
                "game": "doraemon",
                "revision": "original",
                "source_sha256": revision.sha256,
                "resources": [
                    {"id": f"{world}-hierarchy", "kind": "level",
                     "codec": "doraemon.hierarchy.v1",
                     **decode_hierarchy(bytes(payload_size(world)), world)}
                    for world in ("world1", "world3")
                ] + [
                    {"id": "world3-room-palette-selectors",
                     "kind": "palette-selection",
                     "codec": "doraemon.room-palette-selectors.v1",
                     "selectors": [0] * 64},
                ] + [
                    {"id": f"chr-bank-{bank}", "kind": "graphics",
                     "codec": "nes.chr-2bpp.v1",
                     "tiles": [[[0] * 8 for _ in range(8)] for _ in range(512)]}
                    for bank in range(4)
                ] + [
                    {"id": f"world{world}-palette-{index:02d}",
                     "kind": "palette", "codec": "nes.palette-table.v1",
                     "values": [0] * 32}
                    for world, count in ((1, 12), (3, 11))
                    for index in range(count)
                ] + [
                    {"id": "world2-metatiles", "kind": "metatile",
                     "codec": "doraemon.world2-metatiles.v1",
                     "palettes": [0] * 208,
                     "tiles": [[0] * 4 for _ in range(208)],
                     "solid": [False] * 208},
                ] + [
                    {"id": f"world2-palette-{index:02d}",
                     "kind": "palette", "codec": "nes.palette-table.v1",
                     "values": [0] * 16}
                    for index in range(9)
                ] + [
                    {"id": f"shell-palette-{name}",
                     "kind": "palette", "codec": "nes.palette-table.v1",
                     "values": [0] * 32}
                    for name in ("title", "help-1", "help-2", "help-3", "chapter", "ending")
                ] + [
                    {"id": f"shell-help-sprites-{n + 1}", "kind": "sprites",
                     "codec": "doraemon.oam-stream.v1",
                     "sprites": [{"x": 8, "y": 8, "tile": 1, "attributes": 0}] * (size // 4)}
                    for n, (_, size) in enumerate(HELP_SPRITE_STREAMS)
                ] + [
                    {"id": f"world1-{name}-objects", "kind": "objects",
                     "codec": "doraemon.world1-placements.v1",
                     "records": [{"x": 1, "y": 1, "type": 0}] * (size // 3)}
                    for name, (_, size) in zip(("city", "underground"), OBJECT_LISTS)
                ] + [
                    {"id": "world1-enemy-properties", "kind": "objects",
                     "codec": "doraemon.world1-enemy-properties.v1",
                     "metasprites": [0] * 16, "render_flags": [0] * 16, "health": [0] * 16},
                    {"id": "world1-descriptors", "kind": "objects",
                     "codec": "doraemon.world1-descriptors.v1",
                     "records": [{"runtime_type": 0, "metasprite": 0, "render_flags": 0, "behavior": 0}] * 13},
                    {"id": "world3-objects", "kind": "objects",
                     "codec": "doraemon.world3-registry.v1",
                     "records": [{"room": 0, "type": 0, "x": 0, "y": 0, "state": 0}] * 13},
                    {"id": "world2-secret-screens", "kind": "objects",
                     "codec": "doraemon.world2-secret-screens.v1", "screens": [0] * 7},
                    {"id": "world3-schedule", "kind": "objects",
                     "codec": "doraemon.world3-schedule.v1",
                     "rooms": [{"channels": [{"type": 0, "count": 0, "delay": 0}] * 4}] * 64},
                ] + [
                    {"id": f"world{world}-metasprites", "kind": "animation",
                     "codec": "doraemon.metasprites.v1",
                     **decode_doraemon_metasprites(
                         address.to_bytes(2, "little") * (size // 2),
                         synthetic_metasprite_records(records_size), address)}
                    for world, size, records_size, address in ((1, 230, 1509, 0x9D9C), (3, 376, 1119, 0xB84F))
                ] + [
                    {"id": "world2-enemy-metasprites", "kind": "animation",
                     "codec": "doraemon.world2-metasprites.v1",
                     **decode_doraemon_world2_metasprites(bytes(232), bytes(58), bytes(36))},
                ] + [
                    {"id": "world2-screens", "kind": "screen-stream",
                     "codec": "doraemon.world2-screens.v1",
                     **decode_world2_screens(synthetic_world2_payload())},
                ] + [
                    {"id": "world2-stage", "kind": "stage-sequence",
                     "codec": "doraemon.world2-stage.v1",
                     **decode_world2_stage(bytes(229), bytes(3))},
                ] + [
                    {"id": "world2-branches", "kind": "stage-branches",
                     "codec": "doraemon.world2-branches.v1",
                     **decode_world2_branches(bytes(68))},
                ] + [
                    {"id": "world2-chapters", "kind": "chapter-palettes",
                     "codec": "doraemon.world2-chapters.v1",
                     **decode_world2_chapters(bytes((1, 4, 5, 16, 32, 48)))},
                ],
            }
            parse_resource_document(resource_document, self.profile, revision)
            document_path.write_text(json.dumps(resource_document), encoding="utf-8")
            with patched_manifest({
                "schema": "retrolarium.nes.disassembly",
                "schema_version": 1,
                "input": {"sha256": revision.sha256, "size": revision.size},
            }):
                inspection = create_studio_project(
                    manifest_path, root / "doraemon.rlj",
                    project_id="doraemon", name="Doraemon",
                    profile="doraemon.v1", resource_document=document_path,
                )
            graphics = next(tab for tab in inspection["workspace"]["tabs"]
                            if tab["id"] == "graphics")
            self.assertEqual(graphics["kind"], "graphics.tile-set")
            self.assertEqual(graphics["codec"], "nes.chr-2bpp.v1")
            self.assertIn("pixel-paint", graphics["capabilities"])
            levels = next(tab for tab in inspection["workspace"]["tabs"]
                          if tab["id"] == "levels")
            self.assertEqual(levels["kind"], "doraemon.levels")
            self.assertEqual(levels["codec"], "doraemon.hierarchy.v1")
            self.assertEqual(levels["depends_on"], ["graphics", "palettes"])
            palettes = next(tab for tab in inspection["workspace"]["tabs"]
                            if tab["id"] == "palettes")
            self.assertEqual(palettes["kind"], "palette.set")
            metatiles = next(tab for tab in inspection["workspace"]["tabs"]
                             if tab["id"] == "world2-metatiles")
            self.assertEqual(metatiles["kind"], "doraemon.world2-metatiles")
            self.assertEqual(metatiles["depends_on"], ["graphics", "palettes"])
            screens = next(tab for tab in inspection["workspace"]["tabs"]
                           if tab["id"] == "world2-screens")
            self.assertEqual(screens["kind"], "doraemon.world2-screens")
            self.assertEqual(screens["depends_on"],
                             ["world2-metatiles", "graphics", "palettes"])
            routes = next(tab for tab in inspection["workspace"]["tabs"]
                          if tab["id"] == "world2-routes")
            self.assertEqual(routes["kind"], "doraemon.world2-routes")
            self.assertEqual(routes["codec"], "doraemon.world2-stage.v1")
            self.assertEqual(routes["depends_on"], [
                "world2-screens", "world2-metatiles", "graphics", "palettes"])


if __name__ == "__main__":
    unittest.main()
