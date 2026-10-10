"""Profile-aware procedural metatile renderer for SMB-family level streams.

The renderer follows the area parser's logic; every metatile it lays out
comes from the game. The level-tables resource (codec nes.byte-tables.v1)
reads the parser's tables and the metatiles its instructions load from the
image built from the disassembly, so the kit holds none of them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from retrolarium_kit.games.smb.level import AREA_TYPES, positioned_area_objects, positioned_enemy_records


# Brick and question-block objects per engine: question blocks come first in
# the block table, then a row of item bricks for ground areas and one for the
# others, each this long.
ITEM_BRICK_COUNTS = {"smb1": 5, "ann": 5, "smb2": 6}
UPSIDE_DOWN_PIPES = frozenset({"ann", "smb2"})


@dataclass(frozen=True)
class SmbLevelRendering:
    """What one engine renders with: its id (smb1, ann or smb2) and the
    tables of the project's level-tables resource."""

    engine: str
    tables: Mapping[str, Sequence[int]]

    @classmethod
    def from_resource(cls, resource: Mapping[str, object], engine: str) -> "SmbLevelRendering":
        if engine not in ITEM_BRICK_COUNTS:
            raise ValueError(f"unsupported SMB level rendering {engine!r}")
        tables = resource.get("tables")
        if not isinstance(tables, Mapping):
            raise ValueError("the level-tables resource holds no tables")
        return cls(engine, {name: tuple(values) for name, values in tables.items()})

    def __getitem__(self, name: str) -> Sequence[int]:
        return self.tables[name]

    def one(self, name: str) -> int:
        return self.tables[name][0]

    def small_object(self, value: int, area_type: int) -> tuple[int, ...]:
        """The metatiles of row-object value (identifier 0), top down."""
        blocks = self["block_metatiles"]
        bricks = ITEM_BRICK_COUNTS[self.engine]
        questions = len(blocks) - 2 * bricks
        if value < questions:
            return (blocks[value],)
        if value < questions + bricks:
            return (blocks[value + (0 if area_type == 1 else bricks)],)
        extra = value - questions - bricks
        if extra == 0:
            return (self.one("water_pipe_top"), self.one("water_pipe_bottom"))
        if extra == 1:
            return (self.one("empty_block"),)
        if extra == 2:
            return (self.one("jumpspring_top"), self.one("jumpspring_bottom"))
        return ()

    def side_pipe(self, part: str) -> tuple[int, ...]:
        """A sideways pipe's top or bottom row, left to right (the game's
        tables run right to left)."""
        return tuple(reversed(self[f"side_pipe_{part}_metatiles"]))


@dataclass(frozen=True)
class SmbLevelScene:
    metatiles: tuple[tuple[int, ...], ...]
    enemies: tuple[Mapping[str, object], ...]
    width: int


def object_width(item: Mapping[str, object]) -> int:
    row, control = int(item["row"]), int(item["object_control"])
    if row <= 12:
        identifier = (control >> 4) & 7
        if row == 12 or identifier in {1, 2, 3, 4}:
            return (control & 15) + 1
        if identifier == 7:
            return 2
    if row == 13 and control & 0x40:
        identifier = control & 0x3F
        return 4 if identifier == 0 else 13 if identifier == 4 else 1
    if row == 15:
        identifier = (control >> 4) & 7
        if identifier in {0, 1, 5}: return 1
        if identifier == 2: return 5
        if identifier == 3: return (control & 15) + 1
        if identifier == 4: return 4
        return 2
    return 1


def render_level_scene(
    name: str,
    header: Mapping[str, object],
    objects: Sequence[Mapping[str, object]],
    enemies: Sequence[Mapping[str, object]],
    *,
    rendering: SmbLevelRendering,
    world: int = 0,
) -> SmbLevelScene:
    positioned_objects = positioned_area_objects(objects)
    positioned_enemies = positioned_enemy_records(enemies)
    width = max([32]
        + [int(item["x"]) + object_width(item) for item in positioned_objects]
        + [int(item["x"]) + 2 for item in positioned_enemies]) + 2
    prefix = name.rpartition("_")[0]
    if prefix not in AREA_TYPES:
        raise ValueError(f"unsupported SMB area name {name!r}")
    area_type = AREA_TYPES.index(prefix)
    foreground = int(header["foreground_or_color"])
    foreground = foreground if foreground < 4 else 0
    background = int(header["background_scenery"])
    terrain = int(header["terrain_control"])
    style = int(header["area_style"])
    cloud = style == 3
    modifiers: dict[int, list[Mapping[str, object]]] = {}
    for item in positioned_objects:
        if int(item["row"]) == 14:
            modifiers.setdefault(int(item["x"]), []).append(item)
    grid: list[list[int]] = []
    for column in range(width):
        grid.append(_base_column(column, area_type, world, foreground,
                                 background, terrain, cloud, rendering))
        for modifier in modifiers.get(column, ()):
            control = int(modifier["object_control"])
            if control & 0x40:
                value = control & 7
                foreground = value if value < 4 else 0
            else:
                terrain = control & 15
                background = (control >> 4) & 3
    for item in positioned_objects:
        _render_object(grid, item, area_type, cloud, 0 if cloud else style, rendering)
    return SmbLevelScene(tuple(tuple(column) for column in grid),
                         tuple(item for item in positioned_enemies if item["kind"] == "enemy"), width)


