"""Alien Soldier's static screens: the title, options and story
backgrounds and the others the game repository's profile names, and the
credits' picture scenes.

Like a stage, a screen is the RAM and VRAM its asset list leaves behind,
read through plane descriptors. Which screens are static, the list each
plays (an options list ends where another label begins), the planes, the
camera and the palette are facts about the game: the game repository's
profile names them (`screens` in config/game_profile.json), and this module
reads the rest from the source.

Two routines rewrite title maps after loading, before the planes are drawn;
a screen's `transforms` say which, where and over how many groups of sixteen
words:

- `adjust-rows` (Gfx_AdjustTileIndexRows): `(word & $1FFF) + $C180`;
- `update-indices` (Gfx_UpdateTilemapIndices): a non-zero tile index moves
  down $100 tiles, the flip bits stay, and $6000 is added.

The credits' scenes come from the source alone: `Credits_SceneDataPointers`
pairs each scene's sixteen colours (copied into colour line 1 or 3 in turn)
with the asset list that loads its art and its map, which goes straight to
a plane's name table in VRAM, 64 cells wide. The map is a compressed payload
the tile document holds; the scene names it (`name_table`).

The editable part is each map payload's values, as in maps.py; a map no
asset list sends to VRAM as well is compressed whole, as there.
"""

from __future__ import annotations

from typing import Any

from retrolarium_kit.games.alien_soldier import lzss
from retrolarium_kit.games.alien_soldier.assets import LZSS_TO_RAM, LZSS_TO_VRAM, asset_lists, pointer_table
from retrolarium_kit.games.alien_soldier.compressed_tiles import vram_sources
from retrolarium_kit.games.alien_soldier.maps import WIDTHS
from retrolarium_kit.games.alien_soldier.maps import payloads as map_payloads
from retrolarium_kit.games.alien_soldier.stages import RAM_BASE, descriptors, regions
from retrolarium_kit.platform.megadrive.asl import SourceTree


SCHEMA = "alien-soldier.screens"
DOCUMENT = "screens/screens.json"
TRANSFORMS = ("adjust-rows", "update-indices")
CREDITS = "Credits_SceneDataPointers"
# Gfx_GameVDPRegisterValues: plane A's name table at $C000, plane B's at $E000.
NAME_TABLES = (0xC000, 0xE000)
NAME_TABLE_CELLS = 64


# Keys the kit reads again from the source each time a project opens.
DERIVED_KEYS = ("screens",)


class ScreenError(ValueError):
    """A screen definition or document that does not fit the source."""


def _write(tree: SourceTree, command) -> dict[str, Any]:
    """A RAM write, with its decoded bytes: the studio draws a screen from
    them where no document holds the payload's values (and from the
    documents where one does)."""
    plain = lzss.decompress(tree.data(command.source))
    return {
        "source": command.source,
        "ram": RAM_BASE + command.ram_offset,
        "size": len(plain),
        "data": plain.hex().upper(),
    }


def _without_data(scene_list: Any) -> Any:
    """The screens without their writes' bytes, which follow the payloads
    (an edited map document's among them)."""
    if not isinstance(scene_list, list):
        return scene_list
    return [
        {
            **scene,
            "map_writes": [{k: v for k, v in write.items() if k != "data"} for write in scene.get("map_writes", [])],
        }
        if isinstance(scene, dict)
        else scene
        for scene in scene_list
    ]


def _scene(tree: SourceTree, lists: dict[str, Any], screen: dict[str, Any], profile: str) -> dict[str, Any]:
    commands = lists.get(screen["asset_list"])
    if commands is None:
        raise ScreenError(f"{screen['id']}: {screen['asset_list']} is not an asset list")
    known = descriptors(tree)
    planes = []
    for name in screen["planes"]:
        if name is not None and name not in known:
            raise ScreenError(f"{screen['id']}: {name} is not a plane descriptor")
        planes.append(None if name is None else {"descriptor": name, **known[name]})
    for transform in screen.get("transforms", []):
        if transform.get("routine") not in TRANSFORMS:
            raise ScreenError(f"{screen['id']}: unknown transform {transform.get('routine')!r}")
    lookup = screen["lookup_y"]
    return {
        "id": screen["id"],
        "asset_list": screen["asset_list"],
        "map_writes": [_write(tree, command) for command in commands if command.command == LZSS_TO_RAM],
        "vram": [
            {"command": command.command, "source": command.source, "vram": command.destination}
            for command in commands
            if command.command in (1, 2, 3, 5, 7)
        ],
        "planes": planes,
        "camera_x": screen["camera_x"],
        "lookup_y": [value[profile] if isinstance(value, dict) else value for value in lookup],
        "palette": screen["palette"],
        "transforms": screen.get("transforms", []),
        "size": screen.get("size", [320, 224]),
    }


