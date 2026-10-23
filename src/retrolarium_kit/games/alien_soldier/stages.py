"""What Alien Soldier loads for each stage slot, for drawing it.

A stage's background is the RAM its asset lists leave behind: asset command
six decompresses maps into RAM, and the plane streamer reads them through a
descriptor, four longwords naming the coarse map (32x32 chunk indices), the
chunks (8x8 pattern indices) and the patterns (4x4 name-table words), then
the plane's VRAM base. Its tiles are what commands one, three and seven put
into VRAM, then the compact tile commands of its visual loader: a word with
bit 0 clear indexes `Stage_SharedTileSourceTable` and is sent as command
seven, an odd word is a whole record.

`Stage_LoadAssetsForCurrentTableIndex` plays the shared list, then the
stage's loader from `Stage_AssetLoaderOffsets` (a loader may call another
first). The stage record's two entry modes pick each plane's descriptor:
zero is a fill of word zero, other modes are offsets of four from 4 into the
plane's table, and on the primary plane bit 15 shifts the map down $1000
lines. A plane whose maps the stage never loads is not there.

Nothing here edits anything: the scenes are read from the source when a
project opens, and the studio draws them from the edited documents.
"""

from __future__ import annotations

import re
from typing import Any

from retrolarium_kit.games.alien_soldier.assets import (
    LZSS_TO_RAM,
    SHARED_TILE_SOURCES,
    AssetCommand,
    asset_lists,
    pointer_table,
)
from retrolarium_kit.games.alien_soldier.levels import RECORD_FIELDS, records, stages
from retrolarium_kit.games.alien_soldier import lzss
from retrolarium_kit.platform.megadrive.asl import SourceTree, parse_number


SCHEMA = "alien-soldier.stages"
DOCUMENT = "levels/stages.json"
LOADERS = "Stage_AssetLoaderOffsets"
SHARED_LOADER = "Stage_LoadSharedMappings"
VISUAL_LOADERS = "Stage_VisualAssetLoaderOffsets"
PLANE_TABLES = (
    "Stage_EntryPrimaryPlaneVRAMParameterPointers",
    "Stage_EntrySecondaryPlaneVRAMParameterPointers",
)
OFFSET_VARIANT = 0x8000
OFFSET_VARIANT_Y = 0x1000
RAM_BASE = 0xFFFF0000
# The first thing src/ram_addrs.inc names above the coarse map, where the
# coarse region ends.
TERRAIN_COLLISION_BUFFER = 0xFFFF7800
# Gfx_GameVDPRegisterValues sets register 7 to $10: line one, colour zero.
BACKDROP = 0x10
LEA_PC = re.compile(r"^\s+lea\s+(?P<label>[A-Za-z_][A-Za-z0-9_]*)\(pc\),a0\b")
LEA_ABSOLUTE = re.compile(r"^\s+lea\s+\((?P<label>[A-Za-z_][A-Za-z0-9_]*)\)\.l,a0\b")
CALL = re.compile(r"^\s+bsr\.[wsl]\s+(?P<label>[A-Za-z_][A-Za-z0-9_]*)\s*$")


def _table_routines(tree: SourceTree, label: str) -> list[str]:
    """A table of `Routine-Anchor` words, as the routines."""
    return [operand.text.split("-")[0] for operand in tree.block(label).operands]


def loader_lists(tree: SourceTree, lists: dict[str, list[AssetCommand]], loader: str, seen: set[str]) -> list[str]:
    """The asset lists a loader plays, after those of a loader it calls."""
    if loader in seen or loader not in tree.blocks:
        return []
    seen.add(loader)
    found = []
    for line in tree.body(loader):
        call = CALL.match(line)
        if call:
            found += loader_lists(tree, lists, call["label"], seen)
            continue
        lea = LEA_PC.match(line)
        if lea and lea["label"] in lists:
            found.append(lea["label"])
    return found


def descriptors(tree: SourceTree) -> dict[str, dict[str, int]]:
    found = {}
    for label in tree.order:
        block = tree.block(label)
        if not label.endswith("VRAMTransferParameters") or block.kind != "dc.l" or len(block.operands) != 4:
            continue
        coarse, chunks, patterns, plane = (parse_number(operand.text) for operand in block.operands)
        found[label] = {"coarse": coarse, "chunks": chunks, "patterns": patterns, "plane": plane}
    return found


def plane_table(tree: SourceTree, label: str) -> list[str]:
    """A plane's descriptor pointers, through the bare labels inside it."""
    found = []
    for name in tree.order[tree.order.index(label) :]:
        if (name in PLANE_TABLES and name != label) or tree.block(name).kind != "dc.l":
            break
        found += pointer_table(tree, name)
    return found


