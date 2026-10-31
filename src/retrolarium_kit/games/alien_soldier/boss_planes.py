"""Bosses whose body is a plane's tiles their own code queues frame by frame.

The column-scroll boss, the tiled-body boss, the column-backed boss, the column-backed boss, Victor and the stage-backed boss build no
skeleton: their state machines queue blocks of a plane's name table over
their first frames (`Tilemap_QueueIndexedRows` and `...Columns`: a VDP
address word, the patterns' RAM, the block's size in 4x4 groups less one
each way and the groups' indices; `Tilemap_QueueFourRowsFromPackedCommand`:
one group from RAM), scroll the plane with themselves, and move a few
records of their own. `Run` plays them as the game's frame does: every
record moves by its velocity, then runs its type's routine (pose_code's
lenient subset, the boss's files and the game's sprite, math and entity
helpers followed), with a stand-in player; the tile queues are done here
into a name table (`vram`), and the game's own scroll writer then turns the
cameras and the boss's own scroll tables into the line and column scroll
the VDP would take. Once the body has stayed as it is a while, it is the
plane's cells the boss wrote, placed where that scroll shows them from the
boss.

The Caterpillar's body is no code of its own: the stage streams it into
plane B column by column from the secondary camera, out of a map of its own
(`streamed_body`). The radial-chain boss queues its stem and head into plane B and
writes its own X into the line scroll of the lines they cover, where the
stage's raster split, which no frame's scroll tables show, leaves them
unscrolled down: `Run.line_scrolled` places them so.
"""

from __future__ import annotations

from typing import Any

from retrolarium_kit.games.alien_soldier import pose_code
from retrolarium_kit.platform.megadrive.asl import AslSourceError, SourceTree

QUEUE_BLOCKS = ("Tilemap_QueueIndexedRows", "Tilemap_QueueIndexedColumns")
QUEUE_GROUP = "Tilemap_QueueFourRowsFromPackedCommand"
SCROLL = "Scroll_PreparePlaneBuffersAndRegisterShadows"
PLANE_A, PLANE_B = 0xC000, 0xE000
SCROLL_STATE = (
    "ScrollPlaneBufferOffset",
    "PlaneAScrollModeFlags",
    "PlaneAShakeOffset",
    "PlaneBShakeOffset",
    "PrimaryCameraXPosition",
    "PrimaryCameraYPosition",
    "SecondaryCameraXPos",
    "SecondaryCameraYPos",
    "VDPReg11Shadow",
)
# Where the player stands as a boss plays.
PLAYER = (("PlayerXPosition", 0x120), ("PlayerYPosition", 0x140))
# Planes are 64 cells across (a row is $80 bytes) and 32 down.
ROW, ACROSS, DOWN = 0x80, 64, 32
# Routines a record's code follows (Epsilon 1 queues its bands in a dozen).
CALLS = 32
# Frames a boss plays at most, and the frames its plane must stay as it is.
FRAMES, SETTLE = 600, 30


def queues_tiles(tree: SourceTree, files: list[str]) -> bool:
    """Whether the boss's files queue a plane's tiles."""
    names = (*QUEUE_BLOCKS, QUEUE_GROUP)
    return any(
        any(name in line for name in names)
        for path in files
        for label in tree.labels_in(path)
        for line in tree.body(label)
    )


