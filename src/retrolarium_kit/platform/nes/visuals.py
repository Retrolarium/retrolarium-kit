"""Typed fixed NES metatile tables and named PPU palette packets."""

from __future__ import annotations

from typing import Mapping

from retrolarium_kit.core.authoring import AuthoringError


METATILE_CODEC = "nes.metatile-2x2.v1"
PALETTE_PACKET_CODEC = "nes.ppu-palette-packets.v1"


class NesVisualError(AuthoringError):
    """A metatile or palette-packet resource is malformed."""


def _integer(value: object, name: str, *, minimum: int = 0, maximum: int = 0xFFFF) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise NesVisualError(f"{name} must be an integer in {minimum}..{maximum}")
    return value


def _string_array(value: object, name: str) -> list[str]:
    if not isinstance(value, list) or not value or not all(isinstance(item, str) and item for item in value):
        raise NesVisualError(f"{name} must be a non-empty string array")
    if len(set(value)) != len(value):
        raise NesVisualError(f"{name} must contain unique values")
    return list(value)


def _integer_array(value: object, name: str, *, minimum: int = 0, maximum: int = 0xFFFF) -> list[int]:
    if not isinstance(value, list) or not value:
        raise NesVisualError(f"{name} must be a non-empty integer array")
    return [_integer(item, f"{name}[{index}]", minimum=minimum, maximum=maximum) for index, item in enumerate(value)]


def _metatile_layout(parameters: Mapping[str, object]) -> tuple[list[str], list[int], int]:
    names = _string_array(parameters.get("group_names"), "group_names")
    sizes = _integer_array(parameters.get("group_sizes"), "group_sizes", minimum=1)
    if len(names) != len(sizes):
        raise NesVisualError("group_names and group_sizes must have equal lengths")
    tile_base = _integer(parameters.get("tile_base", 0), "tile_base", maximum=0xFFFF)
    return names, sizes, tile_base


def decode_metatiles(payload: bytes, parameters: Mapping[str, object]) -> dict[str, object]:
    """Decode fixed four-byte 2x2 records grouped by palette selector."""
    names, sizes, tile_base = _metatile_layout(parameters)
    expected = sum(sizes) * 4
    if len(payload) != expected:
        raise NesVisualError(f"metatile range has {len(payload)} bytes, expected {expected}")
    records = [list(payload[offset : offset + 4]) for offset in range(0, expected, 4)]
    return {
        "group_names": names,
        "group_sizes": sizes,
        "tile_base": tile_base,
        "records": records,
    }


def encode_metatiles(value: Mapping[str, object], parameters: Mapping[str, object]) -> bytes:
    """Validate and encode grouped 2x2 metatile records."""
    names, sizes, tile_base = _metatile_layout(parameters)
    if set(value) != {"group_names", "group_sizes", "tile_base", "records"}:
        raise NesVisualError("metatile resource fields differ from its codec contract")
    if value.get("group_names") != names or value.get("group_sizes") != sizes:
        raise NesVisualError("metatile groups differ from the profile")
    if value.get("tile_base") != tile_base:
        raise NesVisualError("metatile tile_base differs from the profile")
    records = value.get("records")
    expected = sum(sizes)
    if not isinstance(records, list) or len(records) != expected:
        raise NesVisualError(f"records must contain {expected} metatiles")
    output = bytearray()
    for record_index, record in enumerate(records):
        if not isinstance(record, list) or len(record) != 4:
            raise NesVisualError(f"records[{record_index}] must contain four tile indexes")
        output.extend(
            _integer(item, f"records[{record_index}][{index}]", maximum=0xFF) for index, item in enumerate(record)
        )
    return bytes(output)


def _packet_layout(
    parameters: Mapping[str, object],
) -> tuple[list[str], list[int], int, int, int]:
    names = _string_array(parameters.get("packet_names"), "packet_names")
    capacities = _integer_array(parameters.get("packet_capacities"), "packet_capacities", minimum=5)
    if len(names) != len(capacities):
        raise NesVisualError("packet_names and packet_capacities must have equal lengths")
    terminator = _integer(parameters.get("terminator", 0), "terminator", maximum=0xFF)
    day = _integer(parameters.get("day_background", 0), "day_background", maximum=0x3F)
    night = _integer(parameters.get("night_background", 0x0F), "night_background", maximum=0x3F)
    return names, capacities, terminator, day, night


