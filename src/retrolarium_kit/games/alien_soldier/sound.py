"""Alien Soldier's music and sound effects: the payloads the 68000 sound
driver plays, with their FM instruments.

The driver is Treasure's 68000 SMPS. A BGM payload starts with the
instrument table's offset, the FM/DAC and PSG channel counts, the tick
multiplier and the tempo, then four-byte FM/DAC records (sequence offset,
initial word) and six-byte PSG records (sequence offset, initial word,
pitch envelope, volume envelope). The first FM record is the DAC channel;
the others drive FM channels 1 to 6 in turn, PSG records the three tone
channels. An SFX payload has the instrument offset, the tick multiplier and a
channel count, then six-byte records (a status byte, the channel, the
sequence offset and the initial word). The initial word's high byte is the
channel's transposition.

A sequence is bytes: below $80 a duration, $80-$DF a note (a duration may
follow), $E0-$FE a command with its operands, $FF an extended command. The
jump, loop and call commands move by a signed word displacement. An FM
instrument is 25 bytes: algorithm and feedback, then four bytes each for the
registers $30, $50, $60, $70, $80 and $40 (operator order 1, 3, 2, 4).

The document keeps each payload's bytes and the instructions every channel
reaches. A payload may change only where the driver reads a value: notes,
durations, operands of the commands that do not move the cursor, and
instrument bytes. Its size, its headers and every instruction's offset, size,
opcode and target stay as they are, which is checked by decoding it again.
"""

from __future__ import annotations

from collections import deque
from typing import Any

from retrolarium_kit.platform.megadrive.asl import SourceTree


SCHEMA = "alien-soldier.sound"
DOCUMENT = "sound/sound.json"
INSTRUMENT_BYTES = 25
BGM_TABLE = "Sound_BGMPointerTable"
SFX_TABLES = (
    ("Sound_LowRangeSFXPointerTable", 0x40),
    ("Sound_OrdinarySFXPointerTableBase", 0xA0),
    ("Sound_SpecialSFXPointerTable", 0xF9),
)
BGM_FIRST = 0x81
BGM_FM_TYPES = (0x06, 0x00, 0x01, 0x02, 0x04, 0x05, 0x06)
BGM_PSG_TYPES = (0x80, 0xA0, 0xC0)
# Operand counts of $E0-$FE (-1: $E4 takes 1 when its operand is 0, else 5),
# and how each moves the cursor.
OPERANDS = {
    0xE0: 1, 0xE1: 1, 0xE2: 1, 0xE3: 0, 0xE4: -1, 0xE5: 2, 0xE6: 1, 0xE7: 0,
    0xE8: 1, 0xE9: 2, 0xEA: 1, 0xEB: 1, 0xEC: 1, 0xED: 2, 0xEE: 2, 0xEF: 1,
    0xF0: 4, 0xF1: 2, 0xF2: 0, 0xF3: 1, 0xF4: 1, 0xF5: 1, 0xF6: 2, 0xF7: 4,
    0xF8: 2, 0xF9: 0, 0xFA: 1, 0xFB: 1, 0xFC: 0, 0xFD: 0, 0xFE: 4,
}  # fmt: skip
EXTENDED_OPERANDS = {0x00: 4, 0x01: 1, 0x02: 1, 0x03: 2, 0x04: 0}
STOPS = {0xE3, 0xF2}
# Operands that are not values: the displacements, and the loop's index.
FLOW = {0xF6: (0, 1), 0xF7: (0, 2, 3), 0xF8: (0, 1)}


class SoundError(ValueError):
    """A sound document that does not fit the source."""


def requests(tree: SourceTree) -> dict[str, dict[str, Any]]:
    """Label -> kind and the request IDs that play it, in table order."""
    found: dict[str, dict[str, Any]] = {}
    for index, operand in enumerate(tree.block(BGM_TABLE).operands):
        found.setdefault(operand.text, {"kind": "bgm", "requests": []})["requests"].append(BGM_FIRST + index)
    for table, first in SFX_TABLES:
        for index, operand in enumerate(tree.block(table).operands):
            found.setdefault(operand.text, {"kind": "sfx", "requests": []})["requests"].append(first + index)
    return found


