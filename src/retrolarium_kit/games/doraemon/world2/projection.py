"""Project World 2 selector bytes into directional, editable route cells.

The canonical 16-group decoder and the runtime's horizontal/vertical decoders
consume the same overlapping token pool with different row boundaries. Token
identity (opcode offset plus RLE cell index) gives the inverse coordinate map
without creating another physical owner for screen data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .doraemon_world2_streams import (
    PADDING, POINTER_SIZE, REGION_ADDRESS, SPAWN_BASE, _validate_screens,
    DoraemonWorld2StreamError,
)


@dataclass(frozen=True)
class ProjectedScreen:
    cells: tuple[tuple[int, ...], ...]
    raw_coordinates: tuple[tuple[tuple[int, int] | None, ...], ...]

    def raw_coordinate(self, x: int, y: int) -> tuple[int, int]:
        if not 0 <= y < len(self.raw_coordinates):
            raise IndexError("World 2 route row is outside the screen")
        if not 0 <= x < len(self.raw_coordinates[y]):
            raise IndexError("World 2 route column is outside the screen")
        result = self.raw_coordinates[y][x]
        if result is None:
            raise ValueError("World 2 route cell has no editable raw coordinate")
        return result


@dataclass(frozen=True)
class _TokenCell:
    value: int
    identity: tuple[int, int, str] | None


def _decode_groups(
    region: bytes, start: int, group_count: int, minimum_cells: int,
) -> tuple[tuple[_TokenCell, ...], ...]:
    if not 0 <= start < len(region):
        raise DoraemonWorld2StreamError("World 2 selector points outside stream")
    cursor = start
    result = []
    for _ in range(group_count):
        cells: list[_TokenCell] = []
        while len(cells) < minimum_cells:
            if cursor >= len(region):
                raise DoraemonWorld2StreamError("World 2 projection reads past region")
            token_offset = cursor
            token = region[cursor]
            cursor += 1
            if token < 0xD0:
                cells.append(_TokenCell(
                    token, (token_offset, 0, "literal")))
            elif token < 0xEF:
                cells.append(_TokenCell(
                    SPAWN_BASE + token - 0xD0,
                    (token_offset, 0, "spawn")))
            elif token == 0xEF:
                cells.extend(_TokenCell(PADDING, None)
                             for _ in range(max(0, 16 - len(cells))))
            elif token == 0xF0:
                raise DoraemonWorld2StreamError("World 2 reserved F0 token")
            else:
                if cursor >= len(region) or region[cursor] >= 0xD0:
                    raise DoraemonWorld2StreamError(
                        "World 2 projection has invalid RLE operand")
                value = region[cursor]
                cursor += 1
                cells.extend(_TokenCell(
                    value, (token_offset, index, "rle"))
                    for index in range((token & 0x0F) + 1))
        result.append(tuple(cells))
    return tuple(result)


def project_screen(
    resource: dict[str, Any], selector: int, direction: int,
) -> ProjectedScreen:
    """Return 15×16 runtime cells plus inverse coordinates into raw selector rows."""
    if direction not in range(4):
        raise ValueError("World 2 route direction is outside 0..3")
    if not 0 <= selector < 119:
        raise IndexError("World 2 selector is outside 0..118")
    raw_hex = resource.get("raw_hex")
    if not isinstance(raw_hex, str):
        raise DoraemonWorld2StreamError("World 2 projection needs original raw_hex")
    try:
        payload = bytes.fromhex(raw_hex)
    except ValueError as exc:
        raise DoraemonWorld2StreamError("World 2 projection has invalid hex") from exc
    if len(payload) != 16669:
        raise DoraemonWorld2StreamError("World 2 projection needs 16669 bytes")
    screens = _validate_screens(resource.get("screens"))
    pointer = int.from_bytes(payload[selector * 2:selector * 2 + 2], "little")
    region = payload[POINTER_SIZE:]
    start = pointer - REGION_ADDRESS
    canonical = _decode_groups(region, start, 16, 15)
    canonical_by_token = {
        cell.identity: (raw_x, raw_y)
        for raw_y, row in enumerate(canonical)
        for raw_x, cell in enumerate(row)
        if cell.identity is not None
    }
    if direction in (0, 3):
        columns = tuple(column[:15]
                        for column in _decode_groups(region, start, 16, 15))
        if direction == 3:
            columns = tuple(reversed(columns))
        oriented = tuple(tuple(columns[x][y] for x in range(16))
                         for y in range(15))
    else:
        oriented = tuple(row[:16] for row in
                         _decode_groups(region, start, 15, 16))
        if direction == 1:
            oriented = tuple(reversed(oriented))
    if len(oriented) != 15 or any(len(row) != 16 for row in oriented):
        raise DoraemonWorld2StreamError("World 2 projection is not 16×15")
    cell_rows = []
    coordinate_rows = []
    for row in oriented:
        values = []
        coordinates = []
        for cell in row:
            coordinate = canonical_by_token.get(cell.identity)
            coordinates.append(coordinate)
            if coordinate is None:
                values.append(PADDING if cell.identity is None else cell.value)
            else:
                raw_x, raw_y = coordinate
                values.append(screens[selector][raw_y][raw_x])
        cell_rows.append(tuple(values))
        coordinate_rows.append(tuple(coordinates))
    return ProjectedScreen(tuple(cell_rows), tuple(coordinate_rows))