def regions(descriptor: dict[str, int]) -> dict[str, tuple[int, int]]:
    """Each map's RAM range: from its base to the next base (the coarse map
    runs to the collision buffer)."""
    bases = {"patterns": descriptor["patterns"], "chunks": descriptor["chunks"], "coarse": descriptor["coarse"]}
    edges = sorted({*bases.values(), TERRAIN_COLLISION_BUFFER})
    return {
        role: (base, next((edge for edge in edges if edge > base), RAM_BASE + 0x10000)) for role, base in bases.items()
    }


def _tile_commands(tree: SourceTree, label: str) -> tuple[list[AssetCommand], dict[int, int]]:
    """A compact tile command list as asset commands, and the tile
    attributes it leaves in SpawnedEnemyTileAttr."""
    sources = pointer_table(tree, SHARED_TILE_SOURCES)
    items = [
        (directive.kind, operand.text) for directive in tree.block(label).directives for operand in directive.operands
    ]
    commands, attributes, at = [], {}, 0
    while at < len(items):
        value = parse_number(items[at][1])
        if value is None or value & 0x8000:
            break
        if value & 1:
            commands.append(AssetCommand(value & 0xFFFE, items[at + 1][1], parse_number(items[at + 2][1])))
            at += 3
            continue
        destination = parse_number(items[at + 1][1])
        if value // 2 < len(sources):
            commands.append(AssetCommand(7, sources[value // 2], destination))
        attributes[value] = ((destination >> 5) | 0x800) & 0xFFFF
        at += 2
    return commands, attributes


def scenes(tree: SourceTree) -> list[dict[str, Any]]:
    lists = asset_lists(tree)
    found_records = records(tree)
    all_descriptors = descriptors(tree)
    tables = [plane_table(tree, label) for label in PLANE_TABLES]
    loaders = _table_routines(tree, LOADERS)
    visuals = _table_routines(tree, VISUAL_LOADERS)
    result = []
    for index, slot in enumerate(stages(tree)):
        names = loader_lists(tree, lists, SHARED_LOADER, set()) + loader_lists(tree, lists, loaders[index], set())
        commands = [command for name in names for command in lists[name]]
        visual_palette, attributes = "", {}
        if index < len(visuals):
            for line in tree.body(visuals[index]):
                lea = LEA_PC.match(line)
                if lea and lea["label"] in tree.blocks and tree.block(lea["label"]).kind in {"dc.w", "mixed"}:
                    extra, attributes = _tile_commands(tree, lea["label"])
                    commands += extra
                    break
                absolute = LEA_ABSOLUTE.match(line)
                if absolute and not visual_palette:
                    visual_palette = absolute["label"]
        record = found_records.get(slot["record"])
        writes = []
        for command in commands:
            if command.command == LZSS_TO_RAM and command.source in tree.blocks:
                size = len(lzss.decompress(tree.data(command.source)))
                writes.append({"source": command.source, "ram": RAM_BASE + command.ram_offset, "size": size})
        planes = []
        for which, (field, table) in enumerate(zip(("plane_a_mode", "plane_b_mode"), tables)):
            mode = record[field] if record else 0
            plane = None
            masked = mode & ~OFFSET_VARIANT if which == 0 else mode
            entry = (masked - 4) // 4
            if mode and masked % 4 == 0 and 0 <= entry < len(table):
                descriptor = all_descriptors[table[entry]]
                spans = regions(descriptor)
                loaded = {
                    role for role, (low, high) in spans.items() if any(low <= write["ram"] < high for write in writes)
                }
                if {"chunks", "patterns"} <= loaded:
                    plane = {
                        "descriptor": table[entry],
                        **descriptor,
                        "y_offset": OFFSET_VARIANT_Y if which == 0 and mode & OFFSET_VARIANT else 0,
                    }
            planes.append(plane)
        result.append(
            {
                "index": index,
                "record": slot["record"],
                "lists": names,
                "map_writes": writes,
                "vram": [
                    {"command": command.command, "source": command.source, "vram": command.destination}
                    for command in commands
                    if command.command in (1, 2, 3, 5, 7)
                ],
                "tile_attributes": {f"${key:X}": value for key, value in sorted(attributes.items())},
                "planes": planes,
                "palette_list": record["palette_list"] if record else "",
                "visual_palette": visual_palette,
            }
        )
    return result


def export(tree: SourceTree) -> dict[str, Any]:
    """The scenes, with the decoded bytes of every RAM write the map document
    does not hold (the studio draws those as they are)."""
    from retrolarium_kit.games.alien_soldier.maps import payloads as map_payloads

    editable = set(map_payloads(tree))
    found = scenes(tree)
    for scene in found:
        for write in scene["map_writes"]:
            if write["source"] not in editable:
                write["data"] = lzss.decompress(tree.data(write["source"])).hex().upper()
    return {
        "schema": SCHEMA,
        "schema_version": 1,
        "backdrop": BACKDROP,
        "record_fields": [name for name, _ in RECORD_FIELDS],
        "scenes": found,
    }
