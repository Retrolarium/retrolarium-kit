"""Synthetic document and material fixtures; no cartridge data."""

from retrolarium_kit.games.doraemon.world3.document import resource
from retrolarium_kit.games.doraemon.world3.primitives import SHAPES


def fixture() -> dict:
    hierarchy = {
        "id": "world3-hierarchy",
        "kind": "levels",
        "codec": "doraemon.hierarchy.v1",
        "world": "world3",
        "attributes": [{"palette": 0, "properties": 0} for _ in range(256)],
        "small_blocks": [[0x30] * 4, [0] * 4, [1] * 4] + [[0] * 4 for _ in range(253)],
        "big_blocks": [[0] * 4, [1] * 4, [2] * 4] + [[0] * 4 for _ in range(253)],
        "maps": [{"id": "underwater", "width": 64, "height": 64, "rows": [[0] * 64 for _ in range(64)]}],
    }
    rows = hierarchy["maps"][0]["rows"]
    for room in range(64):
        top, left = (room // 8) * 8, (room % 8) * 8
        for y in range(1, 7):
            for x in range(1, 7):
                rows[top + y][left + x] = 1 + ((x + y + room) % 3 == 0)
        for y in (3, 4):
            rows[top + y][left] = 1
            rows[top + y][left + 7] = 1
    return {
        "schema": "retrolarium.nes-resource-bundle",
        "schema_version": 1,
        "game": "fixture",
        "revision": "original",
        "source_sha256": "0" * 64,
        "resources": [
            hierarchy,
            {"id": "world3-room-palette-selectors", "selectors": [0] * 64},
            {
                "id": "world3-objects",
                "records": [{"room": 63, "type": 0x18, "x": 128, "y": 128, "state": 0} for _ in range(13)],
            },
            {"id": "world3-palette-00", "values": [index % 64 for index in range(32)]},
            {"id": "chr-bank-2", "tiles": [[[1] * 8 for _ in range(8)] for _ in range(512)]},
        ],
    }


def primitive_fixture():
    hierarchy = {
        "world": "world3",
        "attributes": [{"palette": 0, "properties": 0} for _ in range(256)],
        "small_blocks": [[0] * 4 for _ in range(256)],
        "big_blocks": [[0] * 4 for _ in range(256)],
        "maps": [{"id": "underwater", "width": 64, "height": 64, "rows": [[0] * 64 for _ in range(64)]}],
    }
    families = {}
    for name, start in (("wet-rock", 10), ("dry-rock", 40), ("layered-rock", 70)):
        shapes = {shape: [start + index * 2, start + index * 2 + 1] for index, shape in enumerate(SHAPES)}
        families[name] = {"shapes": shapes}
        for shape, values in shapes.items():
            for index in values:
                hierarchy["small_blocks"][index] = [0x40 + index] * 4
                if len(shape) == 2:
                    hierarchy["small_blocks"][index][{"nw": 0, "ne": 1, "sw": 2, "se": 3}[shape]] = 0
    hierarchy["small_blocks"][1] = [1] * 4
    hierarchy["small_blocks"][100] = [0x30] * 4
    hierarchy["small_blocks"][101] = [0x31] * 4
    catalogue = {
        "schema": "doraemon.world3-primitives.v1",
        "families": families,
        "backgrounds": {"sky": 1, "water": 0},
        "sky_rows": {"sea": 4},
        "bricks": [100],
        "supported_cover": [101],
    }
    return hierarchy, catalogue


def themed_fixture() -> dict:
    document = fixture()
    document["resources"].extend(
        [
            {
                "id": "world3-room-music",
                "tables": {
                    "world3_room_music_classes": [0] * 64,
                    "world3_music_tracks": [7, 11, 13, 17],
                },
            },
            {"id": "world3-rooms", "records": [{"type03_metasprite": 0} for _ in range(64)]},
        ]
    )
    resource(document, "world3-room-music")["tables"]["world3_room_music_classes"][9] = 2
    resource(document, "world3-rooms")["records"][9]["type03_metasprite"] = 1
    return document


def world_fixture():
    document = themed_fixture()
    hierarchy, catalogue = primitive_fixture()
    target = resource(document, "world3-hierarchy")
    target.update(hierarchy)
    semantics = {
        "schema": "doraemon.world3-semantics.v1",
        "source_sha256": document["source_sha256"],
        "primitives": [
            {
                "id": i,
                "kind": "background" if i in (0, 1) else "structural-rock",
                "policy": "terrain",
                "allow_new": True,
            }
            for i in range(256)
        ],
    }
    return document, catalogue, semantics
