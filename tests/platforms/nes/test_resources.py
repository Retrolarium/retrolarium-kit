from __future__ import annotations

import copy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from retrolarium_kit.authoring import (
    AuthoringError,
    GameProfile,
    PayloadSpec,
    ResourceOverrideSpec,
    ResourceSpec,
    RevisionSpec,
)
from retrolarium_kit.game_profiles import packaged_game_profiles
import retrolarium_kit.games as games_module
from retrolarium_kit.games import (
    build_game,
    build_game_documents,
    extract_game,
    inspect_game,
    validate_game,
)
from retrolarium_kit.nes_resources import (
    NesResourceError,
    build_resources,
    decode_resources,
    inspection_details,
    parse_resource_document,
)
from retrolarium_kit.smb_player_frames import FRAME_COUNT, TILES_PER_FRAME


def fixture_image() -> bytes:
    header = b"NES\x1a" + bytes((1, 1, 0, 0)) + bytes(8)
    prg = bytearray(0x4000)
    prg[:4] = bytes((0x0F, 0x01, 0x11, 0x21))
    prg[4:10] = bytes((1, 2, 3, 4, 0x55, 0xAA))
    chr_data = bytes((0x80,)) + bytes(15)
    return header + bytes(prg) + chr_data + bytes(0x2000 - 16)


def fixture_profile(image: bytes) -> tuple[GameProfile, RevisionSpec]:
    revision = RevisionSpec(
        "probe-v1",
        "NES resource probe",
        len(image),
        hashlib.sha1(image).hexdigest(),
        hashlib.sha256(image).hexdigest(),
        "ines",
        0,
    )
    resources = (
        ResourceSpec(
            "authoring-bundle", "resource-bundle", "nes.resource-bundle.v1", "fixed"
        ),
        ResourceSpec(
            "palette",
            "palette",
            "nes.palette-table.v1",
            "fixed",
            {
                "file_offset": 16,
                "size": 4,
                "entries": 4,
                "palette_model": "rp2c04_0004",
            },
        ),
        ResourceSpec(
            "screen",
            "screen",
            "nes.nametable.v1",
            "fixed",
            {
                "file_offset": 20,
                "size": 6,
                "width": 2,
                "height": 2,
                "attribute_bytes": 2,
            },
        ),
        ResourceSpec(
            "graphics",
            "graphics",
            "nes.chr-2bpp.v1",
            "fixed",
            {"file_offset": 0x4010, "size": 16},
        ),
    )
    return (
        GameProfile(
            "nes-resource-probe",
            1,
            "NES resource probe",
            "nes",
            ("screens", "palettes", "graphics"),
            (revision,),
            resources,
        ),
        revision,
    )


def write_profile(path: Path, profile: GameProfile) -> None:
    def resource_value(item: ResourceSpec) -> dict[str, object]:
        value = {
            "id": item.resource_id,
            "kind": item.kind,
            "codec": item.codec,
            "capacity_policy": item.capacity_policy,
            "parameters": dict(item.parameters),
        }
        if item.payload is not None:
            value["payload"] = item.payload
        return value

    def revision_value(item: RevisionSpec) -> dict[str, object]:
        value: dict[str, object] = {
            "id": item.revision_id,
            "name": item.name,
            "size": item.size,
            "sha1": item.sha1,
            "sha256": item.sha256,
            "container": item.container,
            "mapper": item.mapper,
        }
        if item.capabilities is not None:
            value["capabilities"] = list(item.capabilities)
        if item.resources is not None:
            value["resources"] = [resource_value(resource) for resource in item.resources]
        if item.resource_overrides:
            value["resource_overrides"] = [
                {
                    "id": override.resource_id,
                    "parameters": dict(override.parameters),
                    **(
                        {"payload": override.payload}
                        if override.payload is not None else {}
                    ),
                }
                for override in item.resource_overrides
            ]
        if item.payloads:
            value["payloads"] = [
                {
                    "id": payload.payload_id,
                    "storage": payload.storage,
                    "size": payload.size,
                    "sha1": payload.sha1,
                    **(
                        {"file_offset": payload.file_offset}
                        if payload.file_offset is not None else {}
                    ),
                    **(
                        {"record_ids": list(payload.record_ids)}
                        if payload.record_ids else {}
                    ),
                    **(
                        {"load_address": payload.load_address}
                        if payload.load_address is not None else {}
                    ),
                }
                for payload in item.payloads
            ]
        return value

    path.write_text(
        json.dumps(
            {
                "schema": "retrolarium.game-profile",
                "schema_version": 1,
                "id": profile.game_id,
                "profile_version": profile.profile_version,
                "name": profile.name,
                "platform": profile.platform,
                "capabilities": list(profile.capabilities),
                "revisions": [revision_value(item) for item in profile.revisions],
                "resources": [resource_value(item) for item in profile.resources],
            },
            indent=2,
        ),
        encoding="utf-8",
    )


