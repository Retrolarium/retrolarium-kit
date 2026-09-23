"""World 2's overlapping screen selectors and fixed-capacity token pool.

Cells are logical metatile IDs (0..207), spawn states (208..238), or the
row-end padding marker (239). The original packed payload is retained for
zero-edit round trips; changed logical screens are packed as a role-compatible
shortest superstring before the fixed ROM-capacity check.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import heapq
from typing import Any

from .authoring import AuthoringError


CODEC = "doraemon.world2-screens.v1"
SELECTOR_COUNT = 119
ROWS_PER_SCREEN = 16
MINIMUM_CELLS = 15
REGION_ADDRESS = 0xBFCC
REGION_SIZE = 16431
POINTER_SIZE = SELECTOR_COUNT * 2
PAYLOAD_SIZE = POINTER_SIZE + REGION_SIZE
SPAWN_BASE = 208
PADDING = 239


class DoraemonWorld2StreamError(AuthoringError):
    """A World 2 screen edit cannot be represented in the shared ROM arena."""


@dataclass(frozen=True)
class _Screen:
    data: bytes
    roles: bytes


@dataclass
class _Chunk:
    data: bytes
    roles: bytes
    members: dict[int, int]


def _decode_screen(region: bytes, start: int) -> list[list[int]]:
    if not 0 <= start < len(region):
        raise DoraemonWorld2StreamError("World 2 selector points outside stream region")
    rows: list[list[int]] = []
    cursor = start
    for row_index in range(ROWS_PER_SCREEN):
        cells: list[int] = []
        while len(cells) < MINIMUM_CELLS:
            if cursor >= len(region):
                raise DoraemonWorld2StreamError(
                    f"World 2 row {row_index} reads past the stream region"
                )
            token = region[cursor]
            cursor += 1
            if token < 0xD0:
                cells.append(token)
            elif token < 0xEF:
                cells.append(SPAWN_BASE + token - 0xD0)
            elif token == 0xEF:
                cells.extend([PADDING] * max(0, 16 - len(cells)))
            elif token == 0xF0:
                raise DoraemonWorld2StreamError("World 2 contains reserved $F0 token")
            else:
                if cursor >= len(region) or region[cursor] >= 0xD0:
                    raise DoraemonWorld2StreamError(
                        "World 2 RLE token has no valid metatile operand"
                    )
                cells.extend([region[cursor]] * ((token & 0x0F) + 1))
                cursor += 1
        rows.append(cells)
    return rows


def _decode_payload(payload: bytes) -> list[list[list[int]]]:
    if len(payload) != PAYLOAD_SIZE:
        raise DoraemonWorld2StreamError(
            f"World 2 screens need exactly {PAYLOAD_SIZE} bytes"
        )
    region = payload[POINTER_SIZE:]
    screens = []
    for selector in range(SELECTOR_COUNT):
        pointer = int.from_bytes(payload[selector * 2:selector * 2 + 2], "little")
        screens.append(_decode_screen(region, pointer - REGION_ADDRESS))
    return screens


def decode_screens(payload: bytes) -> dict[str, object]:
    return {"raw_hex": payload.hex().upper(), "screens": _decode_payload(payload)}


def _validate_screens(value: object) -> list[list[list[int]]]:
    if not isinstance(value, list) or len(value) != SELECTOR_COUNT:
        raise DoraemonWorld2StreamError("World 2 needs 119 logical selectors")
    result = []
    for selector, screen in enumerate(value):
        if not isinstance(screen, list) or len(screen) != ROWS_PER_SCREEN:
            raise DoraemonWorld2StreamError(
                f"World 2 selector {selector} needs 16 rows"
            )
        rows = []
        for row_index, row in enumerate(screen):
            if not isinstance(row, list) or not MINIMUM_CELLS <= len(row) <= 30:
                raise DoraemonWorld2StreamError(
                    f"World 2 selector {selector} row {row_index} has invalid width"
                )
            if any(not isinstance(cell, int) or isinstance(cell, bool)
                   or not 0 <= cell <= PADDING for cell in row):
                raise DoraemonWorld2StreamError(
                    f"World 2 selector {selector} row {row_index} has invalid cells"
                )
            rows.append(row)
        result.append(rows)
    return result


def _encode_row(row: list[int]) -> _Screen:
    width = len(row)

    @lru_cache(maxsize=None)
    def solve(index: int) -> tuple[bytes, bytes] | None:
        if index == width:
            return b"", b""
        if index >= MINIMUM_CELLS:
            return None
        value = row[index]
        candidates: list[tuple[bytes, bytes]] = []

        def append(data: bytes, roles: bytes, end: int) -> None:
            if end >= MINIMUM_CELLS and end != width:
                return
            suffix = solve(end)
            if suffix is not None:
                candidates.append((data + suffix[0], roles + suffix[1]))

        if value == PADDING:
            if width != 16 or any(cell != PADDING for cell in row[index:]):
                raise DoraemonWorld2StreamError(
                    "World 2 row-end padding must be one suffix to width 16"
                )
            append(b"\xEF", b"T", width)
        elif value >= SPAWN_BASE:
            append(bytes((0xD0 + value - SPAWN_BASE,)), b"T", index + 1)
        else:
            append(bytes((value,)), b"T", index + 1)
            run = 1
            while (run < 16 and index + run < width
                   and row[index + run] == value):
                run += 1
                if run >= 2:
                    append(bytes((0xF0 + run - 1, value)), b"TO", index + run)
        if not candidates:
            return None
        return min(candidates, key=lambda item: (len(item[0]), item[0]))

    encoded = solve(0)
    if encoded is None:
        raise DoraemonWorld2StreamError(
            "World 2 row cannot preserve its overrun width after this edit"
        )
    return _Screen(*encoded)


def _encode_screen(rows: list[list[int]]) -> _Screen:
    encoded = [_encode_row(row) for row in rows]
    return _Screen(b"".join(item.data for item in encoded),
                   b"".join(item.roles for item in encoded))


def _placement(left: _Chunk, right: _Chunk) -> tuple[int, int]:
    start = 0
    while True:
        position = left.data.find(right.data, start)
        if position < 0:
            break
        if left.roles[position:position + len(right.roles)] == right.roles:
            return len(right.data), position
        start = position + 1
    for overlap in range(min(len(left.data), len(right.data)), 0, -1):
        if (left.data[-overlap:] == right.data[:overlap]
                and left.roles[-overlap:] == right.roles[:overlap]):
            return overlap, len(left.data) - overlap
    return 0, len(left.data)


def _pack_screens(screens: list[_Screen]) -> tuple[bytes, list[int]]:
    active = {index: _Chunk(screen.data, screen.roles, {index: 0})
              for index, screen in enumerate(screens)}
    queue: list[tuple[int, int, int]] = []
    next_id = len(active)

    def queue_pair(left_id: int, right_id: int) -> None:
        overlap, _ = _placement(active[left_id], active[right_id])
        if overlap:
            heapq.heappush(queue, (-overlap, left_id, right_id))

    for left_id in range(len(screens)):
        for right_id in range(len(screens)):
            if left_id != right_id:
                queue_pair(left_id, right_id)
    while queue:
        negative_overlap, left_id, right_id = heapq.heappop(queue)
        if left_id not in active or right_id not in active:
            continue
        left, right = active[left_id], active[right_id]
        overlap, position = _placement(left, right)
        if overlap != -negative_overlap:
            continue
        data = left.data if overlap == len(right.data) else left.data + right.data[overlap:]
        roles = left.roles if overlap == len(right.data) else left.roles + right.roles[overlap:]
        members = dict(left.members)
        for selector, offset in right.members.items():
            members[selector] = position + offset
        del active[left_id]
        del active[right_id]
        merged_id = next_id
        next_id += 1
        active[merged_id] = _Chunk(data, roles, members)
        for other_id in tuple(active):
            if other_id != merged_id:
                queue_pair(merged_id, other_id)
                queue_pair(other_id, merged_id)
    pointers = [0] * len(screens)
    output = bytearray()
    for chunk in sorted(active.values(), key=lambda item: min(item.members)):
        base = len(output)
        output.extend(chunk.data)
        for selector, offset in chunk.members.items():
            pointers[selector] = base + offset
    return bytes(output), pointers


def encode_screens(value: Any) -> bytes:
    if not isinstance(value, dict) or set(value) != {"raw_hex", "screens"}:
        raise DoraemonWorld2StreamError("World 2 screen fields differ")
    raw_hex = value["raw_hex"]
    if not isinstance(raw_hex, str) or len(raw_hex) != PAYLOAD_SIZE * 2:
        raise DoraemonWorld2StreamError("World 2 original payload has wrong size")
    try:
        original = bytes.fromhex(raw_hex)
    except ValueError as exc:
        raise DoraemonWorld2StreamError("World 2 original payload is not hex") from exc
    original_screens = _decode_payload(original)
    screens = _validate_screens(value["screens"])
    first_alias: dict[int, int] = {}
    for selector in range(SELECTOR_COUNT):
        pointer = int.from_bytes(
            original[selector * 2:selector * 2 + 2], "little"
        )
        first = first_alias.setdefault(pointer, selector)
        if screens[selector] != screens[first]:
            raise DoraemonWorld2StreamError(
                f"World 2 selectors {first} and {selector} share a physical alias"
            )
    if screens == original_screens:
        return original
    encoded = [_encode_screen(screen) for screen in screens]
    packed, offsets = _pack_screens(encoded)
    if len(packed) > REGION_SIZE - 1:
        raise DoraemonWorld2StreamError(
            f"World 2 repack needs {len(packed) + 1} bytes; capacity is {REGION_SIZE}"
        )
    region = packed + bytes(REGION_SIZE - len(packed) - 1) + original[-1:]
    pointers = b"".join((REGION_ADDRESS + offset).to_bytes(2, "little")
                        for offset in offsets)
    payload = pointers + region
    if _decode_payload(payload) != screens:
        raise DoraemonWorld2StreamError("World 2 repack changed logical screens")
    return payload
