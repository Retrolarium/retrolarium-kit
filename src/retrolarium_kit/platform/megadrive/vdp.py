"""What the Mega Drive's VDP draws with: 4bpp tiles, CRAM colours and
name-table words.

A tile is eight by eight pixels at four bits each, 32 bytes, the high nibble
on the left; each pixel is an index into the sixteen-colour CRAM line its
name-table word or sprite attribute selects. Documents keep a tile as its 32
bytes in hexadecimal (64 digits, one per pixel, left to right), which is
also its pixels in order.

A CRAM word is `0000 BBB0 GGG0 RRR0`; the bits a colour does not use are kept
as they were, since games store words such as $FFFF whose ignored bits the
hardware drops. A name-table word is `priority, palette line (2), vertical
flip, horizontal flip, tile index (11)`.
"""

from __future__ import annotations

from dataclasses import dataclass


TILE_BYTES = 32
TILE_PIXELS = 64

# The eight levels of a CRAM channel on screen (measured on hardware, as
# emulators such as Genesis Plus GX use them).
CRAM_LEVELS = (0, 52, 87, 116, 144, 172, 206, 255)


def tile_hex(data: bytes, offset: int = 0) -> str:
    """One tile of `data` as 64 hexadecimal digits."""
    if offset + TILE_BYTES > len(data):
        raise ValueError(f"a tile at {offset} runs past {len(data)} bytes")
    return data[offset : offset + TILE_BYTES].hex().upper()


def tiles_hex(data: bytes) -> list[str]:
    if len(data) % TILE_BYTES:
        raise ValueError(f"{len(data)} bytes are not whole tiles")
    return [tile_hex(data, offset) for offset in range(0, len(data), TILE_BYTES)]


def tile_bytes(text: object) -> bytes:
    """A document's tile back to its 32 bytes."""
    if not isinstance(text, str) or len(text) != TILE_PIXELS:
        raise ValueError("a tile is 64 hexadecimal digits")
    try:
        return bytes.fromhex(text)
    except ValueError:
        raise ValueError("a tile is 64 hexadecimal digits") from None


def tile_pixels(data: bytes, offset: int = 0) -> list[int]:
    """One tile's 64 palette indices, left to right, top to bottom."""
    return [nibble for byte in data[offset : offset + TILE_BYTES] for nibble in (byte >> 4, byte & 15)]


def cram_rgb(word: int) -> tuple[int, int, int]:
    """A CRAM word as eight-bit red, green and blue."""
    return CRAM_LEVELS[(word >> 1) & 7], CRAM_LEVELS[(word >> 5) & 7], CRAM_LEVELS[(word >> 9) & 7]


@dataclass(frozen=True)
class NameWord:
    """A plane or sprite name word."""

    tile: int
    palette: int
    hflip: bool
    vflip: bool
    priority: bool

    @classmethod
    def decode(cls, word: int) -> "NameWord":
        return cls(word & 0x7FF, (word >> 13) & 3, bool(word & 0x800), bool(word & 0x1000), bool(word & 0x8000))

    def encode(self) -> int:
        return (
            (self.priority << 15) | (self.palette << 13) | (self.vflip << 12) | (self.hflip << 11) | (self.tile & 0x7FF)
        )