def _word(data: bytes, at: int) -> int:
    return int.from_bytes(data[at : at + 2], "big")


def _signed(data: bytes, at: int) -> int:
    return int.from_bytes(data[at : at + 2], "big", signed=True)


def instruction(data: bytes, at: int) -> dict[str, Any]:
    """One event or command, as the channel readers take it."""
    if not 0 <= at < len(data):
        raise SoundError(f"a sequence runs out of its payload at ${at:X}")
    opcode = data[at]
    if opcode < 0x80:
        return {"offset": at, "size": 1, "kind": "duration"}
    if opcode < 0xE0:
        size = 2 if at + 1 < len(data) and data[at + 1] < 0x80 else 1
        return {"offset": at, "size": size, "kind": "note"}
    if opcode == 0xFF:
        extension = data[at + 1] if at + 1 < len(data) else -1
        if extension not in EXTENDED_OPERANDS:
            raise SoundError(f"an unknown extended command at ${at:X}")
        return {"offset": at, "size": 2 + EXTENDED_OPERANDS[extension], "kind": "command"}
    count = OPERANDS[opcode]
    if count < 0:
        count = 1 if at + 1 < len(data) and data[at + 1] == 0 else 5
    item: dict[str, Any] = {"offset": at, "size": 1 + count, "kind": "command"}
    if opcode in (0xF6, 0xF8):
        item["target"] = at + 3 + _signed(data, at + 1) - 1
    elif opcode == 0xF7:
        item["target"] = at + 5 + _signed(data, at + 3) - 1
    if at + item["size"] > len(data):
        raise SoundError(f"a command at ${at:X} runs out of its payload")
    return item


def reachable(data: bytes, starts: list[int], header: int) -> list[dict[str, Any]]:
    """Every instruction some channel reaches, following jumps, loops and
    calls without timing them."""
    pending, found, owner = deque(starts), {}, {}
    while pending:
        at = pending.popleft()
        while at not in found:
            if at < header or at in owner:
                raise SoundError(f"a sequence enters its header or an instruction at ${at:X}")
            item = instruction(data, at)
            for position in range(at, at + item["size"]):
                if owner.setdefault(position, at) != at:
                    raise SoundError(f"instructions overlap at ${position:X}")
            found[at] = item
            opcode = data[at]
            if item["kind"] == "command" and (opcode in STOPS or opcode == 0xF9):
                break
            if "target" in item:
                pending.append(item["target"])
                if opcode == 0xF6:
                    break
            at += item["size"]
    return [found[offset] for offset in sorted(found)]


def header(kind: str, data: bytes) -> dict[str, Any]:
    if kind == "bgm":
        fm, psg = data[2], data[3]
        if fm > len(BGM_FM_TYPES) or psg > len(BGM_PSG_TYPES):
            raise SoundError("too many channels")
        size = 6 + 4 * fm + 6 * psg
        channels = [
            {"type": BGM_FM_TYPES[index], "sequence": _word(data, 6 + 4 * index), "initial": _word(data, 8 + 4 * index)}
            for index in range(fm)
        ]
        channels += [
            {
                "type": BGM_PSG_TYPES[index],
                "sequence": _word(data, 6 + 4 * fm + 6 * index),
                "initial": _word(data, 8 + 4 * fm + 6 * index),
                "pitch_envelope": data[10 + 4 * fm + 6 * index],
                "volume_envelope": data[11 + 4 * fm + 6 * index],
            }
            for index in range(psg)
        ]
        return {
            "size": size,
            "instruments": _word(data, 0),
            "tick_multiplier": data[4],
            "tempo": data[5],
            "channels": channels,
        }
    count = data[3]
    if not 1 <= count <= 6:
        raise SoundError("an SFX has 1..6 channels")
    channels = [
        {
            "type": data[5 + 6 * index],
            "status": data[4 + 6 * index],
            "sequence": _word(data, 6 + 6 * index),
            "initial": _word(data, 8 + 6 * index),
        }
        for index in range(count)
    ]
    return {"size": 4 + 6 * count, "instruments": _word(data, 0), "tick_multiplier": data[2], "channels": channels}


