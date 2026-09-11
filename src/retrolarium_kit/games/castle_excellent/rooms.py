"""Castle Excellent room/object codecs and fixed-arena composition.

The implementation is driven entirely by ``GameProfile`` resource
parameters.  The two known ROM revisions share a format, while tests can use
small original fixtures without embedding any commercial data.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any, Mapping, Sequence

from .authoring import (
    Address,
    AddressRange,
    AuthoringError,
    BinaryReader,
    BuildPlan,
    Change,
    ChangeSet,
    GameProfile,
    Provenance,
    ResourceSpec,
    RevisionSpec,
)
from .ines import InesImage, parse_ines


LEVEL_DOCUMENT_SCHEMA = "retrolarium.castle-levels"
LEVEL_DOCUMENT_VERSION = 1


class CastleError(AuthoringError):
    """Castle profile or level content is invalid."""


@dataclass(frozen=True)
class PrefixEntry:
    symbol: int
    bits: str


@dataclass(frozen=True)
class RoomArena:
    bank: int
    first_room: int
    last_room: int
    start: int
    end: int

    @property
    def capacity(self) -> int:
        return self.end - self.start


@dataclass(frozen=True)
class Room:
    number: int
    cells: tuple[tuple[int, ...], ...]
    bank: int
    encoded_offset: int
    encoded_size: int


@dataclass(frozen=True)
class ObjectRecord:
    encoded_type: int
    x: int
    y: int


@dataclass(frozen=True)
class CastleLevelDocument:
    game_id: str
    revision_id: str
    source_sha256: str
    width: int
    height: int
    rooms: tuple[Room, ...]
    objects: tuple[tuple[ObjectRecord, ...], ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "schema": LEVEL_DOCUMENT_SCHEMA,
            "schema_version": LEVEL_DOCUMENT_VERSION,
            "game": self.game_id,
            "revision": self.revision_id,
            "source_sha256": self.source_sha256,
            "width": self.width,
            "height": self.height,
            "rooms": [
                {
                    "number": room.number,
                    "cells": [list(row) for row in room.cells],
                    "objects": [
                        {
                            "encoded_type": record.encoded_type,
                            "x": record.x,
                            "y": record.y,
                        }
                        for record in self.objects[index]
                    ],
                }
                for index, room in enumerate(self.rooms)
            ],
        }


def _integer(value: object, name: str, *, minimum: int = 0) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise CastleError(f"{name} must be an integer >= {minimum}")
    return value


def _sequence(value: object, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise CastleError(f"{name} must be an array")
    return value


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CastleError(f"{name} must be an object")
    return value


def _resource(
    profile: GameProfile, revision: RevisionSpec, resource_id: str, codec: str
) -> ResourceSpec:
    resource = profile.resource(resource_id, revision)
    if resource.codec != codec or resource.capacity_policy != "fixed":
        raise CastleError(
            f"resource {resource_id!r} does not use required fixed codec {codec}"
        )
    return resource


def _cpu_offset(address: int) -> int:
    if not 0x8000 <= address <= 0xFFFF:
        raise CastleError(f"CPU address is outside fixed 32 KiB PRG: 0x{address:X}")
    return address - 0x8000


def _prg_file_offset(image: InesImage, cpu_address: int) -> int:
    return len(image.header) + len(image.trainer) + _cpu_offset(cpu_address)


def _chr_file_offset(image: InesImage, bank: int, offset: int) -> int:
    return (
        len(image.header)
        + len(image.trainer)
        + len(image.prg)
        + bank * 0x2000
        + offset
    )


def parse_prefix_codebook(prg: bytes, cpu_address: int) -> tuple[PrefixEntry, ...]:
    reader = BinaryReader(prg, "Castle PRG")
    cursor = _cpu_offset(cpu_address)
    entries: list[PrefixEntry] = []
    while cursor < len(reader):
        symbol = reader.u8(cursor)
        cursor += 1
        if symbol == 0xFF:
            break
        bits = bytearray()
        while cursor < len(reader):
            value = reader.u8(cursor)
            cursor += 1
            if value == 0:
                break
            if value not in (ord("0"), ord("1")):
                raise CastleError(
                    f"invalid prefix code byte 0x{value:02X} at PRG 0x{cursor - 1:X}"
                )
            bits.append(value)
        else:
            raise CastleError("unterminated Castle prefix-code entry")
        if not bits:
            raise CastleError(f"empty prefix code for symbol 0x{symbol:02X}")
        entries.append(PrefixEntry(symbol, bits.decode("ascii")))
    else:
        raise CastleError("Castle prefix-code sentinel was not found")
    validate_prefix_codebook(entries)
    return tuple(entries)


def validate_prefix_codebook(entries: Sequence[PrefixEntry]) -> None:
    if not entries:
        raise CastleError("Castle prefix codebook is empty")
    symbols: set[int] = set()
    codes: list[str] = []
    for entry in entries:
        if not 0 <= entry.symbol <= 0xFE:
            raise CastleError("prefix symbol must fit a non-sentinel byte")
        if entry.symbol in symbols:
            raise CastleError(f"duplicate prefix symbol 0x{entry.symbol:02X}")
        if not entry.bits or set(entry.bits) - {"0", "1"}:
            raise CastleError(f"invalid prefix code {entry.bits!r}")
        if any(entry.bits.startswith(code) or code.startswith(entry.bits) for code in codes):
            raise CastleError(f"codebook is not prefix-free at {entry.bits!r}")
        symbols.add(entry.symbol)
        codes.append(entry.bits)


def decode_prefix_stream(
    stream: bytes, entries: Sequence[PrefixEntry], output_size: int
) -> tuple[bytes, int]:
    validate_prefix_codebook(entries)
    lookup = {entry.bits: entry.symbol for entry in entries}
    prefixes = {
        code[:length]
        for code in lookup
        for length in range(1, len(code) + 1)
    }
    result = bytearray()
    current = ""
    consumed = 0
    for value in stream:
        for shift in range(7, -1, -1):
            consumed += 1
            current += "1" if value & (1 << shift) else "0"
            symbol = lookup.get(current)
            if symbol is not None:
                result.append(symbol)
                current = ""
                if len(result) == output_size:
                    return bytes(result), consumed
            elif current not in prefixes:
                raise CastleError(f"prefix stream has no code matching bit {consumed}")
    raise CastleError(
        f"truncated prefix stream after {len(result)} of {output_size} cells"
    )


def encode_prefix_stream(
    cells: Sequence[int], entries: Sequence[PrefixEntry]
) -> tuple[bytes, int]:
    validate_prefix_codebook(entries)
    encoding = {entry.symbol: entry.bits for entry in entries}
    bit_parts = []
    for cell in cells:
        if not isinstance(cell, int) or isinstance(cell, bool) or not 0 <= cell <= 0xFF:
            raise CastleError("room cell must be a byte")
        try:
            bit_parts.append(encoding[cell])
        except KeyError as exc:
            raise CastleError(f"room cell 0x{cell:02X} has no prefix code") from exc
    bits = "".join(bit_parts)
    padding = (-len(bits)) % 8
    bits += "1" * padding
    return (
        bytes(int(bits[index:index + 8], 2) for index in range(0, len(bits), 8)),
        padding,
    )


def _room_configuration(resource: ResourceSpec) -> tuple[int, int, int, int, int, tuple[RoomArena, ...]]:
    parameters = resource.parameters
    count = _integer(parameters.get("room_count"), "rooms.room_count", minimum=1)
    width = _integer(parameters.get("width"), "rooms.width", minimum=1)
    height = _integer(parameters.get("height"), "rooms.height", minimum=1)
    codebook_cpu = _integer(parameters.get("codebook_cpu"), "rooms.codebook_cpu")
    pointers_cpu = _integer(
        parameters.get("pointer_table_cpu"), "rooms.pointer_table_cpu"
    )
    arenas = []
    for index, raw in enumerate(_sequence(parameters.get("arenas"), "rooms.arenas")):
        item = _mapping(raw, f"rooms.arenas[{index}]")
        arena = RoomArena(
            bank=_integer(item.get("bank"), f"rooms.arenas[{index}].bank"),
            first_room=_integer(
                item.get("first_room"), f"rooms.arenas[{index}].first_room"
            ),
            last_room=_integer(
                item.get("last_room"), f"rooms.arenas[{index}].last_room"
            ),
            start=_integer(item.get("start"), f"rooms.arenas[{index}].start"),
            end=_integer(item.get("end"), f"rooms.arenas[{index}].end"),
        )
        if (
            arena.first_room >= arena.last_room
            or arena.end <= arena.start
            or arena.end > 0x2000
        ):
            raise CastleError(f"rooms.arenas[{index}] has invalid bounds")
        arenas.append(arena)
    if not arenas:
        raise CastleError("rooms need at least one arena")
    expected = 0
    banks: set[int] = set()
    for arena in arenas:
        if arena.first_room != expected:
            raise CastleError("room arenas must cover rooms contiguously")
        if arena.bank in banks:
            raise CastleError("room arenas must use distinct CHR banks")
        expected = arena.last_room
        banks.add(arena.bank)
    if expected != count:
        raise CastleError("room arenas do not cover the configured room count")
    return count, width, height, codebook_cpu, pointers_cpu, tuple(arenas)


def _decode_rooms(image: InesImage, resource: ResourceSpec) -> tuple[tuple[Room, ...], tuple[PrefixEntry, ...]]:
    count, width, height, codebook_cpu, pointers_cpu, arenas = _room_configuration(resource)
    if len(image.prg) != 0x8000:
        raise CastleError("Castle room codec requires a 32 KiB PRG")
    codebook = parse_prefix_codebook(image.prg, codebook_cpu)
    prg_reader = BinaryReader(image.prg, "Castle PRG")
    pointer_start = _cpu_offset(pointers_cpu)
    prg_reader.read(pointer_start, count * 2)
    fill = _integer(resource.parameters.get("fill_byte", 0xFF), "rooms.fill_byte")
    if fill > 0xFF:
        raise CastleError("rooms.fill_byte must fit a byte")
    rooms: list[Room] = []
    for arena in arenas:
        if (arena.bank + 1) * 0x2000 > len(image.chr):
            raise CastleError(f"CHR bank {arena.bank} is absent")
        cursor = arena.start
        bank_payload = image.chr[arena.bank * 0x2000:(arena.bank + 1) * 0x2000]
        for room_index in range(arena.first_room, arena.last_room):
            offset = prg_reader.u16le(pointer_start + room_index * 2)
            if offset != cursor:
                raise CastleError(
                    f"room {room_index + 1} starts at CHR {arena.bank}:0x{offset:X}, "
                    f"expected 0x{cursor:X}"
                )
            cells, consumed_bits = decode_prefix_stream(
                bank_payload[offset:arena.end], codebook, width * height
            )
            encoded_size = (consumed_bits + 7) // 8
            physical = bank_payload[offset:offset + encoded_size]
            encoded, _padding = encode_prefix_stream(cells, codebook)
            if physical != encoded:
                raise CastleError(f"room {room_index + 1} is not byte-exact")
            cursor += encoded_size
            if cursor > arena.end:
                raise CastleError(f"room {room_index + 1} overflows its CHR arena")
            rows = tuple(
                tuple(cells[row * width:(row + 1) * width])
                for row in range(height)
            )
            rooms.append(Room(room_index + 1, rows, arena.bank, offset, encoded_size))
        if any(value != fill for value in bank_payload[cursor:arena.end]):
            raise CastleError(f"unused CHR arena bytes in bank {arena.bank} are not filled")
    return tuple(rooms), codebook


def _object_configuration(resource: ResourceSpec) -> tuple[int, int, int, int, int, int, int, int, int, int]:
    parameters = resource.parameters
    return (
        _integer(parameters.get("room_count"), "objects.room_count", minimum=1),
        _integer(parameters.get("width"), "objects.width", minimum=1),
        _integer(parameters.get("height"), "objects.height", minimum=1),
        _integer(parameters.get("stream_cpu"), "objects.stream_cpu"),
        _integer(parameters.get("stream_end_cpu"), "objects.stream_end_cpu"),
        _integer(parameters.get("count_table_cpu"), "objects.count_table_cpu"),
        _integer(parameters.get("state_offsets_cpu"), "objects.state_offsets_cpu"),
        _integer(parameters.get("state_capacity"), "objects.state_capacity", minimum=1),
        _integer(parameters.get("type_count"), "objects.type_count", minimum=1),
        _integer(parameters.get("maximum_per_room"), "objects.maximum_per_room", minimum=1),
    )


def _decode_objects(image: InesImage, resource: ResourceSpec) -> tuple[tuple[ObjectRecord, ...], ...]:
    count, width, height, stream_cpu, stream_end_cpu, counts_cpu, offsets_cpu, state_capacity, type_count, maximum = _object_configuration(resource)
    start = _cpu_offset(stream_cpu)
    end = _cpu_offset(stream_end_cpu)
    if start >= end or end > len(image.prg):
        raise CastleError("object stream has invalid PRG bounds")
    reader = BinaryReader(image.prg, "Castle PRG")
    cursor = start
    rooms: list[tuple[ObjectRecord, ...]] = []
    for room_index in range(count):
        records: list[ObjectRecord] = []
        while cursor < end and reader.u8(cursor) != 0xFF:
            if len(records) >= maximum:
                raise CastleError(
                    f"room {room_index + 1} exceeds {maximum} static objects"
                )
            if cursor + 3 > end:
                raise CastleError(f"room {room_index + 1} has a truncated object record")
            encoded_type, x, y = reader.read(cursor, 3)
            if encoded_type >= type_count:
                raise CastleError(
                    f"room {room_index + 1} object type 0x{encoded_type:02X} is invalid"
                )
            if x >= width or y >= height:
                raise CastleError(f"room {room_index + 1} object is outside its grid")
            records.append(ObjectRecord(encoded_type, x, y))
            cursor += 3
        if cursor >= end:
            raise CastleError(f"room {room_index + 1} object list is unterminated")
        cursor += 1
        rooms.append(tuple(records))
    fill = _integer(resource.parameters.get("fill_byte", 0xFF), "objects.fill_byte")
    if fill > 0xFF or any(value != fill for value in image.prg[cursor:end]):
        raise CastleError("unused object-stream bytes are not filled")

    counts = reader.read(_cpu_offset(counts_cpu), count)
    expected_counts = bytes(len(room) for room in rooms)
    if counts != expected_counts:
        raise CastleError("object count table disagrees with the object stream")
    expected_offsets = _state_offsets(rooms, state_capacity, "object")
    actual_offsets = reader.read(_cpu_offset(offsets_cpu), count + 1)
    if actual_offsets != expected_offsets:
        raise CastleError("object state-offset table disagrees with the object stream")
    return tuple(rooms)


def _state_offsets(
    rooms: Sequence[Sequence[object]], capacity: int, kind: str
) -> bytes:
    offsets = [0]
    for room in rooms:
        offsets.append(offsets[-1] + (len(room) + 7) // 8)
    if offsets[-1] > capacity or offsets[-1] > 0xFF:
        raise CastleError(
            f"{kind} state needs {offsets[-1]} bytes, capacity is {capacity}"
        )
    return bytes(offsets)


def _mutable_offsets(
    rooms: Sequence[Room], resource: ResourceSpec
) -> bytes:
    parameters = resource.parameters
    capacity = _integer(
        parameters.get("state_capacity"), "mutable-cell-state.state_capacity", minimum=1
    )
    final_minimum = _integer(
        parameters.get("final_room_minimum_bytes", 0),
        "mutable-cell-state.final_room_minimum_bytes",
    )
    values = _sequence(
        parameters.get("persistent_cell_values"),
        "mutable-cell-state.persistent_cell_values",
    )
    persistent = {
        _integer(value, "mutable-cell-state persistent value") for value in values
    }
    if any(value > 0xFF for value in persistent):
        raise CastleError("mutable-cell-state values must fit bytes")
    offsets = [0]
    for index, room in enumerate(rooms):
        count = sum(cell in persistent for row in room.cells for cell in row)
        width = (count + 7) // 8
        if index == len(rooms) - 1:
            width = max(width, final_minimum)
        offsets.append(offsets[-1] + width)
    if offsets[-1] > capacity or offsets[-1] > 0xFF:
        raise CastleError(
            f"mutable-cell state needs {offsets[-1]} bytes, capacity is {capacity}"
        )
    return bytes(offsets)


def decode_levels(
    payload: bytes, profile: GameProfile, revision: RevisionSpec
) -> CastleLevelDocument:
    if hashlib.sha256(payload).hexdigest() != revision.sha256:
        raise CastleError("input image does not match the selected revision")
    image = parse_ines(payload)
    if image.mapper != revision.mapper:
        raise CastleError(
            f"input mapper {image.mapper} does not match revision mapper {revision.mapper}"
        )
    rooms_resource = _resource(profile, revision, "rooms", "castle.prefix-rooms.v1")
    objects_resource = _resource(profile, revision, "objects", "castle.object-stream.v1")
    mutable_resource = _resource(
        profile, revision, "mutable-cell-state", "castle.mutable-cell-state.v1"
    )
    rooms, _codebook = _decode_rooms(image, rooms_resource)
    objects = _decode_objects(image, objects_resource)
    if len(rooms) != len(objects):
        raise CastleError("room and object counts differ")
    mutable_parameters = mutable_resource.parameters
    mutable_cpu = _integer(
        mutable_parameters.get("state_offsets_cpu"),
        "mutable-cell-state.state_offsets_cpu",
    )
    expected_mutable = _mutable_offsets(rooms, mutable_resource)
    actual_mutable = BinaryReader(image.prg, "Castle PRG").read(
        _cpu_offset(mutable_cpu), len(rooms) + 1
    )
    if actual_mutable != expected_mutable:
        raise CastleError("mutable-cell state-offset table disagrees with rooms")
    return CastleLevelDocument(
        game_id=profile.game_id,
        revision_id=revision.revision_id,
        source_sha256=revision.sha256,
        width=_integer(rooms_resource.parameters.get("width"), "rooms.width"),
        height=_integer(rooms_resource.parameters.get("height"), "rooms.height"),
        rooms=rooms,
        objects=objects,
    )


def parse_level_document(
    document: object, profile: GameProfile, revision: RevisionSpec
) -> CastleLevelDocument:
    value = _mapping(document, "level document")
    expected_header = {
        "schema": LEVEL_DOCUMENT_SCHEMA,
        "schema_version": LEVEL_DOCUMENT_VERSION,
        "game": profile.game_id,
        "revision": revision.revision_id,
        "source_sha256": revision.sha256,
    }
    for field, expected in expected_header.items():
        if value.get(field) != expected:
            raise CastleError(f"level document {field} must be {expected!r}")
    rooms_resource = _resource(profile, revision, "rooms", "castle.prefix-rooms.v1")
    objects_resource = _resource(profile, revision, "objects", "castle.object-stream.v1")
    count, width, height, _codebook, _pointers, _arenas = _room_configuration(
        rooms_resource
    )
    if value.get("width") != width or value.get("height") != height:
        raise CastleError("level document geometry disagrees with the profile")
    room_values = _sequence(value.get("rooms"), "level document rooms")
    if len(room_values) != count:
        raise CastleError(f"level document must contain {count} rooms")
    _, _, _, _, _, _, _, _, type_count, maximum = _object_configuration(
        objects_resource
    )
    rooms: list[Room] = []
    objects: list[tuple[ObjectRecord, ...]] = []
    for index, raw_room in enumerate(room_values):
        item = _mapping(raw_room, f"rooms[{index}]")
        if item.get("number") != index + 1:
            raise CastleError(f"room {index + 1} has an invalid number")
        row_values = _sequence(item.get("cells"), f"rooms[{index}].cells")
        if len(row_values) != height:
            raise CastleError(f"room {index + 1} must have {height} rows")
        rows: list[tuple[int, ...]] = []
        for row_index, raw_row in enumerate(row_values):
            cells = _sequence(raw_row, f"rooms[{index}].cells[{row_index}]")
            if len(cells) != width:
                raise CastleError(
                    f"room {index + 1} row {row_index + 1} must have {width} cells"
                )
            checked = tuple(
                _integer(cell, f"room {index + 1} cell", minimum=0)
                for cell in cells
            )
            if any(cell > 0xFF for cell in checked):
                raise CastleError(f"room {index + 1} cells must fit bytes")
            rows.append(checked)
        object_values = _sequence(item.get("objects"), f"rooms[{index}].objects")
        if len(object_values) > maximum:
            raise CastleError(f"room {index + 1} has too many static objects")
        records = []
        for object_index, raw_record in enumerate(object_values):
            record = _mapping(
                raw_record, f"rooms[{index}].objects[{object_index}]"
            )
            if set(record) != {"encoded_type", "x", "y"}:
                raise CastleError(f"room {index + 1} object has invalid fields")
            encoded_type = _integer(record.get("encoded_type"), "encoded_type")
            x = _integer(record.get("x"), "object x")
            y = _integer(record.get("y"), "object y")
            if encoded_type >= type_count or x >= width or y >= height:
                raise CastleError(f"room {index + 1} object is outside profile limits")
            records.append(ObjectRecord(encoded_type, x, y))
        rooms.append(Room(index + 1, tuple(rows), 0, 0, 0))
        objects.append(tuple(records))
    return CastleLevelDocument(
        profile.game_id,
        revision.revision_id,
        revision.sha256,
        width,
        height,
        tuple(rooms),
        tuple(objects),
    )


def _change(
    image: bytes,
    offset: int,
    replacement: bytes,
    source_sha256: str,
    resource_id: str,
) -> Change:
    return Change(
        AddressRange(Address("file", offset), len(replacement)),
        image[offset:offset + len(replacement)],
        replacement,
        Provenance(source_sha256, resource_id, "encode"),
    )


def build_levels(
    source: bytes,
    document: CastleLevelDocument,
    profile: GameProfile,
    revision: RevisionSpec,
) -> tuple[bytes, dict[str, object]]:
    if document.game_id != profile.game_id or document.revision_id != revision.revision_id:
        raise CastleError("level document profile identity mismatch")
    if document.source_sha256 != revision.sha256:
        raise CastleError("level document source identity mismatch")
    source_sha256 = hashlib.sha256(source).hexdigest()
    if source_sha256 != revision.sha256:
        raise CastleError("build source does not match the selected revision")
    image = parse_ines(source)
    rooms_resource = _resource(profile, revision, "rooms", "castle.prefix-rooms.v1")
    objects_resource = _resource(profile, revision, "objects", "castle.object-stream.v1")
    mutable_resource = _resource(
        profile, revision, "mutable-cell-state", "castle.mutable-cell-state.v1"
    )
    count, width, height, codebook_cpu, pointers_cpu, arenas = _room_configuration(
        rooms_resource
    )
    if len(document.rooms) != count or len(document.objects) != count:
        raise CastleError("level document count disagrees with the profile")
    codebook = parse_prefix_codebook(image.prg, codebook_cpu)
    pointers = bytearray(count * 2)
    changes: list[Change] = []
    bank_reports: list[dict[str, int]] = []
    room_index = 0
    room_fill = _integer(rooms_resource.parameters.get("fill_byte", 0xFF), "rooms.fill_byte")
    if room_fill > 0xFF:
        raise CastleError("rooms.fill_byte must fit a byte")
    for arena in arenas:
        arena_payload = bytearray((room_fill,)) * arena.capacity
        cursor = arena.start
        for room_index in range(arena.first_room, arena.last_room):
            room = document.rooms[room_index]
            flat = tuple(cell for row in room.cells for cell in row)
            if len(room.cells) != height or any(len(row) != width for row in room.cells):
                raise CastleError(f"room {room_index + 1} geometry is invalid")
            encoded, _padding = encode_prefix_stream(flat, codebook)
            if cursor + len(encoded) > arena.end:
                raise CastleError(
                    f"room {room_index + 1} overflows CHR bank {arena.bank} arena"
                )
            pointers[room_index * 2:room_index * 2 + 2] = cursor.to_bytes(2, "little")
            local = cursor - arena.start
            arena_payload[local:local + len(encoded)] = encoded
            cursor += len(encoded)
        changes.append(
            _change(
                source,
                _chr_file_offset(image, arena.bank, arena.start),
                bytes(arena_payload),
                source_sha256,
                "rooms",
            )
        )
        bank_reports.append(
            {
                "bank": arena.bank,
                "used_bytes": cursor - arena.start,
                "capacity_bytes": arena.capacity,
                "free_bytes": arena.end - cursor,
            }
        )
    changes.append(
        _change(
            source,
            _prg_file_offset(image, pointers_cpu),
            bytes(pointers),
            source_sha256,
            "rooms",
        )
    )

    object_count, object_width, object_height, stream_cpu, stream_end_cpu, counts_cpu, object_offsets_cpu, state_capacity, type_count, maximum = _object_configuration(objects_resource)
    if (object_count, object_width, object_height) != (count, width, height):
        raise CastleError("room and object resource geometry differs")
    stream = bytearray()
    for index, records in enumerate(document.objects):
        if len(records) > maximum:
            raise CastleError(f"room {index + 1} has too many static objects")
        for record in records:
            if (
                record.encoded_type >= type_count
                or record.x >= width
                or record.y >= height
                or min(record.encoded_type, record.x, record.y) < 0
            ):
                raise CastleError(f"room {index + 1} object is outside profile limits")
            stream.extend((record.encoded_type, record.x, record.y))
        stream.append(0xFF)
    stream_capacity = stream_end_cpu - stream_cpu
    if len(stream) > stream_capacity:
        raise CastleError(
            f"object stream needs {len(stream)} bytes, capacity is {stream_capacity}"
        )
    object_fill = _integer(
        objects_resource.parameters.get("fill_byte", 0xFF), "objects.fill_byte"
    )
    if object_fill > 0xFF:
        raise CastleError("objects.fill_byte must fit a byte")
    stream.extend(bytes((object_fill,)) * (stream_capacity - len(stream)))
    counts = bytes(len(records) for records in document.objects)
    object_offsets = _state_offsets(document.objects, state_capacity, "object")
    mutable_offsets = _mutable_offsets(document.rooms, mutable_resource)
    mutable_cpu = _integer(
        mutable_resource.parameters.get("state_offsets_cpu"),
        "mutable-cell-state.state_offsets_cpu",
    )
    for cpu, payload, resource_id in (
        (stream_cpu, bytes(stream), "objects"),
        (counts_cpu, counts, "objects"),
        (object_offsets_cpu, object_offsets, "objects"),
        (mutable_cpu, mutable_offsets, "mutable-cell-state"),
    ):
        changes.append(
            _change(
                source,
                _prg_file_offset(image, cpu),
                payload,
                source_sha256,
                resource_id,
            )
        )

    plan = BuildPlan(source_sha256, ChangeSet.from_iterable(changes))
    output = plan.apply(source)
    changed_bytes = sum(left != right for left, right in zip(source, output))
    return output, {
        "schema": "retrolarium.castle-level-build",
        "schema_version": 1,
        "game": profile.game_id,
        "revision": revision.revision_id,
        "source_sha256": source_sha256,
        "output_sha256": hashlib.sha256(output).hexdigest(),
        "size": len(output),
        "changed_bytes": changed_bytes,
        "room_banks": bank_reports,
        "object_stream": {
            "used_bytes": sum(1 + 3 * len(records) for records in document.objects),
            "capacity_bytes": stream_capacity,
            "free_bytes": stream_capacity
            - sum(1 + 3 * len(records) for records in document.objects),
        },
        "object_state_bytes": object_offsets[-1],
        "mutable_cell_state_bytes": mutable_offsets[-1],
    }