def _base_column(column: int, area_type: int, world: int, foreground: int,
                 background: int, terrain: int, cloud: bool,
                 rendering: SmbLevelRendering) -> list[int]:
    values = [0] * 13
    if background:
        index = rendering["background_scenery_offsets"][background - 1] + ((column // 16) % 3) * 16 + column % 16
        pattern = rendering["background_scenery_patterns"][index]
        if pattern:
            row, offset = pattern >> 4, ((pattern & 15) - 1) * 3
            for part in range(3):
                if row + part >= 11: break
                values[row + part] = rendering["background_scenery_metatiles"][offset + part]
    if foreground:
        offset = rendering["foreground_scenery_offsets"][foreground - 1]
        for row in range(13):
            value = rendering["foreground_scenery_metatiles"][offset + row]
            if value: values[row] = value
    terrain_metatiles = rendering["terrain_metatiles"]
    terrain_value = terrain_metatiles[3] if area_type == 0 and world == 7 else terrain_metatiles[area_type]
    if cloud: terrain_value = rendering.one("cloud_terrain")
    masks = rendering["terrain_render_masks"][terrain * 2:terrain * 2 + 2]
    for row in range(13):
        byte, bit = (masks[0], row) if row < 8 else (masks[1], row - 8)
        if cloud and row >= 8: byte &= 8
        if byte & (1 << bit):
            values[row] = terrain_metatiles[1] if area_type == 2 and row >= 11 else terrain_value
    return values


def _write(grid: list[list[int]], column: int, row: int, value: int) -> None:
    if 0 <= column < len(grid) and 0 <= row < 13: grid[column][row] = value


def _fill_down(grid: list[list[int]], column: int, row: int, height: int, value: int) -> None:
    for offset in range(height + 1): _write(grid, column, row + offset, value)


def _render_object(grid: list[list[int]], item: Mapping[str, object], area_type: int,
                   cloud: bool, style: int, rendering: SmbLevelRendering) -> None:
    column, row, control = int(item["x"]), int(item["row"]), int(item["object_control"])
    value, identifier = control & 15, (control >> 4) & 7
    if row <= 11:
        if identifier == 0:
            for offset, tile in enumerate(rendering.small_object(value, area_type)):
                _write(grid, column, row + offset, tile)
        elif identifier == 1:
            _render_style(grid, column, row, value, style, rendering)
        elif identifier in {2, 3, 4}:
            tile = (rendering["brick_metatiles"][4 if cloud else area_type] if identifier == 2
                    else rendering["solid_block_metatiles"][area_type] if identifier == 3
                    else rendering["coin_metatiles"][area_type])
            for offset in range(value + 1): _write(grid, column + offset, row, tile)
        elif identifier in {5, 6}:
            tile = rendering["brick_metatiles"][area_type] if identifier == 5 else rendering["solid_block_metatiles"][area_type]
            _fill_down(grid, column, row, value, tile)
        else:
            # A decoration pipe: the game's table holds right, left pairs.
            pipe = rendering["vertical_pipe_metatiles"]
            _write(grid, column, row, pipe[5]); _write(grid, column + 1, row, pipe[4])
            for offset in range(1, (value & 7) + 1):
                _write(grid, column, row + offset, pipe[7]); _write(grid, column + 1, row + offset, pipe[6])
    elif row == 12:
        _render_row_c(grid, column, identifier, value, area_type, rendering)
    elif row == 13 and control & 0x40:
        _render_row_d(grid, column, control & 0x3F, rendering)
    elif row == 15:
        _render_row_f(grid, column, identifier, value, int(item["page"]), rendering)


def _render_style(grid: list[list[int]], column: int, row: int, length: int,
                  style: int, rendering: SmbLevelRendering) -> None:
    if style == 1:
        ledge = "mushroom_ledge" if "mushroom_ledge_start" in rendering.tables else "cloud_ledge"
        start, middle, end = (rendering.one(f"{ledge}_{part}") for part in ("start", "middle", "end"))
        for offset in range(length + 1):
            _write(grid, column + offset, row, start if offset == 0 else end if offset == length else middle)
        if "mushroom_stem" in rendering.tables:
            center = column + (length + 1) // 2
            _write(grid, center, row + 1, rendering.one("mushroom_stem_top"))
            _fill_down(grid, center, row + 2, 12, rendering.one("mushroom_stem"))
    elif style == 2:
        _write(grid, column, row, rendering.one("cannon_top"))
        if length: _write(grid, column, row + 1, rendering.one("cannon_middle"))
        if length > 1: _fill_down(grid, column, row + 2, length - 2, rendering.one("cannon_bottom"))
    else:
        start, middle, end = (rendering.one(f"tree_ledge_{part}") for part in ("start", "middle", "end"))
        for offset in range(length + 1):
            _write(grid, column + offset, row, start if offset == 0 else end if offset == length else middle)
            if 0 < offset < length: _fill_down(grid, column + offset, row + 1, 12, rendering.one("tree_ledge_trunk"))


def _render_row_c(grid: list[list[int]], column: int, identifier: int, length: int,
                  area_type: int, rendering: SmbLevelRendering) -> None:
    count = length + 1
    if identifier == 0:
        hole = rendering["hole_metatiles"][area_type]
        for x in range(column, column + count):
            for row in range(8, 13): _write(grid, x, row, hole)
    elif identifier == 1:
        start, middle, end = rendering["pulley_rope_metatiles"]
        for offset in range(count): _write(grid, column + offset, 0, start if offset == 0 else end if offset == count - 1 else middle)
    elif identifier in {2, 3, 4}:
        row = (6, 7, 9)[identifier - 2]
        for x in range(column, column + count):
            _write(grid, x, row, rendering.one("bridge_top")); _fill_down(grid, x, row + 1, 12, rendering.one("bridge_support"))
    elif identifier == 5:
        for x in range(column, column + count):
            _write(grid, x, 10, rendering.one("water_surface")); _fill_down(grid, x, 11, 1, rendering.one("water_body"))
    elif identifier in {6, 7}:
        row = 3 if identifier == 6 else 7
        for x in range(column, column + count): _write(grid, x, row, rendering.one("coin_row"))


def _render_row_d(grid: list[list[int]], column: int, identifier: int,
                  rendering: SmbLevelRendering) -> None:
    if identifier == 0:
        top, bottom = rendering.side_pipe("top"), rendering.side_pipe("bottom")
        for offset in range(4):
            _write(grid, column + offset, 9, top[offset])
            _write(grid, column + offset, 10, bottom[offset])
        # An entrance pipe above it: the game's table holds right, left pairs.
        pipe = rendering["vertical_pipe_metatiles"]
        _write(grid, column + 2, 7, pipe[1]); _write(grid, column + 2, 8, pipe[3])
        _write(grid, column + 3, 7, pipe[0]); _write(grid, column + 3, 8, pipe[2])
    elif identifier == 1:
        _write(grid, column, 0, rendering.one("flagpole_ball"))
        for row in range(1, 10): _write(grid, column, row, rendering.one("flagpole_shaft"))
        _write(grid, column, 10, rendering.one("flagpole_base"))
    elif identifier in {2, 3, 4}:
        row = rendering["castle_object_rows"][identifier - 2]
        tile = rendering["castle_object_metatiles"][identifier - 2]
        for offset in range(13 if identifier == 4 else 1): _write(grid, column + offset, row, tile)


def _render_row_f(grid: list[list[int]], column: int, identifier: int, length: int,
                  page: int, rendering: SmbLevelRendering) -> None:
    if identifier == 0: _fill_down(grid, column, 0, 12, rendering.one("rope"))
    elif identifier == 1:
        _fill_down(grid, column, 1, 12, rendering.one("balance_rope_top")); _fill_down(grid, column, 1, length, rendering.one("rope"))
    elif identifier == 2:
        structure = rendering["castle_structure_metatiles"]
        start = min(length, 10)
        for x_offset in range(5):
            table_column = 4 - x_offset
            for row in range(start, 11): _write(grid, column + x_offset, row, structure[table_column + (row - start) * 5])
        if page:
            stop = rendering.one("castle_stop_block")
            _write(grid, column + 3, 10, stop)
            if start == 0: _write(grid, column + 1, 10, stop)
    elif identifier == 3:
        heights, rows = rendering["staircase_heights"], rendering["staircase_start_rows"]
        for offset in range(min(length + 1, len(rows))):
            index = len(rows) - 1 - offset
            _fill_down(grid, column + offset, rows[index], heights[index], rendering.one("staircase"))
    elif identifier == 4:
        shaft = rendering["side_pipe_shaft_metatiles"]
        bottom, mouth = max(0, length - 2), max(0, length - 2) + 1
        for row in range(bottom + 1): _write(grid, column + 2, row, shaft[1]); _write(grid, column + 3, row, shaft[0])
        for offset, tile in enumerate(rendering.side_pipe("top")): _write(grid, column + offset, mouth, tile)
        for offset, tile in enumerate(rendering.side_pipe("bottom")): _write(grid, column + offset, mouth + 1, tile)
    elif identifier == 5: _fill_down(grid, column, 2, length, rendering.one("residual_flag"))
    elif identifier in {6, 7} and rendering.engine in UPSIDE_DOWN_PIPES:
        pipe = rendering["vertical_pipe_metatiles"]
        start, count = (1 if identifier == 6 else 4), max(1, length & 7)
        for offset in range(count): _write(grid, column, start + offset, pipe[3]); _write(grid, column + 1, start + offset, pipe[2])
        _write(grid, column, start + count, pipe[1]); _write(grid, column + 1, start + count, pipe[0])
