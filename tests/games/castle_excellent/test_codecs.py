from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from retrolarium_kit.core.authoring import (
    AuthoringError,
    GameProfile,
    ResourceSpec,
    RevisionSpec,
)
from retrolarium_kit.games.castle_excellent.rooms import (
    CastleExcellentError,
    PrefixEntry,
    build_levels,
    decode_levels,
    encode_prefix_stream,
    parse_level_document,
    validate_prefix_codebook,
)
from helpers import game_profile
from retrolarium_kit.games.catalog import (
    build_game,
    build_game_documents,
    extract_game,
    game_launch_plan,
    inspect_game,
    validate_game,
)


ORACLE = (
    Path(__file__).resolve().parents[3]
    / "fixtures"
    / "synthetic"
    / "nes"
    / "castle_excellent_codec_probe"
    / "oracle.json"
)


def put_cpu(prg: bytearray, address: int, payload: bytes) -> None:
    start = address - 0x8000
    prg[start : start + len(payload)] = payload


def synthetic_image() -> bytes:
    header = b"NES\x1a" + bytes((2, 4, 0x31, 0)) + bytes(8)
    prg = bytearray(0x8000)
    codebook = (
        bytes((0x10,))
        + b"0\0"
        + bytes((0x20,))
        + b"10\0"
        + bytes((0x30,))
        + b"110\0"
        + bytes((0x40,))
        + b"111\0"
        + b"\xff"
    )
    put_cpu(prg, 0x8000, codebook)
    put_cpu(prg, 0x8100, b"\x00\x00\x01\x00\x00\x00\x01\x00")
    object_stream = bytes((1, 1, 1, 0xFF, 0xFF, 2, 0, 1, 3, 1, 0, 0xFF, 0xFF))
    put_cpu(prg, 0x8200, object_stream + b"\xff" * (0x20 - len(object_stream)))
    put_cpu(prg, 0x8240, bytes((1, 0, 2, 0)))
    put_cpu(prg, 0x8250, bytes((0, 1, 1, 2, 2)))
    put_cpu(prg, 0x8260, bytes((0, 1, 1, 1, 1)))
    put_cpu(prg, 0x8300, bytes(range(32)))
    put_cpu(prg, 0x8400, bytes(range(100)))
    put_cpu(prg, 0x8500, bytes(range(1, 101)))
    put_cpu(prg, 0x8600, bytes([0x55]) * 100)
    put_cpu(prg, 0x8700, bytes(16))
    put_cpu(prg, 0x8710, bytes((0x03, 0x0C, 0x30, 0xC0)))
    put_cpu(prg, 0x8720, bytes([0x81]) * 61)
    descriptors = bytearray(406)
    descriptors[0:4] = bytes((1, 2, 3, 2))
    put_cpu(prg, 0x8800, bytes(descriptors))
    chr_data = bytearray(b"\xff" * 0x8000)
    chr_data[2 * 0x2000 : 2 * 0x2000 + 2] = bytes((0x59, 0x0F))
    chr_data[3 * 0x2000 : 3 * 0x2000 + 2] = bytes((0x0F, 0x0F))
    return header + bytes(prg) + bytes(chr_data)


