"""Doraemon World 3 entity behavior: sixteen packed bytecode streams.

World 3's creatures move by small programs ($D9CC-$DDF1, bank 2; the
pointer table at $D9AC). An opcode's high nibble is its command and its low
nibble a parameter: moves, jumps, waits, loops, conditional branches,
position and metasprite changes. A stream decodes by following its control
flow from offset 0, and every byte of it is reached.

The document lists each stream's instructions with their opcode and
operands. An edit keeps each instruction's place, command and size: the
parameter nibble and the operands change, and branch and jump targets must
land on instructions of the same stream. Streams keep their addresses, so
the pointer table never changes.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from retrolarium_kit.core.authoring import AuthoringError


CODEC = "doraemon.world3-behavior.v1"
COMMANDS = (
    "move_x_forward",
    "move_x_reverse",
    "move_y_forward",
    "move_y_reverse",
    "jump",
    "wait",
    "set_rate",
    "set_direction",
    "loop",
    "move_relative",
    "set_position",
    "set_metasprite_variant",
    "conditional_branch",
    "set_follow_anchor",
    "toggle_metasprite_variant",
    "stop",
)


class DoraemonBehaviorError(AuthoringError):
    """A behavior stream does not keep its shape."""


def instruction_shape(data: bytes, offset: int) -> tuple[int, tuple[int, ...]]:
    """An instruction's size and the offsets play goes on to."""
    opcode = data[offset]
    command, parameter = opcode >> 4, opcode & 0x0F
    if command <= 3 or command in (6, 7, 11, 14):
        return 1, (offset + 1,)
    if command == 4:
        return 2, (data[offset + 1],)
    if command in (5, 13):
        return 2, (offset + 2,)
    if command == 8:
        return (2, (offset + 2,)) if parameter == 0 else (1, (offset + 1,))
    if command in (9, 10):
        return 3, (offset + 3,)
    if command == 12:
        if parameter in (1, 2):
            return 2, (offset + 2, data[offset + 1])
        return 3, (offset + 3, data[offset + 2])
    return 1, ()


def _walk(data: bytes) -> dict[int, int]:
    """Instruction offsets and sizes reached from offset 0."""
    sizes: dict[int, int] = {}
    roles: dict[int, str] = {}
    work = [0]
    while work:
        at = work.pop()
        if at in sizes:
            continue
        if not 0 <= at < len(data):
            raise DoraemonBehaviorError(f"control flow leaves the stream at ${at:02X}")
        if roles.get(at) == "operand":
            raise DoraemonBehaviorError(f"control flow lands in an operand at ${at:02X}")
        if at + instruction_shape(data + bytes(2), at)[0] > len(data):
            raise DoraemonBehaviorError(f"the instruction at ${at:02X} runs past the stream")
        size, successors = instruction_shape(data, at)
        for byte in range(at, at + size):
            if roles.get(byte, "opcode" if byte == at else "operand") != ("opcode" if byte == at else "operand"):
                raise DoraemonBehaviorError(f"instructions overlap at ${byte:02X}")
            roles[byte] = "opcode" if byte == at else "operand"
        sizes[at] = size
        work.extend(successor for successor in successors if successor not in sizes)
    return sizes


def decode_behavior(payload: bytes, stream_sizes: list[int]) -> list[dict[str, object]]:
    if sum(stream_sizes) != len(payload):
        raise DoraemonBehaviorError("the behavior streams do not fill their region")
    streams = []
    start = 0
    for index, size in enumerate(stream_sizes):
        data = payload[start : start + size]
        try:
            offsets = _walk(data)
        except DoraemonBehaviorError as exc:
            raise DoraemonBehaviorError(f"behavior stream {index}: {exc}") from exc
        if sum(offsets.values()) != size:
            raise DoraemonBehaviorError(f"behavior stream {index} has bytes play never reaches")
        streams.append(
            {
                "instructions": [
                    {
                        "offset": at,
                        "command": COMMANDS[data[at] >> 4],
                        "opcode": data[at],
                        "operands": list(data[at + 1 : at + offsets[at]]),
                    }
                    for at in sorted(offsets)
                ]
            }
        )
        start += size
    return streams


def encode_behavior(value: Any, stream_sizes: list[int]) -> bytes:
    """The region's bytes. Each stream must decode back to the instructions
    the document declares, so no instruction moves, grows or hides."""
    if not isinstance(value, list) or len(value) != len(stream_sizes):
        raise DoraemonBehaviorError(f"World 3 behavior holds {len(stream_sizes)} streams")
    output = bytearray()
    for index, (stream, size) in enumerate(zip(value, stream_sizes)):
        where = f"behavior stream {index}"
        if (
            not isinstance(stream, Mapping)
            or set(stream) != {"instructions"}
            or not isinstance(stream["instructions"], list)
        ):
            raise DoraemonBehaviorError(f"{where} needs its instruction list")
        data = bytearray(size)
        covered: set[int] = set()
        declared: dict[int, int] = {}
        for entry in stream["instructions"]:
            if not isinstance(entry, Mapping) or set(entry) != {"offset", "command", "opcode", "operands"}:
                raise DoraemonBehaviorError(f"{where}: an instruction needs offset, command, opcode and operands")
            at, opcode, operands = entry["offset"], entry["opcode"], entry["operands"]
            if (
                type(at) is not int
                or type(opcode) is not int
                or not 0 <= opcode <= 255
                or not isinstance(operands, list)
                or any(type(v) is not int or not 0 <= v <= 255 for v in operands)
            ):
                raise DoraemonBehaviorError(f"{where}: instruction values must be bytes")
            if entry["command"] != COMMANDS[opcode >> 4]:
                raise DoraemonBehaviorError(f"{where} ${at:02X}: the opcode is not a {entry['command']}")
            raw = bytes((opcode, *operands))
            if instruction_shape(raw + bytes(2), 0)[0] != len(raw):
                raise DoraemonBehaviorError(f"{where} ${at:02X}: {entry['command']} needs another operand count")
            if not 0 <= at or at + len(raw) > size or covered & set(range(at, at + len(raw))):
                raise DoraemonBehaviorError(f"{where} ${at:02X}: the instruction leaves its place")
            covered.update(range(at, at + len(raw)))
            data[at : at + len(raw)] = raw
            declared[at] = len(raw)
        if len(covered) != size:
            raise DoraemonBehaviorError(f"{where} leaves bytes without an instruction")
        try:
            reached = _walk(bytes(data))
        except DoraemonBehaviorError as exc:
            raise DoraemonBehaviorError(f"{where}: {exc}") from exc
        if reached != declared:
            raise DoraemonBehaviorError(
                f"{where}: a jump or branch must land on one of its instructions, and play must still reach them all"
            )
        output += data
    return bytes(output)