class NesResourceTests(unittest.TestCase):
    def test_castle_fixed_text_and_sfx_records_round_trip(self) -> None:
        source = bytearray(fixture_image())
        source[0x200:0x20A] = bytes(range(10))
        source[0x220:0x228] = bytes(range(0x20, 0x28))
        image = bytes(source)
        base, old_revision = fixture_profile(fixture_image())
        revision = replace(
            old_revision,
            sha1=hashlib.sha1(image).hexdigest(),
            sha256=hashlib.sha256(image).hexdigest(),
        )
        text = ResourceSpec(
            "sub-command-text", "text", "castle.sub-command-text.v1", "fixed", {
                "file_offset": 0x200,
                "size": 10,
                "record_names": ["title", "load"],
                "record_sizes": [6, 4],
            },
        )
        sfx = ResourceSpec(
            "sfx-parameters", "audio-parameter",
            "castle.sfx-parameters.v1", "fixed", {
                "file_offset": 0x220,
                "size": 8,
                "record_names": ["triangle", "noise"],
                "record_sizes": [4, 4],
            },
        )
        marker = replace(base.resources[0], parameters={
            "resource_ids": ["sub-command-text", "sfx-parameters"]
        })
        profile = replace(
            base, revisions=(revision,), resources=(marker, text, sfx)
        )

        document = decode_resources(image, profile, revision)
        raw = document.as_dict()
        self.assertEqual(raw["resources"][0]["records"], [
            {"id": "title", "values": [0, 1, 2, 3, 4, 5]},
            {"id": "load", "values": [6, 7, 8, 9]},
        ])
        self.assertEqual(raw["resources"][1]["records"][1], {
            "id": "noise", "values": [0x24, 0x25, 0x26, 0x27],
        })
        self.assertEqual(inspection_details(document)["fixed_byte_records"], 4)
        self.assertEqual(inspection_details(document)["fixed_byte_values"], 18)
        exact, report = build_resources(image, document, profile, revision)
        self.assertEqual(exact, image)
        self.assertEqual(report["changed_bytes"], 0)

        raw["resources"][0]["records"][1]["values"][2] = 0xAA
        raw["resources"][1]["records"][0]["values"][3] = 0xBB
        parsed = parse_resource_document(raw, profile, revision)
        rebuilt, report = build_resources(image, parsed, profile, revision)
        self.assertEqual(rebuilt[0x208], 0xAA)
        self.assertEqual(rebuilt[0x223], 0xBB)
        self.assertEqual(report["changed_bytes"], 2)

        invalid = document.as_dict()
        invalid["resources"][0]["records"][0]["values"].pop()
        with self.assertRaisesRegex(NesResourceError, "must contain 6 bytes"):
            parse_resource_document(invalid, profile, revision)

    def test_castle_metasprites_round_trip_tile_and_oam_attributes(self) -> None:
        source = bytearray(fixture_image())
        source[0x180:0x188] = bytes((0x10, 0x00, 0x12, 0x40,
                                             0x14, 0x81, 0x16, 0xC3))
        image = bytes(source)
        base, old_revision = fixture_profile(fixture_image())
        revision = replace(
            old_revision,
            sha1=hashlib.sha1(image).hexdigest(),
            sha256=hashlib.sha256(image).hexdigest(),
        )
        metasprites = ResourceSpec(
            "metasprite-descriptors", "animation",
            "castle.metasprite-descriptors.v1", "fixed", {
                "file_offset": 0x180,
                "size": 8,
                "descriptor_count": 4,
            },
        )
        marker = replace(base.resources[0], parameters={
            "resource_ids": ["metasprite-descriptors"]
        })
        profile = replace(
            base, revisions=(revision,), resources=(marker, metasprites)
        )

        document = decode_resources(image, profile, revision)
        self.assertEqual(document.resources[0]["descriptors"], [
            {"index": 0, "tile": 0x10, "attributes": 0x00},
            {"index": 1, "tile": 0x12, "attributes": 0x40},
            {"index": 2, "tile": 0x14, "attributes": 0x81},
            {"index": 3, "tile": 0x16, "attributes": 0xC3},
        ])
        self.assertEqual(
            inspection_details(document)["metasprite_descriptors"], 4
        )
        exact, report = build_resources(image, document, profile, revision)
        self.assertEqual(exact, image)
        self.assertEqual(report["changed_bytes"], 0)

        edited = document.as_dict()
        edited["resources"][0]["descriptors"][2].update({
            "tile": 0x2A, "attributes": 0xC2,
        })
        parsed = parse_resource_document(edited, profile, revision)
        rebuilt, report = build_resources(image, parsed, profile, revision)
        self.assertEqual(rebuilt[0x184:0x186], bytes((0x2A, 0xC2)))
        self.assertEqual(report["changed_bytes"], 2)

    def test_castle_room_cells_round_trip_and_edit_three_rom_tables(self) -> None:
        source = bytearray(fixture_image())
        source[0x100:0x104] = bytes((1, 2, 3, 4))
        source[0x110:0x114] = bytes((5, 6, 7, 8))
        source[0x120:0x124] = bytes((0x00, 0x55, 0xAA, 0xFF))
        image = bytes(source)
        base, old_revision = fixture_profile(fixture_image())
        revision = replace(
            old_revision,
            sha1=hashlib.sha1(image).hexdigest(),
            sha256=hashlib.sha256(image).hexdigest(),
        )
        room_cells = ResourceSpec(
            "room-cell-visuals", "metatile", "castle.room-cell-visuals.v1",
            "fixed", {
                "cell_count": 4,
                "left_file_offset": 0x100,
                "right_file_offset": 0x110,
                "attributes_file_offset": 0x120,
            },
        )
        marker = replace(base.resources[0], parameters={
            "resource_ids": ["room-cell-visuals"]
        })
        profile = replace(
            base, revisions=(revision,), resources=(marker, room_cells)
        )

        document = decode_resources(image, profile, revision)
        self.assertEqual(document.resources[0]["records"], [
            {"index": 0, "left_tile": 1, "right_tile": 5, "palette": 0},
            {"index": 1, "left_tile": 2, "right_tile": 6, "palette": 1},
            {"index": 2, "left_tile": 3, "right_tile": 7, "palette": 2},
            {"index": 3, "left_tile": 4, "right_tile": 8, "palette": 3},
        ])
        self.assertEqual(inspection_details(document)["room_cells"], 4)
        exact, report = build_resources(image, document, profile, revision)
        self.assertEqual(exact, image)
        self.assertEqual(report["changed_bytes"], 0)

        edited = document.as_dict()
        edited["resources"][0]["records"][2].update({
            "left_tile": 0x2A, "right_tile": 0x3B, "palette": 1,
        })
        parsed = parse_resource_document(edited, profile, revision)
        rebuilt, report = build_resources(image, parsed, profile, revision)
        self.assertEqual(rebuilt[0x102], 0x2A)
        self.assertEqual(rebuilt[0x112], 0x3B)
        self.assertEqual(rebuilt[0x122], 0x55)
        self.assertEqual(report["changed_bytes"], 3)

        invalid = bytearray(image)
        invalid[0x121] = 0x01
        invalid_revision = replace(
            revision,
            sha1=hashlib.sha1(invalid).hexdigest(),
            sha256=hashlib.sha256(invalid).hexdigest(),
        )
        invalid_profile = replace(profile, revisions=(invalid_revision,))
        with self.assertRaisesRegex(NesResourceError, "not replicated"):
            decode_resources(bytes(invalid), invalid_profile, invalid_revision)

    def test_scoped_bundle_coexists_with_another_authoring_adapter(self) -> None:
        image = fixture_image()
        base, revision = fixture_profile(image)
        marker = replace(
            base.resources[0], parameters={"resource_ids": ["graphics"]}
        )
        profile = replace(
            base,
            resources=(
                ResourceSpec(
                    "rooms", "room-collection", "castle.prefix-rooms.v1", "fixed"
                ),
                marker,
                *base.resources[1:],
            ),
        )
        document = decode_resources(image, profile, revision)
        self.assertEqual(["graphics"], [item["id"] for item in document.resources])

    def test_composed_build_merges_disjoint_edits_and_rejects_conflicts(self) -> None:
        image = fixture_image()
        profile, revision = fixture_profile(image)

        class Adapter:
            def __init__(self, resource_id: str, offset: int, value: int) -> None:
                self.resource_id = resource_id
                self.offset = offset
                self.value = value

            @staticmethod
            def parse(value, _profile, _revision):
                return value

            def build(self, payload, _document, _profile, _revision):
                rebuilt = bytearray(payload)
                rebuilt[self.offset] = self.value
                return bytes(rebuilt), {"changed_bytes": 1}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.nes"
            first = root / "first.json"
            second = root / "second.json"
            output = root / "edited.nes"
            source.write_bytes(image)
            first.write_text('{"schema":"first"}', encoding="utf-8")
            second.write_text('{"schema":"second"}', encoding="utf-8")
            adapters = {
                "first": Adapter("first", 16, 7),
                "second": Adapter("second", 17, 8),
            }
            with patch.object(
                games_module, "_read_image", return_value=(image, profile, revision)
            ), patch.object(
                games_module,
                "_adapter_for_document",
                side_effect=lambda value, _profile, _revision: adapters[value["schema"]],
            ):
                result = build_game_documents(source, [first, second], output)
                self.assertEqual(2, result["changed_bytes"])
                self.assertEqual(7, output.read_bytes()[16])
                self.assertEqual(8, output.read_bytes()[17])

                adapters["second"] = Adapter("second", 16, 9)
                output.unlink()
                with self.assertRaisesRegex(AuthoringError, "conflict.*0x10"):
                    build_game_documents(source, [first, second], output)
                self.assertFalse(output.exists())

    def test_smb_player_frames_flow_through_resource_pipeline(self) -> None:
        source = bytearray(fixture_image())
        offsets = bytes(index * TILES_PER_FRAME for index in range(16))
        frames = bytes(
            (frame * TILES_PER_FRAME + tile) & 0xFF
            for frame in range(FRAME_COUNT)
            for tile in range(TILES_PER_FRAME)
        )
        payload = offsets + frames + bytes((0x31, 0x46))
        player_offset = 0x100
        source[player_offset:player_offset + len(payload)] = payload
        image = bytes(source)
        base_profile, base_revision = fixture_profile(fixture_image())
        revision = replace(
            base_revision,
            sha1=hashlib.sha1(image).hexdigest(),
            sha256=hashlib.sha256(image).hexdigest(),
        )
        player_resource = ResourceSpec(
            "player-frames", "animation", "smb.player-frames.v1", "fixed",
            {
                "file_offset": player_offset,
                "size": len(payload),
                "tile_base": 0,
                "blank_tile": 0xFC,
            },
        )
        profile = replace(
            base_profile,
            revisions=(revision,),
            resources=(*base_profile.resources, player_resource),
        )

        document = decode_resources(image, profile, revision)
        self.assertEqual(26, len(document.resources[-1]["frames"]))
        self.assertEqual(26, inspection_details(document)["actor_frames"])
        exact, report = build_resources(image, document, profile, revision)
        self.assertEqual(image, exact)
        self.assertEqual(0, report["changed_bytes"])

        edited = document.as_dict()
        edited["resources"][-1]["frames"][4][3] = 0xEA
        parsed = parse_resource_document(edited, profile, revision)
        rebuilt, report = build_resources(image, parsed, profile, revision)
        changed_offset = player_offset + 16 + 4 * 8 + 3
        self.assertEqual(0xEA, rebuilt[changed_offset])
        self.assertEqual(1, report["changed_bytes"])
        rebuilt_revision = replace(
            revision,
            sha1=hashlib.sha1(rebuilt).hexdigest(),
            sha256=hashlib.sha256(rebuilt).hexdigest(),
        )
        rebuilt_profile = replace(profile, revisions=(rebuilt_revision,))
        reopened = decode_resources(rebuilt, rebuilt_profile, rebuilt_revision)
        self.assertEqual(0xEA, reopened.resources[-1]["frames"][4][3])

    def test_packaged_honkeypong_profile_has_open_fixture_identity(self) -> None:
        profile = packaged_game_profiles().get("honkeypong")
        self.assertEqual(profile.revisions[0].sha256, "cce7bf2f71eb9e63de0dba15098679dde06a1da9d7553f2319aa60ea57a1193b")
        self.assertEqual(profile.revisions[0].mapper, 0)
        self.assertEqual(
            [item.codec for item in profile.resources],
            [
                "nes.resource-bundle.v1",
                "nes.palette-table.v1",
                "nes.palette-table.v1",
                "nes.palette-table.v1",
                "nes.nametable.v1",
                "nes.chr-2bpp.v1",
            ],
        )

    def test_typed_resources_round_trip_and_edit_transactionally(self) -> None:
        image = fixture_image()
        profile, revision = fixture_profile(image)
        document = decode_resources(image, profile, revision)
        raw = document.as_dict()
        self.assertEqual(raw["resources"][0]["values"], [0x0F, 1, 0x11, 0x21])
        self.assertEqual(raw["resources"][0]["palette_model"], "rp2c04_0004")
        self.assertEqual(raw["resources"][1]["tiles"], [[1, 2], [3, 4]])
        self.assertEqual(raw["resources"][2]["tiles"][0][0][0], 1)

        exact, exact_report = build_resources(image, document, profile, revision)
        self.assertEqual(exact, image)
        self.assertEqual(exact_report["changed_bytes"], 0)

        edited = copy.deepcopy(raw)
        edited["resources"][0]["values"][1] = 0x02
        edited["resources"][1]["tiles"][1][1] = 9
        edited["resources"][2]["tiles"][0][0][0] = 3
        parsed = parse_resource_document(edited, profile, revision)
        rebuilt, report = build_resources(image, parsed, profile, revision)
        self.assertEqual(rebuilt[17], 0x02)
        self.assertEqual(rebuilt[23], 9)
        self.assertEqual(rebuilt[0x4010], 0x80)
        self.assertEqual(rebuilt[0x4018], 0x80)
        self.assertEqual(report["changed_bytes"], 3)

    def test_document_rejects_missing_resources_and_invalid_chr_pixels(self) -> None:
        image = fixture_image()
        profile, revision = fixture_profile(image)
        raw = decode_resources(image, profile, revision).as_dict()
        missing = copy.deepcopy(raw)
        missing["resources"].pop()
        with self.assertRaisesRegex(NesResourceError, "wrong resource count"):
            parse_resource_document(missing, profile, revision)
        invalid = copy.deepcopy(raw)
        invalid["resources"][2]["tiles"][0][0][0] = 4
        with self.assertRaisesRegex(NesResourceError, "must be <= 3"):
            parse_resource_document(invalid, profile, revision)
        extra = copy.deepcopy(raw)
        extra["resources"][0]["undocumented"] = True
        with self.assertRaisesRegex(NesResourceError, "fields differ"):
            parse_resource_document(extra, profile, revision)
        wrong_model = copy.deepcopy(raw)
        wrong_model["resources"][0]["palette_model"] = "nes"
        with self.assertRaisesRegex(NesResourceError, "differs from the profile"):
            parse_resource_document(wrong_model, profile, revision)

    def test_public_operations_route_through_second_adapter(self) -> None:
        image = fixture_image()
        profile, _revision = fixture_profile(image)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rom = root / "probe.nes"
            profile_path = root / "profile.json"
            document_path = root / "resources.json"
            output = root / "edited.nes"
            rom.write_bytes(image)
            write_profile(profile_path, profile)
            inspected = inspect_game(rom, custom_profile=profile_path)
            self.assertEqual(inspected["details"]["screens"], 1)
            self.assertEqual(inspected["details"]["chr_tiles"], 1)
            extracted = extract_game(
                rom, document_path, custom_profile=profile_path
            )
            self.assertEqual(extracted["resource"], "resource-bundle")
            self.assertEqual(extracted["resource_count"], 3)
            self.assertEqual(
                validate_game(rom, document_path, custom_profile=profile_path)[
                    "status"
                ],
                "valid",
            )
            result = build_game(
                rom, document_path, output, custom_profile=profile_path
            )
            self.assertEqual(result["schema"], "retrolarium.nes-resource-build")
            self.assertEqual(output.read_bytes(), image)

    def test_revision_overrides_drive_capabilities_and_resource_offsets(self) -> None:
        image = fixture_image()
        base_profile, base_revision = fixture_profile(image)
        wrong_palette = replace(
            base_profile.resources[1],
            parameters={"file_offset": 24, "size": 4, "entries": 4},
        )
        revision = replace(
            base_revision,
            capabilities=("revision-palettes",),
            resource_overrides=(ResourceOverrideSpec(
                "palette", {"file_offset": 16}
            ),),
        )
        profile = replace(
            base_profile,
            capabilities=("default-capability",),
            revisions=(revision,),
            resources=(base_profile.resources[0], wrong_palette, *base_profile.resources[2:]),
        )

        self.assertEqual(profile.capabilities_for(revision), ("revision-palettes",))
        self.assertEqual(
            profile.resources_for(revision)[1].parameters["file_offset"], 16
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rom = root / "probe.nes"
            profile_path = root / "profile.json"
            document_path = root / "resources.json"
            rom.write_bytes(image)
            write_profile(profile_path, profile)

            inspected = inspect_game(rom, custom_profile=profile_path)
            self.assertEqual(inspected["capabilities"], ["revision-palettes"])
            extract_game(rom, document_path, custom_profile=profile_path)
            extracted = json.loads(document_path.read_text(encoding="utf-8"))
            self.assertEqual(extracted["resources"][0]["values"], [15, 1, 17, 33])

    def test_revision_defaults_and_override_identity_are_strict(self) -> None:
        image = fixture_image()
        profile, revision = fixture_profile(image)
        self.assertEqual(profile.capabilities_for(revision), profile.capabilities)
        self.assertEqual(profile.resources_for(revision), profile.resources)
        with self.assertRaisesRegex(AuthoringError, "unknown revision"):
            profile.resources_for("missing")
        with self.assertRaisesRegex(AuthoringError, "capabilities must be unique"):
            replace(revision, capabilities=("levels", "levels"))
        with self.assertRaisesRegex(AuthoringError, "resource ids must be unique"):
            replace(
                revision,
                resources=(profile.resources[0], profile.resources[0]),
            )
        with self.assertRaisesRegex(AuthoringError, "mutually exclusive"):
            replace(
                revision,
                resources=profile.resources,
                resource_overrides=(ResourceOverrideSpec("palette"),),
            )
        with self.assertRaisesRegex(AuthoringError, "overrides unknown resource"):
            replace(
                profile,
                revisions=(replace(
                    revision,
                    resource_overrides=(ResourceOverrideSpec("missing"),),
                ),),
            )
        payload = PayloadSpec(
            "prg", "range", 16, hashlib.sha1(bytes(16)).hexdigest(),
            file_offset=16, load_address=0x8000,
        )
        payload_resource = replace(profile.resources[1], payload="prg")
        payload_revision = replace(
            revision, payloads=(payload,),
            resources=(profile.resources[0], payload_resource),
        )
        payload_profile = replace(profile, revisions=(payload_revision,))
        self.assertEqual(
            payload_profile.payload_for(payload_revision, "prg"), payload
        )
        with self.assertRaisesRegex(AuthoringError, "payload ids must be unique"):
            replace(payload_revision, payloads=(payload, payload))
        with self.assertRaisesRegex(AuthoringError, "unknown resource payload"):
            replace(
                profile,
                resources=(profile.resources[0], payload_resource),
            )

    def test_pc10_container_preserves_its_instruction_rom(self) -> None:
        core = fixture_image()
        instruction_rom = bytes((index & 0xFF for index in range(0x2000)))
        image = core + instruction_rom
        base_profile, _base_revision = fixture_profile(core)
        revision = RevisionSpec(
            "pc10", "PC10 probe", len(image), hashlib.sha1(image).hexdigest(),
            hashlib.sha256(image).hexdigest(), "ines-pc10", 0,
        )
        profile = replace(base_profile, revisions=(revision,))

        document = decode_resources(image, profile, revision)
        rebuilt, report = build_resources(image, document, profile, revision)
        self.assertEqual(report["changed_bytes"], 0)
        self.assertEqual(rebuilt, image)
        self.assertEqual(rebuilt[-0x2000:], instruction_rom)

        malformed = image[:-1]
        malformed_revision = replace(
            revision,
            size=len(malformed),
            sha1=hashlib.sha1(malformed).hexdigest(),
            sha256=hashlib.sha256(malformed).hexdigest(),
        )
        with self.assertRaisesRegex(NesResourceError, "8192-byte"):
            decode_resources(malformed, profile, malformed_revision)

    def test_vs_container_uses_its_revision_specific_graphics(self) -> None:
        source = bytearray(b"NES\x1a" + bytes(28))
        source[24:40] = bytes((0x80,)) + bytes(15)
        image = bytes(source)
        revision_resources = (
            ResourceSpec(
                "bundle", "resource-bundle", "nes.resource-bundle.v1", "fixed"
            ),
            ResourceSpec(
                "graphics", "graphics", "nes.chr-2bpp.v1", "fixed",
                {"file_offset": 24, "size": 16},
            ),
        )
        revision = RevisionSpec(
            "vs_smb", "Vs. probe", len(image), hashlib.sha1(image).hexdigest(),
            hashlib.sha256(image).hexdigest(), "ines-vs",
            capabilities=("graphics",),
            resources=(
                revision_resources[0],
                replace(
                    revision_resources[1],
                    parameters={"file_offset": 0, "size": 16},
                    payload="chr",
                ),
            ),
            payloads=(
                PayloadSpec(
                    "chr", "range", 16,
                    hashlib.sha1(image[24:40]).hexdigest(), file_offset=24,
                ),
            ),
        )
        base_profile, _base_revision = fixture_profile(fixture_image())
        profile = replace(base_profile, revisions=(revision,))

        document = decode_resources(image, profile, revision)
        self.assertEqual(len(document.resources), 1)
        self.assertEqual(document.resources[0]["id"], "graphics")
        rebuilt, report = build_resources(image, document, profile, revision)
        self.assertEqual(report["changed_bytes"], 0)
        self.assertEqual(rebuilt, image)


if __name__ == "__main__":
    unittest.main()
