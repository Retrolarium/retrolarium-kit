"""Doraemon World 1 map objects.

The city ($D989) and underground ($D925) placement lists hold packed
three-byte records: X and Y in eight-pixel world cells, then a type, ended
by a zero X. The record index is the object's persistent ID (its collected
bit), so a list keeps its count. Types $00-$0B are enemies; $80-$8C and
$C0-$CC pick one of the thirteen descriptors at $CC0A (bit 6 marks an object
revealed by a shot). The game scans the first 45 records always and stops
scanning the rest once their X passes the camera column, so records after
the 45th stay ordered by X.

Enemy placements take their initial metasprite, render flags and health
from three 16-byte tables at $8E70 (World1_MaterializePlacement); each
descriptor is runtime type, metasprite, render flags and behavior.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from retrolarium_kit.core.authoring import AuthoringError


PLACEMENTS_CODEC = "doraemon.world1-placements.v1"
ENEMY_PROPERTIES_CODEC = "doraemon.world1-enemy-properties.v1"
DESCRIPTORS_CODEC = "doraemon.world1-descriptors.v1"
ENEMY_TYPES = 12  # placement types $00-$0B
DESCRIPTORS = 13  # descriptor indexes $00-$0C
ALWAYS_SCANNED = 45


class DoraemonObjectError(AuthoringError):
    """World 1 object data does not fit its ROM tables."""


def _byte(value: object, context: str, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value <= 255:
        raise DoraemonObjectError(f"{context} must be a byte {minimum}-255")
    return value


def valid_type(value: int) -> bool:
    return value < ENEMY_TYPES or (value & 0xB0) == 0x80 and (value & 0x0F) < DESCRIPTORS


def decode_placements(payload: bytes) -> list[dict[str, int]]:
    if len(payload) % 3 != 1 or payload[-1] != 0:
        raise DoraemonObjectError("a placement list is whole records and a zero terminator")
    records = []
    for at in range(0, len(payload) - 1, 3):
        x, y, kind = payload[at : at + 3]
        if x == 0:
            raise DoraemonObjectError("a placement list ends before its span")
        records.append({"x": x, "y": y, "type": kind})
    return records


def encode_placements(value: Any, size: int, *, sorted_tail: bool) -> bytes:
    count = (size - 1) // 3
    if not isinstance(value, list) or len(value) != count:
        raise DoraemonObjectError(f"the placement list holds {count} objects")
    payload = bytearray()
    for i, record in enumerate(value):
        if not isinstance(record, Mapping) or set(record) != {"x", "y", "type"}:
            raise DoraemonObjectError(f"placement {i} fields differ")
        x = _byte(record["x"], f"placement {i} x", 1)
        y = _byte(record["y"], f"placement {i} y")
        kind = _byte(record["type"], f"placement {i} type")
        if not valid_type(kind):
            raise DoraemonObjectError(f"placement {i} type ${kind:02X} is neither an enemy nor a descriptor object")
        payload += bytes((x, y, kind))
    if sorted_tail:
        tail = [record["x"] for record in value[ALWAYS_SCANNED:]]
        if tail != sorted(tail):
            raise DoraemonObjectError(f"placements after the first {ALWAYS_SCANNED} must stay ordered by X")
    return bytes(payload) + bytes(1)


def decode_enemy_properties(payload: bytes) -> dict[str, list[int]]:
    if len(payload) != 48:
        raise DoraemonObjectError("the enemy property tables are three 16-byte columns")
    return {"metasprites": list(payload[0:16]), "render_flags": list(payload[16:32]), "health": list(payload[32:48])}


def encode_enemy_properties(value: Any) -> bytes:
    if not isinstance(value, Mapping) or set(value) != {"metasprites", "render_flags", "health"}:
        raise DoraemonObjectError("enemy property fields differ")
    payload = bytearray()
    for name in ("metasprites", "render_flags", "health"):
        column = value[name]
        if not isinstance(column, list) or len(column) != 16:
            raise DoraemonObjectError(f"enemy {name} holds 16 bytes")
        payload += bytes(_byte(v, f"enemy {name}") for v in column)
    return bytes(payload)


DESCRIPTOR_FIELDS = ("runtime_type", "metasprite", "render_flags", "behavior")


def decode_descriptors(payload: bytes) -> list[dict[str, int]]:
    if len(payload) != DESCRIPTORS * 4:
        raise DoraemonObjectError("the descriptor table is thirteen four-byte records")
    return [dict(zip(DESCRIPTOR_FIELDS, payload[i * 4 : i * 4 + 4])) for i in range(DESCRIPTORS)]


def encode_descriptors(value: Any) -> bytes:
    if not isinstance(value, list) or len(value) != DESCRIPTORS:
        raise DoraemonObjectError("the descriptor table holds thirteen records")
    payload = bytearray()
    for i, record in enumerate(value):
        if not isinstance(record, Mapping) or set(record) != set(DESCRIPTOR_FIELDS):
            raise DoraemonObjectError(f"descriptor {i} fields differ")
        payload += bytes(_byte(record[name], f"descriptor {i} {name}") for name in DESCRIPTOR_FIELDS)
    return bytes(payload)


REGISTRY_CODEC = "doraemon.world3-registry.v1"
REGISTRY_RECORDS = 13
REGISTRY_FIELDS = ("room", "type", "x", "y", "state")


def decode_registry(payload: bytes) -> list[dict[str, int]]:
    """World 3's persistent objects ($D96B): five 13-byte columns (room,
    type, X and Y in the room, state) copied as they are into RAM $06B0.
    At start the game shuffles the types of slots 0-3 and of slots 4-11."""
    if len(payload) != REGISTRY_RECORDS * len(REGISTRY_FIELDS):
        raise DoraemonObjectError("the World 3 registry is five 13-byte columns")
    return [
        {name: payload[c * REGISTRY_RECORDS + i] for c, name in enumerate(REGISTRY_FIELDS)}
        for i in range(REGISTRY_RECORDS)
    ]


def encode_registry(value: Any) -> bytes:
    if not isinstance(value, list) or len(value) != REGISTRY_RECORDS:
        raise DoraemonObjectError("the World 3 registry holds thirteen objects")
    columns = [bytearray() for _ in REGISTRY_FIELDS]
    for i, record in enumerate(value):
        if not isinstance(record, Mapping) or set(record) != set(REGISTRY_FIELDS):
            raise DoraemonObjectError(f"World 3 object {i} fields differ")
        for c, name in enumerate(REGISTRY_FIELDS):
            columns[c].append(_byte(record[name], f"World 3 object {i} {name}"))
        if record["room"] > 63:
            raise DoraemonObjectError(f"World 3 object {i} room is 0-63")
        if record["type"] > 0x1F:
            raise DoraemonObjectError(f"World 3 object {i} type is $00-$1F")
    return b"".join(bytes(column) for column in columns)


SCHEDULE_CODEC = "doraemon.world3-schedule.v1"
SCHEDULE_ROOMS = 64
SCHEDULE_CHANNELS = 4
SCHEDULE_FIELDS = ("type", "count", "delay")


def decode_schedule(payload: bytes) -> list[dict[str, object]]:
    """World 3's transient spawn schedule ($D66B, bank 2): twelve 64-byte
    columns, type, count and delay of each room's four channels. A zero
    count disables a channel; type zero is a real entity type."""
    if len(payload) != SCHEDULE_ROOMS * SCHEDULE_CHANNELS * len(SCHEDULE_FIELDS):
        raise DoraemonObjectError("the World 3 schedule is twelve 64-byte columns")
    rooms = []
    for room in range(SCHEDULE_ROOMS):
        channels = []
        for channel in range(SCHEDULE_CHANNELS):
            channels.append(
                {
                    name: payload[(f * SCHEDULE_CHANNELS + channel) * SCHEDULE_ROOMS + room]
                    for f, name in enumerate(SCHEDULE_FIELDS)
                }
            )
        rooms.append({"channels": channels})
    return rooms


def encode_schedule(value: Any) -> bytes:
    if not isinstance(value, list) or len(value) != SCHEDULE_ROOMS:
        raise DoraemonObjectError("the World 3 schedule holds 64 rooms")
    payload = bytearray(SCHEDULE_ROOMS * SCHEDULE_CHANNELS * len(SCHEDULE_FIELDS))
    for room, record in enumerate(value):
        channels = record.get("channels") if isinstance(record, Mapping) and set(record) == {"channels"} else None
        if not isinstance(channels, list) or len(channels) != SCHEDULE_CHANNELS:
            raise DoraemonObjectError(f"World 3 room {room} has four schedule channels")
        for channel, entry in enumerate(channels):
            if not isinstance(entry, Mapping) or set(entry) != set(SCHEDULE_FIELDS):
                raise DoraemonObjectError(f"World 3 room {room} channel {channel} fields differ")
            for f, name in enumerate(SCHEDULE_FIELDS):
                payload[(f * SCHEDULE_CHANNELS + channel) * SCHEDULE_ROOMS + room] = _byte(
                    entry[name], f"World 3 room {room} channel {channel} {name}"
                )
            if entry["type"] > 0x0F:
                raise DoraemonObjectError(f"World 3 room {room} channel {channel} type is $00-$0F")
    return bytes(payload)


SECRET_SCREENS_CODEC = "doraemon.world2-secret-screens.v1"
SECRET_SCREENS = 7


def decode_secret_screens(payload: bytes) -> list[int]:
    """World 2's screens that can drop an inventory item ($A6B5): seven
    screen selectors searched backwards; the item appears at X $78, Y $F0."""
    if len(payload) != SECRET_SCREENS:
        raise DoraemonObjectError("the secret item screen table holds seven selectors")
    return list(payload)


def encode_secret_screens(value: Any) -> bytes:
    if not isinstance(value, list) or len(value) != SECRET_SCREENS:
        raise DoraemonObjectError("the secret item screen table holds seven selectors")
    return bytes(_byte(v, f"secret item screen {i}") for i, v in enumerate(value))
