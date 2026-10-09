"""Fixed World 2 metatile tables and collision bits in Doraemon PRG bank 1."""

from __future__ import annotations

from collections.abc import Mapping

from retrolarium_kit.core.authoring import AuthoringError


CODEC = "doraemon.world2-metatiles.v1"
METATILE_COUNT = 208
VISUAL_SIZE = METATILE_COUNT * 5
COLLISION_SIZE = METATILE_COUNT // 8


class DoraemonWorld2MetatileError(AuthoringError):
    """A World 2 metatile cannot fit its physical table."""


def decode_metatiles(visual: bytes, collision: bytes) -> dict[str, object]:
    if len(visual) != VISUAL_SIZE or len(collision) != COLLISION_SIZE:
        raise DoraemonWorld2MetatileError("World 2 metatile table sizes differ")
    palettes = list(visual[:METATILE_COUNT])
    if any(value > 3 for value in palettes):
        raise DoraemonWorld2MetatileError("World 2 palette selector is outside 0..3")
    tiles = [list(visual[METATILE_COUNT + index * 4:
                         METATILE_COUNT + (index + 1) * 4])
             for index in range(METATILE_COUNT)]
    solid = [bool(collision[index // 8] & (0x80 >> (index % 8)))
             for index in range(METATILE_COUNT)]
    return {"palettes": palettes, "tiles": tiles, "solid": solid}


def encode_metatiles(value: Mapping[str, object]) -> tuple[bytes, bytes]:
    if not isinstance(value, Mapping) or set(value) != {"palettes", "tiles", "solid"}:
        raise DoraemonWorld2MetatileError("World 2 metatile fields differ")
    palettes = value["palettes"]
    tiles = value["tiles"]
    solid = value["solid"]
    if not isinstance(palettes, list) or len(palettes) != METATILE_COUNT:
        raise DoraemonWorld2MetatileError("World 2 needs 208 palette selectors")
    if any(not isinstance(item, int) or isinstance(item, bool)
           or not 0 <= item <= 3 for item in palettes):
        raise DoraemonWorld2MetatileError("World 2 palette selector is outside 0..3")
    if not isinstance(tiles, list) or len(tiles) != METATILE_COUNT:
        raise DoraemonWorld2MetatileError("World 2 needs 208 tile quads")
    tile_bytes = bytearray()
    for index, quad in enumerate(tiles):
        if not isinstance(quad, list) or len(quad) != 4 or any(
            not isinstance(item, int) or isinstance(item, bool)
            or not 0 <= item <= 255 for item in quad
        ):
            raise DoraemonWorld2MetatileError(
                f"World 2 tile quad {index} needs four CHR tile IDs"
            )
        tile_bytes.extend(quad)
    if not isinstance(solid, list) or len(solid) != METATILE_COUNT \
            or any(not isinstance(item, bool) for item in solid):
        raise DoraemonWorld2MetatileError("World 2 needs 208 collision flags")
    collision = bytearray(COLLISION_SIZE)
    for index, enabled in enumerate(solid):
        if enabled:
            collision[index // 8] |= 0x80 >> (index % 8)
    return bytes(palettes) + bytes(tile_bytes), bytes(collision)
