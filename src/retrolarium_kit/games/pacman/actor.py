"""Pac-Man four-quadrant actor-frame mapping codec."""

from __future__ import annotations

from typing import Mapping

from .authoring import AuthoringError


CODEC = "pacman.actor-mapping.v1"
ENCODED_SIZE = 624
STANDARD_FRAMES = 64
ALTERNATE_FRAMES = 13


class PacmanActorError(AuthoringError):
    """An actor mapping violates the fixed Pac-Man OAM layout."""


def _records(
    value: object, count: int, width: int, name: str, *, attributes: bool = False
) -> tuple[tuple[int, ...], ...]:
    if not isinstance(value, list) or len(value) != count:
        raise PacmanActorError(f"{name} must contain {count} records")
    result = []
    for index, record in enumerate(value):
        if not isinstance(record, list) or len(record) != width:
            raise PacmanActorError(f"{name}[{index}] must contain {width} bytes")
        normalized = []
        for byte_index, item in enumerate(record):
            if not isinstance(item, int) or isinstance(item, bool) or not 0 <= item <= 0xFF:
                raise PacmanActorError(
                    f"{name}[{index}][{byte_index}] must be a byte"
                )
            if attributes and item & 0x1C:
                raise PacmanActorError(
                    f"{name}[{index}][{byte_index}] uses unsupported OAM attribute bits"
                )
            normalized.append(item)
        result.append(tuple(normalized))
    return tuple(result)


def decode_actor_mapping(payload: bytes) -> dict[str, object]:
    if len(payload) != ENCODED_SIZE:
        raise PacmanActorError(
            f"actor mapping must contain {ENCODED_SIZE} bytes"
        )
    cursor = 0

    def take(count: int, width: int) -> list[list[int]]:
        nonlocal cursor
        result = [
            list(payload[start : start + width])
            for start in range(cursor, cursor + count * width, width)
        ]
        cursor += count * width
        return result

    document = {
        "standard_tile_quads": take(STANDARD_FRAMES, 4),
        "alternate_tile_quads": take(ALTERNATE_FRAMES, 4),
        "standard_attribute_quads": take(STANDARD_FRAMES, 4),
        "alternate_attribute_quads": take(ALTERNATE_FRAMES, 4),
        "oam_offsets_yx": take(4, 2),
    }
    encode_actor_mapping(document)
    return document


def encode_actor_mapping(value: object) -> bytes:
    if not isinstance(value, Mapping):
        raise PacmanActorError("actor mapping must be an object")
    expected = {
        "standard_tile_quads",
        "alternate_tile_quads",
        "standard_attribute_quads",
        "alternate_attribute_quads",
        "oam_offsets_yx",
    }
    if set(value) != expected:
        raise PacmanActorError("actor mapping fields differ from codec v1")
    sections = (
        _records(value["standard_tile_quads"], STANDARD_FRAMES, 4,
                 "standard_tile_quads"),
        _records(value["alternate_tile_quads"], ALTERNATE_FRAMES, 4,
                 "alternate_tile_quads"),
        _records(value["standard_attribute_quads"], STANDARD_FRAMES, 4,
                 "standard_attribute_quads", attributes=True),
        _records(value["alternate_attribute_quads"], ALTERNATE_FRAMES, 4,
                 "alternate_attribute_quads", attributes=True),
        _records(value["oam_offsets_yx"], 4, 2, "oam_offsets_yx"),
    )
    offsets = sections[-1]
    signed = tuple(
        ((row[0] if row[0] < 0x80 else row[0] - 0x100),
         (row[1] if row[1] < 0x80 else row[1] - 0x100))
        for row in offsets
    )
    min_y = min(y for y, _x in signed)
    min_x = min(x for _y, x in signed)
    if {(y - min_y, x - min_x) for y, x in signed} != {
        (0, 0), (0, 8), (8, 0), (8, 8)
    }:
        raise PacmanActorError("OAM offsets must form one 16x16 four-tile layout")
    payload = bytes(item for section in sections for row in section for item in row)
    if len(payload) != ENCODED_SIZE:
        raise PacmanActorError("actor mapping encoder changed its fixed size")
    return payload
