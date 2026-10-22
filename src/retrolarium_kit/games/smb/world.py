"""Typed Super Mario Bros. world-routing and player-physics resource."""

from __future__ import annotations

from typing import Mapping

from retrolarium_kit.core.authoring import AuthoringError


CODEC = "smb.world.v1"
AREA_TYPES = ("water", "ground", "underground", "castle")


class SmbWorldError(AuthoringError):
    """An SMB world document or profile is malformed."""


def _integer(value: object, name: str, *, minimum: int = 0, maximum: int = 0xFF) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum or value > maximum:
        raise SmbWorldError(f"{name} must be an integer in {minimum}..{maximum}")
    return value


def _boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise SmbWorldError(f"{name} must be a boolean")
    return value


def _table_specs(parameters: Mapping[str, object]) -> list[Mapping[str, object]]:
    value = parameters.get("physics_tables")
    if not isinstance(value, list) or not value:
        raise SmbWorldError("physics_tables must be a non-empty array")
    result: list[Mapping[str, object]] = []
    names: set[str] = set()
    for index, raw in enumerate(value):
        if not isinstance(raw, dict):
            raise SmbWorldError(f"physics_tables[{index}] must be an object")
        if set(raw) != {"name", "length", "signed"}:
            raise SmbWorldError(f"physics_tables[{index}] fields differ from contract")
        name = raw.get("name")
        if not isinstance(name, str) or not name or name in names:
            raise SmbWorldError(f"physics_tables[{index}].name is invalid or duplicated")
        names.add(name)
        _integer(raw.get("length"), f"physics_tables[{index}].length", minimum=1)
        _boolean(raw.get("signed"), f"physics_tables[{index}].signed")
        result.append(raw)
    return result


def _world_lengths(parameters: Mapping[str, object]) -> list[int]:
    value = parameters.get("world_lengths")
    if not isinstance(value, list) or not value:
        raise SmbWorldError("world_lengths must be a non-empty array")
    return [_integer(item, f"world_lengths[{index}]", minimum=1, maximum=32) for index, item in enumerate(value)]


def decode_world(routing: bytes, physics: bytes, parameters: Mapping[str, object]) -> dict[str, object]:
    """Decode the two fixed SMB World Studio ranges."""

    lengths = _world_lengths(parameters)
    if len(routing) != sum(lengths):
        raise SmbWorldError(f"routing range has {len(routing)} bytes, expected {sum(lengths)}")
    worlds = []
    offset = 0
    for world_index, length in enumerate(lengths):
        areas = []
        for value in routing[offset : offset + length]:
            areas.append(
                {
                    "area_type": (value >> 5) & 0x03,
                    "area_index": value & 0x1F,
                    "alternate_bit": bool(value & 0x80),
                }
            )
        worlds.append({"world": world_index + 1, "areas": areas})
        offset += length

    tables = []
    offset = 0
    for specification in _table_specs(parameters):
        length = int(specification["length"])
        chunk = physics[offset : offset + length]
        if len(chunk) != length:
            raise SmbWorldError("physics range is shorter than its table contract")
        signed = bool(specification["signed"])
        tables.append(
            {
                "name": specification["name"],
                "signed": signed,
                "values": [value - 256 if signed and value >= 0x80 else value for value in chunk],
            }
        )
        offset += length
    if offset != len(physics):
        raise SmbWorldError(f"physics tables consume {offset} bytes, range has {len(physics)}")
    return {
        "area_types": list(AREA_TYPES),
        "worlds": worlds,
        "physics_tables": tables,
    }


def encode_world(value: Mapping[str, object], parameters: Mapping[str, object]) -> tuple[bytes, bytes]:
    """Validate and encode routing and physics without retaining opaque bytes."""

    if set(value) != {"area_types", "worlds", "physics_tables"}:
        raise SmbWorldError("SMB world resource fields differ from its codec contract")
    if value.get("area_types") != list(AREA_TYPES):
        raise SmbWorldError("area_types differ from the SMB engine contract")

    lengths = _world_lengths(parameters)
    worlds = value.get("worlds")
    if not isinstance(worlds, list) or len(worlds) != len(lengths):
        raise SmbWorldError(f"worlds must contain {len(lengths)} entries")
    routing = bytearray()
    for world_index, (raw_world, length) in enumerate(zip(worlds, lengths)):
        context = f"worlds[{world_index}]"
        if not isinstance(raw_world, dict) or set(raw_world) != {"world", "areas"}:
            raise SmbWorldError(f"{context} fields differ from contract")
        if raw_world.get("world") != world_index + 1:
            raise SmbWorldError(f"{context}.world is not in display order")
        areas = raw_world.get("areas")
        if not isinstance(areas, list) or len(areas) != length:
            raise SmbWorldError(f"{context}.areas must contain {length} routes")
        for area_index, raw_area in enumerate(areas):
            area_context = f"{context}.areas[{area_index}]"
            if not isinstance(raw_area, dict) or set(raw_area) != {"area_type", "area_index", "alternate_bit"}:
                raise SmbWorldError(f"{area_context} fields differ from contract")
            area_type = _integer(raw_area.get("area_type"), f"{area_context}.area_type", maximum=3)
            target = _integer(raw_area.get("area_index"), f"{area_context}.area_index", maximum=31)
            alternate = _boolean(raw_area.get("alternate_bit"), f"{area_context}.alternate_bit")
            routing.append((0x80 if alternate else 0) | (area_type << 5) | target)

    specifications = _table_specs(parameters)
    tables = value.get("physics_tables")
    if not isinstance(tables, list) or len(tables) != len(specifications):
        raise SmbWorldError(f"physics_tables must contain {len(specifications)} entries")
    physics = bytearray()
    for index, (raw_table, specification) in enumerate(zip(tables, specifications)):
        context = f"physics_tables[{index}]"
        if not isinstance(raw_table, dict) or set(raw_table) != {"name", "signed", "values"}:
            raise SmbWorldError(f"{context} fields differ from contract")
        signed = bool(specification["signed"])
        if raw_table.get("name") != specification["name"] or raw_table.get("signed") is not signed:
            raise SmbWorldError(f"{context} identity differs from profile")
        values = raw_table.get("values")
        length = int(specification["length"])
        if not isinstance(values, list) or len(values) != length:
            raise SmbWorldError(f"{context}.values must contain {length} bytes")
        minimum, maximum = (-128, 127) if signed else (0, 255)
        physics.extend(
            _integer(item, f"{context}.values[{position}]", minimum=minimum, maximum=maximum) & 0xFF
            for position, item in enumerate(values)
        )
    return bytes(routing), bytes(physics)
