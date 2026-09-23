"""World 3 room-to-palette selector bytes in Doraemon's PRG bank 2."""

from __future__ import annotations

from .authoring import AuthoringError


ROOM_SELECTOR_CODEC = "doraemon.room-palette-selectors.v1"
ROOM_COUNT = 64
PALETTE_COUNT = 11


class DoraemonPaletteError(AuthoringError):
    """Room palette selector data violates the ROM's fixed table contract."""


def decode_room_selectors(payload: bytes) -> list[int]:
    if len(payload) != ROOM_COUNT:
        raise DoraemonPaletteError("World 3 needs exactly 64 room palette selectors")
    if any(value >= PALETTE_COUNT for value in payload):
        raise DoraemonPaletteError("World 3 room palette selector is outside 0..10")
    return list(payload)


def encode_room_selectors(values: object) -> bytes:
    if not isinstance(values, list) or len(values) != ROOM_COUNT:
        raise DoraemonPaletteError("World 3 needs exactly 64 room palette selectors")
    if any(not isinstance(value, int) or isinstance(value, bool)
           or not 0 <= value < PALETTE_COUNT for value in values):
        raise DoraemonPaletteError("World 3 room palette selector is outside 0..10")
    return bytes(values)
