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
plane's table. On the primary plane bit 15 also copies the rows $1000
lines below the shown ones into the terrain buffer at $FFFF0000
(`Tilemap_MirrorOffsetRowAndQueueScrollingRow`): the snow stages keep their
ground's shape there, under the picture. The plane still shows the rows at
the camera (`y_offset` 0); `terrain_y` says where the terrain rows are. A
plane whose maps the stage never loads is not there.

A RAM write that later writes cover completely is a staging buffer (art
decompressed there before the maps arrive), not a map, and is left out.

Stages that load the same chunks and patterns into plane A share one block
set; a level is a block set and its stages. The plane streamer reads the
coarse map at row `-Y`, so a stage's camera start (x, y) is the scene
position (x, -y). A stage runs as far as its state handlers move the
camera: from the X a handler sets it to (or the record's) and the lower
bounds they set, to a screen past the furthest X they wait for, bound it to
or pin it at for the boss (the handlers from the record's state offset up
to the boss's, or to the next stage's first). A climb they wait for or pin
(more than half a screen) adds the screen-wide column it climbs, at the
furthest X (the area lists its `parts`: the run and the column). Handlers that
never draw the map (Xi-Tiger's only move the camera and the rasters) leave
the picture the entry drew at the record's X, so the stage is measured from
there. A camera Y the handlers work out before the stage scrolls (Epsilon
1's parallax sets it to $28 minus a value the handler stores, $10) moves
the stage's rows by as much: the handlers' word moves, adds, subtractions
and negations into d0 are followed through the routines they call.
A handler that
streams another part of the map into the plane (`TilemapRowXOrFillWord`,
`TilemapRowYPosition`) moves the fight there: the stage lists that screen
as an arena (`arenas`): the 256 lines streamed down from row -Y (all the
plane holds; the camera shows a part of them), with the camera X folded into
the 512-pixel plane. A handler may set the stream up in code it ends by
branching to, and a stage that restarts another's run (stage 22 sets the
state to the Destroyer Prototype's backdrop) goes on with that run's
handlers, up to another restart. A boss loaded once the stage has its
screen, or a second boss, ends the search: what follows is the next part.
Without a boss brought in, the screen draws with the set the stage loaded
for its own boss. The boss fought there is the one the stage's own boss
brings in (an asset set its code, in the files named after it, loads at an
object: Viblack brings Back Stringer); the arena draws with that set's
maps and art and is coloured by its palette command (`palettes`).
A boss may move the fight elsewhere itself: the asset set its handler loads
names the boss's object type, and the code of that type's handler (its
file) sets the camera to an X and Y and waits for camera Ys (Z-Leo starts
at (0, $100) and rises to $240). That screen, from camera Y 0 to the
highest, is an arena drawn with the maps the boss loads (`list`, its RAM
writes and VRAM loads) and shown with the stage (`inline`). A row descriptor
the boss streams its plane from (`Tilemap_QueueRowFromDescriptor`: coarse
map, chunks, patterns and the plane's name table) is the arena's plane
(`planes`; Z-Leo's rise draws plane B with plane A's patterns, his body
takes plane A). The asset lists the boss's code plays (Z-Leo's battle entry
loads new patterns at $4020) are drawn after its set's, and the set's
palette command colours the arena.

A stage whose handlers scroll plane B at a quarter of the camera X
(`Scroll_UpdateQuarterHorizontalPosition`, reached through a couple of calls)
says so (`plane_b_quarter`): each screen of it shows plane B at a quarter of
its camera X. Others (the train's rows run on their own) do not. A stage
whose handlers set plane B's camera X to plane A's (the train) says so too
(`plane_b_follows`): plane B lines up with plane A, whatever the record's
cameras say. When the handlers stream plane B but never plane A, plane A
keeps the 512 pixels the entry drew from the record's camera X (`fixed_x`),
repeated however far the camera goes.

A stage whose handlers move the camera off the record's X without drawing
(Xi-Tiger's) keeps the picture the entry drew: it is not part of the level's
strip but a screen apart (`apart` in its area), shown as its boss finds it,
with the asset lists its handlers play (later loads from its first X: the
caterpillar's and Xi-Tiger's art turn the train's tiles into clouds) and
without a plane its handlers fill with blank rows (`Tilemap_QueueNextConstantRow`
over plane B's table).

A boss that widens the camera's X bounds (`subi.w #n,(CameraXLowerBound)`)
where the stage pins the camera fights on a screen apart too: from the pinned
X less the widening, between the camera Ys the boss's state handler swings
through (Flying-Neo, over the train's roof). Its plane B is streamed from the
rows the handlers before the boss queue it from (`d1` = camera Y + n into
`Tilemap_Queue{Column,Row}FromDescriptor`), measured from plane A's.

A stage initializer may write rows of 4x4 blocks straight into a name table
(`Tilemap_QueueIndexedRows`: the VRAM address, the RAM blocks, then block
indices, a row of them per group) that only a raster split shows: Epsilon
1's HBlank handler points plane B at that table from a line down, scrolled
by the word its parallax works the camera Y out from, so the first row
lands on the split. The scene lists them (`floor`: the RAM blocks, the
indices, how many to a row, and the screen line they start at).
A stage whose handlers say nothing runs to where the next
stage of its block set starts on the same rows, one screen further, or to
the last chunk its rows hold. A stage whose initializer pins the camera
between two X bounds is a screen-wide column: it covers the coarse rows
above and below its start that hold chunks in its columns, up to another
stage's rows.

A stage's initializer may rewrite the maps it loaded before anything draws
them, with the routines the title uses too: `Gfx_UpdateTilemapIndices`
(each non-zero tile index plus d1, the flip bits kept, then d0 added) and
`Gfx_AdjustTileIndexRows` (`(word & $1FFF) + d0`), over d7 + 1 groups of
sixteen words at a0. The scene lists those calls (`transforms`), followed
through the initializer's jumps, so the studio draws the maps rewritten.

The asset lists a stage's first state handler plays before it waits for
the camera (stage 7's counter art at $5BE0) are loaded with the stage's own.

A stage may load more maps while it runs: one of its state handlers waits
for the camera to pass an X and plays an asset list (stage 19 swaps the
snow for the factory at $D80). The loader names the stage process
(`StageProcessTableOffset` into `Stage_ProcessHandlerTable`), whose
dispatcher's table holds the handlers; the record's state offset is the
first. The handlers from there up to the next phase or boss are searched,
and each such load is listed (`later`: the camera X, the list, its RAM
writes and VRAM loads). The screen already shows the columns up to a screen
past that X; the ones after it draw from the new maps.

Nothing here edits anything: the scenes are read from the source when a
project opens, and the studio draws them from the edited documents.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
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
from retrolarium_kit.games.alien_soldier.resampled import resampled_loads
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
TRANSFORM_ROUTINES = {"Gfx_UpdateTilemapIndices": "update-indices", "Gfx_AdjustTileIndexRows": "adjust-rows"}
SET_A0 = re.compile(r"^\s+(?:movea\.l\s+#|lea\s+\(?)(?P<label>[A-Za-z_][A-Za-z0-9_]*)(?:\)\.l|\(pc\))?,a0\s*$")
SET_DATA = re.compile(r"^\s+(?:move\.w|moveq)\s+#(?P<value>[^,\s]+),(?P<register>d[017])\s*$")
JUMP = re.compile(
    r"^\s+(?P<op>jmp|jsr|bra(?:\.[wsl])?|bsr(?:\.[wsl])?)\s+\(?(?P<label>[A-Za-z_][A-Za-z0-9_]*)\)?(?:\.l)?\s*$"
)
LEA_PC = re.compile(r"^\s+lea\s+(?P<label>[A-Za-z_][A-Za-z0-9_]*)\(pc\),a0\b")
LEA_ABSOLUTE = re.compile(r"^\s+lea\s+\((?P<label>[A-Za-z_][A-Za-z0-9_]*)\)\.l,a0\b")
CALL = re.compile(r"^\s+bsr\.[wsl]\s+(?P<label>[A-Za-z_][A-Za-z0-9_]*)\s*$")
TAIL = re.compile(r"^\s+bra\.[wsl]\s+(?P<label>[A-Za-z_][A-Za-z0-9_]*)\s*$")
PROCESS_TABLE = "Stage_ProcessHandlerTable"
SET_PROCESS = re.compile(r"^\s+move\.w\s+#(?P<value>[^,\s]+),\(StageProcessTableOffset\)\.w\s*$")
STATE_TABLE = re.compile(r"^\s+movea\.w\s+(?P<label>[A-Za-z_][A-Za-z0-9_]*)\(pc,d0\.w\),a0\s*$")
CAMERA_GATE = re.compile(r"^\s+cmpi\.w\s+#(?P<value>[^,\s]+),\(PrimaryCameraXPosition\)\.w\s*$")
# A handler that ends the stage's own scrolling: the next phase or a boss.
PHASE_END = re.compile(r"\b(?:Stage_TransitionToNextPhase|Boss_LoadAssetSet)\b")
BOSS = re.compile(r"\bBoss_LoadAssetSet\b")
CAMERA_SET = re.compile(r"^\s+move\.w\s+#(?P<value>[^,\s]+),\(PrimaryCameraXPosition\)\.w\s*$")
CAMERA_LIMIT = re.compile(
    r"^\s+(?:cmpi\.w\s+#(?P<compare>[^,\s]+),\(PrimaryCameraXPosition\)\.w"
    r"|move\.w\s+#(?P<upper>[^,\s]+),\(CameraXUpperBound\)\.w)\s*$"
)
CAMERA_LOWER = re.compile(r"^\s+(?:move\.w\s+#(?P<value>[^,\s]+),|clr\.w\s+)\(CameraXLowerBound\)\.w\s*$")
PIN_VALUE = re.compile(r"^\s+move\.w\s+#(?P<value>[^,\s]+),d0\s*$")
PIN = re.compile(r"^\s+move\.w\s+d0,\((?:PrimaryCameraXPosition|CameraXUpperBound)\)\.w\s*$")
CAMERA_Y_LIMIT = re.compile(r"^\s+cmpi\.w\s+#(?P<value>[^,\s]+),\(PrimaryCameraYPosition\)\.w\s*$")
PIN_Y = re.compile(r"^\s+(?:move\.w\s+d0,\(PrimaryCameraYPosition\)\.w|cmp\.w\s+\(PrimaryCameraYPosition\)\.w,d0)\s*$")
# A routine a handler calls that draws the stage's map as the camera moves.
DRAWS = re.compile(r"^\s+(?:jsr|jmp|bsr(?:\.[wsl])?|bra(?:\.[wsl])?)\s+\(?\w*(?:Render|ScrollAndTilemap)\w*")
BOSS_SET = re.compile(r"^\s+lea\s+\(?(?P<label>[A-Za-z_][A-Za-z0-9_]*)\)?(?:\.l|\(pc\))?,a1\s*$")
ROW_DESCRIPTOR = re.compile(r"^\s+lea\s+(?P<label>[A-Za-z_][A-Za-z0-9_]*)\(pc\),a0\s*$")
QUEUE_ROW = re.compile(r"\bTilemap_QueueRowFromDescriptor\b")
PLAY_LIST = re.compile(r"\bData_ProcessPointer\b")
# A descriptor's name table: plane B's ($6000 in its fourth long), else A.
PLANE_B_TABLE = 0x6000
TELEPORT_X = re.compile(r"^\s+(?:clr\.w\s+|move\.w\s+#(?P<value>[^,\s]+),)\(PrimaryCameraXPosition\)\.w\s*$")
TELEPORT_Y = re.compile(r"^\s+move\.w\s+#(?P<value>[^,\s]+),\(PrimaryCameraYPosition\)\.w\s*$")
RISE = re.compile(r"^\s+cmpi\.w\s+#(?P<value>[^,\s]+),\((?:PrimaryCameraYPosition|SecondaryCameraYPos)\)\.w\s*$")
# The word arithmetic followed to a camera Y the handlers work out.
STORE = re.compile(r"^\s+move\.w\s+#(?P<value>[^,\s]+),\((?P<label>[A-Za-z_][A-Za-z0-9_]*)\)\.w\s*$")
LOAD = re.compile(r"^\s+move\.w\s+(?:#(?P<value>[^,\s]+)|\((?P<label>[A-Za-z_][A-Za-z0-9_]*)\)\.w),d0\s*$")
ARITHMETIC = re.compile(r"^\s+(?P<op>addi|subi)\.w\s+#(?P<value>[^,\s]+),d0\s*$")
NEGATE = re.compile(r"^\s+neg\.w\s+d0\s*$")
SET_Y = re.compile(r"^\s+move\.w\s+d0,\(PrimaryCameraYPosition\)\.w\s*$")
BRANCH = re.compile(r"^\s+(?:b[a-z]{2}|bsr|bra)(?:\.[wsl])?\s+(?P<label>[A-Za-z_][A-Za-z0-9_]*)\s*$")
MAX_ROUTINES = 8
POOL_EMPTY = re.compile(r"^\s+tst\.w\s+\(Entity_ObjectPool\)\.w\s*$")
TAIL_BRANCH = re.compile(r"^\s+(?:bra|jmp)(?:\.[wsl])?\s+\(?(?P<label>[A-Za-z_][A-Za-z0-9_]*)\)?(?:\.l|\(pc\))?\s*$")
STATE_JUMP = re.compile(r"^\s+move\.w\s+#(?P<value>[^,\s]+),\(StageStateOffset\)\.w\s*$")
FOLLOW = re.compile(r"^\s+move\.w\s+\(PrimaryCameraXPosition\)\.w,\(SecondaryCameraXPos\)\.w\s*$")
WIDEN = re.compile(r"^\s+subi\.w\s+#(?P<value>[^,\s]+),\(CameraXLowerBound\)\.w\s*$")
QUEUE_Y = re.compile(
    r"^\s+(?:move\.w\s+(?:#(?P<value>[^,\s]+)|\((?P<camera>PrimaryCameraYPosition)\)\.w),d1"
    r"|(?P<op>addi|subi)\.w\s+#(?P<step>[^,\s]+),d1)\s*$"
)
STREAM_DESCRIPTOR = re.compile(r"^\s+lea\s+\(?(?P<label>[A-Za-z_][A-Za-z0-9_]*)\)?(?:\.l|\(pc\))?,a0\s*$")
QUEUE = re.compile(r"\bTilemap_Queue(?:Column|Row)FromDescriptor\b")
FILL_TABLE = re.compile(r"^\s+move\.w\s+#(?P<value>[^,\s]+),\(TilemapTransferBase\)\.w\s*$")
FILL_ROWS = re.compile(r"\bTilemap_QueueNextConstantRow\b")
INDEXED_ROWS = re.compile(r"^\s+jsr\s+\(Tilemap_QueueIndexedRows\)\.l\s*$")
QUARTER = re.compile(r"^\s+(?:jsr|jmp|bsr|bra)(?:\.[wsl])?\s+\(?Scroll_UpdateQuarterHorizontalPosition\b")
# How many calls deep a handler is followed to the quarter scroll.
QUARTER_DEPTH = 2
ENTITY = re.compile(r"^\s+move\.w\s+#(?P<value>[^,\s]+),\(Entity_ObjectPool\)\.w\s*$")
CAMERA_X = re.compile(r"^\s+move\.w\s+#(?P<value>[^,\s]+),\((?:PrimaryCameraXPosition|CameraXLowerBound)\)\.w\s*$")
SECONDARY_Y = re.compile(r"^\s+(?:clr\.w\s+|move\.w\s+#(?P<value>[^,\s]+),)\(SecondaryCameraYPos\)\.w\s*$")
SECONDARY_D0 = re.compile(r"^\s+move\.w\s+\(SecondaryCameraXPos\)\.w,d0\s*$")
SECONDARY_D1 = re.compile(r"^\s+move\.w\s+\(SecondaryCameraYPos\)\.w,d1\s*$")
LOAD_AT_OBJECT = re.compile(r"\bBoss_LoadAssetSetAtObject\b")
STREAM_X = re.compile(r"^\s+(?:clr\.w\s+|move\.w\s+#(?P<value>[^,\s]+),)\(TilemapRowXOrFillWord\)\.w\s*$")
STREAM_Y = re.compile(r"^\s+move\.w\s+#(?P<value>[^,\s]+),\(TilemapRowYPosition\)\.w\s*$")
PLANE_WIDTH = 512
CLIMB = SCREEN[1] // 2
MAX_HANDLERS = 16


def _table_routines(tree: SourceTree, label: str) -> list[str]:
    """A table of `Routine-Anchor` words, as the routines."""
    return [operand.text.split("-")[0] for operand in tree.block(label).operands]


def loader_lists(tree: SourceTree, lists: dict[str, list[AssetCommand]], loader: str, seen: set[str]) -> list[str]:
    """The asset lists a loader plays, after those of a loader it calls."""
    if loader in seen or loader not in tree.blocks:
        return []
    seen.add(loader)
    found = []
    # Through the labels it falls into, and the shared tail it branches to.
    for line in routine_lines(tree, loader):
        call = CALL.match(line) or TAIL.match(line)
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


def map_transforms(tree: SourceTree, routine: str) -> list[dict[str, Any]]:
    """The map rewrites a stage initializer makes: each call of a transform
    routine with the immediate registers it set, through its jumps."""
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    registers: dict[str, int] = {}

    def walk(label: str) -> None:
        if label in seen or label not in tree.blocks:
            return
        seen.add(label)
        for line in routine_lines(tree, label):
            target = SET_A0.match(line)
            if target:
                name = target["label"]
                if name in tree.equates:
                    registers["a0"] = tree.equates[name] & 0xFFFFFFFF
                continue
            value = SET_DATA.match(line)
            if value and parse_number(value["value"]) is not None:
                registers[value["register"]] = parse_number(value["value"]) & 0xFFFF
                continue
            jump = JUMP.match(line)
            if not jump:
                continue
            routine_name = TRANSFORM_ROUTINES.get(jump["label"])
            if routine_name and {"a0", "d0", "d7"} <= set(registers):
                item = {
                    "routine": routine_name,
                    "ram": f"0x{registers['a0'] | 0xFFFF0000:08X}",
                    "groups": registers["d7"] + 1,
                    "add": registers["d0"],
                }
                if routine_name == "update-indices":
                    item["tiles"] = registers.get("d1", 0)
                found.append(item)
            elif jump["op"].startswith(("jmp", "bra")) and not jump["label"].startswith("Gfx_"):
                # The initializer goes on in shared code.
                walk(jump["label"])

    walk(routine)
    return found


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


def stage_states(tree: SourceTree, found: list[dict[str, Any]]) -> dict[str, list[int]]:
    """By loader, the first state of every stage of its process: one stage's
    handlers end where another's begin."""
    found_records = records(tree)
    loaders = _table_routines(tree, LOADERS)
    process = {
        scene["index"]: _process(tree, loaders[scene["index"]], set()) or 0
        for scene in found
        if scene["index"] < len(loaders)
    }
    states: dict[str, list[int]] = {}
    for scene in found:
        if scene["index"] not in process:
            continue
        states[loaders[scene["index"]]] = [
            found_records[other["record"]]["state_offset"]
            for other in found
            if other["record"] in found_records and process.get(other["index"]) == process[scene["index"]]
        ]
    return states


def levels(tree: SourceTree, found: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[int, dict[str, int]]]:
    """The block sets (plane A's, or plane B's for a stage without plane A)
    with their stages, and each stage's area in scene coordinates."""
    found_records = records(tree)
    initializers = [slot["initializer"] for slot in stages(tree)]
    loaders = _table_routines(tree, LOADERS)
    states = stage_states(tree, found)
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
        record = found_records.get(scene["record"])
        reach = (
            camera_reach(tree, loaders[index], record["state_offset"], x, states.get(loaders[index], []))
            if record and index < len(loaders)
            else None
        )
        if reach and reach[4] is not None and reach[4] != record["camera_y"]:
            # The handlers work out another camera Y: the rows move.
            top -= _signed(reach[4]) - _signed(record["camera_y"])
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
        if reach:
            start, left, furthest, climbs, _, kept = reach
            # A climb's camera Y is the row -Y, measured from the start's.
            camera_y = _signed(record["camera_y"])
            ups = [top + camera_y - _signed(value) for value in climbs if abs(_signed(value) - camera_y) > CLIMB]
            upper = min([top, *ups])
            lower = max([top, *ups]) + height
            areas[index] = {"x": left, "y": upper, "width": furthest + SCREEN[0] - left, "height": lower - upper}
            if ups:
                # The run, then the column the camera climbs at its furthest X.
                areas[index]["parts"] = [
                    {"x": left, "y": top, "width": furthest + SCREEN[0] - left, "height": height},
                    {"x": furthest, "y": upper, "width": SCREEN[0], "height": lower - upper},
                ]
            if start != x:
                # The handlers move the camera off the record's X at once.
                areas[index]["start"] = start
            if kept:
                areas[index]["apart"] = True
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


def _process(tree: SourceTree, loader: str, seen: set[str]) -> int | None:
    """The stage process offset a loader sets, through the loaders it calls."""
    if loader in seen or loader not in tree.blocks:
        return None
    seen.add(loader)
    for line in routine_lines(tree, loader):
        match = SET_PROCESS.match(line)
        if match and parse_number(match["value"]) is not None:
            return parse_number(match["value"])
        call = CALL.match(line) or TAIL.match(line)
        if call:
            found = _process(tree, call["label"], seen)
            if found is not None:
                return found
    return None


def _state_handlers(tree: SourceTree, loader: str) -> list[str]:
    """The state handlers of the stage process a loader sets."""
    if PROCESS_TABLE not in tree.blocks:
        return []
    process = _process(tree, loader, set()) or 0
    dispatchers = pointer_table(tree, PROCESS_TABLE)
    if not 0 <= process // 4 < len(dispatchers):
        return []
    table = next(
        (
            match["label"]
            for line in routine_lines(tree, dispatchers[process // 4])
            if (match := STATE_TABLE.match(line))
        ),
        None,
    )
    return _table_routines(tree, table) if table and table in tree.blocks else []


def _reachable_lines(tree: SourceTree, label: str) -> list[str]:
    """A handler's lines and those of the routines and branches it reaches in
    its own file (a few), in the order they are reached."""
    path = tree.block(label).file.path if label in tree.blocks else None
    lines, queue, seen = [], [label], set()
    while queue and len(seen) < MAX_ROUTINES:
        name = queue.pop(0)
        if name in seen or name not in tree.blocks:
            continue
        seen.add(name)
        body = routine_lines(tree, name)
        lines += body
        for line in body:
            match = BRANCH.match(line)
            if match and match["label"] in tree.blocks and tree.block(match["label"]).file.path == path:
                queue.append(match["label"])
    return lines


def worked_camera_y(tree: SourceTree, handler: str) -> int | None:
    """The camera Y a handler works out in d0 from words it stores, if any."""
    words: dict[str, int] = {}
    d0 = None
    for line in _reachable_lines(tree, handler):
        if (match := STORE.match(line)) and parse_number(match["value"]) is not None:
            words[match["label"]] = parse_number(match["value"]) & 0xFFFF
        elif match := LOAD.match(line):
            d0 = parse_number(match["value"]) if match["value"] else words.get(match["label"])
        elif (match := ARITHMETIC.match(line)) and d0 is not None and parse_number(match["value"]) is not None:
            step = parse_number(match["value"])
            d0 = (d0 + step if match["op"] == "addi" else d0 - step) & 0xFFFF
        elif NEGATE.match(line) and d0 is not None:
            d0 = -d0 & 0xFFFF
        elif SET_Y.match(line) and d0 is not None:
            return d0
    return None


def _first_word(tree: SourceTree, handler: str) -> int | None:
    """The first value a handler loads into d0 (an immediate, or a word it
    stored), if any."""
    words: dict[str, int] = {}
    for line in _reachable_lines(tree, handler):
        if (match := STORE.match(line)) and parse_number(match["value"]) is not None:
            words[match["label"]] = parse_number(match["value"]) & 0xFFFF
        elif match := LOAD.match(line):
            return parse_number(match["value"]) if match["value"] else words.get(match["label"])
    return None


def indexed_rows(tree: SourceTree, routine: str) -> list[dict[str, Any]]:
    """The rows of RAM blocks a stage initializer queues straight into a
    name table (`Tilemap_QueueIndexedRows` with a0 at their description)."""
    found, lines = [], routine_lines(tree, routine) if routine in tree.blocks else []
    for at, line in enumerate(lines):
        if not INDEXED_ROWS.match(line):
            continue
        label = next((match["label"] for other in lines[at - 1 :: -1] if (match := LEA_PC.match(other))), None)
        block = tree.block(label) if label in tree.blocks else None
        if block is None or block.kind != "dc.w":
            continue
        words = block.values()
        data = b"".join(word.to_bytes(2, "big") for word in words)
        per_row, groups = data[4] + 1, data[5] + 1
        indices = list(data[6 : 6 + per_row * groups])
        if len(indices) == per_row * groups:
            found.append({"vram": words[0], "ram": 0xFFFF0000 | words[1], "blocks": indices, "per_row": per_row})
    return found


def _own_handlers(tree: SourceTree, loader: str, state: int, states: list[int]) -> list[str]:
    """A stage's state handlers: from its first state up to the first state
    of another stage (`states`) or MAX_HANDLERS."""
    end = min([other for other in states if other > state], default=state + 2 * MAX_HANDLERS)
    return _state_handlers(tree, loader)[state // 2 : min(end, state + 2 * MAX_HANDLERS) // 2]


def _reached(tree: SourceTree, handlers: list[str]) -> list[list[str]]:
    """The lines of each routine the handlers reach through calls and
    branches into any file, a couple of levels deep."""
    seen: set[str] = set()
    found = []

    def walk(label: str, depth: int) -> None:
        if label in seen or label not in tree.blocks:
            return
        seen.add(label)
        lines = routine_lines(tree, label)
        found.append(lines)
        if depth > 0:
            for line in lines:
                if match := BRANCH.match(line):
                    walk(match["label"], depth - 1)

    for handler in handlers:
        walk(handler, QUARTER_DEPTH)
    return found


def quarter_plane_b(tree: SourceTree, loader: str, state: int, states: list[int] = ()) -> bool:
    """Whether the stage's handlers scroll plane B at a quarter of the
    camera X, through calls and branches into any file."""
    handlers = _own_handlers(tree, loader, state, list(states))
    return any(QUARTER.match(line) for lines in _reached(tree, handlers) for line in lines)


def plane_b_follows(tree: SourceTree, loader: str, state: int, states: list[int] = ()) -> bool:
    """Whether the stage's handlers set plane B's camera X to plane A's."""
    handlers = _own_handlers(tree, loader, state, list(states))
    return any(FOLLOW.match(line) for lines in _reached(tree, handlers) for line in lines)


def plane_streams(tree: SourceTree, handlers: list[str]) -> dict[str, int]:
    """The rows the handlers stream each plane descriptor from, below plane
    A's (a d1 of camera Y - n is n rows below; a fixed d1 is measured from
    camera Y 0); later handlers win."""
    found: dict[str, int] = {}
    for handler in handlers:
        for lines in _reached(tree, [handler]):
            d1 = None
            for at, line in enumerate(lines):
                if match := QUEUE_Y.match(line):
                    if match["camera"]:
                        d1 = 0
                    elif match["value"] and parse_number(match["value"]) is not None:
                        d1 = _signed(parse_number(match["value"]) & 0xFFFF)
                    elif d1 is not None and parse_number(match["step"]) is not None:
                        step = _signed(parse_number(match["step"]) & 0xFFFF)
                        d1 += step if match["op"] == "addi" else -step
                elif (
                    (match := STREAM_DESCRIPTOR.match(line))
                    and d1 is not None
                    and any(QUEUE.search(other) for other in lines[at + 1 : at + 3])
                ):
                    found[match["label"]] = -d1
    return found


def secondary_streams(tree: SourceTree) -> dict[int, dict[str, Any]]:
    """The plane columns a stage streams from the secondary camera (d0 its
    X, d1 its Y), by the object type its handler spawns before (Stage 9
    streams the Caterpillar's ship so): the scene, the descriptor, and the
    camera Y the handlers set before."""
    found_scenes = scenes(tree)
    found_records = records(tree)
    loaders = _table_routines(tree, LOADERS)
    states = stage_states(tree, found_scenes)
    found: dict[int, dict[str, Any]] = {}
    for scene in found_scenes:
        record = found_records.get(scene["record"])
        if not record or scene["index"] >= len(loaders):
            continue
        loader = loaders[scene["index"]]
        camera_y = 0
        for handler in _own_handlers(tree, loader, record["state_offset"], states.get(loader, [])):
            spawned = None
            for lines in _reached(tree, [handler]):
                across = down = False
                for at, line in enumerate(lines):
                    if match := SECONDARY_Y.match(line):
                        camera_y = parse_number(match["value"]) if match["value"] else 0
                    elif match := ENTITY.match(line):
                        spawned = parse_number(match["value"])
                    elif SECONDARY_D0.match(line):
                        across = True
                    elif SECONDARY_D1.match(line):
                        down = True
                    elif (
                        (match := STREAM_DESCRIPTOR.match(line))
                        and across
                        and down
                        and spawned is not None
                        and any(QUEUE.search(other) for other in lines[at + 1 : at + 3])
                        # The first type spawned before it (Xi-Tiger comes
                        # once the ship has passed).
                        and all(item["descriptor"] != match["label"] for item in found.values())
                    ):
                        found[spawned] = {"scene": scene, "descriptor": match["label"], "camera_y": camera_y & 0xFFFF}
    return found


def spawning_scenes(tree: SourceTree) -> dict[int, dict[str, Any]]:
    """The scene whose stage handlers first put each object type in the
    first object record (Stage 16 brings Viblack so), by type, and the
    camera X they set before (None if none)."""
    found_scenes = scenes(tree)
    found_records = records(tree)
    loaders = _table_routines(tree, LOADERS)
    states = stage_states(tree, found_scenes)
    found: dict[int, dict[str, Any]] = {}
    for scene in found_scenes:
        record = found_records.get(scene["record"])
        if not record or scene["index"] >= len(loaders):
            continue
        loader = loaders[scene["index"]]
        handlers = _own_handlers(tree, loader, record["state_offset"], states.get(loader, []))
        camera_x = None
        for lines in _reached(tree, handlers):
            for line in lines:
                if match := CAMERA_X.match(line):
                    camera_x = parse_number(match["value"])
                elif match := ENTITY.match(line):
                    found.setdefault(parse_number(match["value"]), {"scene": scene, "camera_x": camera_x})
    return found


def scene_ram(tree: SourceTree, scene: dict[str, Any]) -> bytearray:
    """The $FFFF0000 page as the scene's map loads leave it, the ones its
    camera brings later too."""
    return _ram(tree, scene["map_writes"] + [write for load in scene.get("later", []) for write in load["map_writes"]])


def blank_tables(tree: SourceTree, handlers: list[str]) -> set[int]:
    """The plane tables the handlers fill with blank rows (a fill word of 0
    queued row by row)."""
    found = set()
    # The table and fill word are set in one handler, the rows queued later.
    table, blank = None, False
    for lines in _reached(tree, handlers):
        for line in lines:
            if match := FILL_TABLE.match(line):
                table = parse_number(match["value"])
            elif match := STREAM_X.match(line):
                blank = not match["value"] or parse_number(match["value"]) == 0
            elif FILL_ROWS.search(line) and table is not None and blank:
                found.add(table)
    return found


def played_lists(tree: SourceTree, lists: dict[str, list[AssetCommand]], handlers: list[str]) -> list[str]:
    """The asset lists the handlers play (`Data_ProcessPointer`), in order."""
    found = []
    for handler in handlers:
        lines = routine_lines(tree, handler)
        for at, line in enumerate(lines):
            match = LEA_PC.match(line)
            if (
                match
                and match["label"] in lists
                and match["label"] not in found
                and any(PLAY_LIST.search(other) for other in lines[at + 1 : at + 4])
            ):
                found.append(match["label"])
    return found


def camera_reach(
    tree: SourceTree, loader: str, state: int, start: int, states: list[int] = ()
) -> tuple[int, int, int, list[int], int | None, bool] | None:
    """Where the stage's state handlers move the camera, as (start X,
    leftmost X, furthest X, the camera Ys of its climbs, the camera Y they
    work out, whether the picture stays where the entry drew it), from its
    first state through the boss's; None when they set no X limit."""
    lows, highs, climbs = [], [], []
    pinned = None
    record_x, draws = start, False
    start_y = None
    for handler in _own_handlers(tree, loader, state, list(states)):
        if start_y is None and not highs and not climbs:
            start_y = worked_camera_y(tree, handler)
        lines = routine_lines(tree, handler)
        draws = draws or any(DRAWS.match(line) for line in lines)
        for line in lines:
            value = None
            if match := CAMERA_Y_LIMIT.match(line):
                climbs.append(parse_number(match["value"]))
                continue
            if PIN_Y.match(line) and pinned is not None:
                climbs.append(pinned)
                continue
            if (match := CAMERA_SET.match(line)) and not highs:
                start = parse_number(match["value"]) if parse_number(match["value"]) is not None else start
            elif match := CAMERA_LIMIT.match(line):
                value = parse_number(match["compare"] or match["upper"])
            elif match := CAMERA_LOWER.match(line):
                low = parse_number(match["value"]) if match["value"] else 0
                if low is not None and low < 0x8000:
                    lows.append(low)
            elif match := PIN_VALUE.match(line):
                pinned = parse_number(match["value"])
            elif PIN.match(line):
                value = pinned
            if value is not None and value < 0x8000:
                highs.append(value)
        if any(BOSS.search(line) for line in lines):
            break
    if not highs:
        return None if start_y is None else (start, start, start, [], start_y, False)
    left, furthest = min([start, *lows]), max([start, *highs])
    kept = not draws and start != record_x
    if kept:
        # The picture stays where the entry drew it.
        left, furthest, start = left - start + record_x, furthest - start + record_x, record_x
    return start, left, furthest, [value for value in climbs if value is not None], start_y, kept


def _asset_set(tree: SourceTree, lists: dict[str, list[AssetCommand]], label: str) -> dict[str, Any] | None:
    """A boss asset set's object type, graphics list and palette command."""
    if label not in tree.blocks or tree.block(label).kind != "mixed":
        return None
    fields = [operand.text for directive in tree.block(label).directives for operand in directive.operands]
    if len(fields) < 3 or fields[1] not in lists or parse_number(fields[0]) is None:
        return None
    return {"type": parse_number(fields[0]), "list": fields[1], "palette": fields[2]}


def _boss_files(tree: SourceTree, handler: str) -> list[str]:
    """The source file of a boss's handler and those named after it, in the
    same folder (z_leo_core.s, z_leo_rendering.s)."""
    path = PurePosixPath(tree.block(handler).file.path)
    prefix = path.stem.split("_")[0] + "_"
    # The files in the order the build includes them.
    return [
        name
        for name, source in tree.files.items()
        if (other := PurePosixPath(name)).parent == path.parent
        and other.name.startswith(prefix)
        and tree.labels_in(name)
    ]


def _brought_in(tree: SourceTree, lists: dict[str, list[AssetCommand]], kind: int) -> dict[str, Any] | None:
    """The boss asset set the code of object type `kind` loads at an object:
    a set in the handler's file or the files named after it (viblack_*)."""
    from retrolarium_kit.games.alien_soldier.levels import handlers

    handler = handlers(tree).get(f"${kind:X}")
    if handler not in tree.blocks:
        return None
    for label in (label for path in _boss_files(tree, handler) for label in tree.labels_in(path)):
        body = tree.body(label)
        for at, line in enumerate(body):
            match = BOSS_SET.match(line)
            if match and any(LOAD_AT_OBJECT.search(next_line) for next_line in body[at + 1 : at + 3]):
                found = _asset_set(tree, lists, match["label"])
                if found:
                    return found
    return None


def arenas(
    tree: SourceTree,
    loader: str,
    state: int,
    camera_x: int,
    states: list[int] = (),
    lists: dict[str, list[AssetCommand]] | None = None,
) -> list[dict[str, Any]]:
    """The screens a stage's handlers stream from elsewhere in the map: the
    map X of the plane's left edge plus the camera X within the plane (the
    last one the handlers wait for or pin before it, else `camera_x`), and
    the 256 lines streamed down from row -Y (all the plane holds)."""
    found = []
    pinned = None
    boss = None
    bosses, loaded, defeated = 0, None, False
    own = _own_handlers(tree, loader, state, list(states))
    # A stage that restarts another's run (stage 22 sets the state to $C)
    # goes on with that run's handlers.
    jump = (
        next((match for line in routine_lines(tree, own[0]) if (match := STATE_JUMP.match(line))), None)
        if own
        else None
    )
    if jump and parse_number(jump["value"]) is not None:
        start = parse_number(jump["value"]) // 2
        own = own[:1] + _state_handlers(tree, loader)[start : start + MAX_HANDLERS]
    for at, handler in enumerate(own):
        lines = routine_lines(tree, handler)
        if at and jump and any(STATE_JUMP.match(line) for line in lines):
            break  # another stage's restart
        # The stage's fight streams before or after its boss loads; a boss
        # loaded after that fight's screen, or a second one, belongs to what
        # follows (stage 21 runs on into 22's, 22's into Wolf-Garopa's).
        if any(BOSS.search(line) for line in lines):
            bosses += 1
            if found or bosses > 1:
                break
            asset_set = next((match["label"] for line in lines if (match := BOSS_SET.match(line))), None)
            loaded = _asset_set(tree, lists or {}, asset_set) if asset_set and lists else loaded
        elif bosses and any(POOL_EMPTY.match(line) for line in lines):
            # Waiting for the boss to go: what streams now is the next part's.
            defeated = True
        stream_x = stream_y = None
        # The handler and the code it ends by branching to (stage 22's
        # restart goes on in the backdrop set-up).
        tail = TAIL_BRANCH.match(next((line for line in reversed(lines) if line.strip()), ""))
        for line in lines + (routine_lines(tree, tail["label"]) if tail and tail["label"] in tree.blocks else []):
            if (match := ENTITY.match(line)) and parse_number(match["value"]):
                boss = parse_number(match["value"])
            if match := CAMERA_LIMIT.match(line):
                value = parse_number(match["compare"] or match["upper"])
                camera_x = value if value is not None and value < 0x8000 else camera_x
            elif match := PIN_VALUE.match(line):
                pinned = parse_number(match["value"])
            elif PIN.match(line) and pinned is not None and pinned < 0x8000:
                camera_x = pinned
            elif match := STREAM_X.match(line):
                stream_x = parse_number(match["value"]) if match["value"] else 0
            elif match := STREAM_Y.match(line):
                stream_y = parse_number(match["value"])
        if stream_x is not None and stream_y is not None and stream_x < 0x8000:
            # The 256 lines streamed down from row -Y, the plane's height.
            arena: dict[str, Any] = {
                "x": stream_x + camera_x % PLANE_WIDTH,
                "y": (-_signed(stream_y)) % (MAP_CHUNKS * CHUNK),
                "width": SCREEN[0],
                "height": PLANE_WIDTH // 2,
            }
            fought = _brought_in(tree, lists or {}, boss) if boss is not None and lists else None
            if defeated and not fought:
                # After the boss, only a boss brought in fights there (Viblack
                # brings Back Stringer); the Destroyer Prototype's stage sets
                # up Shield Viper's backdrop once it is gone.
                continue
            # Else the set the stage loaded for its boss.
            fought = fought or loaded
            if fought:
                commands = lists[fought["list"]]
                arena.update(
                    {
                        "list": fought["list"],
                        "map_writes": _writes(tree, commands),
                        "vram": _vram(commands),
                        "palettes": [[fought["palette"], 0]] if fought["palette"] in tree.blocks else [],
                    }
                )
            if not any((other["x"], other["y"]) == (arena["x"], arena["y"]) for other in found):
                found.append(arena)
    return found


def climbed_arenas(
    tree: SourceTree,
    lists: dict[str, list[AssetCommand]],
    loader: str,
    state: int,
    states: list[int],
    area: dict[str, Any],
    record: dict[str, Any] | None = None,
    planes: list[dict[str, Any] | None] = (),
) -> list[dict[str, Any]]:
    """The screen a stage's camera climbs to before its boss loads, when the
    stage's area does not take it in (the Destroyer Prototype fights in the
    empty space above the asteroid field, camera Y $F600). Plane B's camera
    stays where the record put it, so plane B is read from the climbed
    camera Y less that one (the stars, not the asteroids)."""
    if area.get("parts"):
        return []
    climb = None
    for handler in _own_handlers(tree, loader, state, list(states)):
        lines = routine_lines(tree, handler)
        if any(BOSS.search(line) for line in lines):
            asset_set = next((match["label"] for line in lines if (match := BOSS_SET.match(line))), None)
            found = _asset_set(tree, lists, asset_set) if asset_set else None
            if climb is None or not found:
                return []
            # Rows are read at -Y; a climb within a screen of the start is no
            # other screen (Flying-Neo's lift).
            top = -_signed(climb)
            if abs(top - area["y"]) < SCREEN[1]:
                return []
            commands = lists[found["list"]]
            fight = list(planes)
            if record and len(fight) > 1 and fight[1]:
                shift = _signed(climb) - _signed(record["secondary_camera_y"])
                fight[1] = {**fight[1], "y_offset": fight[1].get("y_offset", 0) + shift}
            return [
                {
                    "x": area["x"],
                    "y": top,
                    "width": SCREEN[0],
                    "height": SCREEN[1],
                    **({"planes": fight} if fight else {}),
                    "list": found["list"],
                    "lists": [],
                    "map_writes": _writes(tree, commands),
                    "vram": _vram(commands),
                    "palettes": [[found["palette"], 0]] if found["palette"] in tree.blocks else [],
                }
            ]
        for line in lines:
            if (match := CAMERA_Y_LIMIT.match(line)) and parse_number(match["value"]) is not None:
                climb = parse_number(match["value"])
    return []


def _ram(tree: SourceTree, writes: list[dict[str, Any]]) -> bytearray:
    """The $FFFF0000 page as the writes leave it."""
    ram = bytearray(0x10000)
    for write in writes:
        data = lzss.decompress(tree.data(write["source"]))
        at = write["ram"] - RAM_BASE
        ram[at : at + len(data)] = data[: 0x10000 - at]
    return ram


def _name_word(ram: bytearray, plane: dict[str, Any], x: int, y: int) -> int:
    """The name-table word a plane reads at map (x, y), as the studio does."""
    y += plane.get("y_offset", 0)
    coarse = plane["coarse"] - RAM_BASE + ((y >> 8) & 31) * MAP_CHUNKS + ((x >> 8) & 31)
    chunk = plane["chunks"] - RAM_BASE + ram[coarse] * 64 + ((y >> 5) & 7) * 8 + ((x >> 5) & 7)
    at = plane["patterns"] - RAM_BASE + ram[chunk & 0xFFFF] * 32 + ((y >> 3) & 3) * 8 + ((x >> 3) & 3) * 2
    return int.from_bytes(ram[at & 0xFFFF : (at & 0xFFFF) + 2], "big")


def _place_inline(
    tree: SourceTree, scene: dict[str, Any], arena: dict[str, Any], run: dict[str, Any], record: dict[str, Any]
) -> None:
    """Widen an inline arena to what its own plane B holds across the plane
    (Z-Leo's core room is 384 pixels: the camera follows him across it), and
    show it over the end of the stage's run where the stage's plane B reads
    the same rows of the same map (the room the corridor ends in, as the
    plane repeats every 512 pixels): (`shown`)."""
    own = arena.get("planes", [None, None])[1]
    stage = scene["planes"][1]
    if not own:
        return
    ram = _ram(tree, scene["map_writes"] + arena.get("map_writes", []))
    right = arena["x"]
    for x in range(arena["x"], arena["x"] + PLANE_WIDTH, 8):
        if any(_name_word(ram, own, x, y) & 0x7FF for y in range(arena["y"], arena["y"] + arena["height"], 8)):
            right = x + 8
    arena["width"] = max(arena["width"], right - arena["x"])
    if (
        not stage
        or (stage["coarse"], stage["chunks"]) != (own["coarse"], own["chunks"])
        or scene.get("plane_b_quarter")
        or scene.get("plane_b_follows")
    ):
        return
    # The stage's plane B reads map (x + secondary X - camera X, y + camera Y
    # - secondary Y), as the studio draws it.
    origin_x = _signed(record["secondary_camera_x"]) - _signed(record["camera_x"])
    offset_y = _signed(record["camera_y"]) - _signed(record["secondary_camera_y"]) + stage.get("y_offset", 0)
    shown_y = arena["y"] + own.get("y_offset", 0) - offset_y
    last = run["x"] + run["width"] - arena["width"]
    shown_x = last - ((last + origin_x - arena["x"]) % PLANE_WIDTH)
    if shown_x >= run["x"] and run["y"] <= shown_y + arena["height"] <= run["y"] + run["height"] + SCREEN[1]:
        arena["shown"] = {"x": shown_x, "y": shown_y}


def boss_arenas(
    tree: SourceTree, lists: dict[str, list[AssetCommand]], loader: str, state: int, states: list[int] = ()
) -> list[dict[str, Any]]:
    """The screens a stage's boss moves its fight to (see the module text)."""
    from retrolarium_kit.games.alien_soldier.levels import handlers

    types = handlers(tree)
    found = []
    for handler in _own_handlers(tree, loader, state, list(states)):
        lines = routine_lines(tree, handler)
        if not any(BOSS.search(line) for line in lines):
            continue
        asset_set = next((match["label"] for line in lines if (match := BOSS_SET.match(line))), None)
        if asset_set not in tree.blocks or tree.block(asset_set).kind != "mixed":
            continue
        fields = [operand.text for directive in tree.block(asset_set).directives for operand in directive.operands]
        if len(fields) < 2 or fields[1] not in lists or f"${parse_number(fields[0]) or 0:X}" not in types:
            continue
        boss = types[f"${parse_number(fields[0]):X}"]
        if boss not in tree.blocks:
            continue
        # The boss's code: its handler's file and the files named after it.
        code = [line for path in _boss_files(tree, boss) for label in tree.labels_in(path) for line in tree.body(label)]
        camera, rises, streamed, played = None, [], None, []
        for at, line in enumerate(code):
            if (
                (match := ROW_DESCRIPTOR.match(line))
                and match["label"] in lists
                and any(PLAY_LIST.search(other) for other in code[at + 1 : at + 4])
                and match["label"] not in played
            ):
                played.append(match["label"])
            if (match := ROW_DESCRIPTOR.match(line)) and any(
                QUEUE_ROW.search(other) for other in code[at + 1 : at + 4]
            ):
                block = tree.block(match["label"]) if match["label"] in tree.blocks else None
                words = block.values() if block is not None and block.kind == "dc.w" else []
                if len(words) >= 8 and streamed is None:
                    coarse, chunks, patterns, table = ((words[i] << 16) | words[i + 1] for i in range(0, 8, 2))
                    plane = {"coarse": coarse, "chunks": chunks, "patterns": patterns, "plane": table, "y_offset": 0}
                    streamed = [None, plane] if table == PLANE_B_TABLE else [plane, None]
            if (match := TELEPORT_X.match(line)) and camera is None:
                y = next((TELEPORT_Y.match(other) for other in code[at + 1 : at + 3] if TELEPORT_Y.match(other)), None)
                if y and parse_number(y["value"]) is not None:
                    camera = (parse_number(match["value"]) if match["value"] else 0, parse_number(y["value"]))
            elif match := RISE.match(line):
                if parse_number(match["value"]) is not None:
                    rises.append(parse_number(match["value"]))
        if camera is None:
            continue
        highest = max([camera[1], *(value for value in rises if value < 0x8000)])
        commands = [command for name in [fields[1], *played] for command in lists[name]]
        found.append(
            {
                "x": camera[0],
                "y": -highest,
                "width": SCREEN[0],
                "height": highest + SCREEN[1],
                "inline": True,
                **({"planes": streamed} if streamed else {}),
                "list": fields[1],
                "lists": played,
                "map_writes": _writes(tree, commands),
                "vram": _vram(commands),
                "palettes": [[fields[2], 0]] if len(fields) > 2 else [],
            }
        )
    return found


def widened_arenas(
    tree: SourceTree,
    lists: dict[str, list[AssetCommand]],
    loader: str,
    state: int,
    states: list[int],
    planes: list[dict[str, Any] | None],
) -> list[dict[str, Any]]:
    """The screen a boss fights on when it widens the X bounds the stage
    pins (see the module text)."""
    from retrolarium_kit.games.alien_soldier.levels import handlers as type_handlers

    types = type_handlers(tree)
    own = _own_handlers(tree, loader, state, list(states))
    pinned = None
    for at, handler in enumerate(own):
        lines = routine_lines(tree, handler)
        value = None
        for line in lines:
            if match := PIN_VALUE.match(line):
                value = parse_number(match["value"])
            elif PIN.match(line) and value is not None and pinned is None:
                pinned = (at, value)
        if not any(BOSS.search(line) for line in lines) or pinned is None:
            continue
        asset_set = next((match["label"] for line in lines if (match := BOSS_SET.match(line))), None)
        found = _asset_set(tree, lists, asset_set) if asset_set else None
        boss = types.get(f"${found['type']:X}") if found else None
        if boss not in tree.blocks:
            return []
        code = [line for path in _boss_files(tree, boss) for label in tree.labels_in(path) for line in tree.body(label)]
        widen = next((parse_number(match["value"]) for line in code if (match := WIDEN.match(line))), None)
        # The camera Ys the boss's state and the ones after it swing through.
        swings = [
            parse_number(match["value"])
            for reached in _reached(tree, own[at:])
            for line in reached
            if (match := CAMERA_Y_LIMIT.match(line))
        ]
        swings = [value for value in swings if value is not None and value < 0x8000]
        if not widen or not swings:
            return []
        streams = plane_streams(tree, own[pinned[0] : at + 1])
        fight = []
        for plane in planes:
            if plane and plane.get("descriptor") in streams:
                plane = {**plane, "y_offset": streams[plane["descriptor"]]}
            fight.append(plane)
        commands = lists[found["list"]]
        low, high = min(swings), max(swings)
        return [
            {
                "x": pinned[1] - widen,
                "y": -high,
                "width": widen + SCREEN[0],
                "height": high - low + SCREEN[1],
                "planes": fight,
                "list": found["list"],
                "lists": [],
                "map_writes": _writes(tree, commands),
                "vram": _vram(commands),
                "palettes": [[found["palette"], 0]] if found["palette"] in tree.blocks else [],
            }
        ]
    return []


def opening_lists(tree: SourceTree, lists: dict[str, list[AssetCommand]], loader: str, state: int) -> list[str]:
    """The asset lists a stage's first state handler plays before it waits
    for the camera."""
    handlers = _state_handlers(tree, loader)[state // 2 : state // 2 + 1]
    found = []
    for handler in handlers:
        lines = routine_lines(tree, handler)
        for at, line in enumerate(lines):
            if CAMERA_GATE.match(line):
                break
            match = LEA_PC.match(line)
            if match and match["label"] in lists and any(PLAY_LIST.search(other) for other in lines[at + 1 : at + 4]):
                found.append(match["label"])
    return found


def later_loads(
    tree: SourceTree, lists: dict[str, list[AssetCommand]], loader: str, state: int
) -> list[tuple[int, str]]:
    """The asset lists a stage's state handlers play once the camera passes
    an X, as (X, list), from its first state to the next phase or boss."""
    found = []
    for handler in _state_handlers(tree, loader)[state // 2 : state // 2 + MAX_HANDLERS]:
        lines = routine_lines(tree, handler)
        gate = None
        for line in lines:
            match = CAMERA_GATE.match(line)
            if match:
                gate = parse_number(match["value"])
                continue
            lea = LEA_PC.match(line)
            if lea and gate is not None and lea["label"] in lists:
                found.append((gate, lea["label"]))
        if any(PHASE_END.search(line) for line in lines):
            break
    return found


def _writes(tree: SourceTree, commands: list[AssetCommand]) -> list[dict[str, Any]]:
    writes = []
    for command in commands:
        if command.command == LZSS_TO_RAM and command.source in tree.blocks:
            size = len(lzss.decompress(tree.data(command.source)))
            writes.append({"source": command.source, "ram": RAM_BASE + command.ram_offset, "size": size})
    return writes


def _vram(commands: list[AssetCommand]) -> list[dict[str, Any]]:
    return [
        {"command": command.command, "source": command.source, "vram": command.destination}
        for command in commands
        if command.command in (1, 2, 3, 5, 7)
    ]


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
        if record and index < len(loaders):
            # The lists the stage's first state plays at once (stage 7's
            # counter art) are part of how it starts.
            for name in opening_lists(tree, lists, loaders[index], record["state_offset"]):
                names.append(name)
                commands += lists[name]
        writes = _lasting(_writes(tree, commands))
        later = [
            {"camera_x": x, "list": name, "map_writes": _writes(tree, lists[name]), "vram": _vram(lists[name])}
            for x, name in (later_loads(tree, lists, loaders[index], record["state_offset"]) if record else [])
        ]
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
                        "y_offset": 0,
                        "terrain_y": OFFSET_VARIANT_Y if which == 0 and mode & OFFSET_VARIANT else 0,
                    }
            planes.append(plane)
        result.append(
            {
                "index": index,
                "record": slot["record"],
                "lists": names,
                "map_writes": writes,
                # Tiles the loader works out go up first (the moon's surface).
                "vram": (resampled_loads(tree, loaders[index]) if index < len(loaders) else []) + _vram(commands),
                "tile_attributes": {f"${key:X}": value for key, value in sorted(attributes.items())},
                "planes": planes,
                "transforms": map_transforms(tree, slot["initializer"]),
                "later": later,
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
    found_records = records(tree)
    loaders = _table_routines(tree, LOADERS)
    initializers = [slot["initializer"] for slot in stages(tree)]
    states = stage_states(tree, found)
    lists = asset_lists(tree)
    for scene in found:
        if scene["index"] in areas:
            scene["area"] = areas[scene["index"]]
            record = found_records.get(scene["record"])
            if record and scene["index"] < len(loaders):
                area = scene["area"]
                loader = loaders[scene["index"]]
                scene["plane_b_quarter"] = quarter_plane_b(tree, loader, record["state_offset"], states.get(loader, []))
                scene["plane_b_follows"] = plane_b_follows(tree, loader, record["state_offset"], states.get(loader, []))
                own_streams = plane_streams(
                    tree, _own_handlers(tree, loader, record["state_offset"], states.get(loader, []))
                )
                first, second = scene["planes"]
                if first and second and second["descriptor"] in own_streams and first["descriptor"] not in own_streams:
                    # Plane A is never streamed: it keeps the 64 cells the
                    # entry drew at the record's X (the train's haze).
                    first["fixed_x"] = record["camera_x"]
                handlers = _own_handlers(tree, loader, record["state_offset"], states.get(loader, []))
                scroll = _first_word(tree, handlers[0]) if handlers else None
                rows = indexed_rows(tree, initializers[scene["index"]])
                if rows and scroll is not None:
                    # A name table row is 64 cells of two bytes.
                    row = (rows[0]["vram"] & 0x1FFF) // 0x80
                    line = row * 8 - scroll
                    if 0 < line < SCREEN[1]:
                        scene["floor"] = {key: rows[0][key] for key in ("ram", "blocks", "per_row")} | {"line": line}
                found_arenas = arenas(
                    tree,
                    loader,
                    record["state_offset"],
                    area["x"] + area["width"] - SCREEN[0],
                    states.get(loader, []),
                    lists,
                ) + boss_arenas(tree, lists, loader, record["state_offset"], states.get(loader, []))
                found_arenas += climbed_arenas(
                    tree, lists, loader, record["state_offset"], states.get(loader, []), area, record, scene["planes"]
                )
                found_arenas += widened_arenas(
                    tree, lists, loader, record["state_offset"], states.get(loader, []), scene["planes"]
                )
                if area.get("apart"):
                    # Shown as the boss finds it: the lists the handlers play,
                    # from the stage's first X, and no plane they blank.
                    for name in played_lists(tree, lists, handlers):
                        if name in scene["lists"]:
                            continue
                        scene["later"].append(
                            {
                                "camera_x": area["x"] - SCREEN[0],
                                "list": name,
                                "map_writes": _writes(tree, lists[name]),
                                "vram": _vram(lists[name]),
                            }
                        )
                    for table in blank_tables(tree, handlers):
                        scene["planes"] = [
                            None if plane and plane["plane"] == table else plane for plane in scene["planes"]
                        ]
                if found_arenas:
                    scene["arenas"] = found_arenas
                for arena in found_arenas:
                    for write in arena.get("map_writes", []):
                        if write["source"] not in editable:
                            write["data"] = lzss.decompress(tree.data(write["source"])).hex().upper()
                    if not arena.get("inline"):
                        continue
                    parts = area.pop("parts", None) or [{key: area[key] for key in ("x", "y", "width", "height")}]
                    _place_inline(tree, scene, arena, parts[0], record)
                    # Shown with the stage: its area takes the arena in.
                    shown = arena.get("shown", arena)
                    rect = {"x": shown["x"], "y": shown["y"], "width": arena["width"], "height": arena["height"]}
                    left, top = min(area["x"], rect["x"]), min(area["y"], rect["y"])
                    right = max(area["x"] + area["width"], rect["x"] + rect["width"])
                    bottom = max(area["y"] + area["height"], rect["y"] + rect["height"])
                    area.update(
                        {"x": left, "y": top, "width": right - left, "height": bottom - top, "parts": [*parts, rect]}
                    )
        for write in scene["map_writes"] + [write for load in scene["later"] for write in load["map_writes"]]:
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
