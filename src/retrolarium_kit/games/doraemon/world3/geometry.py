"""Static player clearance in World 3, before event-dependent wall overrides.

The edge probes follow the player collision routines. The graph permits
one-pixel horizontal and vertical steps and deliberately does not model the
player's movement phases, combat, carried objects or progression events.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping, Sequence

from retrolarium_kit.core.authoring import AuthoringError


SIDES = ("north", "east", "south", "west")
OPPOSITE = {"north": "south", "east": "west", "south": "north", "west": "east"}
X_LIMIT = 240
Y_START = 8
Y_LIMIT = 212


def room_number(value: int) -> int:
    if type(value) is not int or not 0 <= value < 64:
        raise AuthoringError("room must be an integer from 0 to 63")
    return value


def neighbors(room: int) -> dict[str, int]:
    room_number(room)
    row, column = divmod(room, 8)
    result = {}
    if row:
        result["north"] = room - 8
    if column < 7:
        result["east"] = room + 1
    if row < 7:
        result["south"] = room + 8
    if column:
        result["west"] = room - 1
    return result


def room_cells(hierarchy: Mapping, room: int) -> list[list[int]]:
    room_number(room)
    row, column = divmod(room, 8)
    return [list(line[column * 8 : column * 8 + 8]) for line in hierarchy["maps"][0]["rows"][row * 8 : row * 8 + 8]]


def expand_block(hierarchy: Mapping, block: int) -> tuple[tuple[int, ...], ...]:
    small = hierarchy["small_blocks"]
    large = hierarchy["big_blocks"][block]
    return tuple(tuple(small[large[(y // 2) * 2 + x // 2]][(y % 2) * 2 + x % 2] for x in range(4)) for y in range(4))


def room_tiles(hierarchy: Mapping, cells: Sequence[Sequence[int]]) -> list[list[int]]:
    blocks = {block: expand_block(hierarchy, block) for row in cells for block in row}
    return [[blocks[cells[y // 4][x // 4]][y % 4][x % 4] for x in range(32)] for y in range(32)]


def border(tiles: Sequence[Sequence[int]], side: str) -> tuple[bool, ...]:
    """Visible aperture at the room seam, with the bottom at CHR row 29."""
    if side == "north":
        return tuple(tile < 0x26 for tile in tiles[0])
    if side == "south":
        return tuple(tile < 0x26 for tile in tiles[29])
    column = 0 if side == "west" else 31
    return tuple(row[column] < 0x26 for row in tiles[:30])


def clearance(tiles: Sequence[Sequence[int]]) -> tuple[int, ...]:
    """One bit per legal player X, one row per Y, using all ten edge probes."""
    pixels = [sum(255 << (x * 8) for x, tile in enumerate(row) if tile < 0x26) for row in tiles]
    result = [0] * Y_LIMIT
    limit = (1 << X_LIMIT) - 2
    for y in range(Y_START, Y_LIMIT):
        legal = limit
        for offset in (4, 12, 20):
            line = pixels[(y + offset) // 8]
            legal &= (line >> 2) & (line >> 14)
        for offset in (2, 22):
            line = pixels[(y + offset) // 8]
            legal &= (line >> 4) & (line >> 12)
        result[y] = legal
    return tuple(result)


def edge_positions(rows: Sequence[int], side: str, *, arrival: bool = False) -> int:
    """Offsets along an edge at the game's departure or arrival coordinates."""
    if side in ("north", "south"):
        y = {("north", False): 8, ("north", True): 12, ("south", False): 211, ("south", True): 208}[side, arrival]
        return rows[y]
    x = {("west", False): 1, ("west", True): 4, ("east", False): 239, ("east", True): 236}[side, arrival]
    return sum(1 << y for y, row in enumerate(rows) if (row >> x) & 1)


def intervals(bits: int) -> list[list[int]]:
    """Inclusive pixel intervals, sorted along the edge."""
    result = []
    while bits:
        start = (bits & -bits).bit_length() - 1
        end = start
        while (bits >> (end + 1)) & 1:
            end += 1
        result.append([start, end])
        bits &= ~(((1 << (end - start + 1)) - 1) << start)
    return result


def component(rows: Sequence[int], start: tuple[int, int]) -> tuple[int, ...]:
    x, y = start
    reached = [0] * len(rows)
    if not Y_START <= y < len(rows) or not 1 <= x < X_LIMIT or not (rows[y] >> x) & 1:
        return tuple(reached)
    reached[y] = 1 << x
    pending = deque([y])
    while pending:
        y = pending.popleft()
        line = reached[y]
        expanded = (line | (line << 1) | (line >> 1)) & rows[y]
        while expanded != line:
            line = expanded
            expanded = (line | (line << 1) | (line >> 1)) & rows[y]
        reached[y] = line
        for adjacent in (y - 1, y + 1):
            if Y_START <= adjacent < len(rows):
                added = line & rows[adjacent] & ~reached[adjacent]
                if added:
                    reached[adjacent] |= added
                    pending.append(adjacent)
    return tuple(reached)


def point_accessible(rows: Sequence[int], point: tuple[int, int]) -> bool:
    """A reachable player rectangle overlaps the object's saved origin."""
    x, y = point
    left, right = max(1, x - 14), min(X_LIMIT - 1, x)
    if left > right:
        return False
    mask = ((1 << (right - left + 1)) - 1) << left
    return any(rows[py] & mask for py in range(max(Y_START, y - 22), min(Y_LIMIT, y + 1)))
