"""Pac-Man 27x22 maze RLE codec and runtime-sensitive validation."""

from __future__ import annotations

from collections import Counter, deque
from typing import Sequence

from retrolarium_kit.core.authoring import AuthoringError


CODEC = "pacman.maze-rle.v1"
ROWS = 27
COLUMNS = 22
RLE_SIZE = 416
PELLET_TILES = frozenset((0x01, 0x03, 0x09))
POWER_PELLET_TILE = 0x01
# The visible power pellet and its blink frame: the engine eats both as one.
POWER_PELLET_TILES = frozenset((0x01, 0x02))
TUNNEL_ROW = 13
GHOST_DOOR = (11, 11)
PACMAN_SPAWN = (20, 11)
GHOST_SPAWNS = ((10, 11), (13, 10), (13, 11), (13, 12))


class PacmanMazeError(AuthoringError):
    """A Pac-Man maze cannot be represented safely by the original engine."""


def _grid(value: object, rows: int, columns: int) -> tuple[tuple[int, ...], ...]:
    if not isinstance(value, list) or len(value) != rows:
        raise PacmanMazeError(f"maze tiles must contain exactly {rows} rows")
    result = []
    for row_index, row in enumerate(value):
        if not isinstance(row, list) or len(row) != columns:
            raise PacmanMazeError(f"maze tiles[{row_index}] must contain exactly {columns} columns")
        values = []
        for column, tile in enumerate(row):
            if not isinstance(tile, int) or isinstance(tile, bool) or not 0 <= tile <= 0x3F:
                raise PacmanMazeError(f"maze tiles[{row_index}][{column}] must be an integer in 0..63")
            values.append(tile)
        result.append(tuple(values))
    return tuple(result)


def decode_maze_rle(payload: bytes, *, rows: int = ROWS, columns: int = COLUMNS) -> tuple[tuple[int, ...], ...]:
    tiles: list[int] = []
    column = 0
    for index, value in enumerate(payload):
        length = (value >> 6) + 1
        if column + length > columns:
            raise PacmanMazeError(f"maze RLE token {index} crosses a row boundary")
        tiles.extend((value & 0x3F,) * length)
        column = (column + length) % columns
    if len(tiles) != rows * columns or column != 0:
        raise PacmanMazeError(f"maze RLE expands to {len(tiles)} tiles; expected {rows * columns}")
    return tuple(tuple(tiles[start : start + columns]) for start in range(0, len(tiles), columns))


def minimum_rle_size(grid: Sequence[Sequence[int]]) -> int:
    count = 0
    for row in grid:
        cursor = 0
        while cursor < len(row):
            length = 1
            while cursor + length < len(row) and length < 4 and row[cursor + length] == row[cursor]:
                length += 1
            count += 1
            cursor += length
    return count


def encode_maze_grid(
    value: object,
    *,
    rows: int = ROWS,
    columns: int = COLUMNS,
    token_count: int = RLE_SIZE,
) -> bytes:
    grid = _grid(value, rows, columns)
    tokens: list[list[int]] = []
    for row in grid:
        cursor = 0
        while cursor < columns:
            tile = row[cursor]
            length = 1
            while cursor + length < columns and length < 4 and row[cursor + length] == tile:
                length += 1
            tokens.append([length, tile])
            cursor += length
    if len(tokens) > token_count:
        raise PacmanMazeError(f"maze needs {len(tokens)} RLE bytes; fixed layout allows {token_count}")
    cursor = 0
    while len(tokens) < token_count:
        while cursor < len(tokens) and tokens[cursor][0] == 1:
            cursor += 1
        if cursor == len(tokens):
            raise PacmanMazeError(f"maze cannot be expanded to exactly {token_count} RLE bytes")
        tokens[cursor][0] -= 1
        tokens.insert(cursor, [1, tokens[cursor][1]])
        cursor += 1
    return bytes(((length - 1) << 6) | tile for length, tile in tokens)


