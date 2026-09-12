from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from retrolarium_kit.authoring import (
    GameProfile,
    PayloadSpec,
    ResourceSpec,
    RevisionSpec,
)
from retrolarium_kit.fds import fds_payload
from retrolarium_kit.game_profiles import packaged_game_profiles
from retrolarium_kit.games import profile_catalog
from retrolarium_kit.nes_resources import (
    build_resources,
    decode_resources,
    parse_resource_document,
)
from retrolarium_kit.smb_world import decode_world, encode_world


PARAMETERS = {
    "routing_file_offset": 32,
    "routing_size": 4,
    "world_lengths": [2, 2],
    "physics_file_offset": 64,
    "physics_size": 5,
    "physics_tables": [
        {"name": "gravity", "length": 2, "signed": False},
        {"name": "speed", "length": 3, "signed": True},
    ],
}


def fixture() -> tuple[bytes, GameProfile, RevisionSpec]:
    header = b"NES\x1a" + bytes((2, 1, 0, 0)) + bytes(8)
    prg = bytearray(0x8000)
    prg[16:20] = bytes((0x25, 0xC1, 0x43, 0x7F))
    prg[48:53] = bytes((0x20, 0x70, 0xFC, 0x00, 0x28))
    image = header + bytes(prg) + bytes(0x2000)
    revision = RevisionSpec(
        "probe-v1", "SMB world probe", len(image),
        hashlib.sha1(image).hexdigest(), hashlib.sha256(image).hexdigest(),
        "ines", 0,
    )
    profile = GameProfile(
        "smb-world-probe", 1, "SMB world probe", "nes", ("world",),
        (revision,),
        (
            ResourceSpec(
                "bundle", "resource-bundle", "nes.resource-bundle.v1", "fixed"
            ),
            ResourceSpec("world", "world", "smb.world.v1", "fixed", PARAMETERS),
        ),
    )
    return image, profile, revision


def multi_payload_fds_fixture() -> tuple[bytes, GameProfile, RevisionSpec]:
    payloads = {
        5: bytes((0x20, 0x70, 0xFC, 0x00, 0x28)),
        64: bytes((0x25, 0xC1, 0x43, 0x7F)),
    }
    image = bytearray(65_500)
    image[0] = 1
    image[56:58] = bytes((2, len(payloads)))
    position = 58
    for number, (identifier, payload) in enumerate(payloads.items()):
        header = bytearray(16)
        header[0:3] = bytes((3, number, identifier))
        header[3:11] = f"WORLD{number:03d}".encode("ascii")
        header[11:13] = (0x6000 + number * 0x6000).to_bytes(2, "little")
        header[13:15] = len(payload).to_bytes(2, "little")
        image[position:position + 16] = header
        position += 16
        image[position] = 4
        position += 1
        image[position:position + len(payload)] = payload
        position += len(payload)
    source = bytes(image)
    revision = RevisionSpec(
        "fds", "SMB split world probe", len(source),
        hashlib.sha1(source).hexdigest(), hashlib.sha256(source).hexdigest(),
        "fds",
        payloads=(
            PayloadSpec(
                "prg", "fds-records", len(payloads[5]),
                hashlib.sha1(payloads[5]).hexdigest(), record_ids=(5,),
            ),
            PayloadSpec(
                "nsmdata4", "fds-records", len(payloads[64]),
                hashlib.sha1(payloads[64]).hexdigest(), record_ids=(64,),
            ),
        ),
    )
    parameters = {
        "routing_size": 4,
        "routing_span": {"payload": "nsmdata4", "offset": 0, "size": 4},
        "world_lengths": [2, 2],
        "physics_size": 5,
        "physics_span": {"payload": "prg", "offset": 0, "size": 5},
        "physics_tables": [
            {"name": "gravity", "length": 2, "signed": False},
            {"name": "speed", "length": 3, "signed": True},
        ],
    }
    profile = GameProfile(
        "smb-world-fds-probe", 1, "SMB split world probe", "nes",
        ("world",), (revision,),
        (
            ResourceSpec(
                "bundle", "resource-bundle", "nes.resource-bundle.v1", "fixed"
            ),
            ResourceSpec("world", "world", "smb.world.v1", "fixed", parameters),
        ),
    )
    return source, profile, revision


