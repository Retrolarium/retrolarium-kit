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

A RAM write that later writes cover completely is a staging buffer (art
decompressed there before the maps arrive), not a map, and is left out.

Stages that load the same chunks and patterns into plane A share one block
set; a level is a block set and its stages. The plane streamer reads the
coarse map at row `-Y`, so a stage's camera start (x, y) is the scene
position (x, -y). A stage runs from there to where the next stage of its
block set starts on the same rows, one screen further (the boss arena), or
to the last chunk its rows hold. A stage whose initializer pins the camera
between two X bounds is a screen-wide column: it covers the coarse rows
above and below its start that hold chunks in its columns, up to another
stage's rows.

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
SCREEN = (320, 224)
CHUNK = 256
MAP_CHUNKS = 32
COARSE_BYTES = MAP_CHUNKS * MAP_CHUNKS
BOUND = re.compile(r"^\s+(?:move\.w\s+#(?P<value>[^,\s]+),|clr\.w\s+)\(CameraX(?P<side>Lower|Upper)Bound\)\.w\s*$")
ROUTINE_END = re.compile(r"^\s+(?:rts|rte|jmp|bra(?:\.[wsl])?)\b")
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
            commands.append(AssetCommand(value, items[at + 1][1], parse_number(items[at + 2][1])))
            at += 3
            continue
        destination = parse_number(items[at + 1][1])
        if value // 2 < len(sources):
            commands.append(AssetCommand(7, sources[value // 2], destination))
        attributes[value] = ((destination >> 5) | 0x800) & 0xFFFF
        at += 2
    return commands, attributes


def visual_tile_commands(tree: SourceTree) -> list[AssetCommand]:
    """The tile commands of every stage's visual loader."""
    found = []
    for routine in _table_routines(tree, VISUAL_LOADERS):
        for line in tree.body(routine):
            lea = LEA_PC.match(line)
            if lea and lea["label"] in tree.blocks and tree.block(lea["label"]).kind in {"dc.w", "mixed"}:
                found += _tile_commands(tree, lea["label"])[0]
                break
    return found


def _lasting(writes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The writes that leave some byte for the later ones to keep."""
    covered = bytearray(0x10000)
    kept = []
    for write in reversed(writes):
        low = write["ram"] - RAM_BASE
        high = min(low + write["size"], len(covered))
        if not all(covered[low:high]):
            kept.append(write)
        covered[low:high] = b"\1" * (high - low)
    return kept[::-1]


def routine_lines(tree: SourceTree, label: str) -> list[str]:
    """A routine's lines through its local labels, up to its first
    unconditional return or jump."""
    if label not in tree.blocks:
        return []
    path = tree.block(label).file.path
    lines = []
    for name in tree.order[tree.order.index(label) :]:
        if tree.block(name).file.path != path:
            break
        for line in tree.body(name):
            lines.append(line)
            if ROUTINE_END.match(line):
                return lines
    return lines


def camera_bounds(tree: SourceTree, routine: str) -> tuple[int, int] | None:
    """The camera X bounds a stage initializer pins, when it pins both."""
    found = {}
    for line in routine_lines(tree, routine):
        match = BOUND.match(line)
        if match:
            found[match["side"]] = parse_number(match["value"]) if match["value"] else 0
    if None in found.values() or set(found) != {"Lower", "Upper"}:
        return None
    return found["Lower"], found["Upper"]


def _signed(word: int) -> int:
    return word - 0x10000 if word & 0x8000 else word


def _ram(tree: SourceTree, writes: list[dict[str, Any]]) -> bytes:
    ram = bytearray(0x10000)
    for write in writes:
        data = lzss.decompress(tree.data(write["source"]))
        low = write["ram"] - RAM_BASE
        ram[low : low + len(data)] = data[: len(ram) - low]
    return bytes(ram)


def _block_set(planes: list[dict[str, Any] | None], which: int, writes: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """The chunk and pattern payloads a plane draws with, as (role, label):
    the writes whose nearest map base below them is one of that plane's (on
    a tie, both planes read them)."""
    found = []
    for write in sorted(writes, key=lambda write: write["ram"]):
        nearest = max(
            (
                (low, index != which, role)
                for index, plane in enumerate(planes)
                if plane
                for role, (low, high) in regions(plane).items()
                if low <= write["ram"] < high
            ),
            default=None,
            key=lambda item: (item[0], not item[1]),
        )
        if nearest and not nearest[1] and nearest[2] != "coarse":
            found.append((nearest[2], write["source"]))
    return found


def levels(tree: SourceTree, found: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[int, dict[str, int]]]:
    """The block sets (plane A's, or plane B's for a stage without plane A)
    with their stages, and each stage's area in scene coordinates."""
    found_records = records(tree)
    initializers = [slot["initializer"] for slot in stages(tree)]
    sets: dict[tuple[str, ...], dict[str, Any]] = {}
    member: dict[int, tuple[str, ...]] = {}
    for scene in found:
        which = 0 if scene["planes"][0] else 1
        plane = scene["planes"][which]
        if plane is None:
            continue
        payloads = _block_set(scene["planes"], which, scene["map_writes"])
        # A stage that adds chunks to another's set (a later phase) stays in
        # it: the set is named by the first chunks and patterns it loads.
        key = tuple(next((label for kind, label in payloads if kind == role), "") for role in ("chunks", "patterns"))
        if not all(key):
            continue
        item = sets.setdefault(key, {"id": key[0], "plane": which, "payloads": [], "stages": []})
        item["payloads"] += [label for _, label in payloads if label not in item["payloads"]]
        item["stages"].append(scene["index"])
        member[scene["index"]] = key

    starts: dict[int, tuple[int, int]] = {}
    for scene in found:
        record = found_records.get(scene["record"])
        if scene["index"] in member and record:
            starts[scene["index"]] = (record["camera_x"], (-_signed(record["camera_y"])) % (MAP_CHUNKS * CHUNK))

    def rows_of(top: int, height: int, offset: int) -> list[int]:
        return sorted({((y + offset) // CHUNK) % MAP_CHUNKS for y in range(top, top + height, 8)})

    areas: dict[int, dict[str, int]] = {}
    for scene in found:
        index = scene["index"]
        if index not in starts:
            continue
        which = sets[member[index]]["plane"]
        plane = scene["planes"][which]
        ram = _ram(tree, scene["map_writes"])
        coarse = ram[plane["coarse"] - RAM_BASE :][:COARSE_BYTES]
        # A cell draws when it names a chunk the stage loaded, in the run of
        # writes from the chunk base (index 0 is a chunk too, but past a
        # stage's chunks the map holds no level).
        low, high = regions(plane)["chunks"]
        end = low
        for write in sorted(scene["map_writes"], key=lambda write: write["ram"]):
            if low <= write["ram"] <= end < high:
                end = max(end, min(write["ram"] + write["size"], high))
        loaded = (end - low) // 64

        def drawn(row: int, column: int) -> bool:
            return 0 < coarse[row * MAP_CHUNKS + column] < loaded

        offset = plane["y_offset"]
        x, top = starts[index]
        height = SCREEN[1]
        band = rows_of(top, height, offset)
        bounds = camera_bounds(tree, initializers[index]) if index < len(initializers) else None
        if bounds:
            left, right = bounds[0], bounds[1] + SCREEN[0]
            columns = range(left // CHUNK, (right - 1) // CHUNK + 1)
            # Rows held by another stage of the same coarse map stop the column.
            taken = {
                row
                for other in starts
                if other != index
                and found[other]["planes"][which]
                and found[other]["planes"][which]["coarse"] == plane["coarse"]
                for row in rows_of(starts[other][1], SCREEN[1], found[other]["planes"][which]["y_offset"])
            }

            def holds(row: int) -> bool:
                return row not in taken and any(drawn(row, column) for column in columns)

            first = (top + offset) // CHUNK
            last = (top + offset + height - 1) // CHUNK
            bottom = top + height
            while first > 0 and holds(first - 1):
                first -= 1
                top = first * CHUNK - offset
            while last < MAP_CHUNKS - 1 and holds(last + 1):
                last += 1
                bottom = (last + 1) * CHUNK - offset
            height = bottom - top
            areas[index] = {"x": left, "y": top, "width": right - left, "height": height}
            continue
        nexts = [
            starts[other][0]
            for other in sets[member[index]]["stages"]
            if other != index and starts[other][0] > x and set(rows_of(starts[other][1], SCREEN[1], offset)) & set(band)
        ]
        if nexts:
            right = min(nexts) + SCREEN[0]
        else:
            used = [column for column in range(MAP_CHUNKS) for row in band if drawn(row, column)]
            right = max([x + SCREEN[0], *((column + 1) * CHUNK for column in used)])
        areas[index] = {"x": x, "y": top, "width": min(right, MAP_CHUNKS * CHUNK) - x, "height": height}
    return list(sets.values()), areas


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
        writes = _lasting(writes)
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
    sets, areas = levels(tree, found)
    for scene in found:
        if scene["index"] in areas:
            scene["area"] = areas[scene["index"]]
        for write in scene["map_writes"]:
            if write["source"] not in editable:
                write["data"] = lzss.decompress(tree.data(write["source"])).hex().upper()
    return {
        "schema": SCHEMA,
        "schema_version": 1,
        "backdrop": BACKDROP,
        "record_fields": [name for name, _ in RECORD_FIELDS],
        "block_sets": sets,
        "scenes": found,
    }
