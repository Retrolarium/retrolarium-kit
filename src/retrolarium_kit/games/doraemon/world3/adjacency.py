"""Control-anchored world generation from a directional primitive graph.

The graph is learned from the user's world. Native 32px templates constrain
packing while its edges describe individual 16px primitive neighbors. Room
interfaces and runtime assemblies are anchors, not candidates for sampling.
"""

from __future__ import annotations

from collections import Counter, deque
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
import math
import random

from retrolarium_kit.core.authoring import AuthoringError
from retrolarium_kit.games.doraemon.world3.document import hierarchy_of, resource, room_themes
from retrolarium_kit.games.doraemon.world3.geometry import (
    SIDES,
    border,
    edge_positions,
    expand_block,
    intervals,
    room_cells,
    room_number,
    room_tiles,
)
from retrolarium_kit.games.doraemon.world3.primitives import PrimitivePainter, small_grid
from retrolarium_kit.games.doraemon.world3.quality import quality_contract, check_quality
from retrolarium_kit.games.doraemon.world3.world import (
    _interfaces,
    _preserves_interfaces,
    terrain_rows,
    arrival,
    departure,
)


DELTAS = ((0, -1), (1, 0), (0, 1), (-1, 0))
PAIRS = (((0, 1), (2, 3)), ((1, 3), (0, 2)), ((2, 3), (0, 1)), ((0, 2), (1, 3)))


def _bits(mask: int):
    while mask:
        bit = mask & -mask
        yield bit.bit_length() - 1
        mask ^= bit


def controls_for_room(hierarchy: Mapping, room: int) -> list[dict]:
    """Export visible opening intervals explicitly in eight-pixel CHR tiles."""
    tiles = room_tiles(hierarchy, room_cells(hierarchy, room))
    controls = []
    for side in SIDES:
        bits = sum(int(value) << index for index, value in enumerate(border(tiles, side)))
        controls.extend(
            {"side": side, "offset_tiles": start, "size_tiles": end - start + 1, "tile_pixels": 8}
            for start, end in intervals(bits)
        )
    return controls


def default_controls(document: Mapping) -> dict:
    hierarchy = hierarchy_of(document)
    return {
        "schema": "doraemon.world3-controls.v1",
        "source_sha256": document["source_sha256"],
        "tile_pixels": 8,
        "block_pixels": 32,
        "rooms": [{"room": room, "openings": controls_for_room(hierarchy, room), "anchors": []} for room in range(64)],
    }


def _read_controls(document: Mapping, controls: Mapping | None) -> dict:
    expected = default_controls(document)
    if controls is None:
        return {room: [] for room in range(64)}
    if (
        not isinstance(controls, Mapping)
        or controls.get("schema") != expected["schema"]
        or controls.get("source_sha256") != expected["source_sha256"]
        or controls.get("tile_pixels") != 8
        or controls.get("block_pixels") != 32
    ):
        raise AuthoringError("room controls must match the source and 8px/32px units")
    rooms = controls.get("rooms", [])
    if (
        not isinstance(rooms, list)
        or len(rooms) != 64
        or any(not isinstance(r, Mapping) for r in rooms)
        or any(type(r.get("room")) is not int or not 0 <= r["room"] < 64 for r in rooms)
        or sorted(r.get("room", -1) for r in rooms) != list(range(64))
    ):
        raise AuthoringError("room controls need exactly one record per room")
    result = {}
    for item in rooms:
        room = room_number(item["room"])
        if item.get("openings") != expected["rooms"][room]["openings"]:
            raise AuthoringError(f"room {room:#04x}: this experiment preserves the original opening sizes and topology")
        anchors = item.get("anchors", [])
        if not isinstance(anchors, list):
            raise AuthoringError("control anchors must be a list")
        seen = set()
        for anchor in anchors:
            if (
                not isinstance(anchor, Mapping)
                or set(anchor) != {"x", "y", "block"}
                or any(
                    type(anchor.get(k)) is not int or not 0 <= anchor[k] < maximum
                    for k, maximum in (("x", 8), ("y", 8), ("block", 256))
                )
            ):
                raise AuthoringError("control anchors need valid x, y and native block IDs")
            point = anchor["x"], anchor["y"]
            if point in seen:
                raise AuthoringError("duplicate control anchor position")
            seen.add(point)
        result[room] = anchors
    return result