class SmbWorldTests(unittest.TestCase):
    def test_codec_round_trips_and_exposes_signed_values(self) -> None:
        value = decode_world(
            bytes((0x25, 0xC1, 0x43, 0x7F)),
            bytes((0x20, 0x70, 0xFC, 0x00, 0x28)),
            PARAMETERS,
        )
        self.assertEqual(value["area_types"], ["water", "ground", "underground", "castle"])
        self.assertEqual(value["worlds"][0]["areas"][0]["area_type"], 1)
        self.assertTrue(value["worlds"][0]["areas"][1]["alternate_bit"])
        self.assertEqual(value["physics_tables"][1]["values"], [-4, 0, 40])
        self.assertEqual(
            encode_world(value, PARAMETERS),
            (bytes((0x25, 0xC1, 0x43, 0x7F)), bytes((0x20, 0x70, 0xFC, 0x00, 0x28))),
        )

    def test_bundle_updates_both_ranges_transactionally(self) -> None:
        image, profile, revision = fixture()
        document = decode_resources(image, profile, revision)
        exact, report = build_resources(image, document, profile, revision)
        self.assertEqual(exact, image)
        self.assertEqual(report["changed_bytes"], 0)
        self.assertEqual(report["resources"][0]["size"], 9)

        raw = copy.deepcopy(document.as_dict())
        world = raw["resources"][0]
        world["worlds"][0]["areas"][0]["area_index"] = 7
        world["physics_tables"][1]["values"][0] = -5
        parsed = parse_resource_document(raw, profile, revision)
        edited, edited_report = build_resources(image, parsed, profile, revision)
        self.assertEqual(edited[32], 0x27)
        self.assertEqual(edited[64 + 2], 0xFB)
        self.assertEqual(edited_report["changed_bytes"], 2)

    def test_bundle_splits_routing_and_physics_across_fds_payloads(self) -> None:
        image, profile, revision = multi_payload_fds_fixture()
        document = decode_resources(image, profile, revision)
        self.assertEqual(
            document.resources[0]["worlds"][0]["areas"][0]["area_index"], 5
        )
        self.assertEqual(
            document.resources[0]["physics_tables"][1]["values"][0], -4
        )
        exact, report = build_resources(image, document, profile, revision)
        self.assertEqual(exact, image)
        self.assertEqual(report["changed_bytes"], 0)

        raw = copy.deepcopy(document.as_dict())
        world = raw["resources"][0]
        world["worlds"][0]["areas"][0]["area_index"] = 7
        world["physics_tables"][1]["values"][0] = -5
        parsed = parse_resource_document(raw, profile, revision)
        edited, report = build_resources(image, parsed, profile, revision)
        self.assertEqual(fds_payload(edited, (64,))[0], 0x27)
        self.assertEqual(fds_payload(edited, (5,))[2], 0xFB)
        self.assertEqual(report["changed_bytes"], 2)

    def test_invalid_shape_and_ranges_are_rejected(self) -> None:
        image, profile, revision = fixture()
        raw = decode_resources(image, profile, revision).as_dict()
        raw["resources"][0]["physics_tables"][1]["values"][0] = -129
        with self.assertRaisesRegex(Exception, "-128..127"):
            parse_resource_document(raw, profile, revision)

        raw = decode_resources(image, profile, revision).as_dict()
        raw["resources"][0]["worlds"][0]["areas"].pop()
        with self.assertRaisesRegex(Exception, "must contain 2 routes"):
            parse_resource_document(raw, profile, revision)

    def test_packaged_profile_matches_verified_ju_and_pal_identities(self) -> None:
        profile = packaged_game_profiles().get("smb1")
        self.assertEqual(
            [revision.revision_id for revision in profile.revisions],
            [
                "ju", "pal", "pc10", "vs_smb", "fds_smb", "ann_fds",
                "smb2_jp_fds",
            ],
        )
        self.assertEqual(
            profile.revisions[0].sha256,
            "f61548fdf1670cffefcc4f0b7bdcdd9eaba0c226e3b74f8666071496988248de",
        )
        self.assertEqual(
            profile.revisions[1].sha256,
            "ec299b990e8bfee8ba46e3f61d63b2e1ae5b8a2e431de84e2e4bbd692dc53586",
        )
        ju_resources = {
            item.resource_id: item for item in profile.resources_for("ju")
        }
        pal_resources = {
            item.resource_id: item for item in profile.resources_for("pal")
        }
        pc10_resources = {
            item.resource_id: item for item in profile.resources_for("pc10")
        }
        self.assertEqual(
            ju_resources["player-frames"].parameters["file_offset"], 0x6E17
        )
        self.assertEqual(
            pal_resources["player-frames"].parameters["file_offset"], 0x6E1E
        )
        self.assertEqual(
            pc10_resources["player-frames"].parameters["file_offset"], 0x6E17
        )
        for resources in (ju_resources, pal_resources, pc10_resources):
            self.assertEqual(
                resources["player-palettes"].parameters["file_offset"], 0x5E7
            )
            self.assertEqual(
                resources["player-palettes"].parameters["entries"], 12
            )
            self.assertEqual(
                resources["player-palettes"].parameters["palette_model"], "nes"
            )
            self.assertEqual(
                [
                    field["file_offset"]
                    for field in resources["ui-text"].parameters["fields"]
                ],
                [0x765, 0x76D, 0x796, 0x7B3, 0x7C6, 0x7D3, 0x7FD],
            )
            self.assertEqual(
                resources["ui-text"].parameters["encoding"]["padding"], 0x24
            )
        self.assertEqual(
            ju_resources["player-frames"].parameters["size"], 226
        )
        self.assertEqual(
            profile.revisions[2].sha256,
            "f586afeaaaa7a6cf2e3b69e5b17f2f0f1eb3fd9aa1de1e3e14b75f90dc21ae87",
        )
        self.assertEqual(profile.revisions[2].container, "ines-pc10")
        self.assertEqual(
            profile.revisions[3].sha256,
            "60098a93322a9e07a2b164a02e5517bc22664ca33f6323c2ae23a62941ad393b",
        )
        self.assertEqual(
            profile.capabilities_for("vs_smb"),
            ("world", "levels", "graphics", "text", "audio"),
        )
        vs_resources = {
            resource.resource_id: resource
            for resource in profile.resources_for("vs_smb")
        }
        self.assertEqual(vs_resources["world"].payload, "prg")
        self.assertEqual(
            vs_resources["world"].parameters["routing_file_offset"], 0x2C7F
        )
        self.assertEqual(vs_resources["levels"].payload, "chr")
        self.assertEqual(len(vs_resources["levels"].parameters["stream_names"]), 39)
        self.assertEqual(
            sum(vs_resources["levels"].parameters["area_capacities"]), 4439
        )
        self.assertEqual(
            sum(vs_resources["levels"].parameters["enemy_capacities"]), 1368
        )
        self.assertEqual(
            vs_resources["area-palette-packets"].parameters["day_background"],
            0x1A,
        )
        self.assertEqual(
            vs_resources["area-palette-packets"].parameters["palette_model"],
            "rp2c04_0004",
        )
        self.assertEqual(vs_resources["player-palettes"].payload, "prg")
        self.assertEqual(
            vs_resources["player-palettes"].parameters["file_offset"], 0x7B8
        )
        self.assertEqual(
            vs_resources["player-palettes"].parameters["palette_model"],
            "rp2c04_0004",
        )
        self.assertEqual(vs_resources["ui-text"].payload, "prg")
        self.assertEqual(
            [
                field["file_offset"]
                for field in vs_resources["ui-text"].parameters["fields"]
            ],
            [0x951, 0x959, 0x982, 0x99F, 0x9B2, 0x9BF, 0x9E9],
        )
        self.assertEqual(vs_resources["player-frames"].payload, "prg")
        self.assertEqual(
            vs_resources["player-frames"].parameters["file_offset"], 0x6D64
        )
        self.assertEqual(vs_resources["music"].payload, "prg")
        self.assertEqual(vs_resources["music"].parameters["file_offset"], 0x784B)
        self.assertEqual(vs_resources["music"].parameters["cpu_address"], 0xF858)
        self.assertEqual(len(vs_resources["music"].parameters["headers"]), 27)
        self.assertEqual(
            [item.get("location", "trailing")
             for item in vs_resources["music"].parameters["envelopes"]],
            ["leading", "leading", "trailing"],
        )
        self.assertEqual(
            profile.revisions[4].sha256,
            "56ba4c39fd7f9628bffac4c6ee0d5364f86de36d27c904ffc70f0abc97ae0d3a",
        )
        self.assertEqual(
            profile.capabilities_for("fds_smb"),
            ("world", "levels", "graphics", "text", "audio"),
        )
        fds_resources = {
            item.resource_id: item for item in profile.resources_for("fds_smb")
        }
        self.assertEqual(fds_resources["world"].payload, "prg")
        self.assertEqual(
            fds_resources["world"].parameters["routing_file_offset"], 0x1CC4
        )
        self.assertEqual(
            fds_resources["world"].parameters["physics_file_offset"], 0x342C
        )
        self.assertEqual(fds_resources["levels"].payload, "prg")
        self.assertEqual(
            fds_resources["levels"].parameters["enemy_file_offset"], 0x1D78
        )
        self.assertEqual(
            fds_resources["levels"].parameters["area_file_offset"], 0x21B7
        )
        self.assertEqual(fds_resources["player-palettes"].payload, "prg")
        self.assertEqual(
            fds_resources["player-palettes"].parameters["file_offset"], 0x5DD
        )
        self.assertEqual(fds_resources["ui-text"].payload, "prg")
        self.assertEqual(
            [
                field["file_offset"]
                for field in fds_resources["ui-text"].parameters["fields"]
            ],
            [0x75B, 0x763, 0x78C, 0x7A9, 0x7BC, 0x7C9, 0x7F3],
        )
        self.assertEqual(fds_resources["player-frames"].payload, "prg")
        self.assertEqual(
            fds_resources["player-frames"].parameters["file_offset"], 0x6E0B
        )
        self.assertEqual(fds_resources["music"].payload, "prg")
        self.assertEqual(fds_resources["music"].parameters["file_offset"], 0x790D)
        self.assertEqual(fds_resources["music"].parameters["cpu_address"], 0xD90D)
        self.assertEqual(fds_resources["character-graphics"].payload, "chr")
        self.assertEqual(profile.payload_for("fds_smb", "chr").record_ids, (2,))
        self.assertEqual(profile.payload_for("fds_smb", "prg").record_ids, (3, 4))
        self.assertEqual(
            profile.revisions[5].sha256,
            "a762d0a90d16c84b5f2b014816b37675cf5115320be7e815effbb64d91b2014d",
        )
        self.assertEqual(
            profile.revisions[6].sha256,
            "ff8afb9ae6b705b4e51dbcb193dcebadf4c049800a71003d3a45052648e52eda",
        )
        ann_resources = {
            item.resource_id: item for item in profile.resources_for("ann_fds")
        }
        self.assertEqual(
            profile.capabilities_for("ann_fds"),
            ("world", "levels", "graphics", "screens", "text"),
        )
        self.assertEqual(
            list(ann_resources),
            [
                "authoring-bundle", "world-normal", "levels-normal",
                "world-extended", "levels-extended", "background-metatiles",
                "area-palette-packets", "player-palettes", "player-frames",
                "title-screen", "character-graphics",
            ],
        )
        self.assertEqual(
            ann_resources["world-normal"].parameters["routing_span"],
            {"payload": "prg", "offset": 0x6341, "size": 36},
        )
        self.assertEqual(
            ann_resources["world-extended"].parameters["routing_span"],
            {"payload": "nsmdata4", "offset": 0xAB, "size": 18},
        )
        self.assertEqual(
            ann_resources["world-normal"].parameters["physics_tables"][2]["name"],
            "horizontal_friction",
        )
        normal_levels = ann_resources["levels-normal"].parameters
        self.assertEqual(normal_levels["world_resource"], "world-normal")
        self.assertEqual(len(normal_levels["stream_names"]), 44)
        self.assertEqual(normal_levels["stream_names"][-1], "water_4")
        self.assertEqual(
            normal_levels["enemy_stream_spans"][17],
            {"payload": "prg", "offset": 0x626F, "size": 1},
        )
        self.assertEqual(
            normal_levels["enemy_stream_spans"][17],
            normal_levels["enemy_stream_spans"][32],
        )
        extended_levels = ann_resources["levels-extended"].parameters
        self.assertEqual(extended_levels["world_resource"], "world-extended")
        self.assertEqual(len(extended_levels["stream_names"]), 21)
        self.assertEqual(
            extended_levels["area_stream_spans"][16:18],
            [
                {"payload": "prg", "offset": 0x6270, "size": 9},
                {"payload": "prg", "offset": 0x6279, "size": 13},
            ],
        )
        self.assertEqual(
            ann_resources["background-metatiles"].parameters["group_sizes"],
            [39, 47, 10, 6],
        )
        self.assertEqual(
            ann_resources["area-palette-packets"].parameters["file_offset"],
            0xC1F,
        )
        self.assertEqual(ann_resources["player-palettes"].payload, "prg")
        self.assertEqual(
            ann_resources["player-palettes"].parameters["file_offset"], 0x5BE
        )
        self.assertEqual(
            ann_resources["player-palettes"].parameters["entries"], 8
        )
        self.assertEqual(ann_resources["player-frames"].payload, "prg")
        self.assertEqual(
            ann_resources["player-frames"].parameters["file_offset"], 0x5AF2
        )
        self.assertEqual(ann_resources["title-screen"].payload, "prg")
        self.assertEqual(
            ann_resources["title-screen"].parameters["screens"],
            [{
                "key": "title", "label": "Title screen",
                "file_offset": 0x65E6, "size": 0x15F,
            }],
        )
        self.assertEqual(
            ann_resources["title-screen"].parameters["fill_tile"], 0x24
        )
        self.assertEqual(
            ann_resources["title-screen"].parameters["text_fields"],
            [
                {"key": "copyright", "label": "Copyright", "screen": "title",
                 "address": 0x21EF, "width": 13},
                {"key": "hero-game", "label": "Hero game", "screen": "title",
                 "address": 0x224D, "width": 10},
                {"key": "brother-game", "label": "Brother game", "screen": "title",
                 "address": 0x228D, "width": 10},
                {"key": "top-score", "label": "Top score", "screen": "title",
                 "address": 0x22ED, "width": 4},
            ],
        )
        self.assertEqual(ann_resources["character-graphics"].payload, "chr")
        smb2_resources = {
            item.resource_id: item
            for item in profile.resources_for("smb2_jp_fds")
        }
        self.assertEqual(
            profile.capabilities_for("smb2_jp_fds"),
            ("world", "levels", "graphics", "screens", "text"),
        )
        self.assertEqual(
            list(smb2_resources),
            [
                "authoring-bundle", "world-normal", "levels-normal",
                "world-hard", "levels-hard", "background-metatiles",
                "area-palette-packets", "player-palettes", "player-frames",
                "title-screen", "character-graphics",
            ],
        )
        self.assertEqual(
            smb2_resources["world-normal"].parameters["routing_span"],
            {"payload": "prg", "offset": 0x6360, "size": 40},
        )
        self.assertEqual(
            smb2_resources["world-hard"].parameters["routing_span"],
            {"payload": "sm2data4", "offset": 0xAC, "size": 18},
        )
        smb2_normal = smb2_resources["levels-normal"].parameters
        self.assertEqual(smb2_normal["world_resource"], "world-normal")
        self.assertEqual(len(smb2_normal["stream_names"]), 52)
        self.assertEqual(
            smb2_normal["area_stream_spans"][35:37],
            [
                {"payload": "sm2data3", "offset": 1406, "size": 73},
                {"payload": "sm2data3", "offset": 1407, "size": 139},
            ],
        )
        self.assertEqual(
            smb2_normal["enemy_stream_spans"][35],
            smb2_normal["enemy_stream_spans"][36],
        )
        self.assertEqual(
            smb2_normal["enemy_stream_spans"][36],
            smb2_normal["enemy_stream_spans"][49],
        )
        smb2_hard = smb2_resources["levels-hard"].parameters
        self.assertEqual(smb2_hard["world_resource"], "world-hard")
        self.assertEqual(len(smb2_hard["stream_names"]), 21)
        self.assertEqual(
            smb2_hard["enemy_stream_spans"][16],
            smb2_hard["enemy_stream_spans"][17],
        )
        self.assertEqual(
            smb2_resources["background-metatiles"].parameters["group_sizes"],
            [36, 48, 13, 7],
        )
        self.assertEqual(
            smb2_resources["area-palette-packets"].parameters["file_offset"],
            0xB8D,
        )
        self.assertEqual(smb2_resources["player-palettes"].payload, "prg")
        self.assertEqual(
            smb2_resources["player-palettes"].parameters["file_offset"], 0x537
        )
        self.assertEqual(
            smb2_resources["player-palettes"].parameters["entries"], 8
        )
        self.assertEqual(smb2_resources["player-frames"].payload, "prg")
        self.assertEqual(
            smb2_resources["player-frames"].parameters["file_offset"], 0x5AE2
        )
        self.assertEqual(smb2_resources["title-screen"].payload, "prg")
        self.assertEqual(
            smb2_resources["title-screen"].parameters["screens"],
            [{
                "key": "title", "label": "Title screen",
                "file_offset": 0x662B, "size": 0x165,
            }],
        )
        self.assertEqual(
            smb2_resources["title-screen"].parameters["text_fields"],
            [
                {"key": "copyright", "label": "Copyright", "screen": "title",
                 "address": 0x21F0, "width": 13},
                {"key": "hero-game", "label": "Hero game", "screen": "title",
                 "address": 0x224D, "width": 10},
                {"key": "brother-game", "label": "Brother game", "screen": "title",
                 "address": 0x228D, "width": 10},
                {"key": "top-score", "label": "Top score", "screen": "title",
                 "address": 0x22EB, "width": 4},
            ],
        )
        self.assertEqual(smb2_resources["character-graphics"].payload, "chr")
        for revision_id in ("ann_fds", "smb2_jp_fds"):
            self.assertEqual(
                profile.payload_for(revision_id, "chr").record_ids, (1,)
            )
        self.assertEqual(
            [payload.payload_id for payload in profile.revisions[5].payloads],
            ["prg", "nsmdata2", "nsmdata3", "nsmdata4", "chr"],
        )
        self.assertEqual(
            [payload.payload_id for payload in profile.revisions[6].payloads],
            ["prg", "sm2data2", "sm2data3", "sm2data4", "chr"],
        )
        world = next(item for item in profile.resources if item.kind == "world")
        self.assertEqual(world.codec, "smb.world.v1")
        self.assertEqual(
            world.parameters["world_lengths"], [5, 5, 4, 5, 4, 4, 5, 4]
        )
        self.assertEqual(ju_resources["music"].parameters["file_offset"], 0x791D)
        self.assertEqual(ju_resources["music"].parameters["cpu_address"], 0xF90D)
        self.assertEqual(ju_resources["music"].parameters["size"], 1725)
        self.assertEqual(pal_resources["music"].parameters["file_offset"], 0x791E)
        self.assertEqual(pal_resources["music"].parameters["size"], 1724)
        self.assertEqual(pc10_resources["music"].parameters, ju_resources["music"].parameters)

    def test_profile_catalog_resolves_each_revision_override(self) -> None:
        catalog = profile_catalog()
        smb = next(item for item in catalog["profiles"] if item["id"] == "smb1")
        details = {item["id"]: item for item in smb["revision_details"]}
        self.assertEqual(
            details["ju"]["capabilities"],
            ["world", "levels", "graphics", "text", "audio"],
        )
        self.assertEqual(details["pc10"]["resources"], smb["resources"])
        self.assertEqual(
            details["ann_fds"]["capabilities"],
            ["world", "levels", "graphics", "screens", "text"],
        )
        self.assertEqual(
            details["ann_fds"]["resources"],
            [
                "authoring-bundle", "world-normal", "levels-normal",
                "world-extended", "levels-extended", "background-metatiles",
                "area-palette-packets", "player-palettes", "player-frames",
                "title-screen", "character-graphics",
            ],
        )
        self.assertEqual(
            details["smb2_jp_fds"]["capabilities"],
            ["world", "levels", "graphics", "screens", "text"],
        )
        self.assertEqual(
            details["smb2_jp_fds"]["resources"],
            [
                "authoring-bundle", "world-normal", "levels-normal",
                "world-hard", "levels-hard", "background-metatiles",
                "area-palette-packets", "player-palettes", "player-frames",
                "title-screen", "character-graphics",
            ],
        )
        self.assertEqual(
            details["fds_smb"]["capabilities"],
            ["world", "levels", "graphics", "text", "audio"],
        )
        self.assertEqual(details["fds_smb"]["resources"], smb["resources"])
        self.assertEqual(
            details["vs_smb"]["capabilities"],
            ["world", "levels", "graphics", "text", "audio"],
        )
        self.assertEqual(details["vs_smb"]["resources"], smb["resources"])
        self.assertEqual(
            [payload["id"] for payload in details["ann_fds"]["payloads"]],
            ["prg", "nsmdata2", "nsmdata3", "nsmdata4", "chr"],
        )
        self.assertEqual(
            details["smb2_jp_fds"]["payloads"][3]["load_address"], 49844
        )

    def test_revision_matrix_routes_level_banks_and_palette_models(self) -> None:
        profile = packaged_game_profiles().get("smb1")
        expected = {
            "ju": (
                (("world", None),), (("levels", "world", None),), "nes"
            ),
            "pal": (
                (("world", None),), (("levels", "world", None),), "nes"
            ),
            "pc10": (
                (("world", None),), (("levels", "world", None),), "nes"
            ),
            "vs_smb": (
                (("world", "prg"),), (("levels", "world", "chr"),),
                "rp2c04_0004",
            ),
            "fds_smb": (
                (("world", "prg"),), (("levels", "world", "prg"),), "nes"
            ),
            "ann_fds": (
                (("world-normal", "prg"), ("world-extended", "prg")),
                (("levels-normal", "world-normal", "prg"),
                 ("levels-extended", "world-extended", "nsmdata4")),
                "nes",
            ),
            "smb2_jp_fds": (
                (("world-normal", "prg"), ("world-hard", "prg")),
                (("levels-normal", "world-normal", "prg"),
                 ("levels-hard", "world-hard", "sm2data4")),
                "nes",
            ),
        }
        for revision in profile.revisions:
            with self.subTest(revision=revision.revision_id):
                resources = profile.resources_for(revision)
                worlds = tuple(
                    (item.resource_id, item.payload)
                    for item in resources if item.kind == "world"
                )
                levels = tuple(
                    (item.resource_id, item.parameters.get("world_resource"),
                     item.payload)
                    for item in resources if item.kind == "level"
                )
                palette_models = {
                    item.parameters.get("palette_model")
                    for item in resources if item.kind == "palette"
                }
                expected_worlds, expected_levels, palette_model = expected[
                    revision.revision_id
                ]
                self.assertEqual(worlds, expected_worlds)
                self.assertEqual(levels, expected_levels)
                self.assertEqual(palette_models, {palette_model})
                expected_rendering = (
                    "ann" if revision.revision_id == "ann_fds"
                    else "smb2" if revision.revision_id == "smb2_jp_fds"
                    else "smb1"
                )
                self.assertEqual(
                    {
                        item.parameters.get("rendering")
                        for item in resources if item.kind == "level"
                    },
                    {expected_rendering},
                )

                capabilities = profile.capabilities_for(revision)
                has_screens = any(item.kind == "screen" for item in resources)
                has_text = any(
                    item.kind == "text"
                    or (item.kind == "screen" and item.parameters.get("text_fields"))
                    for item in resources
                )
                self.assertEqual("screens" in capabilities, has_screens)
                self.assertEqual("text" in capabilities, bool(has_text))
                self.assertEqual(
                    "audio" in capabilities,
                    any(item.kind == "audio" for item in resources),
                )

    def test_split_course_bank_spans_match_declared_capacities(self) -> None:
        profile = packaged_game_profiles().get("smb1")
        for revision_id in ("ann_fds", "smb2_jp_fds"):
            revision = next(
                item for item in profile.revisions
                if item.revision_id == revision_id
            )
            payloads = {item.payload_id: item for item in revision.payloads}
            resources = profile.resources_for(revision)
            resource_ids = {item.resource_id for item in resources}
            for resource in resources:
                parameters = resource.parameters
                if resource.codec == "smb.world.v1":
                    for component in ("routing", "physics"):
                        span = parameters[f"{component}_span"]
                        payload = payloads[span["payload"]]
                        self.assertEqual(
                            span["size"], parameters[f"{component}_size"]
                        )
                        self.assertLessEqual(
                            span["offset"] + span["size"], payload.size
                        )
                if resource.codec != "smb.levels.v1":
                    continue
                self.assertIn(parameters["world_resource"], resource_ids)
                names = parameters["stream_names"]
                for component in ("area", "enemy"):
                    capacities = parameters[f"{component}_capacities"]
                    spans = parameters[f"{component}_stream_spans"]
                    self.assertEqual(len(names), len(capacities))
                    self.assertEqual(len(names), len(spans))
                    for capacity, span in zip(capacities, spans):
                        payload = payloads[span["payload"]]
                        self.assertEqual(span["size"], capacity)
                        self.assertLessEqual(
                            span["offset"] + span["size"], payload.size
                        )


if __name__ == "__main__":
    unittest.main()
