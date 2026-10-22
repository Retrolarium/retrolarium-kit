"""Typed Super Mario Bros. procedural area and enemy streams."""

from __future__ import annotations

from typing import Mapping, Sequence

from retrolarium_kit.core.authoring import AuthoringError


CODEC = "smb.levels.v1"
AREA_TYPES = ("water", "ground", "underground", "castle")


class SmbLevelError(AuthoringError):
    """An SMB level document or profile is malformed."""


def _integer(value: object, name: str, *, maximum: int = 0xFFFF) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= maximum:
        raise SmbLevelError(f"{name} must be an integer in 0..{maximum}")
    return value


def _boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise SmbLevelError(f"{name} must be a boolean")
    return value


def _byte_array(value: object, name: str) -> list[int]:
    if not isinstance(value, list):
        raise SmbLevelError(f"{name} must be a byte array")
    return [_integer(item, f"{name}[{index}]", maximum=0xFF) for index, item in enumerate(value)]


def _layout(parameters: Mapping[str, object]) -> tuple[list[str], list[int], list[int]]:
    names = parameters.get("stream_names")
    area = parameters.get("area_capacities")
    enemy = parameters.get("enemy_capacities")
    if not isinstance(names, list) or not names or not all(isinstance(item, str) and item for item in names):
        raise SmbLevelError("stream_names must be a non-empty string array")
    if len(set(names)) != len(names):
        raise SmbLevelError("stream_names must be unique")
    if not isinstance(area, list) or not isinstance(enemy, list):
        raise SmbLevelError("area_capacities and enemy_capacities must be arrays")
    if len(area) != len(names) or len(enemy) != len(names):
        raise SmbLevelError("stream capacity arrays must match stream_names")
    areas = [_integer(item, f"area_capacities[{index}]", maximum=0xFFFF) for index, item in enumerate(area)]
    enemies = [_integer(item, f"enemy_capacities[{index}]", maximum=0xFFFF) for index, item in enumerate(enemy)]
    if any(item < 3 for item in areas) or any(item < 1 for item in enemies):
        raise SmbLevelError("area capacities must be >= 3 and enemy capacities >= 1")
    return list(names), areas, enemies


def _split(payload: bytes, capacities: Sequence[int], name: str) -> list[bytes]:
    expected = sum(capacities)
    if len(payload) != expected:
        raise SmbLevelError(f"{name} range has {len(payload)} bytes, expected {expected}")
    result: list[bytes] = []
    offset = 0
    for capacity in capacities:
        result.append(payload[offset : offset + capacity])
        offset += capacity
    return result


def _area_terminator(chunk: bytes) -> int | None:
    for offset in range(2, len(chunk), 2):
        if chunk[offset] == 0xFD:
            return offset
    return None


def _enemy_terminator(chunk: bytes) -> int | None:
    offset = 0
    while offset < len(chunk):
        if chunk[offset] == 0xFF:
            return offset
        if offset + 1 >= len(chunk):
            return None
        offset += 3 if (chunk[offset] & 0x0F) == 0x0E else 2
    return None


def _decode_area(chunk: bytes, name: str) -> dict[str, object]:
    terminator = _area_terminator(chunk)
    implicit = terminator is None
    used = len(chunk) if implicit else terminator + 1
    stream = chunk + (b"\xfd" if implicit else b"")
    if len(stream) < 3 or stream[(used if implicit else terminator)] != 0xFD:
        raise SmbLevelError(f"area stream {name!r} has no valid terminator")
    if (used - (0 if implicit else 1) - 2) % 2:
        raise SmbLevelError(f"area stream {name!r} ends inside an object record")
    header0, header1 = stream[0], stream[1]
    objects = []
    end = len(chunk) if implicit else terminator
    for offset in range(2, end, 2):
        position, control = stream[offset : offset + 2]
        objects.append(
            {
                "column": position >> 4,
                "row": position & 0x0F,
                "page_advance": bool(control & 0x80),
                "object_control": control & 0x7F,
            }
        )
    return {
        "header": {
            "timer_setting": header0 >> 6,
            "entrance_control": (header0 >> 3) & 0x07,
            "foreground_or_color": header0 & 0x07,
            "area_style": header1 >> 6,
            "background_scenery": (header1 >> 4) & 0x03,
            "terrain_control": header1 & 0x0F,
        },
        "objects": objects,
        "implicit_terminator": implicit,
        "padding": [] if implicit else list(chunk[used:]),
    }