def credit_scenes(tree: SourceTree, lists: dict[str, Any]) -> list[dict[str, Any]]:
    """The credits' picture scenes, in the order the credits show them."""
    if CREDITS not in tree.blocks:
        return []
    labels = [label for label in pointer_table(tree, CREDITS) if label in tree.blocks]
    found = []
    for number, (palette, assets) in enumerate(zip(labels[::2], labels[1::2]), 1):
        commands = lists.get(assets)
        if commands is None:
            raise ScreenError(f"{assets} is not an asset list")
        tables = [
            command
            for command in commands
            if command.command == LZSS_TO_VRAM and command.destination & 0xFFFF in NAME_TABLES
        ]
        if len(tables) != 1:
            raise ScreenError(f"{assets} loads no single plane map")
        table = tables[0]
        size = len(lzss.decompress(tree.data(table.source), blocks=True))
        rows = size // (2 * NAME_TABLE_CELLS)
        palette_data = tree.data(palette)
        if len(palette_data) % 2:
            raise ScreenError(f"{palette} has an incomplete CRAM word")
        colours = [int.from_bytes(palette_data[at : at + 2], "big") for at in range(0, len(palette_data), 2)]
        found.append(
            {
                "id": f"credits-{number:02d}",
                "kind": "credits",
                "asset_list": assets,
                "map_writes": [],
                "vram": [
                    {"command": command.command, "source": command.source, "vram": command.destination}
                    for command in commands
                    if command.command in (1, 2, 3, 5, 7)
                ],
                "planes": [None, None],
                "camera_x": [0, 0],
                "lookup_y": [0, 0],
                "palette": {"colours": colours},
                "transforms": [],
                "size": [8 * NAME_TABLE_CELLS, 8 * rows],
                "name_table": {"source": table.source, "vram": table.destination & 0xFFFF, "rows": rows},
            }
        )
    return found


def scenes(tree: SourceTree, definitions: list[dict[str, Any]], profile: str) -> list[dict[str, Any]]:
    lists = asset_lists(tree)
    return [_scene(tree, lists, screen, profile) for screen in definitions] + credit_scenes(tree, lists)


def payloads(tree: SourceTree, scene_list: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """The screens' map payloads but those a stage uses too: the map document
    owns those, and a screen is drawn with its values."""
    stage_maps = set(map_payloads(tree))
    found: dict[str, dict[str, Any]] = {}
    for which in (0, 1):
        for scene in scene_list:
            plane = scene["planes"][which]
            if plane is None:
                continue
            spans = regions(plane)
            primary = regions(scene["planes"][0]) if which and scene["planes"][0] else {}
            for write in scene["map_writes"]:
                role = next((role for role, (low, high) in spans.items() if low <= write["ram"] < high), None)
                if (
                    role is None
                    or write["source"] in stage_maps
                    or which
                    and any(
                        low < write["ram"] + write["size"] and write["ram"] < high for low, high in primary.values()
                    )
                ):
                    continue
                item = found.setdefault(write["source"], {"role": role, "screens": []})
                if item["role"] == role and scene["id"] not in item["screens"]:
                    item["screens"].append(scene["id"])
    return found


def export(tree: SourceTree, definitions: list[dict[str, Any]], profile: str) -> dict[str, Any]:
    scene_list = scenes(tree, definitions, profile)
    items = []
    for label, item in payloads(tree, scene_list).items():
        payload = tree.data(label)
        plain = lzss.decompress(payload)
        width = WIDTHS[item["role"]]
        items.append(
            {
                "label": label,
                "role": item["role"],
                "screens": item["screens"],
                "capacity": len(payload),
                "values": [
                    int.from_bytes(plain[at : at + width], "big")
                    for at in range(0, len(plain) - len(plain) % width, width)
                ],
            }
        )
    return {"schema": SCHEMA, "schema_version": 1, "ram_base": RAM_BASE, "screens": scene_list, "payloads": items}


def apply(tree: SourceTree, document: dict[str, Any], definitions: list[dict[str, Any]], profile: str) -> None:
    if document.get("schema") != SCHEMA or document.get("schema_version") != 1:
        raise ScreenError("not an Alien Soldier screen document")
    scene_list = scenes(tree, definitions, profile)
    if _without_data(document.get("screens")) != _without_data(scene_list):
        raise ScreenError("the screens are the source's")
    source = payloads(tree, scene_list)
    items = document.get("payloads")
    if not isinstance(items, list) or [(item.get("label"), item.get("role")) for item in items] != [
        (label, item["role"]) for label, item in source.items()
    ]:
        raise ScreenError("the screen maps differ from the source")
    to_vram = vram_sources(tree)
    for item in items:
        label, role, original = item["label"], item["role"], tree.data(item["label"])
        plain = lzss.decompress(original)
        width, values = WIDTHS[role], item.get("values")
        if not isinstance(values, list) or len(values) != len(plain) // width:
            raise ScreenError(f"{label}: the number of values is the source's")
        if any(
            isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < 1 << (8 * width)
            for value in values
        ):
            raise ScreenError(f"{label}: a value does not fit its map")
        data = b"".join(value.to_bytes(width, "big") for value in values) + plain[len(values) * width :]
        try:
            tree.set_data(label, lzss.fit(data, original, blocks=label in to_vram))
        except lzss.LzssError as error:
            raise ScreenError(f"{label}: {error}") from None
