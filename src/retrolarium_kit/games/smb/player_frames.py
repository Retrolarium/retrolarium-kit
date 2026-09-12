"""Typed Super Mario Bros. player-frame table codec."""

from __future__ import annotations

from typing import Mapping

from .authoring import AuthoringError


CODEC = "smb.player-frames.v1"
FRAME_OFFSET_COUNT = 16
TILES_PER_FRAME = 8
FRAME_NAMES = (
    "big_walk_1",
    "big_walk_2",
    "big_walk_3",
    "big_skid",
    "big_jump",
    "big_swim_1",
    "big_swim_2",
    "big_swim_3",
    "big_climb_1",
    "big_climb_2",
    "big_crouch",
    "big_throw",
    "small_walk_1",
    "small_walk_2",
    "small_walk_3",
    "small_skid",
    "small_jump",
    "small_swim_1",
    "small_swim_2",
    "small_swim_3",
    "small_climb_1",
    "small_climb_2",
    "small_killed",
    "small_stand",
    "grow_transition",
    "big_stand",
)
FRAME_COUNT = len(FRAME_NAMES)
ENCODED_SIZE = FRAME_OFFSET_COUNT + FRAME_COUNT * TILES_PER_FRAME + 2


class SmbPlayerFrameError(AuthoringError):
    """An SMB player-frame table violates its fixed engine layout."""


def _byte(value: object, name: str) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 0 <= value <= 0xFF
    ):
        raise SmbPlayerFrameError(f"{name} must be a byte")
    return value


def _metadata(parameters: Mapping[str, object]) -> tuple[int, int]:
    tile_base = _byte(parameters.get("tile_base", 0), "tile_base")
    blank_tile = _byte(parameters.get("blank_tile", 0xFC), "blank_tile")
    return tile_base, blank_tile


def _byte_array(value: object, count: int, name: str) -> list[int]:
    if not isinstance(value, list) or len(value) != count:
        raise SmbPlayerFrameError(f"{name} must contain {count} bytes")
    return [_byte(item, f"{name}[{index}]") for index, item in enumerate(value)]


def decode_player_frames(
    payload: bytes, parameters: Mapping[str, object]
) -> dict[str, object]:
    """Decode the fixed SMB action offsets, 2x4 frames, and swim replacements."""
    if len(payload) != ENCODED_SIZE:
        raise SmbPlayerFrameError(
            f"player-frame table has {len(payload)} bytes, expected {ENCODED_SIZE}"
        )
    tile_base, blank_tile = _metadata(parameters)
    frame_offsets = list(payload[:FRAME_OFFSET_COUNT])
    frame_bytes = payload[FRAME_OFFSET_COUNT:-2]
    frames = [
        list(frame_bytes[start : start + TILES_PER_FRAME])
        for start in range(0, len(frame_bytes), TILES_PER_FRAME)
    ]
    result = {
        "frame_offsets": frame_offsets,
        "frame_names": list(FRAME_NAMES),
        "tile_base": tile_base,
        "blank_tile": blank_tile,
        "frames": frames,
        "swim_kick_tiles": list(payload[-2:]),
    }
    encode_player_frames(result, parameters)
    return result


def encode_player_frames(
    value: object, parameters: Mapping[str, object]
) -> bytes:
    """Validate and encode one fixed-size SMB player-frame table."""
    if not isinstance(value, Mapping):
        raise SmbPlayerFrameError("player-frame resource must be an object")
    expected = {
        "frame_offsets",
        "frame_names",
        "tile_base",
        "blank_tile",
        "frames",
        "swim_kick_tiles",
    }
    if set(value) != expected:
        raise SmbPlayerFrameError(
            "player-frame resource fields differ from codec v1"
        )
    tile_base, blank_tile = _metadata(parameters)
    if value.get("tile_base") != tile_base or value.get("blank_tile") != blank_tile:
        raise SmbPlayerFrameError("player-frame metadata differs from the profile")
    if value.get("frame_names") != list(FRAME_NAMES):
        raise SmbPlayerFrameError("player-frame names differ from codec v1")

    offsets = _byte_array(
        value.get("frame_offsets"), FRAME_OFFSET_COUNT, "frame_offsets"
    )
    maximum_offset = (FRAME_COUNT - 1) * TILES_PER_FRAME
    for index, offset in enumerate(offsets):
        if offset % TILES_PER_FRAME or offset > maximum_offset:
            raise SmbPlayerFrameError(
                f"frame_offsets[{index}] must select one of {FRAME_COUNT} frames"
            )

    raw_frames = value.get("frames")
    if not isinstance(raw_frames, list) or len(raw_frames) != FRAME_COUNT:
        raise SmbPlayerFrameError(f"frames must contain {FRAME_COUNT} records")
    frames = [
        _byte_array(frame, TILES_PER_FRAME, f"frames[{index}]")
        for index, frame in enumerate(raw_frames)
    ]
    swim = _byte_array(value.get("swim_kick_tiles"), 2, "swim_kick_tiles")
    payload = bytes(offsets + [tile for frame in frames for tile in frame] + swim)
    if len(payload) != ENCODED_SIZE:
        raise SmbPlayerFrameError("player-frame encoder changed its fixed size")
    return payload