def decode_palette_packets(payload: bytes, parameters: Mapping[str, object]) -> dict[str, object]:
    """Decode independently named, fixed-capacity NES PPU palette packets."""
    names, capacities, terminator, day_background, night_background = _packet_layout(parameters)
    if len(payload) != sum(capacities):
        raise NesVisualError(f"palette packet range has {len(payload)} bytes, expected {sum(capacities)}")
    packets = []
    offset = 0
    for name, capacity in zip(names, capacities):
        chunk = payload[offset : offset + capacity]
        offset += capacity
        if chunk[-1] != terminator:
            raise NesVisualError(f"palette packet {name!r} has no terminator")
        address = (chunk[0] << 8) | chunk[1]
        control = chunk[2]
        length = control & 0x3F
        repeat = bool(control & 0x40)
        payload_length = 1 if repeat else length
        if payload_length + 4 != capacity:
            raise NesVisualError(f"palette packet {name!r} length differs from its capacity")
        values = list(chunk[3 : 3 + payload_length])
        if any(value > 0x3F for value in values):
            raise NesVisualError(f"palette packet {name!r} uses a color outside 0..63")
        packets.append(
            {
                "name": name,
                "address": address,
                "vertical": bool(control & 0x80),
                "repeat": repeat,
                "length": length,
                "values": values,
            }
        )
    return {
        "day_background": day_background,
        "night_background": night_background,
        "packets": packets,
    }


def encode_palette_packets(value: Mapping[str, object], parameters: Mapping[str, object]) -> bytes:
    """Validate and encode named fixed-capacity NES PPU palette packets."""
    names, capacities, terminator, day_background, night_background = _packet_layout(parameters)
    if set(value) != {"day_background", "night_background", "packets"}:
        raise NesVisualError("palette packet resource fields differ from its codec contract")
    if value.get("day_background") != day_background:
        raise NesVisualError("day_background differs from the profile")
    if value.get("night_background") != night_background:
        raise NesVisualError("night_background differs from the profile")
    packets = value.get("packets")
    if not isinstance(packets, list) or len(packets) != len(names):
        raise NesVisualError(f"packets must contain {len(names)} entries")
    output = bytearray()
    required = {"name", "address", "vertical", "repeat", "length", "values"}
    for index, (packet, name, capacity) in enumerate(zip(packets, names, capacities)):
        context = f"packets[{index}]"
        if not isinstance(packet, dict) or set(packet) != required:
            raise NesVisualError(f"{context} fields differ from the codec contract")
        if packet.get("name") != name:
            raise NesVisualError(f"{context}.name differs from the profile")
        address = _integer(packet.get("address"), f"{context}.address", maximum=0xFFFF)
        vertical = packet.get("vertical")
        repeat = packet.get("repeat")
        if not isinstance(vertical, bool) or not isinstance(repeat, bool):
            raise NesVisualError(f"{context} flags must be booleans")
        length = _integer(packet.get("length"), f"{context}.length", maximum=0x3F)
        values = packet.get("values")
        expected_values = 1 if repeat else length
        if not isinstance(values, list) or len(values) != expected_values:
            raise NesVisualError(f"{context}.values has the wrong length")
        colors = bytes(
            _integer(item, f"{context}.values[{value_index}]", maximum=0x3F) for value_index, item in enumerate(values)
        )
        packet_bytes = (
            bytes(
                (
                    address >> 8,
                    address & 0xFF,
                    (0x80 if vertical else 0) | (0x40 if repeat else 0) | length,
                )
            )
            + colors
            + bytes((terminator,))
        )
        if len(packet_bytes) != capacity:
            raise NesVisualError(f"{context} encoded size differs from its capacity")
        output.extend(packet_bytes)
    return bytes(output)