@dataclass
class NeighborGraph:
    counts: tuple[Counter, ...]
    shapes: dict[int, set[str]]
    background: set[int]
    inferred: tuple = field(default_factory=lambda: tuple(set() for _ in SIDES))

    @classmethod
    def learn(
        cls, grids: Sequence[Sequence[Sequence[int]]], catalogue: Mapping, hierarchy: Mapping, *, semantic_closure=False
    ):
        counts = tuple(Counter() for _ in SIDES)
        for grid in grids:
            for y in range(15):
                for x in range(16):
                    for direction, (dx, dy) in enumerate(DELTAS):
                        nx, ny = x + dx, y + dy
                        if 0 <= nx < 16 and 0 <= ny < 15:
                            counts[direction][grid[y][x], grid[ny][nx]] += 1
        shapes = {}
        resolved = PrimitivePainter(hierarchy, catalogue).catalogue
        for family in resolved["families"].values():
            for shape, phases in family["shapes"].items():
                for index in phases:
                    shapes.setdefault(index, set()).add(shape)
        for index, values in catalogue.get("shape_variants", {}).items():
            shapes[int(index)] = set(values)
        result = cls(counts, shapes, set(catalogue["backgrounds"].values()))
        if semantic_closure:
            resolved = PrimitivePainter(hierarchy, catalogue).catalogue
            observed = {value for table in counts for pair in table for value in pair}
            for family_name, family in resolved["families"].items():
                members = {value for values in family["shapes"].values() for value in values}
                if not members & observed:
                    continue
                members.update(
                    i
                    for i in shapes
                    if i in observed
                    and hierarchy["attributes"][i]["palette"] == hierarchy["attributes"][min(members)]["palette"]
                )
                blank = catalogue["backgrounds"]["sky" if family_name == "dry-rock" else "water"]
                for direction in range(4):
                    side, opposite = SIDES[direction][0], SIDES[(direction + 2) % 4][0]
                    for first in members | {blank}:
                        for second in members | {blank}:
                            if first == blank and second == blank:
                                continue
                            if first == blank:
                                valid = any(opposite in shape for shape in shapes[second])
                            elif second == blank:
                                valid = any(side in shape for shape in shapes[first])
                            else:
                                valid = any(
                                    side not in a and opposite not in b for a in shapes[first] for b in shapes[second]
                                )
                            if valid and not counts[direction][first, second]:
                                counts[direction][first, second] = 1
                                result.inferred[direction].add((first, second))
        return result

    def allows(self, first: int, second: int, direction: int) -> bool:
        return bool(self.counts[direction][first, second])

    def safe_coastline(self, first: int, second: int, direction: int) -> bool:
        if not self.allows(first, second, direction):
            return False
        # Observation alone is insufficient: a flat interior fill must never
        # become a newly exposed coastline because of a native special case.
        if first in self.shapes and second in self.background:
            return any(SIDES[direction][0] in shape for shape in self.shapes[first])
        if second in self.shapes and first in self.background:
            return any(SIDES[(direction + 2) % 4][0] in shape for shape in self.shapes[second])
        return True


