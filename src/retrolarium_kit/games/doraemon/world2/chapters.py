"""Three World 2 chapter background selectors and sprite palette offsets."""

from __future__ import annotations

from typing import Any

from .authoring import AuthoringError


CODEC = "doraemon.world2-chapters.v1"
PAYLOAD_SIZE = 6
CHAPTER_COUNT = 3


class DoraemonWorld2ChapterError(AuthoringError):
    """A World 2 chapter palette selector is outside its ROM contract."""


def decode_chapters(payload: bytes) -> dict[str, object]:
    if len(payload) != PAYLOAD_SIZE:
        raise DoraemonWorld2ChapterError("World 2 needs six chapter palette bytes")
    result = {
        "background_palette_ids": list(payload[:CHAPTER_COUNT]),
        "sprite_offsets": list(payload[CHAPTER_COUNT:]),
    }
    encode_chapters(result)
    return result


def encode_chapters(value: Any) -> bytes:
    if not isinstance(value, dict) or set(value) != {
        "background_palette_ids", "sprite_offsets"
    }:
        raise DoraemonWorld2ChapterError("World 2 chapter fields differ")
    background = value["background_palette_ids"]
    sprite = value["sprite_offsets"]
    if (not isinstance(background, list) or len(background) != CHAPTER_COUNT
            or not isinstance(sprite, list) or len(sprite) != CHAPTER_COUNT):
        raise DoraemonWorld2ChapterError("World 2 needs three palette selectors")
    if any(not isinstance(item, int) or isinstance(item, bool)
           or not 1 <= item <= 9 for item in background):
        raise DoraemonWorld2ChapterError(
            "World 2 background palette ID is outside 1..9")
    if any(not isinstance(item, int) or isinstance(item, bool)
           or item not in (0, 16, 32, 48) for item in sprite):
        raise DoraemonWorld2ChapterError(
            "World 2 sprite palette offset is not a catalog-aligned record")
    return bytes(background + sprite)
