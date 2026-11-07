"""Render PNG room previews from a caller's resource document."""

from __future__ import annotations
from collections.abc import Mapping
from retrolarium_kit.core.authoring import AuthoringError
from retrolarium_kit.games.doraemon.world3.document import hierarchy_of, resource
from retrolarium_kit.games.doraemon.world3.geometry import room_cells
from retrolarium_kit.platform.nes.preview import color_rgb, png_rgb


def room_rgb(document: Mapping, room: int) -> bytes:
    hierarchy = hierarchy_of(document)
    cells = room_cells(hierarchy, room)
    selectors = resource(document, "world3-room-palette-selectors")["selectors"]
    palette = resource(document, f"world3-palette-{selectors[room]:02d}")["values"]
    chr_tiles = resource(document, "chr-bank-2")["tiles"]
    if len(chr_tiles) < 512 or len(palette) != 32:
        raise AuthoringError("room previews require both CHR pattern tables and a 32-color room palette")
    pixels = bytearray(256 * 240 * 3)
    cache = {}
    for y in range(30):
        for x in range(32):
            block = hierarchy["big_blocks"][cells[y // 4][x // 4]]
            small = block[((y % 4) // 2) * 2 + (x % 4) // 2]
            tile = hierarchy["small_blocks"][small][(y % 2) * 2 + x % 2]
            colors = hierarchy["attributes"][small]["palette"]
            key = (tile, colors)
            if key not in cache:
                pens = [color_rgb(palette[0] if pen == 0 else palette[colors * 4 + pen]) for pen in range(4)]
                cache[key] = [b"".join(pens[pen] for pen in row) for row in chr_tiles[256 + tile]]
            for dy, line in enumerate(cache[key]):
                offset = ((y * 8 + dy) * 256 + x * 8) * 3
                pixels[offset : offset + 24] = line
    return bytes(pixels)


def room_png(document: Mapping, room: int) -> bytes:
    return png_rgb(256, 240, room_rgb(document, room))
