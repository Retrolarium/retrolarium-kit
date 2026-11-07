"""Static World 3 passage contracts and whole-world route auditing."""

from __future__ import annotations
from collections import deque
from collections.abc import Mapping, Sequence
from retrolarium_kit.core.authoring import AuthoringError
from retrolarium_kit.games.doraemon.world3.document import hierarchy_of
from retrolarium_kit.games.doraemon.world3.geometry import (
    OPPOSITE,
    SIDES,
    clearance,
    component,
    edge_positions,
    neighbors,
    point_accessible,
    room_cells,
    room_number,
    room_tiles,
)


def arrival(side: str, offset: int) -> tuple[int, int]:
    if side == "north":
        return offset, 12
    if side == "south":
        return offset, 208
    if side == "west":
        return 4, offset
    return 236, offset


def departure(side: str, offset: int) -> tuple[int, int]:
    if side == "north":
        return offset, 8
    if side == "south":
        return offset, 211
    if side == "west":
        return 1, offset
    return 239, offset


def _offsets(bits: int):
    while bits:
        bit = bits & -bits
        yield bit.bit_length() - 1
        bits ^= bit


def terrain_rows(
    hierarchy: Mapping, cells: Sequence[Sequence[int]], extra_tiles: Sequence[int] = ()
) -> tuple[int, ...]:
    extra = set(extra_tiles)
    tiles = room_tiles(hierarchy, cells)
    return clearance([[0 if tile in extra else tile for tile in row] for row in tiles])


def _interfaces(rows: Sequence[int], points: Sequence[tuple[int, int]]) -> list[dict]:
    """Components touching a departure, arrival, or protected object position."""
    unseen = list(rows)
    result = []
    for y in range(len(unseen)):
        while unseen[y]:
            x = next(_offsets(unseen[y]))
            connected = component(rows, (x, y))
            unseen = [line & ~used for line, used in zip(unseen, connected)]
            edges = {
                (side, landing): edge_positions(connected, side, arrival=landing)
                for side in SIDES
                for landing in (False, True)
            }
            targets = [point for point in points if point_accessible(connected, point)]
            if any(edges.values()) or targets:
                result.append({"edges": edges, "points": targets})
    return result


def _preserves_interfaces(rows: Sequence[int], interfaces: Sequence[Mapping], *, exact: bool = False) -> bool:
    if exact:
        for side in SIDES:
            for landing in (False, True):
                expected = 0
                for item in interfaces:
                    expected |= item["edges"][side, landing]
                if edge_positions(rows, side, arrival=landing) != expected:
                    return False
    for item in interfaces:
        anchor = None
        for (side, landing), bits in item["edges"].items():
            if bits:
                offset = next(_offsets(bits))
                anchor = arrival(side, offset) if landing else departure(side, offset)
                break
        if anchor is None:
            if not all(point_accessible(rows, point) for point in item["points"]):
                return False
            continue
        connected = component(rows, anchor)
        if any(
            bits & ~edge_positions(connected, side, arrival=landing) for (side, landing), bits in item["edges"].items()
        ):
            return False
        if exact and any(
            edge_positions(connected, side, arrival=landing) != bits for (side, landing), bits in item["edges"].items()
        ):
            return False
        if not all(point_accessible(connected, point) for point in item["points"]):
            return False
    return True


def audit_world(
    document: Mapping,
    *,
    start: tuple[int, int, int] = (0, 128, 128),
    extra_passable: Mapping[int, Sequence[int]] | None = None,
    event_destinations: Sequence[tuple[int, int, int]] = (),
    excluded_rooms: Sequence[int] = (),
) -> dict:
    """Flood room components through real departure/arrival coordinates.

    Event destinations are explicitly conditional extra entry points, such as
    the bonus room's scripted warp. They are recorded separately in the report.
    """
    hierarchy = hierarchy_of(document)
    extras = extra_passable or {}
    rows = [terrain_rows(hierarchy, room_cells(hierarchy, room), extras.get(room, ())) for room in range(64)]
    seeds = [start, *event_destinations]
    for room, x, y in seeds:
        room_number(room)
        if not 0 <= y < len(rows[room]) or not 0 <= x < 240 or not (rows[room][y] >> x) & 1:
            raise AuthoringError(f"entry into room {room:#04x} at ({x}, {y}) has no player clearance")
    excluded = {room_number(room) for room in excluded_rooms}
    reached = [[0] * len(values) for values in rows]
    pending = deque(seeds)
    routes = {
        room: {"entry": [x, y], "via": "start" if index == 0 else "event"} for index, (room, x, y) in enumerate(seeds)
    }
    while pending:
        room, x, y = pending.popleft()
        if (reached[room][y] >> x) & 1:
            continue
        connected = component(rows[room], (x, y))
        reached[room] = [line | used for line, used in zip(reached[room], connected)]
        for side, other in neighbors(room).items():
            offsets = edge_positions(connected, side) & edge_positions(rows[other], OPPOSITE[side], arrival=True)
            for offset in _offsets(offsets):
                nx, ny = arrival(OPPOSITE[side], offset)
                if not (reached[other][ny] >> nx) & 1:
                    routes.setdefault(other, {"from": room, "side": side, "entry": [nx, ny], "via": "passage"})
                    pending.append((other, nx, ny))
    accessible = [room for room in range(64) if any(reached[room])]
    missing = [room for room in range(64) if room not in accessible and room not in excluded]
    return {
        "schema": "retrolarium.doraemon-world-reachability",
        "schema_version": 1,
        "model": "static-player-clearance",
        "room_count": 64,
        "reachable_rooms": accessible,
        "unreachable_rooms": missing,
        "excluded_rooms": sorted(excluded),
        "all_required_rooms_reachable": not missing,
        "conditional_passable_tiles": {str(room): list(tiles) for room, tiles in sorted(extras.items())},
        "event_destinations": [list(entry) for entry in event_destinations],
        "routes": {str(room): route for room, route in sorted(routes.items())},
    }