def decode(kind: str, data: bytes) -> dict[str, Any]:
    head = header(kind, data)
    if head["size"] > len(data) or head["instruments"] > len(data):
        raise SoundError("the header does not fit its payload")
    instructions = reachable(data, [channel["sequence"] for channel in head["channels"]], head["size"])
    start = head["instruments"]
    count = (len(data) - start) // INSTRUMENT_BYTES
    if any(
        item["offset"] < start + count * INSTRUMENT_BYTES and item["offset"] + item["size"] > start
        for item in instructions
    ):
        raise SoundError("a sequence runs into the instruments")
    return {**head, "instrument_count": count, "instructions": instructions}


def editable(data: bytes, decoded: dict[str, Any]) -> list[bool]:
    """Which bytes of a payload an edit may change."""
    mask = [False] * len(data)
    for item in decoded["instructions"]:
        at, opcode = item["offset"], data[item["offset"]]
        if item["kind"] != "command":
            for position in range(at, at + item["size"]):
                mask[position] = True
            continue
        fixed = FLOW.get(opcode, ())
        # An extended command's selector, and $E4's first operand (which
        # chooses its size), are not values either.
        first = at + (2 if opcode in (0xFF, 0xE4) else 1)
        for index, position in enumerate(range(first, at + item["size"])):
            mask[position] = index not in fixed
    start = decoded["instruments"]
    for position in range(start, start + decoded["instrument_count"] * INSTRUMENT_BYTES):
        mask[position] = True
    return mask


def export(tree: SourceTree) -> dict[str, Any]:
    items = []
    for label, item in requests(tree).items():
        data = tree.data(label)
        try:
            decoded = decode(item["kind"], data)
        except (SoundError, IndexError) as error:
            raise SoundError(f"{label}: {error}") from None
        items.append({"label": label, **item, "data": data.hex().upper(), **decoded})
    return {"schema": SCHEMA, "schema_version": 1, "payloads": items}


def apply(tree: SourceTree, document: dict[str, Any]) -> None:
    if document.get("schema") != SCHEMA or document.get("schema_version") != 1:
        raise SoundError("not an Alien Soldier sound document")
    source = requests(tree)
    items = document.get("payloads")
    if not isinstance(items, list) or [item.get("label") for item in items] != list(source):
        raise SoundError("the sound payloads differ from the source")
    for item in items:
        label, kind = item["label"], source[item["label"]]["kind"]
        original = tree.data(label)
        try:
            data = bytes.fromhex(item.get("data", ""))
        except ValueError:
            raise SoundError(f"{label}: the data is not hexadecimal") from None
        if data == original:
            continue
        if len(data) != len(original):
            raise SoundError(f"{label}: the payload keeps its size")
        before = decode(kind, original)
        mask = editable(original, before)
        changed = [at for at in range(len(data)) if data[at] != original[at]]
        if any(not mask[at] for at in changed):
            raise SoundError(f"{label}: ${changed[0]:X} is not a value the driver reads")
        try:
            after = decode(kind, data)
        except (SoundError, IndexError) as error:
            raise SoundError(f"{label}: {error}") from None
        if [(i["offset"], i["size"], i["kind"], i.get("target")) for i in after["instructions"]] != [
            (i["offset"], i["size"], i["kind"], i.get("target")) for i in before["instructions"]
        ] or any(data[i["offset"]] != original[i["offset"]] for i in before["instructions"] if i["kind"] == "command"):
            raise SoundError(f"{label}: an edit changed an instruction's kind or size")
        for item_after in after["instructions"]:
            at = item_after["offset"]
            if data[at] == 0xEF and data[at + 1] >= after["instrument_count"]:
                raise SoundError(f"{label}: instrument {data[at + 1]} is not in its table")
        tree.set_data(label, data)
