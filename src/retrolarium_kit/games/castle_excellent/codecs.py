"""Castle Excellent's resources in the NES resource bundle: room cells,
metasprite descriptors, fixed byte records and MML music."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from retrolarium_kit.core.authoring import AuthoringError, ResourceSpec
from retrolarium_kit.games.castle_excellent.mml import (
    CODEC as CASTLE_EXCELLENT_MML_MUSIC_CODEC,
    decode_music as decode_castle_excellent_mml_music,
    encode_music as encode_castle_excellent_mml_music,
)
from retrolarium_kit.platform.nes.resources import (
    Encoded,
    NesCodec,
    NesResourceError,
    _EncodedSpan,
    _byte,
    _integer,
    _span,
    register,
    register_counters,
)

CASTLE_EXCELLENT_ROOM_CELL_CODEC = "castle-excellent.room-cell-visuals.v1"
CASTLE_EXCELLENT_METASPRITE_CODEC = "castle-excellent.metasprite-descriptors.v1"
CASTLE_EXCELLENT_SUB_COMMAND_TEXT_CODEC = "castle-excellent.sub-command-text.v1"
CASTLE_EXCELLENT_SFX_PARAMETERS_CODEC = "castle-excellent.sfx-parameters.v1"
CASTLE_EXCELLENT_FIXED_RECORD_CODECS = (
    CASTLE_EXCELLENT_SUB_COMMAND_TEXT_CODEC, CASTLE_EXCELLENT_SFX_PARAMETERS_CODEC
)


def _decode_room_cell(image: bytes, resource: ResourceSpec) -> dict[str, object]:
    count = _integer(
        resource.parameters.get("cell_count"),
        f"{resource.resource_id}.cell_count",
        minimum=1,
    )
    spans = []
    for component in ("left", "right", "attributes"):
        offset = _integer(
            resource.parameters.get(f"{component}_file_offset"),
            f"{resource.resource_id}.{component}_file_offset",
        )
        payload = image[offset:offset + count]
        if len(payload) != count:
            raise NesResourceError(
                f"{resource.resource_id}.{component} exceeds the source image"
            )
        spans.append(payload)
    left, right, attributes = spans
    for index, value in enumerate(attributes):
        if value not in (0x00, 0x55, 0xAA, 0xFF):
            raise NesResourceError(
                f"{resource.resource_id}.records[{index}].palette is not replicated"
            )
    return {
        "id": resource.resource_id,
        "kind": resource.kind,
        "codec": resource.codec,
        "records": [
            {
                "index": index,
                "left_tile": left[index],
                "right_tile": right[index],
                "palette": attributes[index] & 3,
            }
            for index in range(count)
        ],
    }


def _parse_room_cell(raw: dict[str, Any], spec: ResourceSpec) -> Encoded:
    payload: bytes | None = None
    encoded_spans: tuple[_EncodedSpan, ...] | None = None
    expected_fields = {"id", "kind", "codec", "records"}
    count = _integer(
        spec.parameters.get("cell_count"),
        f"{spec.resource_id}.cell_count",
        minimum=1,
    )
    records = raw.get("records")
    if not isinstance(records, list) or len(records) != count:
        raise NesResourceError(
            f"{spec.resource_id}.records must contain {count} entries"
        )
    left = bytearray()
    right = bytearray()
    attributes = bytearray()
    for index, record in enumerate(records):
        context = f"{spec.resource_id}.records[{index}]"
        if not isinstance(record, dict) or set(record) != {
            "index", "left_tile", "right_tile", "palette"
        }:
            raise NesResourceError(f"{context} fields differ from contract")
        if record.get("index") != index:
            raise NesResourceError(f"{context}.index must be {index}")
        left.append(_byte(record.get("left_tile"), f"{context}.left_tile"))
        right.append(_byte(record.get("right_tile"), f"{context}.right_tile"))
        palette = _byte(
            record.get("palette"), f"{context}.palette", maximum=3
        )
        attributes.append(palette * 0x55)
    encoded_spans = tuple(
        _EncodedSpan(
            _integer(
                spec.parameters.get(f"{component}_file_offset"),
                f"{spec.resource_id}.{component}_file_offset",
            ),
            bytes(values),
        )
        for component, values in (
            ("left", left), ("right", right), ("attributes", attributes)
        )
    )
    return expected_fields, payload, encoded_spans


def _decode_metasprite(image: bytes, resource: ResourceSpec) -> dict[str, object]:
    count = _integer(
        resource.parameters.get("descriptor_count"),
        f"{resource.resource_id}.descriptor_count",
        minimum=1,
    )
    offset, size = _span(resource, count * 2)
    payload = image[offset:offset + size]
    if len(payload) != size:
        raise NesResourceError(
            f"{resource.resource_id} exceeds the source image"
        )
    return {
        "id": resource.resource_id,
        "kind": resource.kind,
        "codec": resource.codec,
        "descriptors": [
            {
                "index": index,
                "tile": payload[index * 2],
                "attributes": payload[index * 2 + 1],
            }
            for index in range(count)
        ],
    }


def _parse_metasprite(raw: dict[str, Any], spec: ResourceSpec) -> Encoded:
    payload: bytes | None = None
    encoded_spans: tuple[_EncodedSpan, ...] | None = None
    expected_fields = {"id", "kind", "codec", "descriptors"}
    count = _integer(
        spec.parameters.get("descriptor_count"),
        f"{spec.resource_id}.descriptor_count",
        minimum=1,
    )
    descriptors = raw.get("descriptors")
    if not isinstance(descriptors, list) or len(descriptors) != count:
        raise NesResourceError(
            f"{spec.resource_id}.descriptors must contain {count} entries"
        )
    encoded = bytearray()
    for index, descriptor in enumerate(descriptors):
        context = f"{spec.resource_id}.descriptors[{index}]"
        if not isinstance(descriptor, dict) or set(descriptor) != {
            "index", "tile", "attributes"
        }:
            raise NesResourceError(f"{context} fields differ from contract")
        if descriptor.get("index") != index:
            raise NesResourceError(f"{context}.index must be {index}")
        encoded.append(_byte(descriptor.get("tile"), f"{context}.tile"))
        encoded.append(_byte(
            descriptor.get("attributes"), f"{context}.attributes"
        ))
    payload = bytes(encoded)
    return expected_fields, payload, encoded_spans


def _decode_fixed_records(image: bytes, resource: ResourceSpec) -> dict[str, object]:
    names = resource.parameters.get("record_names")
    sizes = resource.parameters.get("record_sizes")
    if (not isinstance(names, list) or not names
            or any(not isinstance(name, str) or not name for name in names)
            or len(set(names)) != len(names)):
        raise NesResourceError(
            f"{resource.resource_id}.record_names must contain unique names"
        )
    if not isinstance(sizes, list) or len(sizes) != len(names):
        raise NesResourceError(
            f"{resource.resource_id}.record_sizes must match record_names"
        )
    parsed_sizes = [
        _integer(size, f"{resource.resource_id}.record_sizes[{index}]", minimum=1)
        for index, size in enumerate(sizes)
    ]
    offset, size = _span(resource, sum(parsed_sizes))
    payload = image[offset:offset + size]
    if len(payload) != size:
        raise NesResourceError(f"{resource.resource_id} exceeds the source image")
    records = []
    cursor = 0
    for name, record_size in zip(names, parsed_sizes):
        records.append({
            "id": name,
            "values": list(payload[cursor:cursor + record_size]),
        })
        cursor += record_size
    return {
        "id": resource.resource_id,
        "kind": resource.kind,
        "codec": resource.codec,
        "records": records,
    }


def _parse_fixed_records(raw: dict[str, Any], spec: ResourceSpec) -> Encoded:
    payload: bytes | None = None
    encoded_spans: tuple[_EncodedSpan, ...] | None = None
    expected_fields = {"id", "kind", "codec", "records"}
    names = spec.parameters.get("record_names")
    sizes = spec.parameters.get("record_sizes")
    if (not isinstance(names, list) or not names
            or any(not isinstance(name, str) or not name for name in names)
            or len(set(names)) != len(names)):
        raise NesResourceError(
            f"{spec.resource_id}.record_names must contain unique names"
        )
    if not isinstance(sizes, list) or len(sizes) != len(names):
        raise NesResourceError(
            f"{spec.resource_id}.record_sizes must match record_names"
        )
    parsed_sizes = [
        _integer(size, f"{spec.resource_id}.record_sizes[{index}]", minimum=1)
        for index, size in enumerate(sizes)
    ]
    records = raw.get("records")
    if not isinstance(records, list) or len(records) != len(names):
        raise NesResourceError(
            f"{spec.resource_id}.records must contain {len(names)} entries"
        )
    encoded = bytearray()
    for index, (record, name, record_size) in enumerate(
            zip(records, names, parsed_sizes)):
        context = f"{spec.resource_id}.records[{index}]"
        if not isinstance(record, dict) or set(record) != {"id", "values"}:
            raise NesResourceError(f"{context} fields differ from contract")
        if record.get("id") != name:
            raise NesResourceError(f"{context}.id must be {name!r}")
        values = record.get("values")
        if not isinstance(values, list) or len(values) != record_size:
            raise NesResourceError(
                f"{context}.values must contain {record_size} bytes"
            )
        encoded.extend(
            _byte(value, f"{context}.values[{value_index}]")
            for value_index, value in enumerate(values)
        )
    payload = bytes(encoded)
    return expected_fields, payload, encoded_spans


def _decode_mml_music(image: bytes, resource: ResourceSpec) -> dict[str, object]:
    try:
        decoded = decode_castle_excellent_mml_music(image, resource.parameters)
    except AuthoringError as exc:
        raise NesResourceError(str(exc)) from exc
    return {
        "id": resource.resource_id,
        "kind": resource.kind,
        "codec": resource.codec,
        **decoded,
    }


def _parse_mml_music(raw: dict[str, Any], spec: ResourceSpec) -> Encoded:
    payload: bytes | None = None
    encoded_spans: tuple[_EncodedSpan, ...] | None = None
    expected_fields = {
        "id", "kind", "codec", "capacity_bytes", "used_bytes",
        "mml_cpu_address", "playback_prefix_cpu_address", "channel_names",
        "playback_prefix", "track_sets", "streams",
    }
    try:
        encoded_spans = tuple(
            _EncodedSpan(offset, replacement)
            for offset, replacement in encode_castle_excellent_mml_music(
                {
                    key: raw.get(key)
                    for key in expected_fields - {"id", "kind", "codec"}
                },
                spec.parameters,
            )
        )
    except AuthoringError as exc:
        raise NesResourceError(str(exc)) from exc
    return expected_fields, payload, encoded_spans


def _counters(resources: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    def of(*codecs: str) -> list[Mapping[str, Any]]:
        return [item for item in resources if item.get("codec") in codecs]

    return {
        "room_cells": sum(len(item.get("records", [])) for item in of(CASTLE_EXCELLENT_ROOM_CELL_CODEC)),
        "metasprite_descriptors": sum(
            len(item.get("descriptors", [])) for item in of(CASTLE_EXCELLENT_METASPRITE_CODEC)),
        "fixed_byte_records": sum(
            len(item.get("records", [])) for item in of(*CASTLE_EXCELLENT_FIXED_RECORD_CODECS)),
        "fixed_byte_values": sum(
            len(record.get("values", []))
            for item in of(*CASTLE_EXCELLENT_FIXED_RECORD_CODECS) for record in item.get("records", [])),
        "castle_excellent_music_tracks": sum(len(item.get("track_sets", [])) for item in of(CASTLE_EXCELLENT_MML_MUSIC_CODEC)),
        "castle_excellent_mml_streams": sum(len(item.get("streams", [])) for item in of(CASTLE_EXCELLENT_MML_MUSIC_CODEC)),
        "castle_excellent_mml_used_bytes": sum(int(item.get("used_bytes", 0)) for item in of(CASTLE_EXCELLENT_MML_MUSIC_CODEC)),
    }


register(NesCodec(CASTLE_EXCELLENT_ROOM_CELL_CODEC, decode=_decode_room_cell, parse=_parse_room_cell))
register(NesCodec(CASTLE_EXCELLENT_METASPRITE_CODEC, decode=_decode_metasprite, parse=_parse_metasprite))
register(NesCodec(CASTLE_EXCELLENT_SUB_COMMAND_TEXT_CODEC, decode=_decode_fixed_records, parse=_parse_fixed_records))
register(NesCodec(CASTLE_EXCELLENT_SFX_PARAMETERS_CODEC, decode=_decode_fixed_records, parse=_parse_fixed_records))
register(NesCodec(CASTLE_EXCELLENT_MML_MUSIC_CODEC, decode=_decode_mml_music, parse=_parse_mml_music))
register_counters(_counters)
