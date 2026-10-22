"""Derived tutorial-route geometry for the World 2 stage bytecode.

Screen placements, branch graph and chapter palettes are derived from three
editable, disjoint physical ROM owners.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from retrolarium_kit.games.doraemon.world2.branches import encode_branches
from retrolarium_kit.games.doraemon.world2.chapters import encode_chapters
from retrolarium_kit.games.doraemon.world2.projection import project_screen
from retrolarium_kit.games.doraemon.world2.stage import encode_stage
from retrolarium_kit.games.doraemon.world2.streams import encode_screens


ROUTE_NAMES = ("Part 1", "Part 2", "Part 2A", "Part 2B", "Part 3")
STEPS = ((1, 0), (0, -1), (0, 1), (-1, 0))
DESTINATIONS = (134, 144, 154, 162, 180, 192, 202, 212, 215, 223)


@dataclass(frozen=True)
class Placement:
    sequence_offset: int
    selector_id: int
    direction: int
    screen_x: int
    screen_y: int
    palette_index: int


@dataclass(frozen=True)
class Annotation:
    screen_x: int
    screen_y: int
    text: str


@dataclass(frozen=True)
class Route:
    name: str
    placements: tuple[Placement, ...]
    annotations: tuple[Annotation, ...] = ()

    @property
    def min_x(self) -> int:
        return min(item.screen_x for item in self.placements)

    @property
    def min_y(self) -> int:
        return min(item.screen_y for item in self.placements)

    @property
    def width(self) -> int:
        return (
            max(item.screen_x for item in self.placements) - min(item.screen_x for item in self.placements) + 1
        ) * 16

    @property
    def height(self) -> int:
        return (
            max(item.screen_y for item in self.placements) - min(item.screen_y for item in self.placements) + 1
        ) * 15

    def visible_placements(self) -> tuple[Placement, ...]:
        seen: set[tuple[int, int]] = set()
        result = []
        for item in self.placements:
            position = (item.screen_x, item.screen_y)
            if position not in seen:
                seen.add(position)
                result.append(item)
        return tuple(result)

    def locate(self, x: int, y: int) -> tuple[Placement, int, int] | None:
        if not 0 <= x < self.width or not 0 <= y < self.height:
            return None
        screen_x = x // 16 + self.min_x
        screen_y = y // 15 + self.min_y
        for item in self.visible_placements():
            if (item.screen_x, item.screen_y) == (screen_x, screen_y):
                return item, x % 16, y % 15
        return None


def _walk(tokens: bytes, start: int, end: int, palette: int) -> tuple[Placement, ...]:
    x = y = direction = 0
    result: list[Placement] = []
    for offset in range(start, end + 1):
        token = tokens[offset]
        if 0xF0 <= token < 0xF7:
            direction = token & 3
            if result:
                dx, dy = STEPS[direction]
                x, y = result[-1].screen_x + dx, result[-1].screen_y + dy
        elif token >= 0xF9:
            palette = max(0, (token & 7) - 1)
        elif token < 0xF0:
            selector = token & 0x7F
            if selector <= 0x76:
                result.append(Placement(offset, selector, direction, x, y, palette))
            dx, dy = STEPS[direction]
            x, y = x + dx, y + dy
    return tuple(result)


def _selector(placements: tuple[Placement, ...], selector: int) -> Placement:
    try:
        return next(item for item in placements if item.selector_id == selector)
    except StopIteration as exc:
        raise ValueError(f"World 2 route has no selector ${selector:02X}") from exc


def _attach(main: tuple[Placement, ...], branch: tuple[Placement, ...], selector: int) -> tuple[Placement, ...]:
    if not branch:
        return main
    trigger = _selector(main, selector)
    dx, dy = STEPS[branch[0].direction]
    shift_x = trigger.screen_x + dx - branch[0].screen_x
    shift_y = trigger.screen_y + dy - branch[0].screen_y
    return main + tuple(
        Placement(
            item.sequence_offset,
            item.selector_id,
            item.direction,
            item.screen_x + shift_x,
            item.screen_y + shift_y,
            item.palette_index,
        )
        for item in branch
    )


def _endpoints(name: str, placements: tuple[Placement, ...]) -> tuple[Annotation, ...]:
    return (
        Annotation(placements[0].screen_x, placements[0].screen_y, f"{name} entrance"),
        Annotation(placements[-1].screen_x, placements[-1].screen_y, f"{name} exit"),
    )


def build_routes(
    stage: dict[str, Any],
    branch_document: dict[str, Any],
    chapter_document: dict[str, Any],
) -> dict[str, Route]:
    """Project five routes from editable stage bytes and canonical branch data."""
    tokens, starts = encode_stage(stage)
    encode_branches(branch_document)
    encode_chapters(chapter_document)
    initial_palette_ids = chapter_document["background_palette_ids"]
    branches = tuple(
        (entry["destination_offset"], entry["trigger_screen_id"], entry["return_offset_override"])
        for entry in branch_document["branches"]
    )
    destinations = tuple(sorted({destination for destination, _, _ in branches if destination >= 134}))
    if destinations != DESTINATIONS:
        raise ValueError("World 2 branch route boundaries differ")
    if not starts[0] < starts[1] < starts[2] < DESTINATIONS[0]:
        raise ValueError("World 2 main-route chapter starts are out of order")
    if any(not 1 <= palette <= 9 for palette in initial_palette_ids):
        raise ValueError("World 2 initial palette IDs must be in 1..9")
    end = {
        start: (DESTINATIONS[index + 1] - 1 if index + 1 < len(DESTINATIONS) else len(tokens) - 1)
        for index, start in enumerate(DESTINATIONS)
    }
    initial = tuple(item - 1 for item in initial_palette_ids)
    main1 = _walk(tokens, starts[0], starts[1] - 1, initial[0])
    main2 = _walk(tokens, starts[1], starts[2] - 1, initial[1])
    main3 = _walk(tokens, starts[2], DESTINATIONS[0] - 1, initial[2])
    by_destination = {destination: (selector, override) for destination, selector, override in branches}

    part1 = main1
    for destination in (134, 144, 154):
        selector, _ = by_destination[destination]
        trigger = _selector(main1, selector)
        part1 = _attach(part1, _walk(tokens, destination, end[destination], trigger.palette_index), selector)
    part3 = main3
    for destination in (215, 223):
        selector, _ = by_destination[destination]
        trigger = _selector(main3, selector)
        part3 = _attach(part3, _walk(tokens, destination, end[destination], trigger.palette_index), selector)
    part2a = _walk(tokens, 162, end[162], initial[1])
    part2b_segments = tuple(
        _walk(tokens, destination, end[destination], initial[1]) for destination in (180, 192, 202, 212)
    )
    part2b = tuple(item for segment in part2b_segments for item in segment)

    annotations: list[Annotation] = []
    for destination, selector, override in branches:
        name = "Part 2A" if destination == 162 else "Part 2B" if destination in (180, 192, 202, 212) else None
        if name is None:
            continue
        trigger = _selector(main2, selector)
        annotations.append(Annotation(trigger.screen_x, trigger.screen_y, f"{name} entrance"))
        return_offset = trigger.sequence_offset if override is None else override
        exit_placement = next((item for item in main2 if item.sequence_offset > return_offset), main2[-1])
        annotations.append(Annotation(exit_placement.screen_x, exit_placement.screen_y, f"{name} exit"))

    return {
        "Part 1": Route("Part 1", part1),
        "Part 2": Route("Part 2", main2, tuple(annotations)),
        "Part 2A": Route("Part 2A", part2a, _endpoints("Part 2A", part2a)),
        "Part 2B": Route("Part 2B", part2b, _endpoints("Part 2B", part2b_segments[0])),
        "Part 3": Route("Part 3", part3),
    }


def paint_route_cell(
    screen_resource: dict[str, Any],
    route: Route,
    x: int,
    y: int,
    metatile: int,
) -> tuple[int, int, int] | None:
    """Paint a background cell, propagating pointer aliases atomically.

    Returns (selector, raw_x, raw_y), or None for an empty or unchanged cell.
    The original payload stays in raw_hex so zero-edit and inverse ownership
    remain explicit; the edited logical screens are capacity-checked now.
    """
    if not isinstance(metatile, int) or isinstance(metatile, bool) or not 0 <= metatile < 208:
        raise ValueError("World 2 route brush must be a background metatile")
    located = route.locate(x, y)
    if located is None:
        return None
    placement, local_x, local_y = located
    projection = project_screen(screen_resource, placement.selector_id, placement.direction)
    if projection.cells[local_y][local_x] >= 208:
        raise ValueError("World 2 spawn or padding cell is not a background")
    raw_x, raw_y = projection.raw_coordinate(local_x, local_y)
    if projection.cells[local_y][local_x] == metatile:
        return None
    payload = bytes.fromhex(screen_resource["raw_hex"])
    selected_pointer = payload[placement.selector_id * 2 : placement.selector_id * 2 + 2]
    updated = [[list(row) for row in screen] for screen in screen_resource["screens"]]
    for selector in range(119):
        if payload[selector * 2 : selector * 2 + 2] == selected_pointer:
            updated[selector][raw_y][raw_x] = metatile
    encode_screens({"raw_hex": screen_resource["raw_hex"], "screens": updated})
    screen_resource["screens"] = updated
    return placement.selector_id, raw_x, raw_y


def route_view(
    screen_resource: dict[str, Any],
    stage: dict[str, Any],
    branch_document: dict[str, Any],
    chapter_document: dict[str, Any],
) -> dict[str, Any]:
    """The five routes as panels an editor can draw and paint.

    Each visible panel carries its selector, direction and background palette
    preset, and per runtime cell either the raw (x, y) of the selector's
    logical screen it shows or the fixed value of a padding cell. The
    geometry depends only on the stage, branch and chapter bytes and the
    original stream layout (raw_hex), so an editor can keep it while it
    changes cell values; aliases lists the selectors that share one stream.
    """
    routes = build_routes(stage, branch_document, chapter_document)
    payload = bytes.fromhex(screen_resource["raw_hex"])
    pointers = [payload[index * 2 : index * 2 + 2] for index in range(119)]
    projections: dict[tuple[int, int], Any] = {}
    result_routes = []
    for name in ROUTE_NAMES:
        route = routes[name]
        panels = []
        for item in route.visible_placements():
            key = (item.selector_id, item.direction)
            if key not in projections:
                projections[key] = project_screen(screen_resource, *key)
            projection = projections[key]
            panels.append(
                {
                    "selector": item.selector_id,
                    "direction": item.direction,
                    "x": item.screen_x - route.min_x,
                    "y": item.screen_y - route.min_y,
                    "palette": item.palette_index,
                    "sequence_offset": item.sequence_offset,
                    "cells": [
                        [list(coordinate) if coordinate is not None else None for coordinate in row]
                        for row in projection.raw_coordinates
                    ],
                    "fixed": [
                        [value if coordinate is None else None for value, coordinate in zip(values, coordinates)]
                        for values, coordinates in zip(projection.cells, projection.raw_coordinates)
                    ],
                }
            )
        result_routes.append(
            {
                "name": name,
                "columns": route.width // 16,
                "rows": route.height // 15,
                "panels": panels,
                "annotations": [
                    {"x": item.screen_x - route.min_x, "y": item.screen_y - route.min_y, "text": item.text}
                    for item in route.annotations
                ],
            }
        )
    return {
        "schema": "retrolarium.doraemon-world2-view",
        "schema_version": 1,
        "routes": result_routes,
        "aliases": [
            [other for other in range(119) if pointers[other] == pointers[selector]] for selector in range(119)
        ],
    }