def _decode_enemies(chunk: bytes, name: str) -> dict[str, object]:
    terminator = _enemy_terminator(chunk)
    implicit = terminator is None
    end = len(chunk) if implicit else terminator
    records = []
    offset = 0
    while offset < end:
        if offset + 1 >= end:
            raise SmbLevelError(f"enemy stream {name!r} ends inside a record")
        position, control = chunk[offset], chunk[offset + 1]
        row = position & 0x0F
        kind = "entrance" if row == 0x0E else "page" if row == 0x0F else "enemy"
        record: dict[str, object] = {
            "kind": kind,
            "column": position >> 4,
            "row": row,
            "page_advance": bool(control & 0x80),
            "hard_mode": bool(control & 0x40),
            "object_or_page": control & 0x3F,
        }
        offset += 2
        if kind == "entrance":
            if offset >= end:
                raise SmbLevelError(f"enemy stream {name!r} has a truncated entrance")
            destination = chunk[offset]
            record["destination_world"] = destination >> 5
            record["destination_page"] = destination & 0x1F
            offset += 1
        records.append(record)
    return {
        "records": records,
        "implicit_terminator": implicit,
        "padding": [] if implicit else list(chunk[terminator + 1 :]),
    }


def positioned_area_objects(objects: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    """Attach engine page and absolute metatile X positions to area objects."""
    page = 0
    result = []
    for index, item in enumerate(objects):
        if bool(item.get("page_advance")):
            page += 1
        row = int(item["row"])
        control = int(item["object_control"])
        if row == 0x0D and not control & 0x40:
            page = control & 0x1F
        result.append({**item, "index": index, "page": page, "x": page * 16 + int(item["column"])})
    return result


def positioned_enemy_records(records: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    """Attach engine page and absolute metatile X positions to enemy commands."""
    page = 0
    result = []
    for index, item in enumerate(records):
        if bool(item.get("page_advance")):
            page += 1
        if item.get("kind") == "page":
            page = int(item["object_or_page"]) & 0x1F
        result.append({**item, "index": index, "page": page, "x": page * 16 + int(item["column"])})
    return result


def decode_levels(area_payload: bytes, enemy_payload: bytes, parameters: Mapping[str, object]) -> dict[str, object]:
    """Decode all parallel fixed-capacity SMB area/enemy streams."""
    names, area_capacities, enemy_capacities = _layout(parameters)
    areas = _split(area_payload, area_capacities, "area")
    enemies = _split(enemy_payload, enemy_capacities, "enemy")
    result = []
    for name, area_capacity, enemy_capacity, area, enemy in zip(
        names, area_capacities, enemy_capacities, areas, enemies
    ):
        area_value = _decode_area(area, name)
        enemy_value = _decode_enemies(enemy, name)
        result.append(
            {
                "name": name,
                "area_capacity": area_capacity,
                "enemy_capacity": enemy_capacity,
                "header": area_value["header"],
                "objects": area_value["objects"],
                "area_implicit_terminator": area_value["implicit_terminator"],
                "area_padding": area_value["padding"],
                "enemies": enemy_value["records"],
                "enemy_implicit_terminator": enemy_value["implicit_terminator"],
                "enemy_padding": enemy_value["padding"],
            }
        )
    return {"area_types": list(AREA_TYPES), "areas": result}


def _area_stream(value: Mapping[str, object], name: str) -> bytes:
    """The area stream of a document area: header, objects, terminator."""
    header = value.get("header")
    if not isinstance(header, dict) or set(header) != {
        "timer_setting",
        "entrance_control",
        "foreground_or_color",
        "area_style",
        "background_scenery",
        "terrain_control",
    }:
        raise SmbLevelError(f"area {name!r} header fields differ from contract")
    header0 = _integer(header["timer_setting"], f"{name}.timer_setting", maximum=3) << 6
    header0 |= _integer(header["entrance_control"], f"{name}.entrance_control", maximum=7) << 3
    header0 |= _integer(header["foreground_or_color"], f"{name}.foreground_or_color", maximum=7)
    header1 = _integer(header["area_style"], f"{name}.area_style", maximum=3) << 6
    header1 |= _integer(header["background_scenery"], f"{name}.background_scenery", maximum=3) << 4
    header1 |= _integer(header["terrain_control"], f"{name}.terrain_control", maximum=15)
    output = bytearray((header0, header1))
    objects = value.get("objects")
    if not isinstance(objects, list):
        raise SmbLevelError(f"area {name!r} objects must be an array")
    for index, raw in enumerate(objects):
        context = f"{name}.objects[{index}]"
        if not isinstance(raw, dict) or set(raw) != {"column", "row", "page_advance", "object_control"}:
            raise SmbLevelError(f"{context} fields differ from contract")
        first = (_integer(raw["column"], f"{context}.column", maximum=15) << 4) | _integer(
            raw["row"], f"{context}.row", maximum=15
        )
        # The game ends an area's objects at a first byte of $FD: column 15,
        # row 13 cannot be written.
        if first == 0xFD:
            raise SmbLevelError(f"{context}: column 15, row 13 reads as the end of the objects")
        output.append(first)
        output.append(
            (0x80 if _boolean(raw["page_advance"], f"{context}.page_advance") else 0)
            | _integer(raw["object_control"], f"{context}.object_control", maximum=127)
        )
    output.append(0xFD)
    return bytes(output)


def _encode_area(value: Mapping[str, object], name: str, capacity: int) -> bytes:
    output = _area_stream(value, name)
    implicit = _boolean(value.get("area_implicit_terminator"), f"{name}.area_implicit_terminator")
    padding = _byte_array(value.get("area_padding"), f"{name}.area_padding")
    if implicit:
        if len(output) != capacity + 1:
            raise SmbLevelError(f"implicit area stream {name!r} must consume exactly {capacity} bytes")
        return bytes(output[:-1])
    if len(output) > capacity:
        raise SmbLevelError(f"area stream {name!r} exceeds its {capacity}-byte capacity")
    suffix = bytes(padding[-(capacity - len(output)) :]) if capacity > len(output) else b""
    return bytes(output) + bytes(capacity - len(output) - len(suffix)) + suffix


def _enemy_stream(value: Mapping[str, object], name: str) -> bytes:
    """The enemy stream of a document area: records, terminator."""
    records = value.get("enemies")
    if not isinstance(records, list):
        raise SmbLevelError(f"area {name!r} enemies must be an array")
    output = bytearray()
    for index, raw in enumerate(records):
        context = f"{name}.enemies[{index}]"
        if not isinstance(raw, dict):
            raise SmbLevelError(f"{context} must be an object")
        kind = raw.get("kind")
        base_fields = {"kind", "column", "row", "page_advance", "hard_mode", "object_or_page"}
        expected = base_fields | ({"destination_world", "destination_page"} if kind == "entrance" else set())
        if set(raw) != expected or kind not in {"enemy", "entrance", "page"}:
            raise SmbLevelError(f"{context} fields differ from contract")
        row = _integer(raw["row"], f"{context}.row", maximum=15)
        expected_kind = "entrance" if row == 14 else "page" if row == 15 else "enemy"
        if kind != expected_kind:
            raise SmbLevelError(f"{context}.kind does not match its row")
        first = (_integer(raw["column"], f"{context}.column", maximum=15) << 4) | row
        # The game ends an area's enemies at a first byte of $FF: column 15,
        # row 15 cannot be written.
        if first == 0xFF:
            raise SmbLevelError(f"{context}: column 15, row 15 reads as the end of the enemies")
        output.append(first)
        output.append(
            (0x80 if _boolean(raw["page_advance"], f"{context}.page_advance") else 0)
            | (0x40 if _boolean(raw["hard_mode"], f"{context}.hard_mode") else 0)
            | _integer(raw["object_or_page"], f"{context}.object_or_page", maximum=63)
        )
        if kind == "entrance":
            output.append(
                (_integer(raw["destination_world"], f"{context}.destination_world", maximum=7) << 5)
                | _integer(raw["destination_page"], f"{context}.destination_page", maximum=31)
            )
    output.append(0xFF)
    return bytes(output)


def _encode_enemies(value: Mapping[str, object], name: str, capacity: int) -> bytes:
    output = _enemy_stream(value, name)
    implicit = _boolean(value.get("enemy_implicit_terminator"), f"{name}.enemy_implicit_terminator")
    padding = _byte_array(value.get("enemy_padding"), f"{name}.enemy_padding")
    if implicit:
        if len(output) != capacity + 1:
            raise SmbLevelError(f"implicit enemy stream {name!r} must consume exactly {capacity} bytes")
        return bytes(output[:-1])
    if len(output) > capacity:
        raise SmbLevelError(f"enemy stream {name!r} exceeds its {capacity}-byte capacity")
    suffix = bytes(padding[-(capacity - len(output)) :]) if capacity > len(output) else b""
    return bytes(output) + bytes(capacity - len(output) - len(suffix)) + suffix


# The engine indexes an area or enemy stream with an 8-bit Y register.
MAX_STREAM_BYTES = 256

_POOL_FIELDS = {
    "cpu_bias",
    "area_pointer_low",
    "area_pointer_high",
    "enemy_pointer_low",
    "enemy_pointer_high",
    "area_pointer_streams",
    "enemy_pointer_streams",
}


def stream_pool(parameters: Mapping[str, object]) -> dict[str, object] | None:
    """The pooled layout of a profile, or None for fixed stream capacities.

    A pooled profile names the engine's area and enemy pointer tables (low
    and high byte arrays at file offsets), which stream each entry points
    at, and the bias from a file offset to its CPU address. Streams are then
    packed one after another into the whole range, so bytes freed in one area
    serve any other, and the pointer tables are rewritten to match.
    """
    value = parameters.get("stream_pool")
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != _POOL_FIELDS:
        raise SmbLevelError("stream_pool fields differ from contract")
    if "area_file_offset" not in parameters or "enemy_file_offset" not in parameters:
        raise SmbLevelError("stream_pool needs one contiguous area and enemy range")
    names, _areas, _enemies = _layout(parameters)
    result: dict[str, object] = {}
    for key in ("cpu_bias", "area_pointer_low", "area_pointer_high", "enemy_pointer_low", "enemy_pointer_high"):
        result[key] = _integer(value[key], f"stream_pool.{key}", maximum=0x7FFFFFFF)
    for key in ("area_pointer_streams", "enemy_pointer_streams"):
        entries = value[key]
        if not isinstance(entries, list) or not entries:
            raise SmbLevelError(f"stream_pool.{key} must be a non-empty array")
        result[key] = [
            _integer(item, f"stream_pool.{key}[{index}]", maximum=len(names) - 1) for index, item in enumerate(entries)
        ]
    return result


def _pack(
    streams: Sequence[bytes],
    implicit: Sequence[bool],
    paddings: Sequence[Sequence[int]],
    terminator: int,
    size: int,
    label: str,
) -> tuple[bytes, list[int]]:
    """Pack streams in their original order into a range of `size` bytes.

    A stream marked implicit leaves out its terminator while the next stream
    starts with that byte, as the original layout shares it. Declared padding
    stays after its stream while everything fits, so unedited levels keep
    their bytes; the free rest of the range is filled with $FF.
    """

    def build(with_padding: bool) -> tuple[bytearray, list[int]]:
        output = bytearray()
        starts = []
        for index, stream in enumerate(streams):
            starts.append(len(output))
            following = streams[index + 1] if index + 1 < len(streams) else b""
            if implicit[index] and following[:1] == bytes((terminator,)):
                output.extend(stream[:-1])
                continue
            output.extend(stream)
            if with_padding:
                output.extend(paddings[index])
        return output, starts

    output, starts = build(True)
    if len(output) > size:
        output, starts = build(False)
    if len(output) > size:
        raise SmbLevelError(f"the {label} streams of all areas need {len(output)} bytes; the ROM keeps {size} for them")
    return bytes(output) + b"\xff" * (size - len(output)), starts


def _encode(
    value: Mapping[str, object], parameters: Mapping[str, object]
) -> tuple[bytes, bytes, tuple[tuple[int, bytes], ...]]:
    if set(value) != {"area_types", "areas"} or value.get("area_types") != list(AREA_TYPES):
        raise SmbLevelError("SMB level resource fields differ from its codec contract")
    names, area_capacities, enemy_capacities = _layout(parameters)
    areas = value.get("areas")
    if not isinstance(areas, list) or len(areas) != len(names):
        raise SmbLevelError(f"areas must contain {len(names)} entries")
    pool = stream_pool(parameters)
    area_output = bytearray()
    enemy_output = bytearray()
    area_streams: list[bytes] = []
    enemy_streams: list[bytes] = []
    area_implicit: list[bool] = []
    enemy_implicit: list[bool] = []
    area_paddings: list[list[int]] = []
    enemy_paddings: list[list[int]] = []
    required = {
        "name",
        "area_capacity",
        "enemy_capacity",
        "header",
        "objects",
        "area_implicit_terminator",
        "area_padding",
        "enemies",
        "enemy_implicit_terminator",
        "enemy_padding",
    }
    for index, (raw, name, area_capacity, enemy_capacity) in enumerate(
        zip(areas, names, area_capacities, enemy_capacities)
    ):
        if not isinstance(raw, dict) or set(raw) != required:
            raise SmbLevelError(f"areas[{index}] fields differ from contract")
        if (
            raw.get("name") != name
            or raw.get("area_capacity") != area_capacity
            or raw.get("enemy_capacity") != enemy_capacity
        ):
            raise SmbLevelError(f"areas[{index}] identity differs from profile")
        if pool is None:
            area_output.extend(_encode_area(raw, name, area_capacity))
            enemy_output.extend(_encode_enemies(raw, name, enemy_capacity))
            continue
        area_stream = _area_stream(raw, name)
        enemy_stream = _enemy_stream(raw, name)
        for kind, stream in (("area", area_stream), ("enemy", enemy_stream)):
            if len(stream) > MAX_STREAM_BYTES:
                raise SmbLevelError(
                    f"{kind} stream {name!r} is {len(stream)} bytes; the engine reads at most {MAX_STREAM_BYTES}"
                )
        area_streams.append(area_stream)
        enemy_streams.append(enemy_stream)
        area_implicit.append(_boolean(raw.get("area_implicit_terminator"), f"{name}.area_implicit_terminator"))
        enemy_implicit.append(_boolean(raw.get("enemy_implicit_terminator"), f"{name}.enemy_implicit_terminator"))
        area_paddings.append(_byte_array(raw.get("area_padding"), f"{name}.area_padding"))
        enemy_paddings.append(_byte_array(raw.get("enemy_padding"), f"{name}.enemy_padding"))
    if pool is None:
        return bytes(area_output), bytes(enemy_output), ()

    area_size = _integer(parameters.get("area_size"), "area_size")
    enemy_size = _integer(parameters.get("enemy_size"), "enemy_size")
    area, area_starts = _pack(area_streams, area_implicit, area_paddings, 0xFD, area_size, "object")
    enemy, enemy_starts = _pack(enemy_streams, enemy_implicit, enemy_paddings, 0xFF, enemy_size, "enemy")
    bias = int(pool["cpu_bias"])

    def table(base: int, starts: list[int], entries: list[int], shift: int) -> bytes:
        return bytes(((base + starts[stream] + bias) >> shift) & 0xFF for stream in entries)

    area_base = _integer(parameters.get("area_file_offset"), "area_file_offset", maximum=0x7FFFFFFF)
    enemy_base = _integer(parameters.get("enemy_file_offset"), "enemy_file_offset", maximum=0x7FFFFFFF)
    area_entries = list(pool["area_pointer_streams"])
    enemy_entries = list(pool["enemy_pointer_streams"])
    pointers = (
        (int(pool["area_pointer_low"]), table(area_base, area_starts, area_entries, 0)),
        (int(pool["area_pointer_high"]), table(area_base, area_starts, area_entries, 8)),
        (int(pool["enemy_pointer_low"]), table(enemy_base, enemy_starts, enemy_entries, 0)),
        (int(pool["enemy_pointer_high"]), table(enemy_base, enemy_starts, enemy_entries, 8)),
    )
    return area, enemy, pointers


def encode_levels(value: Mapping[str, object], parameters: Mapping[str, object]) -> tuple[bytes, bytes]:
    """Validate and encode all level streams, preserving declared padding.

    With a stream pool the streams are packed into the whole ranges instead
    of each area's own capacity; encode_level_pointers gives the pointer
    tables that go with them.
    """
    area, enemy, _pointers = _encode(value, parameters)
    return area, enemy


def encode_level_pointers(
    value: Mapping[str, object], parameters: Mapping[str, object]
) -> tuple[tuple[int, bytes], ...]:
    """The rewritten (file offset, bytes) pointer tables of a pooled layout."""
    return _encode(value, parameters)[2]
