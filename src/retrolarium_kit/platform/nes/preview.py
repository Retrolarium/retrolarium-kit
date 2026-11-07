"""NES PPU display colors and dependency-free RGB PNG encoding.

The 2C02 display palette matches the Studio's nes/masterColor renderer.
It describes the console's colors, independently of any cartridge assets.
"""

from __future__ import annotations

import struct
import zlib


NES_COLORS = (
    0x626262,
    0x001FB2,
    0x2404C8,
    0x5200B2,
    0x730076,
    0x800024,
    0x730B00,
    0x522800,
    0x244400,
    0x005700,
    0x005C00,
    0x005324,
    0x003C76,
    0x000000,
    0x000000,
    0x000000,
    0xABABAB,
    0x0D57FF,
    0x4B30FF,
    0x8A13FF,
    0xBC08D6,
    0xD21269,
    0xC72E00,
    0x9D5400,
    0x607B00,
    0x209800,
    0x00A300,
    0x009942,
    0x007DB4,
    0x000000,
    0x000000,
    0x000000,
    0xFFFFFF,
    0x53AEFF,
    0x9085FF,
    0xD365FF,
    0xFF57FF,
    0xFF5DCF,
    0xFF7757,
    0xFA9E00,
    0xBDC700,
    0x7AE700,
    0x43F611,
    0x26EF7E,
    0x2CD5F6,
    0x4E4E4E,
    0x000000,
    0x000000,
    0xFFFFFF,
    0xB6E1FF,
    0xCED1FF,
    0xE9C3FF,
    0xFFBCFF,
    0xFFBDF4,
    0xFFC6C3,
    0xFFD59A,
    0xE9E681,
    0xCEF481,
    0xB6FB9A,
    0xA9FAC3,
    0xA9F0F4,
    0xB8B8B8,
    0x000000,
    0x000000,
)


def color_rgb(index: int) -> bytes:
    return NES_COLORS[index & 63].to_bytes(3, "big")


def png_rgb(width: int, height: int, pixels: bytes | bytearray) -> bytes:
    if width < 1 or height < 1 or len(pixels) != width * height * 3:
        raise ValueError("RGB pixel count must match positive image dimensions")

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))

    stride = width * 3
    filtered = b"".join(b"\x00" + pixels[y * stride : (y + 1) * stride] for y in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(filtered)) + chunk(b"IEND", b"")