def _passable_components(grid: Sequence[Sequence[int]]) -> list[int]:
    rows = len(grid)
    columns = len(grid[0])
    remaining = {(row, column) for row in range(rows) for column in range(columns) if grid[row][column] < 0x10}
    sizes = []
    while remaining:
        start = remaining.pop()
        queue = deque((start,))
        size = 1
        while queue:
            row, column = queue.popleft()
            for neighbor in (
                (row - 1, column),
                (row + 1, column),
                (row, (column - 1) % columns),
                (row, (column + 1) % columns),
            ):
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    queue.append(neighbor)
                    size += 1
        sizes.append(size)
    return sorted(sizes, reverse=True)


def inspect_maze(
    value: object,
    *,
    power_pellets: Sequence[Sequence[int]] | None = None,
    counted: bool = False,
) -> dict[str, object]:
    """Check a maze against what the engine assumes.

    ``counted``: the build writes the maze's pellet count into the round
    counter, so any count from 1 to 255 works; otherwise it must stay 192.
    ``power_pellets``: the cells the engine's blinking table draws and its
    eaten-slot logic finds; each must hold a power pellet and no other cell
    may. Without them only the count of four is checked."""
    grid = _grid(value, ROWS, COLUMNS)
    counts = Counter(tile for row in grid for tile in row)
    errors: list[str] = []
    warnings: list[str] = []
    pellets = sum(counts[tile] for tile in PELLET_TILES)
    if counted:
        if not 1 <= pellets <= 255:
            errors.append(f"maze has {pellets} collectible pellets; the round counter holds 1..255")
    elif pellets != 192:
        errors.append(f"maze has {pellets} collectible pellets; runtime round counter expects 192")
    if power_pellets is not None:
        fixed = {(int(row), int(column)) for row, column in power_pellets}
        for row, column in sorted(fixed):
            if grid[row][column] != POWER_PELLET_TILE:
                errors.append(f"power pellet at ({row},{column}) must stay: the blinking table draws it there")
        for row in range(ROWS):
            for column in range(COLUMNS):
                if grid[row][column] in POWER_PELLET_TILES and (row, column) not in fixed:
                    errors.append(f"power pellet at ({row},{column}) is outside the cells the blinking table draws")
    elif counts[POWER_PELLET_TILE] != 4:
        errors.append(f"maze has {counts[POWER_PELLET_TILE]} visible power pellets; marker table expects 4")
    if grid[GHOST_DOOR[0]][GHOST_DOOR[1]] != 0x2C:
        errors.append("ghost-house door at (11,11) must remain tile $2C")
    spawn_points = (("Pac-Man spawn", PACMAN_SPAWN),) + tuple(
        (f"ghost spawn {index}", cell) for index, cell in enumerate(GHOST_SPAWNS)
    )
    for name, (row, column) in spawn_points:
        if grid[row][column] >= 0x10:
            errors.append(f"{name} at ({row},{column}) is blocked")
    for column in (0, COLUMNS - 1):
        if grid[TUNNEL_ROW][column] >= 0x10:
            errors.append(f"tunnel endpoint ({TUNNEL_ROW},{column}) must remain passable")
    components = _passable_components(grid)
    if len(components) > 2 or (len(components) == 2 and components[1] > 6):
        errors.append(f"passable maze is unexpectedly disconnected: {components}")
    elif len(components) == 2:
        warnings.append("six-tile ghost-house interior is separate from the main path")
    minimum = minimum_rle_size(grid)
    if minimum > RLE_SIZE:
        errors.append(f"maze needs {minimum} RLE bytes; fixed layout allows {RLE_SIZE}")
    return {
        "errors": errors,
        "warnings": warnings,
        "pellets": pellets,
        "power_pellets": counts[POWER_PELLET_TILE],
        "components": components,
        "minimum_rle_bytes": minimum,
        "export_rle_bytes": RLE_SIZE,
    }
