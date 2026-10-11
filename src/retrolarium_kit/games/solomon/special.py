"""mirror records and special-room tables for Solomon's Key."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from retrolarium_kit.core.authoring import AuthoringError
from retrolarium_kit.games.solomon.streams import Position, decode_position, parse_position


class SolomonSpecialError(AuthoringError):
    """Invalid mirror or special-room data."""


SEAL_ROOMS = (9, 13, 17, 19, 21, 29, 46, 47)
SPECIAL_SIZES = {
    "bonus_positions_prg": 32,
    "bonus_types_prg": 16,
    "seal_positions_prg": 8,
    "princess_positions_prg": 12,
    "room20_plane_prg": 24,
    "room30_plane_prg": 24,
}


def _byte(value: object, name: str) -> int:
    if type(value) is not int or not 0 <= value <= 255:
        raise SolomonSpecialError(f"{name} must be a byte")
    return value


def _fields(value: object, keys: set[str], name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise SolomonSpecialError(f"{name} has invalid fields")
    return value


@dataclass(frozen=True)
class MirrorSchedule:
    initial_phase: bytes
    loop_phase: bytes

    def as_dict(self) -> dict[str, object]:
        return {"initial_phase": list(self.initial_phase),
                "loop_phase": list(self.loop_phase)}


def decode_mirror_schedule(prg: bytes, start: int, limit: int) -> tuple[MirrorSchedule, int]:
    if limit - start != 8:
        raise SolomonSpecialError("mirror schedule must occupy eight bytes")
    return MirrorSchedule(prg[start:start + 4], prg[start + 4:limit]), limit


def parse_mirror_schedule(value: object, name: str) -> MirrorSchedule:
    item = _fields(value, {"initial_phase", "loop_phase"}, name)
    phases = []
    for field in ("initial_phase", "loop_phase"):
        raw = item[field]
        if not isinstance(raw, list) or len(raw) != 4:
            raise SolomonSpecialError(f"{name}.{field} needs four bytes")
        phases.append(bytes(_byte(value, f"{name}.{field}") for value in raw))
    return MirrorSchedule(*phases)


def encode_mirror_schedule(value: MirrorSchedule) -> bytes:
    if len(value.initial_phase) != 4 or len(value.loop_phase) != 4:
        raise SolomonSpecialError("mirror schedule must occupy eight bytes")
    return value.initial_phase + value.loop_phase


@dataclass(frozen=True)
class MirrorEnemySet:
    enemy_types: bytes
    loop_offset: int

    def as_dict(self) -> dict[str, object]:
        return {"enemy_types": list(self.enemy_types),
                "loop_offset": self.loop_offset}


def decode_mirror_enemy_set(
    prg: bytes, start: int, limit: int
) -> tuple[MirrorEnemySet, int]:
    if not 0 <= start < limit <= len(prg):
        raise SolomonSpecialError("mirror enemy-set pointer escapes its arena")
    cursor = start
    enemy_types = bytearray()
    while cursor < limit:
        value = prg[cursor]
        cursor += 1
        if value >= 0x90:
            offset = value - 0x90
            if not enemy_types or offset >= len(enemy_types):
                raise SolomonSpecialError("mirror loop offset is invalid")
            return MirrorEnemySet(bytes(enemy_types), offset), cursor
        if not 0x18 <= value <= 0x83:
            raise SolomonSpecialError("mirror enemy type is invalid")
        enemy_types.append(value)
    raise SolomonSpecialError("mirror enemy set has no loop command")


def parse_mirror_enemy_set(value: object, name: str) -> MirrorEnemySet:
    item = _fields(value, {"enemy_types", "loop_offset"}, name)
    raw = item["enemy_types"]
    if not isinstance(raw, list) or not raw:
        raise SolomonSpecialError(f"{name}.enemy_types must be non-empty")
    types = bytes(_byte(enemy, f"{name}.enemy_types") for enemy in raw)
    if any(not 0x18 <= enemy <= 0x83 for enemy in types):
        raise SolomonSpecialError(f"{name} contains an invalid enemy type")
    offset = item["loop_offset"]
    if type(offset) is not int or not 0 <= offset < len(types):
        raise SolomonSpecialError(f"{name}.loop_offset escapes enemy types")
    if offset > 0x6F:
        raise SolomonSpecialError(f"{name}.loop_offset cannot fit a loop command")
    return MirrorEnemySet(types, offset)


def encode_mirror_enemy_set(value: MirrorEnemySet) -> bytes:
    parsed = parse_mirror_enemy_set(value.as_dict(), "mirror enemy set")
    return parsed.enemy_types + bytes((0x90 + parsed.loop_offset,))


def _plane_positions(data: bytes) -> tuple[Position, ...]:
    if len(data) != 24:
        raise SolomonSpecialError("special-room bitplane needs 24 bytes")
    return tuple(
        Position(x, y)
        for y in range(12) for x in range(16)
        if data[y * 2 + x // 8] & (0x80 >> (x % 8))
    )


def _encode_plane(positions: tuple[Position, ...], name: str) -> bytes:
    output = bytearray(24)
    occupied: set[tuple[int, int]] = set()
    for position in positions:
        if not 0 <= position.x < 16 or not 0 <= position.y < 12:
            raise SolomonSpecialError(f"{name} position escapes 16x12 room")
        key = (position.x, position.y)
        if key in occupied:
            raise SolomonSpecialError(f"{name} contains a duplicate cell")
        occupied.add(key)
        output[position.y * 2 + position.x // 8] |= 0x80 >> (position.x % 8)
    return bytes(output)


@dataclass(frozen=True)
class SpecialRooms:
    bonus_positions: tuple[Position, ...]
    bonus_item_types: bytes
    seal_positions: tuple[Position, ...]
    princess_positions: tuple[Position, ...]
    room20_bat_symbols: tuple[Position, ...]
    room30_blue_opals: tuple[Position, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "random_bonus_room": {
                "positions": [value.as_dict() for value in self.bonus_positions],
                "item_types": list(self.bonus_item_types),
            },
            "solomon_seals": [
                {"room": room, "position": position.as_dict()}
                for room, position in zip(SEAL_ROOMS, self.seal_positions)
            ],
            "princess_room_hidden_cells": [
                value.as_dict() for value in self.princess_positions
            ],
            "room_20_bat_symbols": [
                value.as_dict() for value in self.room20_bat_symbols
            ],
            "room_30_blue_opals": [
                value.as_dict() for value in self.room30_blue_opals
            ],
        }


def decode_special_rooms(prg: bytes, offsets: Mapping[str, int]) -> SpecialRooms:
    chunks = {
        name: prg[offsets[name]:offsets[name] + size]
        for name, size in SPECIAL_SIZES.items()
    }
    if any(len(chunks[name]) != size for name, size in SPECIAL_SIZES.items()):
        raise SolomonSpecialError("special-room table escapes PRG")
    return SpecialRooms(
        tuple(map(decode_position, chunks["bonus_positions_prg"])),
        chunks["bonus_types_prg"],
        tuple(map(decode_position, chunks["seal_positions_prg"])),
        tuple(map(decode_position, chunks["princess_positions_prg"])),
        _plane_positions(chunks["room20_plane_prg"]),
        _plane_positions(chunks["room30_plane_prg"]),
    )


def parse_special_rooms(value: object) -> SpecialRooms:
    item = _fields(value, {
        "random_bonus_room", "solomon_seals", "princess_room_hidden_cells",
        "room_20_bat_symbols", "room_30_blue_opals",
    }, "special_rooms")
    bonus = _fields(item["random_bonus_room"], {"positions", "item_types"},
                    "random_bonus_room")
    if not isinstance(bonus["positions"], list) or len(bonus["positions"]) != 32:
        raise SolomonSpecialError("random bonus room needs 32 positions")
    if not isinstance(bonus["item_types"], list) or len(bonus["item_types"]) != 16:
        raise SolomonSpecialError("random bonus room needs 16 item types")
    types = bytes(_byte(raw, "random bonus item type")
                  for raw in bonus["item_types"])
    if any(not 1 <= raw < 0xC0 for raw in types):
        raise SolomonSpecialError("random bonus item type is invalid")
    raw_seals = item["solomon_seals"]
    if not isinstance(raw_seals, list) or len(raw_seals) != len(SEAL_ROOMS):
        raise SolomonSpecialError("Solomon Seal table needs eight positions")
    seals = []
    for room, raw in zip(SEAL_ROOMS, raw_seals):
        record = _fields(raw, {"room", "position"}, "Solomon Seal")
        if type(record["room"]) is not int or record["room"] != room:
            raise SolomonSpecialError("Solomon Seal room identities are not canonical")
        seals.append(parse_position(record["position"], "Solomon Seal position"))
    princess = item["princess_room_hidden_cells"]
    if not isinstance(princess, list) or len(princess) != 12:
        raise SolomonSpecialError("Princess room needs twelve hidden cells")
    planes = []
    for key in ("room_20_bat_symbols", "room_30_blue_opals"):
        raw = item[key]
        if not isinstance(raw, list):
            raise SolomonSpecialError(f"{key} must be a position array")
        positions = tuple(parse_position(position, key) for position in raw)
        _encode_plane(positions, key)
        planes.append(positions)
    return SpecialRooms(
        tuple(parse_position(value, "bonus position") for value in bonus["positions"]),
        types,
        tuple(seals),
        tuple(parse_position(value, "Princess hidden cell") for value in princess),
        *planes,
    )


def encode_special_rooms(value: SpecialRooms) -> dict[str, bytes]:
    checked = parse_special_rooms(value.as_dict())
    return {
        "bonus_positions_prg": bytes(position.encode()
                                     for position in checked.bonus_positions),
        "bonus_types_prg": checked.bonus_item_types,
        "seal_positions_prg": bytes(position.encode()
                                    for position in checked.seal_positions),
        "princess_positions_prg": bytes(position.encode()
                                        for position in checked.princess_positions),
        "room20_plane_prg": _encode_plane(checked.room20_bat_symbols,
                                          "room_20_bat_symbols"),
        "room30_plane_prg": _encode_plane(checked.room30_blue_opals,
                                          "room_30_blue_opals"),
    }
