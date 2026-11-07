"""Reject new sealed voids and new bottlenecks relative to the source room.

Existing event geometry can contain deliberate closed regions or narrow ports.
It is retained as a baseline, not permission to create new trapped space.
"""

from collections import deque

from retrolarium_kit.games.doraemon.world3.geometry import SIDES, clearance, component, edge_positions


def _parts(points):
    pending = set(points)
    result = []
    while pending:
        start = pending.pop()
        part = {start}
        queue = deque([start])
        while queue:
            x, y = queue.popleft()
            for point in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if point in pending:
                    pending.remove(point)
                    part.add(point)
                    queue.append(point)
        result.append(part)
    return result


def _describe(tiles, extra):
    extra = set(extra)
    normalized = [[0 if value in extra else value for value in row] for row in tiles]
    free = {(x, y) for y in range(30) for x in range(32) if normalized[y][x] < 0x26}
    closed = set()
    for part in _parts(free):
        if all(x not in (0, 31) and y not in (0, 29) for x, y in part):
            closed.update(part)
    rows = clearance(normalized)
    unseen = list(rows)
    sealed = [0] * len(rows)
    for y in range(len(rows)):
        while unseen[y]:
            x = (unseen[y] & -unseen[y]).bit_length() - 1
            connected = component(rows, (x, y))
            unseen = [line & ~used for line, used in zip(unseen, connected)]
            if not any(edge_positions(connected, side, arrival=landing) for side in SIDES for landing in (False, True)):
                sealed = [line | used for line, used in zip(sealed, connected)]
    # Full 32px squares on the 8px grid describe broad navigable regions.
    # Their connected-component count detects rooms joined by a narrow throat.
    wide = {
        (x, y)
        for y in range(27)
        for x in range(29)
        if all((x + dx, y + dy) in free for dy in range(4) for dx in range(4))
    }
    return {"closed": frozenset(closed), "sealed": tuple(sealed), "wide_regions": len(_parts(wide))}


def quality_contract(tiles, extra=()):
    return _describe(tiles, extra)


def check_quality(tiles, contract, extra=()):
    after = _describe(tiles, extra)
    return (
        after["closed"] <= contract["closed"]
        and not any(a & ~b for a, b in zip(after["sealed"], contract["sealed"]))
        and (not contract["wide_regions"] or after["wide_regions"] > 0)
        and after["wide_regions"] <= max(1, contract["wide_regions"])
    )
