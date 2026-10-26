"""Tiles a stage loader works out instead of loading: stages 21 and 22 draw
their moon's surface in perspective from a packed strip
(`Gfx_PrepareStage3Phase2ResampledTiles`).

The loader's routine builds a table of scale steps (`$600000 / d1`, d1 from
$2000 up by $1A0 a line, shifted up 8 bits), points the resampler at the
packed source, and uploads the result to VRAM. The resampler takes each
group of four longs (a stride of $20 apart), mirrors it into 64 pixels, and
draws one pixel line 24 tiles wide from it at that line's step; eight lines
make a strip of 24 tiles, the strips follow one another. Every number comes
from the routine's own lines; the scene lists the upload with its bytes
(`data`), as the studio cannot work them out itself.
"""

from __future__ import annotations

import re
from typing import Any

from retrolarium_kit.platform.megadrive.asl import SourceTree, parse_number


PACKED = re.compile(r"^\s+move\.l\s+#(?P<label>[A-Za-z_][A-Za-z0-9_]*),\(Stage3PackedTilePtr\)\.w\s*$")
SETTING = re.compile(
    r"^\s+(?:move\.[wl]|moveq)\s+#(?P<value>[^,\s]+),(?:\((?P<field>Stage3Resample(?:Groups|Passes)M1)\)\.w|(?P<register>d[0-7]))\s*$"
)
CALL = re.compile(r"^\s+(?:bsr|jsr)(?:\.[wsl])?\s+\(?(?P<label>[A-Za-z_][A-Za-z0-9_]*)\)?(?:\.l)?\s*$")
TILE_BYTES = 32
STRIP_TILES = 24


def resample(packed: bytes, groups: int, passes: int, numerator: int, first: int, step: int) -> bytes:
    """The tiles the resampler draws (see the module text)."""
    steps, divisor = [], first
    for _ in range(groups):
        quotient = (numerator // divisor) & 0xFFFF
        quotient -= 0x10000 if quotient & 0x8000 else 0
        steps.append((quotient << 8) & 0xFFFFFFFF)
        divisor = (divisor + step) & 0xFFFF
    # Each group: four longs $20 apart, then the same bytes mirrored.
    buffer, at, span = bytearray(), 0, passes << 5
    for _ in range(groups):
        gathered = bytearray()
        for _ in range(passes):
            gathered += packed[at : at + 4]
            at += 0x20
        buffer += gathered + bytes(((value >> 4) | (value << 4)) & 0xFF for value in reversed(gathered))
        at = at - span + 4
        if at & 0x1C == 0:
            at += span - 0x20
    size = passes * 8
    wrap = size << 16
    out = bytearray(groups * STRIP_TILES * 4)
    for line in range(groups):
        # The line's position: its strip, then its row in the tiles.
        write = line // 8 * STRIP_TILES * TILE_BYTES + line % 8 * 4
        base, position = line * size, 0
        for _ in range(STRIP_TILES):
            for offset in range(4):
                pixels = 0
                for shift in (4, 0):
                    value = buffer[base + (position >> 16)]
                    odd = position & 0x8000
                    pixels |= ((value << 4 if odd else value) if shift else (value if odd else value >> 4)) & (
                        0xF0 if shift else 0x0F
                    )
                    position = (position + steps[line]) & 0xFFFFFFFF
                    if position >= wrap:
                        position -= wrap
                out[write + offset] = pixels
            write += TILE_BYTES
    return bytes(out)


def _routine(tree: SourceTree, label: str) -> list[str]:
    from retrolarium_kit.games.alien_soldier.stages import routine_lines

    return routine_lines(tree, label) if label in tree.blocks else []


def resampled_loads(tree: SourceTree, loader: str) -> list[dict[str, Any]]:
    """The uploads of tiles a loader's routines work out, with their bytes."""
    found = []
    for line in _routine(tree, loader):
        call = CALL.match(line)
        lines = _routine(tree, call["label"]) if call else []
        packed = next((match["label"] for item in lines if (match := PACKED.match(item))), None)
        if packed is None or packed not in tree.blocks:
            continue
        fields, registers = {}, {}
        for item in lines:
            if (match := SETTING.match(item)) and parse_number(match["value"]) is not None:
                if match["field"]:
                    fields[match["field"]] = parse_number(match["value"])
                else:
                    registers[match["register"]] = parse_number(match["value"])
        if not {"Stage3ResampleGroupsM1", "Stage3ResamplePassesM1"} <= set(fields) or not {
            "d0",
            "d1",
            "d3",
            "d5",
        } <= set(registers):
            continue
        tiles = resample(
            tree.data(packed),
            fields["Stage3ResampleGroupsM1"] + 1,
            fields["Stage3ResamplePassesM1"] + 1,
            registers["d0"],
            registers["d1"],
            registers["d3"],
        )
        found.append({"command": 0, "source": packed, "vram": registers["d5"], "data": tiles.hex().upper()})
    return found
