"""Fixed-size World 2 stage bytecode and its three chapter entry points.

The sequence is a physical ROM owner. Route geometry is derived from these
bytes; it must never be persisted as a second, conflicting owner.
"""

from __future__ import annotations

from typing import Any

from retrolarium_kit.core.authoring import AuthoringError


CODEC = "doraemon.world2-stage.v1"
SEQUENCE_SIZE = 229
START_SIZE = 3


class DoraemonWorld2StageError(AuthoringError):
    """A World 2 stage sequence does not fit its fixed ROM contract."""


def token_command(token: int) -> str:
    if token < 0xF0:
        return "select_screen"
    if token < 0xF7:
        return "set_pending_direction"
    if token == 0xF7:
        return "restore_saved_offset"
    if token == 0xF8:
        return "stop_scroll"
    return "set_background_palette"


def decode_stage(sequence: bytes, starts: bytes) -> dict[str, object]:
    if len(sequence) != SEQUENCE_SIZE or len(starts) != START_SIZE:
        raise DoraemonWorld2StageError("World 2 stage needs 229 tokens and 3 starts")
    if any(start >= SEQUENCE_SIZE for start in starts):
        raise DoraemonWorld2StageError("World 2 chapter start exceeds the sequence")
    return {"start_offsets": list(starts), "tokens": list(sequence)}


def encode_stage(value: Any) -> tuple[bytes, bytes]:
    if not isinstance(value, dict) or set(value) != {"start_offsets", "tokens"}:
        raise DoraemonWorld2StageError("World 2 stage fields differ")
    starts = value["start_offsets"]
    tokens = value["tokens"]
    if not isinstance(starts, list) or len(starts) != START_SIZE:
        raise DoraemonWorld2StageError("World 2 stage needs three chapter starts")
    if not isinstance(tokens, list) or len(tokens) != SEQUENCE_SIZE:
        raise DoraemonWorld2StageError("World 2 stage needs 229 tokens")
    if any(not isinstance(item, int) or isinstance(item, bool)
           or not 0 <= item < SEQUENCE_SIZE for item in starts):
        raise DoraemonWorld2StageError("World 2 chapter starts are invalid")
    if any(not isinstance(item, int) or isinstance(item, bool)
           or not 0 <= item <= 0xFF for item in tokens):
        raise DoraemonWorld2StageError("World 2 stage tokens are invalid")
    return bytes(tokens), bytes(starts)
