"""Fixed-size World 1/3 hierarchy codec for Doraemon's GNROM PRG banks.

One resource owns its attributes, 16x16 blocks, 32x32 blocks and every map.
This keeps the level editor and later graphics/metatile views from writing the
same physical bytes through competing documents.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .authoring import AuthoringError


CODEC = "doraemon.hierarchy.v1"
WORLD_MAPS = {
    "world1": (("city", 64, 64), ("underground", 64, 25)),
    "world3": (("underwater", 64, 64),),
}


class DoraemonHierarchyError(AuthoringError):
    """The hierarchy document cannot be represented in its fixed ROM range."""


def _world_maps(world: object) -> tuple[tuple[str, int, int], ...]:
    if not isinstance(world, str) or world not in WORLD_MAPS:
        raise DoraemonHierarchyError("hierarchy world must be world1 or world3")
    return WORLD_MAPS[world]


def payload_size(world: str) -> int:
    return 256 + 256 * 4 + 256 * 4 + sum(
        width * height for _name, width, height in _world_maps(world)
    )


def decode_hierarchy(payload: bytes, world: str) -> dict[str, Any]:
    maps = _world_maps(world)
    if len(payload) != payload_size(world):
        raise DoraemonHierarchyError(
            f"{world} hierarchy needs exactly {payload_size(world)} bytes"
        )
    attributes = [
        {"palette": value & 3, "properties": value >> 2}
        for value in payload[:256]
    ]
    small_blocks = [
        list(payload[256 + index * 4 : 260 + index * 4])
        for index in range(256)
    ]
    big_blocks = [
        list(payload[1280 + index * 4 : 1284 + index * 4])
        for index in range(256)
    ]
    offset = 2304
    decoded_maps: list[dict[str, Any]] = []
    for name, width, height in maps:
        length = width * height
        values = payload[offset : offset + length]
        decoded_maps.append({
            "id": name,
            "width": width,
            "height": height,
            "rows": [list(values[row * width : (row + 1) * width])
                     for row in range(height)],
        })
        offset += length
    return {
        "world": world,
        "attributes": attributes,
        "small_blocks": small_blocks,
        "big_blocks": big_blocks,
        "maps": decoded_maps,
    }


def _integer(value: object, maximum: int, context: str) -> int:
    if (not isinstance(value, int) or isinstance(value, bool)
            or not 0 <= value <= maximum):
        raise DoraemonHierarchyError(
            f"{context} must be an integer from 0 to {maximum}"
        )
    return value


def _records(value: object, context: str) -> bytes:
    if not isinstance(value, list) or len(value) != 256:
        raise DoraemonHierarchyError(f"{context} must contain 256 records")
    result = bytearray()
    for index, record in enumerate(value):
        if not isinstance(record, list) or len(record) != 4:
            raise DoraemonHierarchyError(
                f"{context}[{index}] must contain four values"
            )
        result.extend(
            _integer(cell, 255, f"{context}[{index}][{cell_index}]")
            for cell_index, cell in enumerate(record)
        )
    return bytes(result)


def encode_hierarchy(document: Mapping[str, Any], world: str) -> bytes:
    maps = _world_maps(world)
    if not isinstance(document, Mapping) or set(document) != {
        "world", "attributes", "small_blocks", "big_blocks", "maps"
    } or document["world"] != world:
        raise DoraemonHierarchyError(f"{world} hierarchy fields differ from contract")
    attributes = document["attributes"]
    if not isinstance(attributes, list) or len(attributes) != 256:
        raise DoraemonHierarchyError("attributes must contain 256 entries")
    output = bytearray()
    for index, attribute in enumerate(attributes):
        if not isinstance(attribute, Mapping) or set(attribute) != {
            "palette", "properties"
        }:
            raise DoraemonHierarchyError(
                f"attributes[{index}] needs palette and properties"
            )
        palette = _integer(attribute["palette"], 3,
                           f"attributes[{index}].palette")
        properties = _integer(attribute["properties"], 63,
                              f"attributes[{index}].properties")
        output.append((properties << 2) | palette)
    output.extend(_records(document["small_blocks"], "small_blocks"))
    output.extend(_records(document["big_blocks"], "big_blocks"))
    raw_maps = document["maps"]
    if not isinstance(raw_maps, list) or len(raw_maps) != len(maps):
        raise DoraemonHierarchyError(f"{world} has the wrong map count")
    for index, (raw, (name, width, height)) in enumerate(zip(raw_maps, maps)):
        if not isinstance(raw, Mapping) or set(raw) != {
            "id", "width", "height", "rows"
        } or (raw["id"], raw["width"], raw["height"]) != (name, width, height):
            raise DoraemonHierarchyError(f"maps[{index}] identity or size differs")
        rows = raw["rows"]
        if not isinstance(rows, list) or len(rows) != height:
            raise DoraemonHierarchyError(f"maps[{index}] has the wrong height")
        for row_index, row in enumerate(rows):
            if not isinstance(row, list) or len(row) != width:
                raise DoraemonHierarchyError(
                    f"maps[{index}].rows[{row_index}] has the wrong width"
                )
            output.extend(
                _integer(cell, 255,
                         f"maps[{index}].rows[{row_index}][{column}]")
                for column, cell in enumerate(row)
            )
    if len(output) != payload_size(world):
        raise DoraemonHierarchyError(f"{world} hierarchy changed fixed capacity")
    return bytes(output)
