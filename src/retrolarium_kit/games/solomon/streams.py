"""Lossless Solomon room enemy and item stream grammar.

Records retain their original command boundaries.  In particular, repeated
items are not expanded and terminator opcodes keep their low, unused bits.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from retrolarium_kit.core.authoring import AuthoringError


class SolomonStreamError(AuthoringError):
    """Invalid encoded stream or semantic room record."""


def _byte(value: object, name: str, maximum: int = 255) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        raise SolomonStreamError(f"{name} must be an integer from 0 to {maximum}")
    return value


def _fields(value: object, fields: set[str], name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise SolomonStreamError(f"{name} has invalid fields")
    return value


@dataclass(frozen=True)
class Position:
    x: int
    y: int

    def as_dict(self) -> dict[str, int]:
        return {"x": self.x, "y": self.y}

    def encode(self) -> int:
        _byte(self.x, "position.x", 15)
        if type(self.y) is not int or not -1 <= self.y <= 14:
            raise SolomonStreamError("position.y must be from -1 to 14")
        return ((self.y + 1) << 4) | self.x


def decode_position(value: int) -> Position:
    return Position(value & 15, (value >> 4) - 1)


def parse_position(value: object, name: str) -> Position:
    item = _fields(value, {"x", "y"}, name)
    position = Position(item["x"], item["y"])
    position.encode()
    return position


@dataclass(frozen=True)
class EnemyRecord:
    enemy_type: int
    position: Position

    def as_dict(self) -> dict[str, object]:
        return {"type": self.enemy_type, "position": self.position.as_dict()}


@dataclass(frozen=True)
class EnemyStream:
    spawn_lifetime: int
    enemies: tuple[EnemyRecord, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "spawn_lifetime": self.spawn_lifetime,
            "enemies": [record.as_dict() for record in self.enemies],
        }


def decode_enemy_stream(prg: bytes, start: int, limit: int) -> tuple[EnemyStream, int]:
    if not 0 <= start < limit <= len(prg):
        raise SolomonStreamError("enemy stream pointer escapes its arena")
    encoded_lifetime = prg[start]
    lifetime = ((encoded_lifetime & 31) << 3) | (encoded_lifetime >> 5)
    cursor = start + 1
    enemies = []
    while cursor < limit:
        enemy_type = prg[cursor]
        cursor += 1
        if enemy_type == 0:
            return EnemyStream(lifetime, tuple(enemies)), cursor
        if not 0x18 <= enemy_type <= 0x83 or cursor >= limit:
            raise SolomonStreamError("invalid or truncated enemy record")
        enemies.append(EnemyRecord(enemy_type, decode_position(prg[cursor])))
        cursor += 1
    raise SolomonStreamError("enemy stream has no terminator")


def parse_enemy_stream(value: object, name: str) -> EnemyStream:
    item = _fields(value, {"spawn_lifetime", "enemies"}, name)
    lifetime = _byte(item["spawn_lifetime"], f"{name}.spawn_lifetime")
    raw_records = item["enemies"]
    if not isinstance(raw_records, list) or len(raw_records) > 17:
        raise SolomonStreamError(f"{name}.enemies must have at most 17 records")
    records = []
    for index, raw in enumerate(raw_records):
        record = _fields(raw, {"type", "position"}, f"{name}.enemies[{index}]")
        enemy_type = _byte(record["type"], f"{name}.enemies[{index}].type")
        if not 0x18 <= enemy_type <= 0x83:
            raise SolomonStreamError("enemy type is outside the proven range")
        records.append(EnemyRecord(
            enemy_type,
            parse_position(record["position"], f"{name}.enemies[{index}].position"),
        ))
    return EnemyStream(lifetime, tuple(records))


def encode_enemy_stream(stream: EnemyStream) -> bytes:
    lifetime = _byte(stream.spawn_lifetime, "spawn_lifetime")
    output = bytearray((((lifetime >> 3) | ((lifetime & 7) << 5)),))
    if len(stream.enemies) > 17:
        raise SolomonStreamError("enemy stream exceeds the 17-slot runtime pool")
    for record in stream.enemies:
        if not 0x18 <= record.enemy_type <= 0x83:
            raise SolomonStreamError("enemy type is outside the proven range")
        output.extend((record.enemy_type, record.position.encode()))
    output.append(0)
    return bytes(output)


_POSITION_FIELDS = ("door", "key", "player_start", "mirror_1", "mirror_2")
_MIRROR_FIELDS = (
    "mirror_2_schedule", "mirror_1_schedule",
    "mirror_2_enemy_set", "mirror_1_enemy_set",
)


@dataclass(frozen=True)
class ItemMetadata:
    mirror_2_schedule: int
    mirror_1_schedule: int
    mirror_2_enemy_set: int
    mirror_1_enemy_set: int
    status_rate_raw: int
    door: Position
    key: Position
    player_start: Position
    mirror_1: Position
    mirror_2: Position

    def as_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            field: getattr(self, field)
            for field in (*_MIRROR_FIELDS, "status_rate_raw")
        }
        result.update({field: getattr(self, field).as_dict()
                       for field in _POSITION_FIELDS})
        return result


@dataclass(frozen=True)
class ItemCommand:
    kind: str
    opcode: int | None = None
    item_type: int | None = None
    position: Position | None = None
    positions: tuple[Position, ...] = ()

    def as_dict(self) -> dict[str, object]:
        if self.kind == "end":
            return {"kind": "end", "opcode": self.opcode}
        if self.kind == "constellation":
            return {"kind": "constellation", "opcode": self.opcode,
                    "position": self.position.as_dict()}
        if self.kind == "repeat":
            return {"kind": "repeat", "opcode": self.opcode,
                    "type": self.item_type,
                    "positions": [position.as_dict() for position in self.positions]}
        return {"kind": "item", "type": self.item_type,
                "position": self.position.as_dict()}


@dataclass(frozen=True)
class ItemStream:
    metadata: ItemMetadata
    commands: tuple[ItemCommand, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "metadata": self.metadata.as_dict(),
            "commands": [command.as_dict() for command in self.commands],
        }


def decode_item_stream(prg: bytes, start: int, limit: int) -> tuple[ItemStream, int]:
    if not 0 <= start < limit <= len(prg) or start + 10 > limit:
        raise SolomonStreamError("item stream pointer escapes its arena")
    header = prg[start:start + 10]
    metadata = ItemMetadata(*header[:5], *(decode_position(raw) for raw in header[5:]))
    cursor = start + 10
    commands = []
    while cursor < limit:
        code = prg[cursor]
        cursor += 1
        if code == 0 or 0xE0 <= code <= 0xEF:
            commands.append(ItemCommand("end", opcode=code))
            return ItemStream(metadata, tuple(commands)), cursor
        if 0xF0 <= code <= 0xFB:
            if cursor >= limit:
                break
            commands.append(ItemCommand(
                "constellation", opcode=code,
                position=decode_position(prg[cursor]),
            ))
            return ItemStream(metadata, tuple(commands)), cursor + 1
        if 0xC0 <= code <= 0xDF:
            count = code - 0xC0 + 1
            if cursor + 1 + count > limit:
                break
            item_type = prg[cursor]
            positions = tuple(decode_position(raw)
                              for raw in prg[cursor + 1:cursor + 1 + count])
            commands.append(ItemCommand(
                "repeat", opcode=code, item_type=item_type,
                positions=positions,
            ))
            cursor += 1 + count
            continue
        if not (1 <= code < 0xC0 or 0xFC <= code <= 0xFF) or cursor >= limit:
            break
        commands.append(ItemCommand(
            "item", item_type=code,
            position=decode_position(prg[cursor]),
        ))
        cursor += 1
    raise SolomonStreamError("invalid or unterminated item stream")


def parse_item_stream(value: object, name: str) -> ItemStream:
    item = _fields(value, {"metadata", "commands"}, name)
    raw_meta = _fields(
        item["metadata"],
        set(_MIRROR_FIELDS) | {"status_rate_raw"} | set(_POSITION_FIELDS),
        f"{name}.metadata",
    )
    mirror_values = []
    for index, field in enumerate(_MIRROR_FIELDS):
        value = _byte(raw_meta[field], f"{name}.{field}")
        if value >= (16 if index < 2 else 17):
            raise SolomonStreamError(f"{name}.{field} escapes its table")
        mirror_values.append(value)
    metadata = ItemMetadata(
        *mirror_values,
        _byte(raw_meta["status_rate_raw"], f"{name}.status_rate_raw"),
        *(parse_position(raw_meta[field], f"{name}.{field}")
          for field in _POSITION_FIELDS),
    )
    raw_commands = item["commands"]
    if not isinstance(raw_commands, list) or not raw_commands:
        raise SolomonStreamError(f"{name}.commands must be non-empty")
    commands = []
    for index, raw in enumerate(raw_commands):
        label = f"{name}.commands[{index}]"
        if not isinstance(raw, Mapping):
            raise SolomonStreamError(f"{label} must be an object")
        kind = raw.get("kind")
        if kind == "end":
            data = _fields(raw, {"kind", "opcode"}, label)
            opcode = _byte(data["opcode"], f"{label}.opcode")
            if opcode != 0 and not 0xE0 <= opcode <= 0xEF:
                raise SolomonStreamError(f"{label} has an invalid end opcode")
            command = ItemCommand("end", opcode=opcode)
        elif kind == "constellation":
            data = _fields(raw, {"kind", "opcode", "position"}, label)
            opcode = _byte(data["opcode"], f"{label}.opcode")
            if not 0xF0 <= opcode <= 0xFB:
                raise SolomonStreamError(f"{label} has an invalid constellation opcode")
            command = ItemCommand(
                "constellation", opcode=opcode,
                position=parse_position(data["position"], f"{label}.position"),
            )
        elif kind == "repeat":
            data = _fields(raw, {"kind", "opcode", "type", "positions"}, label)
            opcode = _byte(data["opcode"], f"{label}.opcode")
            item_type = _byte(data["type"], f"{label}.type")
            positions = data["positions"]
            if not 0xC0 <= opcode <= 0xDF or not isinstance(positions, list) \
                    or len(positions) != opcode - 0xC0 + 1:
                raise SolomonStreamError(f"{label} has an invalid repeated-item count")
            command = ItemCommand(
                "repeat", opcode=opcode, item_type=item_type,
                positions=tuple(parse_position(position, f"{label}.positions")
                                for position in positions),
            )
        elif kind == "item":
            data = _fields(raw, {"kind", "type", "position"}, label)
            item_type = _byte(data["type"], f"{label}.type")
            if not (1 <= item_type < 0xC0 or 0xFC <= item_type <= 0xFF):
                raise SolomonStreamError(f"{label} has an invalid item type")
            command = ItemCommand(
                "item", item_type=item_type,
                position=parse_position(data["position"], f"{label}.position"),
            )
        else:
            raise SolomonStreamError(f"{label} has an unknown command kind")
        if commands and commands[-1].kind in {"end", "constellation"}:
            raise SolomonStreamError(f"{label} follows an item terminator")
        commands.append(command)
    if commands[-1].kind not in {"end", "constellation"}:
        raise SolomonStreamError(f"{name} has no item terminator")
    return ItemStream(metadata, tuple(commands))


def encode_item_stream(stream: ItemStream) -> bytes:
    metadata = stream.metadata
    output = bytearray(_byte(getattr(metadata, field), field)
                       for field in _MIRROR_FIELDS)
    output.append(_byte(metadata.status_rate_raw, "status_rate_raw"))
    output.extend(getattr(metadata, field).encode() for field in _POSITION_FIELDS)
    for command in stream.commands:
        if command.kind == "end":
            output.append(command.opcode)
        elif command.kind == "constellation":
            output.extend((command.opcode, command.position.encode()))
        elif command.kind == "repeat":
            output.extend((command.opcode, command.item_type))
            output.extend(position.encode() for position in command.positions)
        else:
            output.extend((command.item_type, command.position.encode()))
    return bytes(output)