def synthetic_profile(image: bytes) -> tuple[GameProfile, RevisionSpec]:
    revision = RevisionSpec(
        "castle-excellent-probe",
        "Castle Excellent codec probe",
        len(image),
        hashlib.sha1(image).hexdigest(),
        hashlib.sha256(image).hexdigest(),
        "ines",
        3,
    )
    rooms = ResourceSpec(
        "rooms",
        "room-collection",
        "castle-excellent.prefix-rooms.v1",
        "fixed",
        {
            "room_count": 4,
            "width": 2,
            "height": 2,
            "codebook_cpu": 0x8000,
            "pointer_table_cpu": 0x8100,
            "fill_byte": 0xFF,
            "arenas": [
                {"bank": 2, "first_room": 0, "last_room": 2, "start": 0, "end": 2},
                {"bank": 3, "first_room": 2, "last_room": 4, "start": 0, "end": 2},
            ],
        },
    )
    objects = ResourceSpec(
        "objects",
        "object-layer",
        "castle-excellent.object-stream.v1",
        "fixed",
        {
            "room_count": 4,
            "width": 2,
            "height": 2,
            "stream_cpu": 0x8200,
            "stream_end_cpu": 0x8220,
            "count_table_cpu": 0x8240,
            "state_offsets_cpu": 0x8250,
            "state_capacity": 4,
            "type_count": 4,
            "maximum_per_room": 3,
            "fill_byte": 0xFF,
        },
    )
    mutable = ResourceSpec(
        "mutable-cell-state",
        "derived-state-layout",
        "castle-excellent.mutable-cell-state.v1",
        "fixed",
        {
            "state_offsets_cpu": 0x8260,
            "state_capacity": 4,
            "final_room_minimum_bytes": 0,
            "persistent_cell_values": [0x20],
        },
    )
    rendering = ResourceSpec(
        "room-rendering",
        "rendering-data",
        "castle-excellent.room-rendering.v1",
        "fixed",
        {
            "chr_bank": 0,
            "chr_size": 8192,
            "room_palette_cpu": 0x8300,
            "room_palette_size": 32,
            "left_tiles_cpu": 0x8400,
            "right_tiles_cpu": 0x8500,
            "cell_attributes_cpu": 0x8600,
            "cell_count": 100,
            "initial_attributes_cpu": 0x8700,
            "initial_attributes_size": 16,
            "attribute_masks_cpu": 0x8710,
            "attribute_masks_size": 4,
            "attribute_preserving_cells": [9, 83],
            "runtime_object_types_cpu": 0x8720,
            "runtime_object_type_count": 61,
            "metasprite_descriptors_cpu": 0x8800,
            "metasprite_descriptors_size": 406,
            "object_previews": [
                {"runtime_type": 0x81, "descriptor_cpu": 0x8800, "x_offsets": [0, 8]},
            ],
        },
    )
    profile = GameProfile(
        "castle-excellent-probe",
        1,
        "Castle Excellent codec probe",
        "nes",
        ("rooms", "objects", "fixed-arena-write"),
        (revision,),
        (rooms, objects, mutable, rendering),
    )
    return profile, revision


