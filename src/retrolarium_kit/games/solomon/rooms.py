"""Solomon's Key room bitplanes and shared stream arenas for USA and Europe.

The 53 room records are 24 brown and 24 white bytes each.  Their regional
PRG offsets come from the packaged profile, never from the filename.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Mapping

from .authoring import (
    Address,
    AddressRange,
    AuthoringError,
    BuildPlan,
    Change,
    ChangeSet,
    GameProfile,
    Provenance,
    ResourceSpec,
    RevisionSpec,
)
from .ines import parse_ines
from .solomon_streams import (
    EnemyStream,
    ItemStream,
    decode_enemy_stream,
    decode_item_stream,
    encode_enemy_stream,
    encode_item_stream,
    parse_enemy_stream,
    parse_item_stream,
)
from .solomon_special import (
    MirrorEnemySet,
    MirrorSchedule,
    SPECIAL_SIZES,
    SpecialRooms,
    decode_mirror_enemy_set,
    decode_mirror_schedule,
    decode_special_rooms,
    encode_mirror_enemy_set,
    encode_mirror_schedule,
    encode_special_rooms,
    parse_mirror_enemy_set,
    parse_mirror_schedule,
    parse_special_rooms,
)


DOCUMENT_SCHEMA = "retrolarium.solomon-rooms"
DOCUMENT_VERSION = 3
STREAM_DOCUMENT_VERSION = 2
BLOCK_CODEC = "solomon.block-bitplanes.v1"
ENEMY_CODEC = "solomon.enemy-streams.v1"
ITEM_CODEC = "solomon.item-streams.v1"
MIRROR_SCHEDULE_CODEC = "solomon.mirror-schedules.v1"
MIRROR_SET_CODEC = "solomon.mirror-enemy-sets.v1"
SPECIAL_CODEC = "solomon.special-rooms.v1"


class SolomonError(AuthoringError):
    """A Solomon profile, image or room document violates its contract."""


@dataclass(frozen=True)
class SolomonRoom:
    number: int
    brown: bytes
    white: bytes
    enemy_stream: EnemyStream | None = None
    item_stream: ItemStream | None = None

    def as_dict(self, width: int, height: int) -> dict[str, object]:
        result: dict[str, object] = {
            "number": self.number,
            "brown": _unpack_plane(self.brown, width, height),
            "white": _unpack_plane(self.white, width, height),
        }
        if self.enemy_stream is not None and self.item_stream is not None:
            result["enemy_stream"] = self.enemy_stream.as_dict()
            result["item_stream"] = self.item_stream.as_dict()
        return result


@dataclass(frozen=True)
class SolomonRoomDocument:
    game_id: str
    revision_id: str
    source_sha256: str
    width: int
    height: int
    rooms: tuple[SolomonRoom, ...]
    schema_version: int = 1
    mirror_schedules: tuple[MirrorSchedule, ...] = ()
    mirror_enemy_sets: tuple[MirrorEnemySet, ...] = ()
    special_rooms: SpecialRooms | None = None

    def as_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "schema": DOCUMENT_SCHEMA,
            "schema_version": self.schema_version,
            "game": self.game_id,
            "revision": self.revision_id,
            "source_sha256": self.source_sha256,
            "width": self.width,
            "height": self.height,
            "rooms": [room.as_dict(self.width, self.height) for room in self.rooms],
        }
        if self.schema_version == DOCUMENT_VERSION and self.special_rooms is not None:
            result["mirror_schedules"] = [
                record.as_dict() for record in self.mirror_schedules
            ]
            result["mirror_enemy_sets"] = [
                record.as_dict() for record in self.mirror_enemy_sets
            ]
            result["special_rooms"] = self.special_rooms.as_dict()
        return result


def _resource(profile: GameProfile, revision: RevisionSpec) -> ResourceSpec:
    matches = [
        item for item in profile.resources_for(revision)
        if item.resource_id == "room-blocks" and item.codec == BLOCK_CODEC
    ]
    if len(matches) != 1:
        raise SolomonError("profile needs one Solomon room-blocks resource")
    return matches[0]


def _stream_resources(
    profile: GameProfile, revision: RevisionSpec
) -> tuple[ResourceSpec, ResourceSpec] | None:
    resources = profile.resources_for(revision)
    matches = []
    for identifier, codec in (("enemy-streams", ENEMY_CODEC),
                              ("item-streams", ITEM_CODEC)):
        found = [item for item in resources if item.resource_id == identifier]
        if len(found) > 1 or (found and found[0].codec != codec):
            raise SolomonError(f"profile has invalid {identifier} resource")
        matches.append(found[0] if found else None)
    if all(item is None for item in matches):
        return None
    if any(item is None for item in matches):
        raise SolomonError("profile needs both Solomon stream arenas")
    return matches[0], matches[1]


def _special_resources(
    profile: GameProfile, revision: RevisionSpec
) -> tuple[ResourceSpec, ResourceSpec, ResourceSpec] | None:
    resources = profile.resources_for(revision)
    matches = []
    for identifier, codec in (
        ("mirror-schedules", MIRROR_SCHEDULE_CODEC),
        ("mirror-enemy-sets", MIRROR_SET_CODEC),
        ("special-rooms", SPECIAL_CODEC),
    ):
        found = [item for item in resources if item.resource_id == identifier]
        if len(found) > 1 or (found and found[0].codec != codec):
            raise SolomonError(f"profile has invalid {identifier} resource")
        matches.append(found[0] if found else None)
    if all(item is None for item in matches):
        return None
    if any(item is None for item in matches):
        raise SolomonError("profile needs all Spawn Mirror and special-room resources")
    return matches[0], matches[1], matches[2]


def _special_offsets(resource: ResourceSpec) -> dict[str, int]:
    offsets = {
        name: _integer(resource.parameters.get(name), name)
        for name in SPECIAL_SIZES
    }
    spans = []
    for name, start in offsets.items():
        end = start + SPECIAL_SIZES[name]
        if end > 0x8000:
            raise SolomonError(f"special-room {name} escapes PRG")
        spans.append((start, end))
    spans.sort()
    if any(first[1] > second[0] for first, second in zip(spans, spans[1:])):
        raise SolomonError("special-room profile ranges overlap")
    return offsets


def _stream_geometry(resource: ResourceSpec, count: int) -> tuple[int, int, int]:
    parameters = resource.parameters
    pointer = _integer(parameters.get("pointer_table_prg"), "pointer_table_prg")
    start = _integer(parameters.get("arena_start_prg"), "arena_start_prg")
    end = _integer(parameters.get("arena_end_prg"), "arena_end_prg")
    if pointer + count * 2 > start or not start < end <= 0x8000:
        raise SolomonError(f"{resource.resource_id} table/arena layout is invalid")
    return pointer, start, end


def _pointers(prg: bytes, pointer: int, count: int) -> tuple[int, ...]:
    return tuple(
        (prg[pointer + index] | (prg[pointer + count + index] << 8)) - 0x8000
        for index in range(count)
    )


def _decode_streams(
    prg: bytes, resource: ResourceSpec, count: int, decoder
) -> tuple[object, ...]:
    pointer, start, end = _stream_geometry(resource, count)
    offsets = _pointers(prg, pointer, count)
    if offsets[0] != start or any(not start <= value < end for value in offsets):
        raise SolomonError(f"{resource.resource_id} pointers escape their arena")
    records = []
    for index, offset in enumerate(offsets):
        limit = offsets[index + 1] if index + 1 < count else end
        if not offset < limit:
            raise SolomonError(f"{resource.resource_id} pointers are not ordered")
        record, cursor = decoder(prg, offset, limit)
        if cursor != limit:
            raise SolomonError(
                f"{resource.resource_id} record {index + 1} has unowned gap bytes"
            )
        records.append(record)
    return tuple(records)


def _integer(value: object, name: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise SolomonError(f"{name} must be an integer >= {minimum}")
    return value


def _geometry(resource: ResourceSpec) -> tuple[int, int, int, int, int]:
    parameters = resource.parameters
    count = _integer(parameters.get("room_count"), "room_count", minimum=1)
    width = _integer(parameters.get("width"), "width", minimum=1)
    height = _integer(parameters.get("height"), "height", minimum=1)
    plane_size = _integer(
        parameters.get("bytes_per_plane"), "bytes_per_plane", minimum=1
    )
    offset = _integer(parameters.get("prg_offset"), "prg_offset")
    if width % 8 or width * height != plane_size * 8:
        raise SolomonError("room bitplane geometry disagrees with the profile")
    if offset + count * plane_size * 2 > 0x8000:
        raise SolomonError("room bitplanes escape the 32 KiB PRG")
    return count, width, height, plane_size, offset


def _unpack_plane(data: bytes, width: int, height: int) -> list[list[int]]:
    return [
        [int(bool(data[(y * width + x) // 8] & (0x80 >> (x % 8))))
         for x in range(width)]
        for y in range(height)
    ]


def _pack_plane(value: object, width: int, height: int, name: str) -> bytes:
    if not isinstance(value, list) or len(value) != height:
        raise SolomonError(f"{name} must have {height} rows")
    output = bytearray(width * height // 8)
    for y, row in enumerate(value):
        if not isinstance(row, list) or len(row) != width:
            raise SolomonError(f"{name}[{y}] must have {width} cells")
        for x, cell in enumerate(row):
            if type(cell) is not int or cell not in (0, 1):
                raise SolomonError(f"{name}[{y}][{x}] must be 0 or 1")
            if cell:
                output[(y * width + x) // 8] |= 0x80 >> (x % 8)
    return bytes(output)


def _source_image(source: bytes, revision: RevisionSpec):
    if hashlib.sha256(source).hexdigest() != revision.sha256:
        raise SolomonError("input image does not match the selected revision")
    image = parse_ines(source)
    if image.mapper != revision.mapper or len(image.prg) != 0x8000:
        raise SolomonError("Solomon room blocks require a 32 KiB mapper-3 PRG")
    return image


def decode_rooms(
    source: bytes, profile: GameProfile, revision: RevisionSpec
) -> SolomonRoomDocument:
    image = _source_image(source, revision)
    count, width, height, plane_size, offset = _geometry(_resource(profile, revision))
    streams = _stream_resources(profile, revision)
    special = _special_resources(profile, revision)
    if special is not None and streams is None:
        raise SolomonError("Spawn Mirror resources need room streams")
    enemies: tuple[object, ...] = ()
    items: tuple[object, ...] = ()
    schedules: tuple[object, ...] = ()
    mirror_sets: tuple[object, ...] = ()
    special_rooms = None
    if streams is not None:
        enemies = _decode_streams(
            image.prg, streams[0], count, decode_enemy_stream
        )
        items = _decode_streams(
            image.prg, streams[1], count, decode_item_stream
        )
    if special is not None:
        schedules = _decode_streams(
            image.prg, special[0], 16, decode_mirror_schedule
        )
        mirror_sets = _decode_streams(
            image.prg, special[1], 17, decode_mirror_enemy_set
        )
        special_rooms = decode_special_rooms(
            image.prg, _special_offsets(special[2])
        )
    rooms = []
    for index in range(count):
        start = offset + index * plane_size * 2
        rooms.append(SolomonRoom(
            index + 1,
            image.prg[start:start + plane_size],
            image.prg[start + plane_size:start + plane_size * 2],
            enemies[index] if streams is not None else None,
            items[index] if streams is not None else None,
        ))
    return SolomonRoomDocument(
        profile.game_id, revision.revision_id, revision.sha256,
        width, height, tuple(rooms),
        (DOCUMENT_VERSION if special is not None else
         STREAM_DOCUMENT_VERSION if streams is not None else 1),
        schedules, mirror_sets, special_rooms,
    )


def parse_room_document(
    document: object, profile: GameProfile, revision: RevisionSpec
) -> SolomonRoomDocument:
    if not isinstance(document, Mapping):
        raise SolomonError("Solomon room document must be an object")
    version = document.get("schema_version")
    if type(version) is not int or version not in (1, STREAM_DOCUMENT_VERSION,
                                                   DOCUMENT_VERSION):
        raise SolomonError("unsupported Solomon room document schema version")
    expected = {
        "schema": DOCUMENT_SCHEMA,
        "schema_version": version,
        "game": profile.game_id,
        "revision": revision.revision_id,
        "source_sha256": revision.sha256,
    }
    for field, value in expected.items():
        if document.get(field) != value:
            raise SolomonError(f"Solomon room document {field} must be {value!r}")
    root_fields = set(expected) | {"width", "height", "rooms"}
    if version == DOCUMENT_VERSION:
        root_fields |= {"mirror_schedules", "mirror_enemy_sets", "special_rooms"}
    if set(document) != root_fields:
        raise SolomonError("Solomon room document has invalid fields")
    count, width, height, _, _ = _geometry(_resource(profile, revision))
    if version >= STREAM_DOCUMENT_VERSION \
            and _stream_resources(profile, revision) is None:
        raise SolomonError("Solomon room streams need a version-2 profile")
    if version == DOCUMENT_VERSION and _special_resources(profile, revision) is None:
        raise SolomonError("Spawn Mirror tables need a version-3 profile")
    if document.get("width") != width or document.get("height") != height:
        raise SolomonError("Solomon room geometry disagrees with the profile")
    raw_rooms = document.get("rooms")
    if not isinstance(raw_rooms, list) or len(raw_rooms) != count:
        raise SolomonError(f"Solomon room document must have {count} rooms")
    rooms = []
    for index, raw in enumerate(raw_rooms):
        fields = {"number", "brown", "white"}
        if version >= STREAM_DOCUMENT_VERSION:
            fields |= {"enemy_stream", "item_stream"}
        if not isinstance(raw, Mapping) or set(raw) != fields:
            raise SolomonError(f"room {index + 1} has invalid fields")
        if type(raw.get("number")) is not int or raw["number"] != index + 1:
            raise SolomonError(f"room {index + 1} has an invalid number")
        rooms.append(SolomonRoom(
            index + 1,
            _pack_plane(raw["brown"], width, height, f"room {index + 1}.brown"),
            _pack_plane(raw["white"], width, height, f"room {index + 1}.white"),
            (parse_enemy_stream(raw["enemy_stream"], f"room {index + 1}.enemy_stream")
             if version >= STREAM_DOCUMENT_VERSION else None),
            (parse_item_stream(raw["item_stream"], f"room {index + 1}.item_stream")
             if version >= STREAM_DOCUMENT_VERSION else None),
        ))
    schedules: tuple[MirrorSchedule, ...] = ()
    mirror_sets: tuple[MirrorEnemySet, ...] = ()
    special_rooms = None
    if version == DOCUMENT_VERSION:
        raw_schedules = document["mirror_schedules"]
        raw_sets = document["mirror_enemy_sets"]
        if not isinstance(raw_schedules, list) or len(raw_schedules) != 16:
            raise SolomonError("Spawn Mirror needs 16 schedules")
        if not isinstance(raw_sets, list) or len(raw_sets) != 17:
            raise SolomonError("Spawn Mirror needs 17 enemy sets")
        schedules = tuple(parse_mirror_schedule(record, f"mirror_schedules[{index}]")
                          for index, record in enumerate(raw_schedules))
        mirror_sets = tuple(parse_mirror_enemy_set(record, f"mirror_enemy_sets[{index}]")
                            for index, record in enumerate(raw_sets))
        special_rooms = parse_special_rooms(document["special_rooms"])
    return SolomonRoomDocument(
        profile.game_id, revision.revision_id, revision.sha256,
        width, height, tuple(rooms), version,
        schedules, mirror_sets, special_rooms,
    )


def _stream_changes(
    source: bytes,
    image,
    resource: ResourceSpec,
    records: list[EnemyStream] | list[ItemStream],
    encoder,
    source_sha256: str,
) -> tuple[list[Change], dict[str, int]]:
    count = len(records)
    pointer, start, end = _stream_geometry(resource, count)
    encoded = bytearray()
    offsets = []
    for record in records:
        offsets.append(start + len(encoded) + 0x8000)
        encoded.extend(encoder(record))
    capacity = end - start
    used = len(encoded)
    if used > capacity:
        raise SolomonError(
            f"{resource.resource_id} needs {used} bytes; arena capacity is {capacity}"
        )
    pointer_bytes = (
        bytes(value & 0xFF for value in offsets)
        + bytes(value >> 8 for value in offsets)
    )
    encoded.extend(image.prg[start + used:end])
    base = len(image.header) + len(image.trainer)
    changes = []
    for offset, replacement in ((pointer, pointer_bytes), (start, bytes(encoded))):
        file_offset = base + offset
        changes.append(Change(
            AddressRange(Address("file", file_offset), len(replacement)),
            source[file_offset:file_offset + len(replacement)],
            replacement,
            Provenance(source_sha256, resource.resource_id, "encode"),
        ))
    return changes, {"used_bytes": used, "capacity_bytes": capacity,
                     "free_bytes": capacity - used}


def build_rooms(
    source: bytes,
    document: SolomonRoomDocument,
    profile: GameProfile,
    revision: RevisionSpec,
) -> tuple[bytes, dict[str, object]]:
    if (document.game_id, document.revision_id, document.source_sha256) != (
        profile.game_id, revision.revision_id, revision.sha256
    ):
        raise SolomonError("Solomon room document identity mismatch")
    image = _source_image(source, revision)
    count, width, height, plane_size, offset = _geometry(_resource(profile, revision))
    if (len(document.rooms), document.width, document.height) != (count, width, height):
        raise SolomonError("Solomon room document geometry mismatch")
    if document.schema_version not in (1, STREAM_DOCUMENT_VERSION,
                                      DOCUMENT_VERSION):
        raise SolomonError("unsupported Solomon room document schema version")
    encoded = bytearray()
    for index, room in enumerate(document.rooms):
        if room.number != index + 1 or len(room.brown) != plane_size \
                or len(room.white) != plane_size:
            raise SolomonError(f"room {index + 1} has invalid plane size or number")
        encoded.extend(room.brown)
        encoded.extend(room.white)
    file_offset = len(image.header) + len(image.trainer) + offset
    expected = source[file_offset:file_offset + len(encoded)]
    changes = [Change(
        AddressRange(Address("file", file_offset), len(encoded)),
        expected,
        bytes(encoded),
        Provenance(revision.sha256, "room-blocks", "encode"),
    )]
    stream_reports: dict[str, dict[str, int]] = {}
    if document.schema_version >= STREAM_DOCUMENT_VERSION:
        resources = _stream_resources(profile, revision)
        if resources is None:
            raise SolomonError("Solomon room streams need a version-2 profile")
        for key, resource, records, encoder in (
            ("enemy_streams", resources[0],
             [room.enemy_stream for room in document.rooms], encode_enemy_stream),
            ("item_streams", resources[1],
             [room.item_stream for room in document.rooms], encode_item_stream),
        ):
            if any(record is None for record in records):
                raise SolomonError(f"{key} are missing from a version-2 document")
            stream_changes, report = _stream_changes(
                source, image, resource, records, encoder, revision.sha256
            )
            changes.extend(stream_changes)
            stream_reports[key] = report
    if document.schema_version == DOCUMENT_VERSION:
        resources = _special_resources(profile, revision)
        if resources is None or document.special_rooms is None \
                or len(document.mirror_schedules) != 16 \
                or len(document.mirror_enemy_sets) != 17:
            raise SolomonError("Spawn Mirror and special-room records are incomplete")
        for key, resource, records, encoder in (
            ("mirror_schedules", resources[0],
             list(document.mirror_schedules), encode_mirror_schedule),
            ("mirror_enemy_sets", resources[1],
             list(document.mirror_enemy_sets), encode_mirror_enemy_set),
        ):
            mirror_changes, report = _stream_changes(
                source, image, resource, records, encoder, revision.sha256
            )
            changes.extend(mirror_changes)
            stream_reports[key] = report
        offsets = _special_offsets(resources[2])
        encoded_special = encode_special_rooms(document.special_rooms)
        base = len(image.header) + len(image.trainer)
        for name, replacement in encoded_special.items():
            prg_offset = offsets[name]
            file_offset = base + prg_offset
            changes.append(Change(
                AddressRange(Address("file", file_offset), len(replacement)),
                source[file_offset:file_offset + len(replacement)],
                replacement,
                Provenance(revision.sha256, resources[2].resource_id, "encode"),
            ))
        stream_reports["special_rooms"] = {
            "encoded_bytes": sum(map(len, encoded_special.values()))
        }
    output = BuildPlan(revision.sha256, ChangeSet(tuple(changes))).apply(source)
    return output, {
        "schema": "retrolarium.solomon-room-build",
        "schema_version": 1,
        "game": profile.game_id,
        "revision": revision.revision_id,
        "source_sha256": revision.sha256,
        "output_sha256": hashlib.sha256(output).hexdigest(),
        "size": len(output),
        "changed_bytes": sum(left != right for left, right in zip(source, output)),
        "room_count": count,
        "room_block_file_offset": file_offset,
        "room_block_bytes": len(encoded),
        **stream_reports,
    }
