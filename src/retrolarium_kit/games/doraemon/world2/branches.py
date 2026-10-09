"""Four adjacent World 2 branch-control tables, one physical ROM owner."""

from __future__ import annotations

from typing import Any

from retrolarium_kit.core.authoring import AuthoringError


CODEC = "doraemon.world2-branches.v1"
BRANCH_COUNT = 17
TABLE_SIZE = BRANCH_COUNT
PAYLOAD_SIZE = BRANCH_COUNT * 4
CONDITION_NAMES = (
    "always", "player_y_below_0x50", "player_x_at_least_0xA0",
    "player_y_at_least_0xA0",
)


class DoraemonWorld2BranchError(AuthoringError):
    """A World 2 branch table violates its fixed ROM contract."""


def decode_branches(payload: bytes) -> dict[str, object]:
    if len(payload) != PAYLOAD_SIZE:
        raise DoraemonWorld2BranchError("World 2 branch tables need 68 bytes")
    result = {"branches": [
        {
            "index": index,
            "trigger_screen_id": payload[index],
            "destination_offset": payload[TABLE_SIZE + index],
            "condition_code": payload[TABLE_SIZE * 2 + index],
            "return_offset_override": (
                None if payload[TABLE_SIZE * 3 + index] == 0
                else payload[TABLE_SIZE * 3 + index]
            ),
        }
        for index in range(BRANCH_COUNT)
    ]}
    encode_branches(result)
    return result


def encode_branches(value: Any) -> bytes:
    if not isinstance(value, dict) or set(value) != {"branches"}:
        raise DoraemonWorld2BranchError("World 2 branch fields differ")
    branches = value["branches"]
    if not isinstance(branches, list) or len(branches) != BRANCH_COUNT:
        raise DoraemonWorld2BranchError("World 2 needs 17 branch records")
    columns: list[list[int]] = [[], [], [], []]
    for index, entry in enumerate(branches):
        if not isinstance(entry, dict) or set(entry) != {
            "index", "trigger_screen_id", "destination_offset",
            "condition_code", "return_offset_override"
        } or entry["index"] != index:
            raise DoraemonWorld2BranchError(
                f"World 2 branch {index} fields or index differ")
        trigger = entry["trigger_screen_id"]
        destination = entry["destination_offset"]
        condition = entry["condition_code"]
        override = entry["return_offset_override"]
        if (not isinstance(trigger, int) or isinstance(trigger, bool)
                or not 0 <= trigger <= 118):
            raise DoraemonWorld2BranchError(
                f"World 2 branch {index} trigger is invalid")
        if (not isinstance(destination, int) or isinstance(destination, bool)
                or not 0 <= destination <= 228):
            raise DoraemonWorld2BranchError(
                f"World 2 branch {index} destination is invalid")
        if (not isinstance(condition, int) or isinstance(condition, bool)
                or not 0 <= condition < len(CONDITION_NAMES)):
            raise DoraemonWorld2BranchError(
                f"World 2 branch {index} condition is invalid")
        if override is not None and (not isinstance(override, int)
                                     or isinstance(override, bool)
                                     or not 1 <= override <= 228):
            raise DoraemonWorld2BranchError(
                f"World 2 branch {index} return override is invalid")
        for column, item in zip(columns, (
            trigger, destination, condition, override or 0
        )):
            column.append(item)
    return b"".join(bytes(column) for column in columns)