class Run:
    """A boss's records played frame by frame on `machine` (see the module
    text); `vram` holds the name-table words they queued."""

    def __init__(self, tree: SourceTree, files: list[str], machine: pose_code.Machine, slot: int, part_size: int):
        self.tree, self.files, self.machine = tree, files, machine
        self.slot, self.part_size = slot, part_size
        self.symbols = pose_code.Symbols(tree.equates, tree.blocks)
        self.vram: dict[int, int] = {}
        self.frame = 0
        machine.natives = {
            **{name: self._blocks for name in QUEUE_BLOCKS},
            QUEUE_GROUP: self._group,
        }
        self.programs: dict[int, list[list[Any]] | None] = {}
        # The stage's RAM loads reach the scroll writer's variables; the
        # stage sets them again before the fight.
        for name in SCROLL_STATE:
            if name in tree.equates:
                machine.write(tree.equates[name] & 0xFFFFFF, 2, 0)
        # The player starts mid-screen on the floor (the stage-backed boss rises under it
        # and drops to its feet); the boss's code may move it.
        for name, value in PLAYER:
            if name in tree.equates:
                machine.write(tree.equates[name] & 0xFFFFFF, 2, value)
        # A boss that queues no tiles (the path-following boss's records) scrolls nothing.
        self.scroll = (
            self._compile(SCROLL, lambda name: True) if SCROLL in tree.blocks and queues_tiles(tree, files) else None
        )

    def _compile(self, label: str, follow) -> list[list[Any]] | None:
        block = self.tree.block(label)
        try:
            return pose_code.compile_lines(
                block.file.lines[block.line :],
                self.symbols,
                self.tree,
                lenient=True,
                follow=follow,
                natives=frozenset(self.machine.natives),
                tails=True,
                calls=CALLS,
            )
        except (pose_code.PoseCodeError, AslSourceError, KeyError, IndexError, ValueError):
            return None

    def program(self, kind: int, handlers: dict[str, str]) -> list[list[Any]] | None:
        """The routine records of `kind` run, compiled once."""
        from retrolarium_kit.games.alien_soldier import bosses

        if kind not in self.programs:
            handler = handlers.get(f"${kind:X}")
            code = None
            if handler in self.tree.blocks and self.tree.block(handler).file.path in self.files:
                # The boss's files and the game's shared sprite, math and
                # entity helpers (Victor's parts orbit it by one), and those
                # that rewrite tile words in RAM (the stage-backed boss's on its stage's).
                code = self._compile(
                    handler,
                    lambda name: (
                        self.tree.block(name).file.path in self.files
                        or name.startswith(("Sprite_", "Math_", "Entity_", "Gfx_Adjust"))
                    ),
                )
            self.programs[kind] = code
            if code:
                bosses._preload(self.tree, self.symbols, [code], self.machine)
        return self.programs[kind]

    def _put(self, address: int, word: int) -> None:
        self.vram[address & 0xFFFF] = word

    def _blocks(self, machine: pose_code.Machine) -> None:
        """Rows or columns of 4x4 groups, by index, from the patterns' RAM."""
        at = machine.a[0] & 0xFFFFFF
        word, source = machine.read(at, 2), machine.read(at + 2, 2)
        across, down = machine.read(at + 4, 1) + 1, machine.read(at + 5, 1) + 1
        address = 0xC000 | (word & 0x3FFE)
        for group_row in range(down):
            for group in range(across):
                index = machine.read(at + 6 + group_row * across + group, 1)
                pattern = 0xFF0000 | ((source + index * 32) & 0xFFFF)
                for row in range(4):
                    for column in range(4):
                        self._put(
                            address + (group_row * 4 + row) * ROW + (group * 4 + column) * 2,
                            machine.read(pattern + row * 8 + column * 2, 2),
                        )

    def _group(self, machine: pose_code.Machine) -> None:
        """One 4x4 group from RAM: d0 holds the VDP address word and the RAM."""
        source, word = machine.d[0] & 0xFFFF, (machine.d[0] >> 16) & 0xFFFF
        address = 0xC000 | (word & 0x3FFE)
        for row in range(4):
            for column in range(4):
                self._put(
                    address + row * ROW + column * 2,
                    machine.read(0xFF0000 | ((source + row * 8 + column * 2) & 0xFFFF), 2),
                )

    def step(self, handlers: dict[str, str]) -> None:
        """One frame: every live record, then the scroll writer; the frame
        counter goes on."""
        machine = self.machine
        # The player faces the boss level with it, $80 to its left (Victor's
        # eyes look straight at it so as it flies in).
        for name, field, offset in (("PlayerCenterX", 0x10, -0x80), ("PlayerCenterY", 0x14, 0)):
            if name in self.tree.equates:
                at = self.tree.equates[name] & 0xFFFFFF
                machine.write(at, 2, (machine.read(self.slot + field, 2) + offset) & 0xFFFF)
        # The game first moves every record by its velocity
        # (Object_ApplyCameraMotion): $18 into X with byte 2's bit 3, $1C
        # into Y with its bit 2 (Victor flies in so).
        for index in range(60):
            record = self.slot + index * self.part_size
            if not machine.read(record, 2):
                continue
            flags = machine.read(record + 2, 1)
            for bit, place in ((8, 0x10), (4, 0x14)):
                if flags & bit:
                    moved = machine.read(record + place, 4) + machine.read(record + place + 8, 4)
                    machine.write(record + place, 4, moved & 0xFFFFFFFF)
        for index in range(60):
            record = self.slot + index * self.part_size
            kind = machine.read(record, 2)
            code = self.program(kind, handlers) if kind else None
            if not code:
                continue
            machine.a[5] = record
            try:
                pose_code.run(code, machine)
            except pose_code.PoseCodeError:
                continue
        if self.scroll:
            try:
                pose_code.run(self.scroll, machine)
            except pose_code.PoseCodeError:
                pass
        # The game counts its frames (Epsilon 1 queues its wings' columns
        # on three frames of four).
        if "FrameCounter" in self.tree.equates:
            counter = self.tree.equates["FrameCounter"] & 0xFFFFFF
            machine.write(counter, 2, (machine.read(counter, 2) + 1) & 0xFFFF)
        self.frame += 1

    def play(self, handlers: dict[str, str]) -> dict[str, Any] | None:
        """Frames until the body (its cells and its place from the boss) has
        stayed as it is SETTLE frames; the body then. A body that never
        settles (the stage-backed boss rocks on its columns) is the last one that lay
        level while the boss stood still, if any, its records put back as
        they were then."""
        body, since, level, stood = None, 0, None, None
        pool = range(self.slot, self.slot + 60 * self.part_size)
        while self.frame < FRAMES:
            self.step(handlers)
            if not self.vram:
                continue
            place = self.machine.read(self.slot + 0x10, 2), self.machine.read(self.slot + 0x14, 2)
            now = self.body(*place)
            since = since + 1 if now == body else 0
            body = now
            if since >= SETTLE:
                return body
            if now is not None and len(set(now.get("columns", [0]))) == 1 and place == stood:
                level = now, [self.machine.memory.get(address, 0) for address in pool]
            stood = place
        if level is None:
            return body
        for address, value in zip(pool, level[1]):
            self.machine.memory[address] = value
        return level[0]

    def cells(self) -> tuple[int, int, int, list[list[int]]] | None:
        """The plane the boss wrote most of, the row and column of the
        corner of what it wrote there, and those cells."""
        planes = {PLANE_A: [], PLANE_B: []}
        for address in self.vram:
            planes[PLANE_A if address < PLANE_B else PLANE_B].append(address)
        base = max(planes, key=lambda item: len(planes[item]))
        if not planes[base]:
            return None
        cells = {((address - base) // ROW, (address - base) % ROW // 2): self.vram[address] for address in planes[base]}
        top, left = min(row for row, _ in cells), min(column for _, column in cells)
        height = max(row for row, _ in cells) - top + 1
        width = max(column for _, column in cells) - left + 1
        grid = [[cells.get((top + row, left + column), 0) for column in range(width)] for row in range(height)]
        if not any(any(row) for row in grid):
            return None
        return base, top, left, grid

    def line_scrolled(self, boss_y: int) -> dict[str, Any] | None:
        """The cells of a boss that scrolls the lines they cover by its own
        X (the plane's X plus the boss's is where they show) on a plane the
        stage leaves unscrolled down: their corner from the boss at
        (any X, boss_y)."""
        layout = self.cells()
        if layout is None:
            return None
        base, top, left, grid = layout
        return {
            "plane": "A" if base == PLANE_A else "B",
            "x": _near(left * 8 + 0x80, ACROSS * 8),
            "y": top * 8 - (boss_y - 0x80),
            "cells": grid,
        }

    def body(self, boss_x: int, boss_y: int) -> dict[str, Any] | None:
        """The plane's cells the boss wrote (the plane it wrote most of), and
        their corner from the boss's place, as the scroll shows them."""
        layout = self.cells()
        if layout is None:
            return None
        base, top, left, grid = layout
        width, height = len(grid[0]), len(grid)
        machine, tree = self.machine, self.tree
        hscroll = [machine.read((tree.equates["HScrollBuffer"] + 2 * at) & 0xFFFFFF, 2) for at in range(224 * 2)]
        vscroll = [machine.read((tree.equates["VScrollBuffer"] + 2 * at) & 0xFFFFFF, 2) for at in range(40)]
        mode = machine.read((tree.equates["VDPReg11Shadow"] + 1) & 0xFFFFFF, 1)
        which = 0 if base == PLANE_A else 1
        # The stage, not the boss, sets column scroll (the column-scroll boss's): a boss
        # that writes its columns apart scrolls by column (the writer fills
        # only the first column else).
        if len({vscroll[2 * column + which] for column in range(1, 20)} - {0}) > 0:
            mode |= 4
        x, y, columns = place(left * 8, top * 8, width * 8, height * 8, which, hscroll, vscroll, mode)
        return {
            "plane": "A" if base == PLANE_A else "B",
            "x": _near(x - (boss_x - 0x80), ACROSS * 8),
            "y": _near(y - (boss_y - 0x80), DOWN * 8),
            "cells": grid,
            **columns,
        }


def streamed_body(
    tree: SourceTree, stream: dict[str, Any], camera_x: int, boss_x: int, boss_y: int
) -> dict[str, Any] | None:
    """The body a stage streams (see stages.secondary_streams): the map's
    first stretch along the 256 lines the descriptor reads from row -Y
    (gaps under a coarse cell bridged; Stage 9's map repeats junk past the
    ship), its empty edges cut, and its corner from the boss at (boss_x,
    boss_y), with the plane scrolled by -camera_x (the secondary camera's
    X). Its vertical scroll is the boss's wave less its rest (the
    Caterpillar's), none as the boss stands."""
    from retrolarium_kit.games.alien_soldier import stages

    data = tree.data(stream["descriptor"])
    plane = {
        "coarse": int.from_bytes(data[0:4], "big"),
        "chunks": int.from_bytes(data[4:8], "big"),
        "patterns": int.from_bytes(data[8:12], "big"),
    }
    # The VDP command word's address bits, the write's top two set.
    address = (int.from_bytes(data[14:16], "big") & 0x3FFF) | 0xC000
    scene = stream["scene"]
    ram = stages._ram(
        tree, scene["map_writes"] + [write for load in scene.get("later", []) for write in load["map_writes"]]
    )
    top = -stream["camera_y"] & 0xFFFF

    def column(x: int) -> list[int]:
        return [stages._name_word(ram, plane, x, top + 8 * row) for row in range(DOWN)]

    used = [x for x in range(0, 0x2000, 8) if any(word & 0x7FF for word in column(x))]
    if not used:
        return None
    last = used[0]
    for x in used:
        if x - last > 0x100:
            break
        last = x
    grid = [column(x) for x in range(used[0], last + 8, 8)]
    rows = [row for row in range(DOWN) if any(cells[row] & 0x7FF for cells in grid)]
    return {
        "plane": "A" if address < PLANE_B else "B",
        "x": used[0] - camera_x - (boss_x - 0x80),
        "y": rows[0] * 8 - (boss_y - 0x80),
        "cells": [[cells[row] for cells in grid] for row in range(rows[0], rows[-1] + 1)],
    }


def place(
    left: int, top: int, width: int, height: int, which: int, hscroll: list[int], vscroll: list[int], mode: int
) -> tuple[int, int, dict[str, Any]]:
    """Where a block of a plane (its corner in the plane's pixels) shows on
    screen: `hscroll` the line table (A, B words a line), `vscroll` the
    column table (A, B words a 16-pixel column), `mode` VDP register 11's
    scroll bits. With column scroll, the screen's 16-pixel columns the block
    crosses move down by `columns` from that corner, the first `column_x`
    from the block's left edge."""

    def signed(value: int) -> int:
        return value - 0x10000 if value & 0x8000 else value

    def across(line: int) -> int:
        line = min(max(line, 0), 223)
        at = line if mode & 3 == 3 else line & ~7 if mode & 3 == 2 else 0
        return signed(hscroll[2 * at + which])

    def down(column: int) -> int:
        column = min(max(column, 0), 19)
        return signed(vscroll[2 * (column if mode & 4 else 0) + which])

    def wrapped(x: int) -> int:
        """`x` on the plane, a block that runs over its right edge shown on
        from the screen's left (Epsilon 1's wings span the plane)."""
        x %= ACROSS * 8
        return x - ACROSS * 8 if x + width > ACROSS * 8 else x

    x = wrapped(left + across(0))
    middle = (x + width // 2) // 16
    y = (top - down(middle)) % (DOWN * 8)
    x = wrapped(left + across(y + height // 2))
    if not mode & 4:
        return x, y, {}
    first = x // 16
    return (
        x,
        y,
        {
            "columns": [down(middle) - down(column) for column in range(first, (x + width - 1) // 16 + 1)],
            "column_x": first * 16 - x,
        },
    )


def _near(value: int, size: int) -> int:
    """`value` modulo `size`, the one nearest zero."""
    value %= size
    return value - size if value >= size // 2 else value
