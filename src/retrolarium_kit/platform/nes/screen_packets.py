"""Named fixed-capacity NES PPU display-list screens."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Mapping

from .authoring import AuthoringError
from .nes_text import TextEncoding, decode_text, parse_encoding


CODEC = "nes.ppu-screen-packets.v1"


class NesScreenPacketError(AuthoringError):
    """A PPU screen packet layout or authored command is invalid."""


@dataclass(frozen=True)
class ScreenSpan:
    key: str
    label: str
    file_offset: int
    size: int


@dataclass(frozen=True)
class ScreenGeometry:
    width: int
    height: int
    base_address: int
    attribute_address: int
    attribute_bytes: int
    fill_tile: int
    fill_attribute: int


@dataclass(frozen=True)
class ScreenTextField:
    key: str
    label: str
    screen: str
    start: int
    width: int

    def as_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "label": self.label,
            "screen": self.screen,
            "start": self.start,
            "width": self.width,
        }


@dataclass(frozen=True)
class PacketPool:
    """Screens packed one after another behind the engine's offset table.

    The screens' spans form one contiguous range; the engine finds each
    display list through an 8-bit offset table relative to `offset_base`,
    each entry naming a screen and the command it starts at (a variant may
    start past a leading command). Screens may then grow and shrink within
    the range, and the fields in `growable` may lengthen their text.
    """
    offset_table: int
    offset_base: int
    entries: tuple[tuple[str, int], ...]
    growable: tuple[str, ...]


def _integer(
    value: object, name: str, *, minimum: int = 0, maximum: int = 0xFF
) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not minimum <= value <= maximum
    ):
        raise NesScreenPacketError(
            f"{name} must be an integer in {minimum}..{maximum}"
        )
    return value


def _geometry(parameters: Mapping[str, object]) -> ScreenGeometry:
    geometry = ScreenGeometry(
        width=_integer(parameters.get("width"), "width", minimum=1, maximum=256),
        height=_integer(parameters.get("height"), "height", minimum=1, maximum=256),
        base_address=_integer(
            parameters.get("base_address"), "base_address",
            minimum=0x2000, maximum=0x3FFF,
        ),
        attribute_address=_integer(
            parameters.get("attribute_address"), "attribute_address",
            minimum=0x2000, maximum=0x3FFF,
        ),
        attribute_bytes=_integer(
            parameters.get("attribute_bytes"), "attribute_bytes", maximum=0x400
        ),
        fill_tile=_integer(parameters.get("fill_tile"), "fill_tile"),
        fill_attribute=_integer(
            parameters.get("fill_attribute"), "fill_attribute"
        ),
    )
    tile_end = geometry.base_address + geometry.width * geometry.height
    attribute_end = geometry.attribute_address + geometry.attribute_bytes
    if tile_end > 0x4000 or attribute_end > 0x4000:
        raise NesScreenPacketError("screen geometry exceeds PPU memory")
    tile_range = set(range(geometry.base_address, tile_end))
    if tile_range.intersection(
        range(geometry.attribute_address, attribute_end)
    ):
        raise NesScreenPacketError("screen tile and attribute ranges overlap")
    return geometry


def _layout(
    parameters: Mapping[str, object],
) -> tuple[tuple[ScreenSpan, ...], int, ScreenGeometry]:
    raw_screens = parameters.get("screens")
    if not isinstance(raw_screens, list) or not raw_screens:
        raise NesScreenPacketError("screens must be a non-empty array")
    screens = []
    keys: set[str] = set()
    occupied: set[int] = set()
    for index, raw in enumerate(raw_screens):
        context = f"screens[{index}]"
        if not isinstance(raw, Mapping) or set(raw) != {
            "key", "label", "file_offset", "size"
        }:
            raise NesScreenPacketError(f"{context} has the wrong shape")
        key = raw.get("key")
        label = raw.get("label")
        if not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9-]*", key):
            raise NesScreenPacketError(f"{context}.key is invalid")
        if key in keys:
            raise NesScreenPacketError(f"duplicate screen key: {key}")
        if not isinstance(label, str) or not label:
            raise NesScreenPacketError(f"{context}.label must be non-empty")
        offset = _integer(
            raw.get("file_offset"), f"{context}.file_offset", maximum=0x7FFFFFFF
        )
        size = _integer(raw.get("size"), f"{context}.size", minimum=1,
                        maximum=0x10000)
        positions = set(range(offset, offset + size))
        if occupied.intersection(positions):
            raise NesScreenPacketError(f"screen {key} overlaps another screen")
        occupied.update(positions)
        keys.add(key)
        screens.append(ScreenSpan(key, label, offset, size))
    terminator = _integer(parameters.get("terminator", 0), "terminator")
    return tuple(screens), terminator, _geometry(parameters)


def _text_layout(
    parameters: Mapping[str, object], spans: tuple[ScreenSpan, ...],
    geometry: ScreenGeometry,
) -> tuple[TextEncoding | None, tuple[ScreenTextField, ...]]:
    has_encoding = "encoding" in parameters
    has_fields = "text_fields" in parameters
    if not has_encoding and not has_fields:
        return None, ()
    if not has_encoding or not has_fields:
        raise NesScreenPacketError(
            "screen text requires both encoding and text_fields"
        )
    try:
        encoding = parse_encoding(parameters)
    except AuthoringError as exc:
        raise NesScreenPacketError(str(exc)) from exc
    raw_fields = parameters.get("text_fields")
    if not isinstance(raw_fields, list) or not raw_fields:
        raise NesScreenPacketError("text_fields must be a non-empty array")
    screen_keys = {span.key for span in spans}
    keys: set[str] = set()
    occupied: set[tuple[str, int]] = set()
    fields = []
    for index, raw in enumerate(raw_fields):
        context = f"text_fields[{index}]"
        if not isinstance(raw, Mapping) or set(raw) != {
            "key", "label", "screen", "address", "width"
        }:
            raise NesScreenPacketError(f"{context} has the wrong shape")
        key = raw.get("key")
        label = raw.get("label")
        screen = raw.get("screen")
        if not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9-]*", key):
            raise NesScreenPacketError(f"{context}.key is invalid")
        if key in keys:
            raise NesScreenPacketError(f"duplicate text field key: {key}")
        if not isinstance(label, str) or not label:
            raise NesScreenPacketError(f"{context}.label must be non-empty")
        if not isinstance(screen, str) or screen not in screen_keys:
            raise NesScreenPacketError(f"{context}.screen is unknown")
        address = _integer(
            raw.get("address"), f"{context}.address",
            minimum=geometry.base_address, maximum=0x3FFF,
        )
        width = _integer(raw.get("width"), f"{context}.width",
                         minimum=1, maximum=0x100)
        start = _location(address, geometry)
        end = _location(address + width - 1, geometry)
        if (
            start is None or end is None
            or start[0] != "tile" or end[0] != "tile"
            or start[1] // geometry.width != end[1] // geometry.width
        ):
            raise NesScreenPacketError(
                f"{context} must fit one visible tile row"
            )
        positions = {(screen, start[1] + offset) for offset in range(width)}
        if occupied.intersection(positions):
            raise NesScreenPacketError(f"text field {key} overlaps another field")
        occupied.update(positions)
        keys.add(key)
        fields.append(ScreenTextField(key, label, screen, start[1], width))
    return encoding, tuple(fields)


def _packet_pool(
    parameters: Mapping[str, object], spans: tuple[ScreenSpan, ...],
    fields: tuple[ScreenTextField, ...],
) -> PacketPool | None:
    value = parameters.get("packet_pool")
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != {
        "offset_table", "offset_base", "entries", "growable_fields"
    }:
        raise NesScreenPacketError("packet_pool fields differ from contract")
    for first, second in zip(spans, spans[1:]):
        if first.file_offset + first.size != second.file_offset:
            raise NesScreenPacketError("packet_pool screens must be contiguous")
    keys = {span.key for span in spans}
    raw_entries = value.get("entries")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise NesScreenPacketError("packet_pool.entries must be a non-empty array")
    entries = []
    for index, raw in enumerate(raw_entries):
        if not isinstance(raw, Mapping) or set(raw) != {"screen", "command"} \
                or raw.get("screen") not in keys:
            raise NesScreenPacketError(f"packet_pool.entries[{index}] is invalid")
        entries.append((str(raw["screen"]), _integer(
            raw.get("command"), f"packet_pool.entries[{index}].command", maximum=0x3F
        )))
    growable = value.get("growable_fields")
    field_keys = {field.key for field in fields}
    if not isinstance(growable, list) or not all(item in field_keys for item in growable):
        raise NesScreenPacketError("packet_pool.growable_fields must name text fields")
    return PacketPool(
        _integer(value.get("offset_table"), "packet_pool.offset_table", maximum=0x7FFFFFFF),
        _integer(value.get("offset_base"), "packet_pool.offset_base", maximum=0x7FFFFFFF),
        tuple(entries),
        tuple(str(item) for item in growable),
    )


def _grown_fields(
    raw_fields: object, fields: tuple[ScreenTextField, ...], pool: PacketPool,
    screens: list[Mapping[str, object]], geometry: ScreenGeometry,
) -> tuple[ScreenTextField, ...]:
    """The document's text fields: the profile's, with growable widths.

    A growable field may take another width when it is exactly the run of
    one non-repeated command across a row, so lengthening the text means
    lengthening that command.
    """
    if not isinstance(raw_fields, list) or len(raw_fields) != len(fields):
        raise NesScreenPacketError("screen text fields differ from the profile")
    by_key = {str(screen.get("key")): screen for screen in screens}
    result = []
    for raw, field in zip(raw_fields, fields):
        if not isinstance(raw, Mapping):
            raise NesScreenPacketError("screen text fields differ from the profile")
        expected = field.as_dict()
        if field.key not in pool.growable or raw == expected:
            if raw != expected:
                raise NesScreenPacketError(f"screen text field {field.key} differs from the profile")
            result.append(field)
            continue
        if {key: raw.get(key) for key in expected if key != "width"} != {
            key: value for key, value in expected.items() if key != "width"
        } or set(raw) != set(expected):
            raise NesScreenPacketError(f"screen text field {field.key} differs from the profile")
        width = _integer(raw.get("width"), f"text field {field.key}.width", minimum=1, maximum=0x3F)
        if field.start % geometry.width + width > geometry.width:
            raise NesScreenPacketError(f"text field {field.key} runs past its row")
        commands = by_key[field.screen].get("commands")
        address = geometry.base_address + field.start
        owned = isinstance(commands, list) and any(
            isinstance(command, Mapping) and command.get("address") == address
            and command.get("length") == width and command.get("vertical") is False
            and command.get("repeat") is False
            for command in commands
        )
        if not owned:
            raise NesScreenPacketError(
                f"text field {field.key} can only grow as the whole write it starts"
            )
        result.append(ScreenTextField(field.key, field.label, field.screen, field.start, width))
    return tuple(result)


def _validate_text_tiles(
    screens: list[Mapping[str, object]], fields: tuple[ScreenTextField, ...],
    encoding: TextEncoding, geometry: ScreenGeometry,
) -> None:
    if not all(isinstance(screen, Mapping) for screen in screens):
        raise NesScreenPacketError("screens must contain objects")
    by_key = {str(screen.get("key")): screen for screen in screens}
    for field in fields:
        tiles = _tile_matrix(by_key[field.screen].get("tiles"), geometry, field.screen)
        decode_text(
            bytes(tiles[field.start : field.start + field.width]),
            encoding,
            field.key,
        )


def _target_addresses(command: Mapping[str, object]) -> list[int]:
    address = int(command["address"])
    step = 32 if command["vertical"] else 1
    targets = [address + step * index for index in range(int(command["length"]))]
    if any(target > 0x3FFF for target in targets):
        raise NesScreenPacketError("screen command crosses PPU memory")
    return targets


def _location(
    address: int, geometry: ScreenGeometry
) -> tuple[str, int] | None:
    tile_count = geometry.width * geometry.height
    if geometry.base_address <= address < geometry.base_address + tile_count:
        return "tile", address - geometry.base_address
    if (
        geometry.attribute_address
        <= address
        < geometry.attribute_address + geometry.attribute_bytes
    ):
        return "attribute", address - geometry.attribute_address
    return None


def _project(
    commands: list[dict[str, object]], geometry: ScreenGeometry
) -> tuple[list[int], list[int], dict[tuple[str, int], tuple[int, int]]]:
    tiles = [geometry.fill_tile] * (geometry.width * geometry.height)
    attributes = [geometry.fill_attribute] * geometry.attribute_bytes
    writers: dict[tuple[str, int], tuple[int, int]] = {}
    for command_index, command in enumerate(commands):
        values = command["values"]
        assert isinstance(values, list)
        for target_index, address in enumerate(_target_addresses(command)):
            location = _location(address, geometry)
            if location is None:
                continue
            value_index = 0 if command["repeat"] else target_index
            value = int(values[value_index])
            if location[0] == "tile":
                tiles[location[1]] = value
            else:
                attributes[location[1]] = value
            writers[location] = (command_index, value_index)
    return tiles, attributes, writers


def _tile_matrix(value: object, geometry: ScreenGeometry, name: str) -> list[int]:
    if not isinstance(value, list) or len(value) != geometry.height:
        raise NesScreenPacketError(f"screen {name}.tiles has the wrong height")
    result = []
    for row_index, row in enumerate(value):
        if not isinstance(row, list) or len(row) != geometry.width:
            raise NesScreenPacketError(
                f"screen {name}.tiles[{row_index}] has the wrong width"
            )
        result.extend(
            _integer(tile, f"screen {name}.tiles[{row_index}][{column}]")
            for column, tile in enumerate(row)
        )
    return result


def _decode_commands(payload: bytes, terminator: int, name: str) -> dict[str, object]:
    commands = []
    offset = 0
    while offset < len(payload):
        if payload[offset] == terminator:
            return {
                "commands": commands,
                "trailing": list(payload[offset + 1 :]),
            }
        if offset + 3 > len(payload):
            raise NesScreenPacketError(f"screen {name} has a truncated command header")
        address = (payload[offset] << 8) | payload[offset + 1]
        if not 0x2000 <= address <= 0x3FFF:
            raise NesScreenPacketError(
                f"screen {name} command address is outside PPU memory"
            )
        control = payload[offset + 2]
        length = control & 0x3F
        if length == 0:
            raise NesScreenPacketError(f"screen {name} command has zero length")
        repeat = bool(control & 0x40)
        value_count = 1 if repeat else length
        end = offset + 3 + value_count
        if end > len(payload):
            raise NesScreenPacketError(f"screen {name} has truncated command values")
        commands.append({
            "address": address,
            "vertical": bool(control & 0x80),
            "repeat": repeat,
            "length": length,
            "values": list(payload[offset + 3 : end]),
        })
        offset = end
    raise NesScreenPacketError(f"screen {name} has no terminator")


def decode_screen_packets(
    source: bytes, parameters: Mapping[str, object]
) -> dict[str, object]:
    """Decode named disjoint PPU display lists without losing fixed padding."""
    spans, terminator, geometry = _layout(parameters)
    encoding, text_fields = _text_layout(parameters, spans, geometry)
    screens = []
    for span in spans:
        payload = source[span.file_offset : span.file_offset + span.size]
        if len(payload) != span.size:
            raise NesScreenPacketError(f"screen {span.key} exceeds the source image")
        decoded = _decode_commands(payload, terminator, span.key)
        commands = decoded["commands"]
        assert isinstance(commands, list)
        tiles, attributes, _writers = _project(commands, geometry)
        screens.append({
            "key": span.key,
            "label": span.label,
            "capacity": span.size,
            "width": geometry.width,
            "height": geometry.height,
            "tiles": [
                tiles[row * geometry.width : (row + 1) * geometry.width]
                for row in range(geometry.height)
            ],
            "attributes": attributes,
            **decoded,
        })
    result: dict[str, object] = {"screens": screens}
    if encoding is not None:
        _validate_text_tiles(screens, text_fields, encoding, geometry)
        result["encoding"] = encoding.as_dict()
        result["text_fields"] = [field.as_dict() for field in text_fields]
    pool = _packet_pool(parameters, spans, text_fields)
    if pool is not None:
        # Editors size screens against the shared range and may grow these.
        result["capacity_pool"] = True
        result["growable_fields"] = list(pool.growable)
    return result


def _encode_commands(
    screen: Mapping[str, object], span: ScreenSpan, terminator: int,
    geometry: ScreenGeometry, pooled: bool = False, trailing_kept: bool = True,
) -> bytes:
    if set(screen) != {
        "key", "label", "capacity", "width", "height", "tiles",
        "attributes", "commands", "trailing",
    }:
        raise NesScreenPacketError(f"screen {span.key} has the wrong shape")
    if (
        screen.get("key"), screen.get("label"), screen.get("capacity"),
        screen.get("width"), screen.get("height"),
    ) != (
        span.key, span.label, span.size, geometry.width, geometry.height,
    ):
        raise NesScreenPacketError(f"screen {span.key} metadata differs from the profile")
    commands = screen.get("commands")
    if not isinstance(commands, list):
        raise NesScreenPacketError(f"screen {span.key}.commands must be an array")
    normalized_commands = []
    required = {"address", "vertical", "repeat", "length", "values"}
    for index, command in enumerate(commands):
        context = f"screen {span.key}.commands[{index}]"
        if not isinstance(command, Mapping) or set(command) != required:
            raise NesScreenPacketError(f"{context} has the wrong shape")
        address = _integer(
            command.get("address"), f"{context}.address",
            minimum=0x2000, maximum=0x3FFF,
        )
        vertical = command.get("vertical")
        repeat = command.get("repeat")
        if not isinstance(vertical, bool) or not isinstance(repeat, bool):
            raise NesScreenPacketError(f"{context} flags must be booleans")
        length = _integer(command.get("length"), f"{context}.length",
                          minimum=1, maximum=0x3F)
        values = command.get("values")
        expected = 1 if repeat else length
        if not isinstance(values, list) or len(values) != expected:
            raise NesScreenPacketError(f"{context}.values has the wrong length")
        normalized_values = [
            _integer(value, f"{context}.values[{value_index}]")
            for value_index, value in enumerate(values)
        ]
        normalized_commands.append({
            "address": address,
            "vertical": vertical,
            "repeat": repeat,
            "length": length,
            "values": normalized_values,
        })

    desired_tiles = _tile_matrix(screen.get("tiles"), geometry, span.key)
    raw_attributes = screen.get("attributes")
    if not isinstance(raw_attributes, list) or len(raw_attributes) != geometry.attribute_bytes:
        raise NesScreenPacketError(
            f"screen {span.key}.attributes has the wrong length"
        )
    desired_attributes = [
        _integer(value, f"screen {span.key}.attributes[{index}]")
        for index, value in enumerate(raw_attributes)
    ]
    projected_tiles, projected_attributes, writers = _project(
        normalized_commands, geometry
    )
    assignments: dict[tuple[int, int], int] = {}
    for kind, projected, desired in (
        ("tile", projected_tiles, desired_tiles),
        ("attribute", projected_attributes, desired_attributes),
    ):
        for index, (before, after) in enumerate(zip(projected, desired)):
            if before == after:
                continue
            writer = writers.get((kind, index))
            if writer is None:
                raise NesScreenPacketError(
                    f"screen {span.key} {kind} {index} is not writable by its packet layout"
                )
            if writer in assignments and assignments[writer] != after:
                raise NesScreenPacketError(
                    f"screen {span.key} edit splits a repeated packet"
                )
            assignments[writer] = after
    for (command_index, value_index), value in assignments.items():
        values = normalized_commands[command_index]["values"]
        assert isinstance(values, list)
        values[value_index] = value
    synchronized_tiles, synchronized_attributes, _writers = _project(
        normalized_commands, geometry
    )
    if synchronized_tiles != desired_tiles or synchronized_attributes != desired_attributes:
        raise NesScreenPacketError(
            f"screen {span.key} edit cannot be represented by its packet layout"
        )

    output = bytearray()
    for command in normalized_commands:
        output.extend((
            int(command["address"]) >> 8,
            int(command["address"]) & 0xFF,
            (0x80 if command["vertical"] else 0)
            | (0x40 if command["repeat"] else 0)
            | int(command["length"]),
        ))
        values = command["values"]
        assert isinstance(values, list)
        output.extend(values)
    output.append(terminator)
    trailing = screen.get("trailing")
    if not isinstance(trailing, list):
        raise NesScreenPacketError(f"screen {span.key}.trailing must be an array")
    trailing_bytes = [
        _integer(value, f"screen {span.key}.trailing[{index}]")
        for index, value in enumerate(trailing)
    ]
    if trailing_kept:
        output.extend(trailing_bytes)
    if not pooled and len(output) != span.size:
        raise NesScreenPacketError(
            f"screen {span.key} encoded size differs from its capacity"
        )
    return bytes(output)


def _command_offset(screen: Mapping[str, object], command: int, key: str) -> int:
    commands = screen.get("commands")
    if not isinstance(commands, list) or command > len(commands):
        raise NesScreenPacketError(f"screen {key} has no command {command}")
    offset = 0
    for item in commands[:command]:
        values = item.get("values") if isinstance(item, Mapping) else None
        offset += 3 + (len(values) if isinstance(values, list) else 0)
    return offset


def encode_screen_packets(
    value: Mapping[str, object], parameters: Mapping[str, object]
) -> tuple[tuple[int, bytes], ...]:
    """Validate and encode named fixed-capacity PPU display lists."""
    spans, terminator, geometry = _layout(parameters)
    encoding, text_fields = _text_layout(parameters, spans, geometry)
    pool = _packet_pool(parameters, spans, text_fields)
    expected_fields = {"screens"}
    if encoding is not None:
        expected_fields.update(("encoding", "text_fields"))
    # Documents decoded before pooling lack these; they are optional.
    if pool is not None:
        for key in ("capacity_pool", "growable_fields"):
            if key in value:
                expected_fields.add(key)
        if "capacity_pool" in value and value.get("capacity_pool") is not True:
            raise NesScreenPacketError("capacity_pool differs from the profile")
        if "growable_fields" in value and value.get("growable_fields") != list(pool.growable):
            raise NesScreenPacketError("growable_fields differ from the profile")
    if set(value) != expected_fields:
        raise NesScreenPacketError("screen-packet resource has the wrong shape")
    screens = value.get("screens")
    if not isinstance(screens, list) or len(screens) != len(spans):
        raise NesScreenPacketError(f"screens must contain {len(spans)} entries")
    if encoding is not None:
        if value.get("encoding") != encoding.as_dict():
            raise NesScreenPacketError("screen text encoding differs from the profile")
        if pool is None:
            if value.get("text_fields") != [field.as_dict() for field in text_fields]:
                raise NesScreenPacketError("screen text fields differ from the profile")
            fields = text_fields
        else:
            fields = _grown_fields(value.get("text_fields"), text_fields, pool, screens, geometry)
        _validate_text_tiles(screens, fields, encoding, geometry)
    if pool is None:
        return tuple(
            (span.file_offset, _encode_commands(screen, span, terminator, geometry))
            for screen, span in zip(screens, spans)
        )

    # One after another through the whole range, the offset table rewritten.
    # Trailing bytes stay after their screen while everything fits.
    start = spans[0].file_offset
    size = sum(span.size for span in spans)

    def pack(trailing_kept: bool) -> tuple[bytearray, dict[str, int]]:
        packed = bytearray()
        starts: dict[str, int] = {}
        for screen, span in zip(screens, spans):
            starts[span.key] = start + len(packed)
            packed.extend(_encode_commands(
                screen, span, terminator, geometry, pooled=True, trailing_kept=trailing_kept
            ))
        return packed, starts

    packed, starts = pack(True)
    if len(packed) > size:
        packed, starts = pack(False)
    if len(packed) > size:
        raise NesScreenPacketError(
            f"the screens need {len(packed)} bytes; the ROM keeps {size} for them"
        )
    packed.extend(bytes((terminator,)) * (size - len(packed)))
    by_key = {span.key: screen for screen, span in zip(screens, spans)}
    offsets = bytearray()
    for key, command in pool.entries:
        offset = starts[key] + _command_offset(by_key[key], command, key) - pool.offset_base
        if not 0 <= offset <= 0xFF:
            raise NesScreenPacketError(f"screen {key} moved beyond its 8-bit offset")
        offsets.append(offset)
    return ((start, bytes(packed)), (pool.offset_table, bytes(offsets)))