def write_profile(path: Path, profile: GameProfile) -> None:
    path.write_text(
        json.dumps(
            {
                "schema": "retrolarium.game-profile",
                "schema_version": 1,
                "profile_version": profile.profile_version,
                "id": profile.game_id,
                "name": profile.name,
                "platform": profile.platform,
                "capabilities": list(profile.capabilities),
                "revisions": [
                    {
                        "id": revision.revision_id,
                        "name": revision.name,
                        "size": revision.size,
                        "sha1": revision.sha1,
                        "sha256": revision.sha256,
                        "container": revision.container,
                        "mapper": revision.mapper,
                    }
                    for revision in profile.revisions
                ],
                "resources": [
                    {
                        "id": resource.resource_id,
                        "kind": resource.kind,
                        "codec": resource.codec,
                        "capacity_policy": resource.capacity_policy,
                        "parameters": dict(resource.parameters),
                    }
                    for resource in profile.resources
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


class CastleExcellentCodecTests(unittest.TestCase):
    def test_packaged_profile_has_both_known_revisions(self) -> None:
        profile = game_profile("castle-excellent")
        self.assertEqual(
            [revision.revision_id for revision in profile.revisions],
            ["castle-excellent-japan-hsp-05", "castlequest-usa"],
        )
        self.assertEqual(profile.resource("rooms").codec, "castle-excellent.prefix-rooms.v1")
        self.assertEqual(
            profile.resource("graphics-bundle").parameters["resource_ids"],
            [
                "chr-bank-0",
                "chr-bank-1",
                "screen-palettes",
                "room-palettes",
                "room-cell-visuals",
                "metasprite-descriptors",
                "sub-command-text",
                "sfx-parameters",
                "music",
            ],
        )
        self.assertEqual(
            [profile.resource(f"chr-bank-{bank}").parameters["file_offset"] for bank in range(2)],
            [32784, 40976],
        )
        self.assertEqual(
            [
                profile.resource(identifier).parameters["file_offset"]
                for identifier in ("screen-palettes", "room-palettes")
            ],
            [17677, 17741],
        )
        room_cells = profile.resource("room-cell-visuals")
        self.assertEqual(room_cells.codec, "castle-excellent.room-cell-visuals.v1")
        self.assertEqual(
            room_cells.parameters,
            {
                "cell_count": 100,
                "left_file_offset": 32576,
                "right_file_offset": 32676,
                "attributes_file_offset": 27106,
            },
        )
        metasprites = profile.resource("metasprite-descriptors")
        self.assertEqual(metasprites.codec, "castle-excellent.metasprite-descriptors.v1")
        self.assertEqual(
            metasprites.parameters,
            {
                "file_offset": 13510,
                "size": 406,
                "descriptor_count": 203,
            },
        )
        text = profile.resource("sub-command-text")
        self.assertEqual(text.codec, "castle-excellent.sub-command-text.v1")
        self.assertEqual(
            text.parameters,
            {
                "file_offset": 31623,
                "size": 53,
                "record_names": ["title", "load", "save", "reset", "back", "game", "loading", "saving"],
                "record_sizes": [12, 5, 5, 6, 5, 5, 8, 7],
            },
        )
        sfx = profile.resource("sfx-parameters")
        self.assertEqual(sfx.codec, "castle-excellent.sfx-parameters.v1")
        self.assertEqual(
            sfx.parameters,
            {
                "file_offset": 17465,
                "size": 20,
                "record_names": [
                    "triangle_type_2_period_low",
                    "inventory_period_low",
                    "noise_type_4_volume",
                    "noise_type_4_period",
                    "noise_type_4_length",
                ],
                "record_sizes": [4, 4, 4, 4, 4],
            },
        )
        music = profile.resource("music")
        self.assertEqual(music.codec, "castle-excellent.mml-music.v1")
        self.assertEqual(music.parameters["track_table_file_offset"], 14550)
        self.assertEqual(music.parameters["track_count"], 8)
        self.assertEqual(music.parameters["playback_prefix_file_offset"], 15142)
        self.assertEqual(music.parameters["playback_prefix_size"], 209)
        self.assertEqual(music.parameters["mml_file_offset"], 15351)
        self.assertEqual(music.parameters["mml_cpu_address"], 0xBBE7)
        self.assertEqual(music.parameters["mml_capacity"], 1574)
        self.assertEqual(music.parameters["stream_count"], 21)

    def test_open_probe_matches_independent_oracle_and_round_trips(self) -> None:
        image = synthetic_image()
        profile, revision = synthetic_profile(image)
        document = decode_levels(image, profile, revision)
        oracle = json.loads(ORACLE.read_text(encoding="utf-8"))
        actual = document.as_dict()
        self.assertEqual(actual["width"], oracle["width"])
        self.assertEqual(actual["height"], oracle["height"])
        self.assertEqual(actual["rooms"], oracle["rooms"])
        self.assertEqual(actual["rendering"]["codec"], "castle-excellent.room-rendering.v1")
        self.assertEqual(len(actual["rendering"]["chr_2bpp"]), 8192)
        self.assertEqual(actual["rendering"]["left_tiles"][7], 7)
        self.assertEqual(actual["rendering"]["runtime_object_types"][0], 0x81)
        self.assertEqual(
            actual["rendering"]["object_previews"][0],
            {
                "runtime_type": 0x81,
                "descriptor_offset": 0,
                "x_offsets": [0, 8],
            },
        )
        rebuilt, report = build_levels(image, document, profile, revision)
        self.assertEqual(rebuilt, image)
        self.assertEqual(
            [item["used_bytes"] for item in report["room_banks"]],
            oracle["room_arena_used_bytes"],
        )
        self.assertEqual(
            report["object_stream"]["used_bytes"],
            oracle["object_stream_used_bytes"],
        )
        self.assertEqual(report["object_state_bytes"], oracle["object_state_bytes"])
        self.assertEqual(report["mutable_cell_state_bytes"], oracle["mutable_cell_state_bytes"])

    def test_edit_updates_room_objects_and_side_tables_transactionally(self) -> None:
        image = synthetic_image()
        profile, revision = synthetic_profile(image)
        source_document = decode_levels(image, profile, revision).as_dict()
        source_document["rooms"][1]["cells"][0][0] = 0x20
        source_document["rooms"][2]["objects"].pop()
        document = parse_level_document(source_document, profile, revision)
        rebuilt, report = build_levels(image, document, profile, revision)
        self.assertNotEqual(rebuilt, image)
        self.assertGreater(report["changed_bytes"], 0)
        prg_start = 16
        self.assertEqual(rebuilt[prg_start + 0x240 : prg_start + 0x244], bytes((1, 0, 1, 0)))
        self.assertEqual(rebuilt[prg_start + 0x250 : prg_start + 0x255], bytes((0, 1, 1, 2, 2)))
        self.assertEqual(rebuilt[prg_start + 0x260 : prg_start + 0x265], bytes((0, 1, 2, 2, 2)))

    def test_room_arena_overflow_is_rejected(self) -> None:
        image = synthetic_image()
        profile, revision = synthetic_profile(image)
        raw = decode_levels(image, profile, revision).as_dict()
        raw["rooms"][0]["cells"] = [[0x30, 0x30], [0x30, 0x30]]
        document = parse_level_document(raw, profile, revision)
        with self.assertRaisesRegex(CastleExcellentError, "overflows CHR bank 2"):
            build_levels(image, document, profile, revision)

    def test_profile_driven_public_operations_use_atomic_outputs(self) -> None:
        image = synthetic_image()
        profile, _revision = synthetic_profile(image)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image_path = root / "probe.nes"
            profile_path = root / "profile.json"
            document_path = root / "levels.json"
            output_path = root / "rebuilt.nes"
            report_path = root / "report.json"
            mame_path = root / "consoles.exe"
            image_path.write_bytes(image)
            mame_path.write_bytes(b"mame")
            write_profile(profile_path, profile)

            inspected = inspect_game(image_path, custom_profile=profile_path)
            self.assertEqual(inspected["details"]["rooms"], 4)
            extracted = extract_game(image_path, document_path, custom_profile=profile_path)
            self.assertEqual(extracted["rooms"], 4)
            validated = validate_game(image_path, document_path, custom_profile=profile_path)
            self.assertEqual(validated["status"], "valid")
            result = build_game(
                image_path,
                document_path,
                output_path,
                custom_profile=profile_path,
                report_path=report_path,
            )
            self.assertEqual(output_path.read_bytes(), image)
            self.assertEqual(result["changed_bytes"], 0)
            self.assertTrue(report_path.is_file())
            launch = game_launch_plan(
                image_path,
                output_path,
                mame=mame_path,
                custom_profile=profile_path,
            )
            self.assertEqual(launch["game"], "castle-excellent-probe")
            self.assertEqual(launch["arguments"][0], "nes")

            with self.assertRaisesRegex(AuthoringError, "must not overwrite"):
                build_game(
                    image_path,
                    document_path,
                    image_path,
                    custom_profile=profile_path,
                )
            # The report may not land on an input or on the built image either,
            # and nothing is written before that is known.
            before = image_path.read_bytes()
            for report in (image_path, document_path, root / "elsewhere.nes"):
                with self.assertRaisesRegex(AuthoringError, "must not overwrite"):
                    build_game(
                        image_path,
                        document_path,
                        root / "elsewhere.nes",
                        custom_profile=profile_path,
                        report_path=report,
                    )
            self.assertEqual(before, image_path.read_bytes())

            unknown_path = root / "unknown-revision.nes"
            unknown = bytearray(image)
            unknown[-1] ^= 0x01
            unknown_path.write_bytes(unknown)
            with self.assertRaisesRegex(AuthoringError, "unsupported game revision"):
                inspect_game(unknown_path, custom_profile=profile_path)

            overflowing = json.loads(document_path.read_text(encoding="utf-8"))
            overflowing["rooms"][0]["cells"] = [[0x30, 0x30], [0x30, 0x30]]
            document_path.write_text(json.dumps(overflowing), encoding="utf-8")
            with self.assertRaisesRegex(CastleExcellentError, "overflows CHR bank 2"):
                validate_game(image_path, document_path, custom_profile=profile_path)

    def test_castle_excellent_rooms_and_two_chr_banks_build_as_one_transaction(self) -> None:
        image = synthetic_image()
        base, revision = synthetic_profile(image)
        profile = replace(
            base,
            capabilities=(*base.capabilities, "graphics"),
            resources=(
                *base.resources,
                ResourceSpec(
                    "graphics-bundle",
                    "resource-bundle",
                    "nes.resource-bundle.v1",
                    "fixed",
                    {"resource_ids": ["chr-bank-0", "chr-bank-1", "room-palettes"]},
                ),
                ResourceSpec(
                    "chr-bank-0",
                    "graphics",
                    "nes.chr-2bpp.v1",
                    "fixed",
                    {"file_offset": 32784, "size": 8192},
                ),
                ResourceSpec(
                    "chr-bank-1",
                    "graphics",
                    "nes.chr-2bpp.v1",
                    "fixed",
                    {"file_offset": 40976, "size": 8192},
                ),
                ResourceSpec(
                    "room-palettes",
                    "palette",
                    "nes.palette-table.v1",
                    "fixed",
                    {
                        "file_offset": 784,
                        "size": 32,
                        "entries": 32,
                        "palette_model": "nes",
                    },
                ),
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rom = root / "castle-excellent.nes"
            profile_path = root / "profile.json"
            levels_path = root / "levels.json"
            graphics_path = root / "graphics.json"
            output = root / "edited.nes"
            rom.write_bytes(image)
            write_profile(profile_path, profile)
            extract_game(rom, levels_path, custom_profile=profile_path, resource="levels")
            extract_game(
                rom,
                graphics_path,
                custom_profile=profile_path,
                resource="resource-bundle",
            )
            graphics = json.loads(graphics_path.read_text(encoding="utf-8"))
            self.assertEqual(
                ["chr-bank-0", "chr-bank-1", "room-palettes"],
                [item["id"] for item in graphics["resources"]],
            )
            exact = build_game_documents(
                rom,
                [levels_path, graphics_path],
                output,
                custom_profile=profile_path,
            )
            self.assertEqual(0, exact["changed_bytes"])
            self.assertEqual(image, output.read_bytes())
            levels = json.loads(levels_path.read_text(encoding="utf-8"))
            levels["rooms"][1]["cells"][0][0] = 0x20
            levels_path.write_text(json.dumps(levels), encoding="utf-8")
            graphics["resources"][0]["tiles"][0][0][0] = 0
            graphics["resources"][2]["values"][1] = 0x2A
            graphics_path.write_text(json.dumps(graphics), encoding="utf-8")

            result = build_game_documents(
                rom,
                [levels_path, graphics_path],
                output,
                custom_profile=profile_path,
            )
            rebuilt = output.read_bytes()
            self.assertEqual("retrolarium.composed-game-build", result["schema"])
            self.assertGreater(result["changed_bytes"], 1)
            self.assertNotEqual(image[32784], rebuilt[32784])
            self.assertEqual(0x2A, rebuilt[785])
            bank2 = 32784 + 2 * 8192
            self.assertNotEqual(image[bank2 : bank2 + 8192], rebuilt[bank2 : bank2 + 8192])

    def test_invalid_codebook_and_unknown_cell_are_rejected(self) -> None:
        with self.assertRaisesRegex(CastleExcellentError, "prefix-free"):
            validate_prefix_codebook((PrefixEntry(1, "0"), PrefixEntry(2, "01")))
        with self.assertRaisesRegex(CastleExcellentError, "no prefix code"):
            encode_prefix_stream((0x99,), (PrefixEntry(1, "0"), PrefixEntry(2, "1")))


if __name__ == "__main__":
    unittest.main()