@dataclass
class PackedGrammar:
    graph: NeighborGraph
    blocks: list[list[int]]
    compatible: tuple[list[int], ...]
    eligible: set[int]
    weights: Counter
    last_contradiction: tuple | None = None
    anchor_grid: list | None = None
    coast_cache: dict = field(default_factory=dict)
    exception_cells: set | None = None
    clauses: list = field(default_factory=list)
    bottom_eligible: set = field(default_factory=set)

    def coastal_neighbors(self, position, direction, first):
        key = position, direction, first
        if self.anchor_grid is not None and key in self.coast_cache:
            return self.coast_cache[key]
        y, x = divmod(position, 8)
        outgoing, incoming = PAIRS[direction]
        if y == 7 and direction in (1, 3):
            outgoing, incoming = outgoing[:1], incoming[:1]
            raw = sum(
                1 << i
                for i, quad in enumerate(self.blocks)
                if self.graph.allows(self.blocks[first][outgoing[0]], quad[incoming[0]], direction)
            )
        else:
            raw = self.compatible[direction][first]
        if self.anchor_grid is None:
            return raw
        key = position, direction, first
        if key in self.coast_cache:
            return self.coast_cache[key]
        y, x = divmod(position, 8)
        dx, dy = DELTAS[direction]
        outgoing, incoming = PAIRS[direction]
        allowed = 0
        if y == 7 and direction in (1, 3):
            outgoing, incoming = outgoing[:1], incoming[:1]
        for second in _bits(raw):
            valid = True
            for a, b in zip(outgoing, incoming):
                one, two = self.blocks[first][a], self.blocks[second][b]
                if self.graph.safe_coastline(one, two, direction):
                    continue
                sx, sy = x * 2 + a % 2, y * 2 + a // 2
                tx, ty = (x + dx) * 2 + b % 2, (y + dy) * 2 + b // 2
                inherited = one == self.anchor_grid[sy][sx] and two == self.anchor_grid[ty][tx]
                permitted = (
                    self.exception_cells is None
                    or (sx // 2, sy // 2) in self.exception_cells
                    or (tx // 2, ty // 2) in self.exception_cells
                )
                if not inherited or not permitted:
                    valid = False
                    break
            if valid:
                allowed |= 1 << second
        self.coast_cache[key] = allowed
        return allowed

    @classmethod
    def learn(cls, hierarchy: Mapping, grids, rooms, allowed: set[int], catalogue: Mapping, *, semantic_closure=False):
        graph = NeighborGraph.learn(grids, catalogue, hierarchy, semantic_closure=semantic_closure)
        blocks = hierarchy["big_blocks"]
        weights = Counter(block for room in rooms for line in room for block in line)
        eligible = {
            i
            for i, quad in enumerate(blocks)
            if set(quad) <= allowed
            and graph.allows(quad[0], quad[1], 1)
            and graph.allows(quad[2], quad[3], 1)
            and graph.allows(quad[0], quad[2], 2)
            and graph.allows(quad[1], quad[3], 2)
        }
        # Identical definitions are one graph symbol, not visual variation.
        canonical = {}
        for index in sorted(eligible, key=lambda i: (-weights[i], i)):
            canonical.setdefault(tuple(blocks[index]), index)
        eligible = set(canonical.values())
        compatible = tuple([0] * 256 for _ in SIDES)
        for direction, (outgoing, incoming) in enumerate(PAIRS):
            for first, quad in enumerate(blocks):
                compatible[direction][first] = sum(
                    1 << second
                    for second, other in enumerate(blocks)
                    if all(graph.allows(quad[a], other[b], direction) for a, b in zip(outgoing, incoming))
                )
        bottom = {}
        for index in sorted(range(256), key=lambda i: (-weights[i], i)):
            quad = blocks[index]
            if set(quad) <= allowed and graph.allows(quad[0], quad[1], 1):
                bottom.setdefault(tuple(quad[:2]), index)
        return cls(graph, blocks, compatible, eligible, weights, bottom_eligible=set(bottom.values()))

    def propagate(self, domains: list[int], pending: deque[int], pinned: set[int]) -> bool:
        if not self._propagate_pairs(domains, pending, pinned):
            return False
        while self.clauses:
            changed = deque()
            for clause in self.clauses:
                capable = []
                satisfied = False
                for position, mask in clause:
                    blocking = domains[position] & mask
                    if blocking == domains[position]:
                        satisfied = True
                        break
                    if blocking:
                        capable.append((position, blocking))
                if satisfied:
                    continue
                if not capable:
                    return False
                if len(capable) == 1:
                    position, narrowed = capable[0]
                    domains[position] = narrowed
                    changed.append(position)
            if not changed:
                break
            if not self._propagate_pairs(domains, changed, pinned):
                return False
        return True

    def _propagate_pairs(self, domains: list[int], pending: deque[int], pinned: set[int]) -> bool:
        while pending:
            position = pending.popleft()
            y, x = divmod(position, 8)
            for direction, (dx, dy) in enumerate(DELTAS):
                nx, ny = x + dx, y + dy
                if not 0 <= nx < 8 or not 0 <= ny < 8:
                    continue
                target = ny * 8 + nx
                if position in pinned and target in pinned:
                    continue
                allowed = 0
                for value in _bits(domains[position]):
                    allowed |= self.coastal_neighbors(position, direction, value)
                narrowed = domains[target] & allowed
                if not narrowed:
                    self.last_contradiction = (
                        position,
                        target,
                        list(_bits(domains[position])),
                        list(_bits(domains[target])),
                    )
                    return False
                if narrowed != domains[target]:
                    domains[target] = narrowed
                    pending.append(target)
        return True

    def collapse(
        self, initial: list[int], pinned: set[int], rng: random.Random, *, budget: int = 256, open_bias: float = 16
    ):
        visits = 0

        def search(domains):
            nonlocal visits
            visits += 1
            if visits > budget:
                return None
            choices = [i for i, mask in enumerate(domains) if mask.bit_count() > 1]
            if not choices:
                return [[next(_bits(domains[y * 8 + x])) for x in range(8)] for y in range(8)]
            size = min(domains[i].bit_count() for i in choices)
            position = rng.choice([i for i in choices if domains[i].bit_count() == size])
            # The first pass constructs the connected shell. Empty space is
            # favored so interior islands are reserved for the second pass.
            options = sorted(
                _bits(domains[position]),
                key=lambda i: (
                    -math.log(max(rng.random(), 1e-15))
                    / (max(1, self.weights[i]) * (open_bias if set(self.blocks[i]) <= self.graph.background else 1))
                ),
            )
            for value in options:
                branch = list(domains)
                branch[position] = 1 << value
                if self.propagate(branch, deque([position]), pinned):
                    result = search(branch)
                    if result is not None:
                        return result
                if visits > budget:
                    break
            return None

        domains = list(initial)
        if not all(domains) or not self.propagate(domains, deque(range(64)), pinned):
            return None
        return search(domains)


def _locks(hierarchy: Mapping, cells, policies: Mapping[int, str]) -> set[tuple[int, int]]:
    return {
        (x, y)
        for y in range(8)
        for x in range(8)
        if any(policies[i] == "event-only" for i in hierarchy["big_blocks"][cells[y][x]])
        or any(tile == 0x14 or 0x26 <= tile < 0x2A for row in expand_block(hierarchy, cells[y][x]) for tile in row)
    }


def _probe_tiles(x, y):
    return frozenset(
        [((x + dx) // 8, (y + dy) // 8) for dy in (4, 12, 20) for dx in (2, 14)]
        + [((x + dx) // 8, (y + dy) // 8) for dy in (2, 22) for dx in (4, 12)]
    )


def _port_domains(hierarchy, cells, models):
    """Constrain port physics, without copying a perimeter or landing collar.

    Valid departure/arrival probes must stay clear. Every invalid probe set
    retains at least one blocker, represented as a disjunctive domain clause.
    Thus walls can move around a closed side without accidentally opening it.
    """
    full = (1 << 256) - 1
    expanded = [sum(expand_block(hierarchy, i), ()) for i in range(256)]
    source = room_tiles(hierarchy, cells)
    domains = [full] * 64
    clauses = set()
    for extra in models:
        extra = set(extra)
        masks = [
            sum(1 << i for i, tiles in enumerate(expanded) if tiles[offset] < 0x26 or tiles[offset] in extra)
            for offset in range(16)
        ]

        def address(tx, ty):
            return ty // 4 * 8 + tx // 4, ty % 4 * 4 + tx % 4

        # The requested apertures fix only passability of the boundary tiles.
        border_tiles = (
            {(x, 0) for x in range(32)} | {(x, 29) for x in range(32)} | {(x, y) for x in (0, 31) for y in range(30)}
        )
        for tx, ty in border_tiles:
            position, offset = address(tx, ty)
            old = source[ty][tx]
            domains[position] &= masks[offset] if old < 0x26 or old in extra else full ^ masks[offset]
        rows = terrain_rows(hierarchy, cells, extra)
        for side in SIDES:
            offsets = range(1, 240) if side in ("north", "south") else range(8, 212)
            for landing in (False, True):
                legal = edge_positions(rows, side, arrival=landing)
                for offset in offsets:
                    x, y = arrival(side, offset) if landing else departure(side, offset)
                    probes = _probe_tiles(x, y)
                    if legal >> offset & 1:
                        for tx, ty in probes:
                            position, within = address(tx, ty)
                            domains[position] &= masks[within]
                    else:
                        blocking = {}
                        for tx, ty in probes:
                            position, within = address(tx, ty)
                            blocking[position] = blocking.get(position, 0) | (full ^ masks[within])
                        clauses.add(tuple(sorted(blocking.items())))
    return domains, sorted(clauses)


def _components(grid, hierarchy):
    pending = {
        (x, y)
        for y in range(15)
        for x in range(16)
        if sum(t >= 0x26 for t in hierarchy["small_blocks"][grid[y][x]]) >= 2
    }
    result = []
    while pending:
        start = pending.pop()
        found, todo = {start}, [start]
        while todo:
            x, y = todo.pop()
            for dx, dy in DELTAS:
                point = x + dx, y + dy
                if point in pending:
                    pending.remove(point)
                    found.add(point)
                    todo.append(point)
        result.append(found)
    return result


def _island_library(grids, hierarchy, allowed):
    result, seen = [], set()
    for grid in grids:
        for component in _components(grid, hierarchy):
            if any(x in (0, 15) or y in (0, 14) or grid[y][x] not in allowed for x, y in component):
                continue
            left, right = min(x for x, _ in component), max(x for x, _ in component)
            top, bottom = min(y for _, y in component), max(y for _, y in component)
            if right - left > 5 or bottom - top > 5:
                continue
            pattern = tuple(sorted((x - left, y - top, grid[y][x]) for x, y in component))
            key = pattern, left % 2, top % 2
            if key not in seen:
                seen.add(key)
                result.append(
                    {
                        "cells": pattern,
                        "phase": (left % 2, top % 2),
                        "width": right - left + 1,
                        "height": bottom - top + 1,
                    }
                )
    return result


def _valid_graph(grid, graph, locked, original_grid=None, strict_cells=(), exception_cells=None):
    for y in range(15):
        for x in range(16):
            for direction in (1, 2):
                dx, dy = DELTAS[direction]
                nx, ny = x + dx, y + dy
                if nx >= 16 or ny >= 15 or (x // 2, y // 2) in locked and (nx // 2, ny // 2) in locked:
                    continue
                first, second = grid[y][x], grid[ny][nx]
                if not graph.allows(first, second, direction):
                    return False
                if not graph.safe_coastline(first, second, direction):
                    inherited = (
                        original_grid is not None and first == original_grid[y][x] and second == original_grid[ny][nx]
                    )
                    if exception_cells is not None:
                        inherited = inherited and (
                            (x // 2, y // 2) in exception_cells or (nx // 2, ny // 2) in exception_cells
                        )
                    if not inherited or (x, y) in strict_cells or (nx, ny) in strict_cells:
                        return False
    return True


def _assemblies_supported(grid, rules, locked, *, free_geometry=False, diagnostics=None):
    supporting = {
        "structural-rock",
        "brick",
        "ruins-brick",
        "floor-cover",
        "volcanic-ground-assembly",
        "cave-entrance-assembly",
        "temple-column-assembly",
        "temple-statue-assembly",
        "scenic-mountain-assembly",
    }
    kinds = ("coral-decoration", "supported-decoration")
    if free_geometry:
        kinds += (
            "floor-cover",
            "scenic-mountain-assembly",
            "volcanic-ground-assembly",
            "temple-column-assembly",
            "temple-statue-assembly",
            "cave-entrance-assembly",
        )
    for kind in kinds:
        pending = {(x, y) for y in range(15) for x in range(16) if rules[grid[y][x]]["kind"] == kind}
        while pending:
            start = pending.pop()
            component = {start}
            todo = [start]
            while todo:
                x, y = todo.pop()
                for dx, dy in DELTAS:
                    point = x + dx, y + dy
                    if point in pending:
                        pending.remove(point)
                        component.add(point)
                        todo.append(point)
            if all((x // 2, y // 2) in locked for x, y in component):
                continue
            if kind not in ("coral-decoration", "supported-decoration") and any(y == 14 for x, y in component):
                continue
            directions = DELTAS if kind == "coral-decoration" else ((0, 1),)
            if not any(
                0 <= x + dx < 16
                and 0 <= y + dy < 15
                and (x + dx, y + dy) not in component
                and rules[grid[y + dy][x + dx]]["kind"] in supporting
                for x, y in component
                for dx, dy in directions
            ):
                if diagnostics is not None:
                    diagnostics.append(
                        {
                            "kind": kind,
                            "cells": sorted(component),
                            "below": [
                                (grid[y + 1][x], rules[grid[y + 1][x]]["kind"])
                                for x, y in sorted(component)
                                if y < 14 and (x, y + 1) not in component
                            ],
                        }
                    )
                return False
    return True


def generate_adjacency_world(
    document: Mapping,
    *,
    catalogue: Mapping,
    semantics: Mapping,
    fixed_rooms: Sequence[int],
    seed: int = 0,
    attempts_per_room: int = 64,
    island_probability: float = 0.2,
    extra_passable: Mapping[int, Sequence[int]] | None = None,
    room_controls: Mapping | None = None,
) -> tuple[dict, dict]:
    if (
        type(seed) is not int
        or type(attempts_per_room) is not int
        or not 1 <= attempts_per_room <= 10000
        or isinstance(island_probability, bool)
        or not isinstance(island_probability, (int, float))
        or not 0 <= island_probability <= 1
    ):
        raise AuthoringError("adjacency generation needs an integer seed, bounded attempts and island probability 0..1")
    if semantics.get("schema") != "doraemon.world3-semantics.v1" or semantics.get("source_sha256") != document.get(
        "source_sha256"
    ):
        raise AuthoringError("primitive semantics must match the source ROM")
    rules = semantics.get("primitives", [])
    if len(rules) != 256 or [r.get("id") for r in rules] != list(range(256)):
        raise AuthoringError("primitive semantics need all 256 ordered IDs")
    fixed = {room_number(i) for i in fixed_rooms}
    hierarchy = hierarchy_of(document)
    themes = room_themes(document)
    original = [room_cells(hierarchy, i) for i in range(64)]
    additional_controls = _read_controls(document, room_controls)
    grids = [small_grid(hierarchy, cells) for cells in original]
    allowed = {r["id"] for r in rules if r["policy"] not in ("event-only", "unresolved", "discard")}
    policies = {r["id"]: r["policy"] for r in rules}
    background = set(catalogue["backgrounds"].values())
    pool = PrimitivePainter(hierarchy, catalogue)
    libraries = {}
    for theme in themes:
        key = theme["kind"], theme["palette"], theme["music_class"]
        if key not in libraries:
            donors = [
                other["room"] for other in themes if (other["kind"], other["palette"], other["music_class"]) == key
            ]
            grammar = PackedGrammar.learn(
                hierarchy,
                [grids[i] for i in donors],
                [original[i] for i in donors],
                allowed,
                catalogue,
                semantic_closure=True,
            )
            libraries[key] = (grammar, _island_library([grids[i] for i in donors], hierarchy, allowed - background))
            # Grammar templates can be unreferenced in the original map. Once
            # admitted to the vocabulary they too must survive island packing.
            pool.used.update(grammar.eligible | grammar.bottom_eligible)
    result = deepcopy(document)
    output = resource(result, "world3-hierarchy")["maps"][0]["rows"]
    registry = resource(document, "world3-objects")["records"]
    reviews = []
    for room, cells in enumerate(original):
        theme = themes[room]
        controls = controls_for_room(hierarchy, room)
        if room in fixed:
            if any(cells[a["y"]][a["x"]] != a["block"] for a in additional_controls[room]):
                raise AuthoringError(f"room {room:#04x}: an anchor changes a fixed event room")
            reviews.append(
                {**theme, "status": "fixed", "controls": controls, "changed_cells": 0, "changed_terrain_tiles": 0}
            )
            continue
        rng = random.Random(f"{seed}:room:{room}")
        grammar, islands = libraries[theme["kind"], theme["palette"], theme["music_class"]]
        grammar.anchor_grid = grids[room]
        grammar.coast_cache.clear()
        locked = _locks(hierarchy, cells, policies)
        native_locked = set(locked)
        anchor_cells = [list(line) for line in cells]
        for anchor in additional_controls[room]:
            x, y, value = anchor["x"], anchor["y"], anchor["block"]
            if (x, y) in locked and value != cells[y][x]:
                raise AuthoringError(f"room {room:#04x}: an anchor changes a protected structure")
            if (x, y) not in locked and value not in grammar.eligible:
                raise AuthoringError(f"room {room:#04x}: control block is not in the room's graph vocabulary")
            if value == cells[y][x]:
                native_locked.add((x, y))
            locked.add((x, y))
            anchor_cells[y][x] = value
        pinned = {y * 8 + x for x, y in native_locked}
        points = [(r["x"], r["y"]) for r in registry if r["room"] == room]
        models = [()]
        if extra_passable and extra_passable.get(room):
            models.append(tuple(extra_passable[room]))
        grammar.exception_cells = native_locked
        port_domains, grammar.clauses = _port_domains(hierarchy, cells, models)
        contracts = [_interfaces(terrain_rows(hierarchy, cells, extra), points) for extra in models]
        quality = [quality_contract(room_tiles(hierarchy, cells), extra) for extra in models]

        def valid(candidate, h):
            return all(
                _preserves_interfaces(terrain_rows(h, candidate, extra), contract, exact=True)
                and check_quality(room_tiles(h, candidate), guard, extra)
                for extra, contract, guard in zip(models, contracts, quality)
            )

        best = None
        last_support = None
        last_empty = []
        rejected = Counter()
        for attempt in range(attempts_per_room):
            depth = rng.choice((1, 2, 3))
            domains = []
            for y in range(8):
                for x in range(8):
                    if (x, y) in locked:
                        domains.append((1 << anchor_cells[y][x]) & port_domains[y * 8 + x])
                        continue
                    distance = min(x, 7 - x, y, 7 - y)
                    candidates = grammar.bottom_eligible if y == 7 else grammar.eligible
                    if distance > depth:
                        candidates = {i for i in candidates if set(hierarchy["big_blocks"][i]) <= background}
                    domains.append(sum(1 << i for i in candidates) & port_domains[y * 8 + x])
            candidate = grammar.collapse(domains, pinned, rng, open_bias=rng.choice((1, 2, 4, 8)))
            if candidate is None:
                last_empty = [
                    (i, cells[i // 8][i % 8], bool(port_domains[i] & (1 << cells[i // 8][i % 8])))
                    for i, m in enumerate(domains)
                    if not m
                ]
                rejected["graph_contradiction"] += 1
                continue
            grid = small_grid(hierarchy, candidate)
            if not _valid_graph(
                grid, grammar.graph, native_locked, grids[room], exception_cells=grammar.exception_cells
            ):
                rejected["new_flat_coastline"] += 1
                continue
            issues = []
            if not _assemblies_supported(grid, rules, native_locked, free_geometry=True, diagnostics=issues):
                last_support = issues[0]
                rejected["support:" + issues[0]["kind"]] += 1
                continue
            if any(
                all((x // 2, y // 2) not in locked and x not in (0, 15) and y not in (0, 14) for x, y in component)
                for component in _components(grid, hierarchy)
            ):
                rejected["premature_island"] += 1
                continue
            if not valid(candidate, hierarchy):
                rejected["player_contract"] += 1
                continue
            best = candidate
            break
        if best is None:
            raise AuthoringError(
                f"room {room:#04x}: adjacency graph found no layout in {attempts_per_room} attempts: {dict(rejected)}; {grammar.last_contradiction}; support {last_support}; empty {last_empty}"
            )
        island_added = False
        island_reason = "seed-no-island"
        island_rng = random.Random(f"{seed}:islands:{room}")
        if island_rng.random() < island_probability:
            island_reason = "no-compatible-space-or-motif"
            options = list(islands)
            island_rng.shuffle(options)
            for pattern in options:
                positions = [
                    (x, y)
                    for y in range(2, 13 - pattern["height"])
                    for x in range(2, 14 - pattern["width"])
                    if x % 2 == pattern["phase"][0] and y % 2 == pattern["phase"][1]
                ]
                island_rng.shuffle(positions)
                for x, y in positions:
                    grid = small_grid(pool.hierarchy, best)
                    cells_to_add = {(x + dx, y + dy): small for dx, dy, small in pattern["cells"]}
                    region = {(px + dx, py + dy) for px, py in cells_to_add for dx in (-1, 0, 1) for dy in (-1, 0, 1)}
                    if any((px // 2, py // 2) in locked or grid[py][px] not in background for px, py in region):
                        continue
                    for (px, py), small in cells_to_add.items():
                        grid[py][px] = small
                    if not _valid_graph(
                        grid,
                        grammar.graph,
                        native_locked,
                        grids[room],
                        cells_to_add,
                        exception_cells=grammar.exception_cells,
                    ):
                        continue
                    if not _assemblies_supported(grid, rules, native_locked, free_geometry=True):
                        continue
                    trial = deepcopy(pool)
                    candidate = [list(line) for line in best]
                    try:
                        for bx, by in sorted({(px // 2, py // 2) for px, py in cells_to_add}):
                            candidate[by][bx] = trial._block(
                                [
                                    grid[by * 2][bx * 2],
                                    grid[by * 2][bx * 2 + 1],
                                    grid[by * 2 + 1][bx * 2],
                                    grid[by * 2 + 1][bx * 2 + 1],
                                ]
                            )
                    except AuthoringError:
                        continue
                    if not valid(candidate, trial.hierarchy):
                        continue
                    pool, best = trial, candidate
                    island_added = True
                    island_reason = "graph-validated"
                    break
                if island_added:
                    break
        for y, line in enumerate(best):
            output[room // 8 * 8 + y][room % 8 * 8 : room % 8 * 8 + 8] = line
        before = room_tiles(hierarchy, cells)
        after = room_tiles(pool.hierarchy, best)
        reviews.append(
            {
                **theme,
                "status": "generated",
                "boundary_mode": "ports",
                "archetype": "adjacency-shell",
                "controls": controls,
                "locked_cells": [list(p) for p in sorted(locked)],
                "native_locked_cells": [list(p) for p in sorted(native_locked)],
                "anchors": [{"x": x, "y": y, "block": anchor_cells[y][x]} for x, y in sorted(locked)],
                "attempts": attempt + 1,
                "rejected": dict(rejected),
                "graph_templates": len(grammar.eligible),
                "inferred_graph_pairs": sum(len(v) for v in grammar.graph.inferred),
                "island_motifs": len(islands),
                "island_added": island_added,
                "island_reason": island_reason,
                "changed_cells": sum(a != b for aa, bb in zip(cells, best) for a, b in zip(aa, bb)),
                "changed_terrain_tiles": sum(
                    (a >= 0x26) != (b >= 0x26) for aa, bb in zip(before[:30], after[:30]) for a, b in zip(aa, bb)
                ),
            }
        )
    for key in ("attributes", "small_blocks", "big_blocks"):
        resource(result, "world3-hierarchy")[key] = pool.hierarchy[key]
    return result, {
        "schema": "doraemon.adjacency-world.v1",
        "seed": seed,
        "boundary_mode": "ports",
        "island_probability": island_probability,
        "room_count": 64,
        "fixed_rooms": sorted(fixed),
        "changed_rooms": sum(r["changed_cells"] > 0 for r in reviews),
        "changed_cells": sum(r["changed_cells"] for r in reviews),
        "changed_terrain_tiles": sum(r["changed_terrain_tiles"] for r in reviews),
        "island_rooms": [r["room"] for r in reviews if r.get("island_added")],
        "rooms": reviews,
    }


def audit_adjacency_world(
    original: Mapping,
    candidate: Mapping,
    catalogue: Mapping,
    semantics: Mapping,
    report: Mapping,
    *,
    extra_passable=None,
) -> dict:
    source, output = hierarchy_of(original), hierarchy_of(candidate)
    themes = room_themes(original)
    allowed = {r["id"] for r in semantics["primitives"] if r["policy"] not in ("event-only", "unresolved", "discard")}
    graphs = {}
    checked = 0
    for item in report["rooms"]:
        room = item["room"]
        old = room_cells(source, room)
        new = room_cells(output, room)
        if item["status"] == "fixed":
            if old != new:
                raise AuthoringError(f"fixed room {room:#04x} changed")
            continue
        locked = {tuple(p) for p in item["native_locked_cells"]}
        if any(new[a["y"]][a["x"]] != a["block"] for a in item["anchors"]):
            raise AuthoringError(f"room {room:#04x}: a control block changed")
        if controls_for_room(source, room) != controls_for_room(output, room):
            raise AuthoringError(f"room {room:#04x}: an opening interval changed")
        key = item["kind"], item["palette"], item["music_class"]
        if key not in graphs:
            donors = [
                other["room"] for other in themes if (other["kind"], other["palette"], other["music_class"]) == key
            ]
            graphs[key] = NeighborGraph.learn(
                [small_grid(source, room_cells(source, i)) for i in donors],
                catalogue,
                source,
                semantic_closure=True,
            )
        grid = small_grid(output, new)
        if not _valid_graph(grid, graphs[key], locked, small_grid(source, old), exception_cells=locked):
            raise AuthoringError(f"room {room:#04x}: an unobserved or unsafe adjacency was introduced")
        if not _assemblies_supported(grid, semantics["primitives"], locked, free_geometry=True):
            raise AuthoringError(f"room {room:#04x}: unsupported new decoration")
        points = [(r["x"], r["y"]) for r in resource(original, "world3-objects")["records"] if r["room"] == room]
        models = [(), *([extra_passable[room]] if extra_passable and extra_passable.get(room) else [])]
        for extra in models:
            contract = _interfaces(terrain_rows(source, old, extra), points)
            if not _preserves_interfaces(terrain_rows(output, new, extra), contract, exact=True):
                raise AuthoringError(f"room {room:#04x}: player routes or object access changed")
            if not check_quality(room_tiles(output, new), quality_contract(room_tiles(source, old), extra), extra):
                raise AuthoringError(f"room {room:#04x}: new isolated space or narrow chamber")
        for y in range(15):
            for x in range(16):
                if (x // 2, y // 2) not in locked:
                    if grid[y][x] not in allowed:
                        raise AuthoringError(f"room {room:#04x}: forbidden primitive {grid[y][x]:#04x}")
                    checked += 1
    return {
        "schema": "doraemon.adjacency-audit.v1",
        "checked_new_primitives": checked,
        "checked_rooms": len(report["rooms"]),
        "violations": [],
        "island_rooms": report["island_rooms"],
        "scope": "palette/environment/music-conditioned graph with shape-derived rock contacts; only runtime assemblies and port physics fixed",
    }
