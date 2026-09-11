"""Profile-aware procedural metatile renderer for SMB-family level streams."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from .smb_level import AREA_TYPES, positioned_area_objects, positioned_enemy_records


BACKGROUND_SCENERY_OFFSETS = (0x00, 0x30, 0x60)
BACKGROUND_SCENERY_PATTERNS = (
    0x93, 0, 0, 0x11, 0x12, 0x12, 0x13, 0, 0, 0x51, 0x52, 0x53, 0, 0, 0, 0,
    0, 0, 0x01, 0x02, 0x02, 0x03, 0, 0, 0, 0, 0, 0, 0x91, 0x92, 0x93, 0,
    0, 0, 0, 0x51, 0x52, 0x53, 0x41, 0x42, 0x43, 0, 0, 0, 0, 0, 0x91, 0x92,
    0x97, 0x87, 0x88, 0x89, 0x99, 0, 0, 0, 0x11, 0x12, 0x13, 0xA4, 0xA5, 0xA5, 0xA5, 0xA6,
    0x97, 0x98, 0x99, 0x01, 0x02, 0x03, 0, 0xA4, 0xA5, 0xA6, 0, 0x11, 0x12, 0x12, 0x12, 0x13,
    0, 0, 0, 0, 0x01, 0x02, 0x02, 0x03, 0, 0xA4, 0xA5, 0xA5, 0xA6, 0, 0, 0,
    0x11, 0x12, 0x12, 0x13, 0, 0, 0, 0, 0, 0, 0, 0x9C, 0, 0x8B, 0xAA, 0xAA,
    0xAA, 0xAA, 0x11, 0x12, 0x13, 0x8B, 0, 0x9C, 0x9C, 0, 0, 0x01, 0x02, 0x03, 0x11, 0x12,
    0x12, 0x13, 0, 0, 0, 0, 0xAA, 0xAA, 0x9C, 0xAA, 0, 0x8B, 0, 0x01, 0x02, 0x03,
)
BACKGROUND_SCENERY_METATILES = (
    0x80, 0x83, 0, 0x81, 0x84, 0, 0x82, 0x85, 0,
    0x02, 0, 0, 0x03, 0, 0, 0x04, 0, 0,
    0, 0x05, 0x06, 0x07, 0x06, 0x0A, 0, 0x08, 0x09,
    0x4D, 0, 0, 0x0D, 0x0F, 0x4E, 0x0E, 0x4E, 0x4E,
)
FOREGROUND_SCENERY_OFFSETS = (0x00, 0x0D, 0x1A)
FOREGROUND_SCENERY_METATILES = (
    0x86, 0x87, 0x87, 0x87, 0x87, 0x87, 0x87, 0x87, 0x87, 0x87, 0x87, 0x69, 0x69,
    0, 0, 0, 0, 0, 0x45, 0x47, 0x47, 0x47, 0x47, 0x47, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0x86, 0x87,
)
TERRAIN_RENDER_MASKS = (
    (0x00, 0x00), (0x00, 0x18), (0x01, 0x18), (0x07, 0x18),
    (0x0F, 0x18), (0xFF, 0x18), (0x01, 0x1F), (0x07, 0x1F),
    (0x0F, 0x1F), (0x81, 0x1F), (0x01, 0x00), (0x8F, 0x1F),
    (0xF1, 0x1F), (0xF9, 0x18), (0xF1, 0x18), (0xFF, 0x1F),
)
CASTLE_STRUCTURE_METATILES = (
    0, 0x45, 0x45, 0x45, 0, 0, 0x48, 0x47, 0x46, 0,
    0x45, 0x49, 0x49, 0x49, 0x45, 0x47, 0x47, 0x4A, 0x47, 0x47,
    0x47, 0x47, 0x4B, 0x47, 0x47, 0x49, 0x49, 0x49, 0x49, 0x49,
    0x47, 0x4A, 0x47, 0x4A, 0x47, 0x47, 0x4B, 0x47, 0x4B, 0x47,
    0x47, 0x47, 0x47, 0x47, 0x47, 0x4A, 0x47, 0x4A, 0x47, 0x4A,
    0x4B, 0x47, 0x4B, 0x47, 0x4B,
)
SMALL_OBJECTS = (
    ((0xC1,), (0xC1,)), ((0xC0,), (0xC0,)), ((0x5F,), (0x5F,)),
    ((0x60,), (0x60,)), ((0x55,), (0x5A,)), ((0x56,), (0x5B,)),
    ((0x57,), (0x5C,)), ((0x58,), (0x5D,)), ((0x59,), (0x5E,)),
    ((0x6B, 0x6C), (0x6B, 0x6C)), ((0xC4,), (0xC4,)),
    ((0x67, 0x68), (0x67, 0x68)),
)


@dataclass(frozen=True)
class SmbLevelRendering:
    terrain_metatiles: tuple[int, ...] = (0x69, 0x54, 0x52, 0x62)
    foreground_scenery_metatiles: tuple[int, ...] = FOREGROUND_SCENERY_METATILES
    brick_metatiles: tuple[int, ...] = (0x22, 0x51, 0x52, 0x52, 0x88)
    solid_metatiles: tuple[int, ...] = (0x69, 0x61, 0x61, 0x62)
    coin_metatiles: tuple[int, ...] = (0xC3, 0xC2, 0xC2, 0xC2)
    side_pipe_top_metatiles: tuple[int, ...] = (0x1C, 0x1D, 0x1E, 0x15)
    side_pipe_bottom_metatiles: tuple[int, ...] = (0x1F, 0x20, 0x21, 0x15)
    castle_special_metatiles: tuple[int, ...] = (0xC5, 0x0C, 0x89)
    secondary_ledge_metatiles: tuple[int, ...] = (0x19, 0x1A, 0x1B)
    secondary_ledge_support_metatiles: tuple[int, ...] = (0x4F, 0x50)
    cannon_metatiles: tuple[int, ...] = (0x64, 0x65, 0x66)
    bridge_metatile: int = 0x63
    flagpole_metatiles: tuple[int, ...] = (0x24, 0x25, 0x61)
    staircase_metatile: int = 0x61
    residual_flag_metatile: int = 0x6D
    upside_down_pipes: bool = False
    small_objects: tuple[tuple[tuple[int, ...], tuple[int, ...]], ...] = SMALL_OBJECTS


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
    world: int = 0,
    rendering: SmbLevelRendering = SmbLevelRendering(),
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
        index = BACKGROUND_SCENERY_OFFSETS[background - 1] + ((column // 16) % 3) * 16 + column % 16
        pattern = BACKGROUND_SCENERY_PATTERNS[index]
        if pattern:
            row, offset = pattern >> 4, ((pattern & 15) - 1) * 3
            for part in range(3):
                if row + part >= 11: break
                values[row + part] = BACKGROUND_SCENERY_METATILES[offset + part]
    if foreground:
        offset = FOREGROUND_SCENERY_OFFSETS[foreground - 1]
        for row in range(13):
            value = rendering.foreground_scenery_metatiles[offset + row]
            if value: values[row] = value
    terrain_value = rendering.terrain_metatiles[3] if area_type == 0 and world == 7 else rendering.terrain_metatiles[area_type]
    if cloud: terrain_value = 0x88
    masks = TERRAIN_RENDER_MASKS[terrain]
    for row in range(13):
        byte, bit = (masks[0], row) if row < 8 else (masks[1], row - 8)
        if cloud and row >= 8: byte &= 8
        if byte & (1 << bit):
            values[row] = rendering.terrain_metatiles[1] if area_type == 2 and row >= 11 else terrain_value
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
            if value < len(rendering.small_objects):
                for offset, tile in enumerate(rendering.small_objects[value][0 if area_type == 1 else 1]):
                    _write(grid, column, row + offset, tile)
        elif identifier == 1:
            _render_style(grid, column, row, value, style, rendering)
        elif identifier in {2, 3, 4}:
            tile = rendering.brick_metatiles[4 if cloud else area_type] if identifier == 2 else rendering.solid_metatiles[area_type] if identifier == 3 else rendering.coin_metatiles[area_type]
            for offset in range(value + 1): _write(grid, column + offset, row, tile)
        elif identifier in {5, 6}:
            tile = rendering.brick_metatiles[area_type] if identifier == 5 else rendering.solid_metatiles[area_type]
            _fill_down(grid, column, row, value, tile)
        else:
            _write(grid, column, row, 0x12); _write(grid, column + 1, row, 0x13)
            for offset in range(1, (value & 7) + 1):
                _write(grid, column, row + offset, 0x14); _write(grid, column + 1, row + offset, 0x15)
    elif row == 12:
        _render_row_c(grid, column, identifier, value, area_type, rendering)
    elif row == 13 and control & 0x40:
        _render_row_d(grid, column, control & 0x3F, rendering)
    elif row == 15:
        _render_row_f(grid, column, identifier, value, int(item["page"]), rendering)


def _render_style(grid: list[list[int]], column: int, row: int, length: int,
                  style: int, rendering: SmbLevelRendering) -> None:
    if style == 1:
        start, middle, end = rendering.secondary_ledge_metatiles
        for offset in range(length + 1):
            _write(grid, column + offset, row, start if offset == 0 else end if offset == length else middle)
        top, body = rendering.secondary_ledge_support_metatiles
        center = column + (length + 1) // 2
        _write(grid, center, row + 1, top); _fill_down(grid, center, row + 2, 12, body)
    elif style == 2:
        top, middle, bottom = rendering.cannon_metatiles
        _write(grid, column, row, top)
        if length: _write(grid, column, row + 1, middle)
        if length > 1: _fill_down(grid, column, row + 2, length - 2, bottom)
    else:
        for offset in range(length + 1):
            _write(grid, column + offset, row, 0x16 if offset == 0 else 0x18 if offset == length else 0x17)
            if 0 < offset < length: _fill_down(grid, column + offset, row + 1, 12, 0x4C)


def _render_row_c(grid: list[list[int]], column: int, identifier: int, length: int,
                  area_type: int, rendering: SmbLevelRendering) -> None:
    count = length + 1
    if identifier == 0:
        hole = (0x87, 0, 0, 0)[area_type]
        for x in range(column, column + count):
            for row in range(8, 13): _write(grid, x, row, hole)
    elif identifier == 1:
        for offset in range(count): _write(grid, column + offset, 0, 0x42 if offset == 0 else 0x43 if offset == count - 1 else 0x41)
    elif identifier in {2, 3, 4}:
        row = (6, 7, 9)[identifier - 2]
        for x in range(column, column + count): _write(grid, x, row, 0x0B); _fill_down(grid, x, row + 1, 12, rendering.bridge_metatile)
    elif identifier == 5:
        for x in range(column, column + count): _write(grid, x, 10, 0x86); _fill_down(grid, x, 11, 1, 0x87)
    elif identifier in {6, 7}:
        row = 3 if identifier == 6 else 7
        for x in range(column, column + count): _write(grid, x, row, 0xC0)


def _render_row_d(grid: list[list[int]], column: int, identifier: int,
                  rendering: SmbLevelRendering) -> None:
    if identifier == 0:
        for offset in range(4):
            _write(grid, column + offset, 9, rendering.side_pipe_top_metatiles[offset])
            _write(grid, column + offset, 10, rendering.side_pipe_bottom_metatiles[offset])
        _write(grid, column + 2, 7, 0x10); _write(grid, column + 2, 8, 0x14)
        _write(grid, column + 3, 7, 0x11); _write(grid, column + 3, 8, 0x15)
    elif identifier == 1:
        ball, shaft, base = rendering.flagpole_metatiles
        _write(grid, column, 0, ball)
        for row in range(1, 10): _write(grid, column, row, shaft)
        _write(grid, column, 10, base)
    elif identifier in {2, 3, 4}:
        row = (6, 7, 8)[identifier - 2]
        for offset in range(13 if identifier == 4 else 1): _write(grid, column + offset, row, rendering.castle_special_metatiles[identifier - 2])


def _render_row_f(grid: list[list[int]], column: int, identifier: int, length: int,
                  page: int, rendering: SmbLevelRendering) -> None:
    if identifier == 0: _fill_down(grid, column, 0, 12, 0x40)
    elif identifier == 1:
        _fill_down(grid, column, 1, 12, 0x44); _fill_down(grid, column, 1, length, 0x40)
    elif identifier == 2:
        start = min(length, 10)
        for x_offset in range(5):
            table_column = 4 - x_offset
            for row in range(start, 11): _write(grid, column + x_offset, row, CASTLE_STRUCTURE_METATILES[table_column + (row - start) * 5])
        if page:
            _write(grid, column + 3, 10, 0x50)
            if start == 0: _write(grid, column + 1, 10, 0x50)
    elif identifier == 3:
        heights, rows = (7, 7, 6, 5, 4, 3, 2, 1, 0), (3, 3, 4, 5, 6, 7, 8, 9, 10)
        for offset in range(min(length + 1, len(rows))):
            index = 8 - offset; _fill_down(grid, column + offset, rows[index], heights[index], rendering.staircase_metatile)
    elif identifier == 4:
        bottom, mouth = max(0, length - 2), max(0, length - 2) + 1
        for row in range(bottom + 1): _write(grid, column + 2, row, 0x14); _write(grid, column + 3, row, 0x15)
        for offset, tile in enumerate(rendering.side_pipe_top_metatiles): _write(grid, column + offset, mouth, tile)
        for offset, tile in enumerate(rendering.side_pipe_bottom_metatiles): _write(grid, column + offset, mouth + 1, tile)
    elif identifier == 5: _fill_down(grid, column, 2, length, rendering.residual_flag_metatile)
    elif identifier in {6, 7} and rendering.upside_down_pipes:
        start, count = (1 if identifier == 6 else 4), max(1, length & 7)
        for offset in range(count): _write(grid, column, start + offset, 0x14); _write(grid, column + 1, start + offset, 0x15)
        _write(grid, column, start + count, 0x10); _write(grid, column + 1, start + count, 0x11)
