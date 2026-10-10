"""Doraemon's resources in the NES resource bundle: World 2's stage, routes,
branches, chapters, screens and metatiles, metasprites, OAM streams, object
tables, room selectors and the block hierarchy."""

from __future__ import annotations

from typing import Any

from retrolarium_kit.core.authoring import AuthoringError, ResourceSpec
from retrolarium_kit.games.doraemon.behavior import (
    CODEC as DORAEMON_BEHAVIOR_CODEC,
    decode_behavior as decode_doraemon_behavior,
    encode_behavior as encode_doraemon_behavior,
)
from retrolarium_kit.games.doraemon.hierarchy import (
    CODEC as DORAEMON_HIERARCHY_CODEC,
    decode_hierarchy as decode_doraemon_hierarchy,
    encode_hierarchy as encode_doraemon_hierarchy,
    payload_size as doraemon_hierarchy_size,
)
from retrolarium_kit.games.doraemon.palettes import (
    ROOM_SELECTOR_CODEC as DORAEMON_ROOM_SELECTOR_CODEC,
    decode_room_selectors as decode_doraemon_room_selectors,
    encode_room_selectors as encode_doraemon_room_selectors,
)
from retrolarium_kit.games.doraemon.objects import (
    DESCRIPTORS_CODEC as DORAEMON_DESCRIPTORS_CODEC,
    ENEMY_PROPERTIES_CODEC as DORAEMON_ENEMY_PROPERTIES_CODEC,
    PLACEMENTS_CODEC as DORAEMON_PLACEMENTS_CODEC,
    REGISTRY_CODEC as DORAEMON_REGISTRY_CODEC,
    SCHEDULE_CODEC as DORAEMON_SCHEDULE_CODEC,
    SECRET_SCREENS_CODEC as DORAEMON_SECRET_SCREENS_CODEC,
    decode_schedule as decode_doraemon_schedule,
    encode_schedule as encode_doraemon_schedule,
    decode_secret_screens as decode_doraemon_secret_screens,
    encode_secret_screens as encode_doraemon_secret_screens,
    decode_registry as decode_doraemon_registry,
    encode_registry as encode_doraemon_registry,
    decode_descriptors as decode_doraemon_descriptors,
    decode_enemy_properties as decode_doraemon_enemy_properties,
    decode_placements as decode_doraemon_placements,
    encode_descriptors as encode_doraemon_descriptors,
    encode_enemy_properties as encode_doraemon_enemy_properties,
    encode_placements as encode_doraemon_placements,
)
from retrolarium_kit.games.doraemon.metasprites import (
    VARIABLE_CODEC as DORAEMON_METASPRITE_CODEC,
    WORLD2_ATTRIBUTES as DORAEMON_WORLD2_METASPRITE_ATTRIBUTES,
    WORLD2_CODEC as DORAEMON_WORLD2_METASPRITE_CODEC,
    WORLD2_RECORDS as DORAEMON_WORLD2_METASPRITE_RECORDS,
    decode_variable as decode_doraemon_metasprites,
    decode_world2 as decode_doraemon_world2_metasprites,
    encode_variable as encode_doraemon_metasprites,
    encode_world2 as encode_doraemon_world2_metasprites,
    OAM_STREAM_CODEC as DORAEMON_OAM_STREAM_CODEC,
    decode_oam_stream as decode_doraemon_oam_stream,
    encode_oam_stream as encode_doraemon_oam_stream,
)
from retrolarium_kit.games.doraemon.world2.metatiles import (
    CODEC as DORAEMON_WORLD2_METATILE_CODEC,
    COLLISION_SIZE as DORAEMON_WORLD2_COLLISION_SIZE,
    VISUAL_SIZE as DORAEMON_WORLD2_VISUAL_SIZE,
    decode_metatiles as decode_doraemon_world2_metatiles,
    encode_metatiles as encode_doraemon_world2_metatiles,
)
from retrolarium_kit.games.doraemon.world2.streams import (
    CODEC as DORAEMON_WORLD2_SCREEN_CODEC,
    PAYLOAD_SIZE as DORAEMON_WORLD2_SCREEN_SIZE,
    decode_screens as decode_doraemon_world2_screens,
    encode_screens as encode_doraemon_world2_screens,
)
from retrolarium_kit.games.doraemon.world2.stage import (
    CODEC as DORAEMON_WORLD2_STAGE_CODEC,
    SEQUENCE_SIZE as DORAEMON_WORLD2_STAGE_SEQUENCE_SIZE,
    START_SIZE as DORAEMON_WORLD2_STAGE_START_SIZE,
    decode_stage as decode_doraemon_world2_stage,
    encode_stage as encode_doraemon_world2_stage,
)
from retrolarium_kit.games.doraemon.world2.branches import (
    CODEC as DORAEMON_WORLD2_BRANCH_CODEC,
    PAYLOAD_SIZE as DORAEMON_WORLD2_BRANCH_SIZE,
    decode_branches as decode_doraemon_world2_branches,
    encode_branches as encode_doraemon_world2_branches,
)
from retrolarium_kit.games.doraemon.world2.chapters import (
    CODEC as DORAEMON_WORLD2_CHAPTER_CODEC,
    PAYLOAD_SIZE as DORAEMON_WORLD2_CHAPTER_SIZE,
    decode_chapters as decode_doraemon_world2_chapters,
    encode_chapters as encode_doraemon_world2_chapters,
)
from retrolarium_kit.platform.nes.resources import (
    Encoded,
    NesCodec,
    NesResourceError,
    _EncodedSpan,
    _integer,
    _span,
    register,
)


def _decode_world2_chapter(image: bytes, resource: ResourceSpec) -> dict[str, object]:
    offset, size = _span(resource, DORAEMON_WORLD2_CHAPTER_SIZE)
    try:
        decoded = decode_doraemon_world2_chapters(image[offset:offset + size])
    except AuthoringError as exc:
        raise NesResourceError(str(exc)) from exc
    return {"id": resource.resource_id, "kind": resource.kind,
            "codec": resource.codec, **decoded}


def _parse_world2_chapter(raw: dict[str, Any], spec: ResourceSpec) -> Encoded:
    payload: bytes | None = None
    encoded_spans: tuple[_EncodedSpan, ...] | None = None
    expected_fields = {"id", "kind", "codec",
                       "background_palette_ids", "sprite_offsets"}
    _span(spec, DORAEMON_WORLD2_CHAPTER_SIZE)
    try:
        payload = encode_doraemon_world2_chapters({
            "background_palette_ids": raw.get("background_palette_ids"),
            "sprite_offsets": raw.get("sprite_offsets"),
        })
    except AuthoringError as exc:
        raise NesResourceError(str(exc)) from exc
    return expected_fields, payload, encoded_spans


def _decode_world2_branch(image: bytes, resource: ResourceSpec) -> dict[str, object]:
    offset, size = _span(resource, DORAEMON_WORLD2_BRANCH_SIZE)
    try:
        decoded = decode_doraemon_world2_branches(image[offset:offset + size])
    except AuthoringError as exc:
        raise NesResourceError(str(exc)) from exc
    return {"id": resource.resource_id, "kind": resource.kind,
            "codec": resource.codec, **decoded}


def _parse_world2_branch(raw: dict[str, Any], spec: ResourceSpec) -> Encoded:
    payload: bytes | None = None
    encoded_spans: tuple[_EncodedSpan, ...] | None = None
    expected_fields = {"id", "kind", "codec", "branches"}
    _span(spec, DORAEMON_WORLD2_BRANCH_SIZE)
    try:
        payload = encode_doraemon_world2_branches(
            {"branches": raw.get("branches")}
        )
    except AuthoringError as exc:
        raise NesResourceError(str(exc)) from exc
    return expected_fields, payload, encoded_spans


def _decode_world2_stage(image: bytes, resource: ResourceSpec) -> dict[str, object]:
    offset, size = _span(resource, DORAEMON_WORLD2_STAGE_SEQUENCE_SIZE)
    start_offset = _integer(resource.parameters.get("start_file_offset"),
                            f"{resource.resource_id}.start_file_offset")
    start_size = _integer(resource.parameters.get("start_size"),
                          f"{resource.resource_id}.start_size", minimum=1)
    if start_size != DORAEMON_WORLD2_STAGE_START_SIZE:
        raise NesResourceError("World 2 stage needs three chapter starts")
    try:
        decoded = decode_doraemon_world2_stage(
            image[offset:offset + size],
            image[start_offset:start_offset + start_size],
        )
    except AuthoringError as exc:
        raise NesResourceError(str(exc)) from exc
    return {"id": resource.resource_id, "kind": resource.kind,
            "codec": resource.codec, **decoded}


def _parse_world2_stage(raw: dict[str, Any], spec: ResourceSpec) -> Encoded:
    payload: bytes | None = None
    encoded_spans: tuple[_EncodedSpan, ...] | None = None
    expected_fields = {"id", "kind", "codec", "start_offsets", "tokens"}
    offset, _ = _span(spec, DORAEMON_WORLD2_STAGE_SEQUENCE_SIZE)
    start_offset = _integer(spec.parameters.get("start_file_offset"),
                            f"{spec.resource_id}.start_file_offset")
    start_size = _integer(spec.parameters.get("start_size"),
                          f"{spec.resource_id}.start_size", minimum=1)
    if start_size != DORAEMON_WORLD2_STAGE_START_SIZE:
        raise NesResourceError("World 2 stage needs three chapter starts")
    try:
        sequence, starts = encode_doraemon_world2_stage({
            "start_offsets": raw.get("start_offsets"), "tokens": raw.get("tokens")
        })
    except AuthoringError as exc:
        raise NesResourceError(str(exc)) from exc
    encoded_spans = (_EncodedSpan(offset, sequence),
                     _EncodedSpan(start_offset, starts))
    return expected_fields, payload, encoded_spans


def _decode_world2_screen(image: bytes, resource: ResourceSpec) -> dict[str, object]:
    offset, size = _span(resource, DORAEMON_WORLD2_SCREEN_SIZE)
    try:
        decoded = decode_doraemon_world2_screens(image[offset:offset + size])
    except AuthoringError as exc:
        raise NesResourceError(str(exc)) from exc
    return {"id": resource.resource_id, "kind": resource.kind,
            "codec": resource.codec, **decoded}


def _parse_world2_screen(raw: dict[str, Any], spec: ResourceSpec) -> Encoded:
    payload: bytes | None = None
    encoded_spans: tuple[_EncodedSpan, ...] | None = None
    expected_fields = {"id", "kind", "codec", "raw_hex", "screens"}
    _span(spec, DORAEMON_WORLD2_SCREEN_SIZE)
    try:
        payload = encode_doraemon_world2_screens(
            {"raw_hex": raw.get("raw_hex"), "screens": raw.get("screens")}
        )
    except AuthoringError as exc:
        raise NesResourceError(str(exc)) from exc
    return expected_fields, payload, encoded_spans


def _decode_metasprite(image: bytes, resource: ResourceSpec) -> dict[str, object]:
    offset, size = _span(resource, _integer(resource.parameters.get("size"), f"{resource.resource_id}.size", minimum=2))
    records_offset = _integer(resource.parameters.get("records_file_offset"), f"{resource.resource_id}.records_file_offset")
    records_size = _integer(resource.parameters.get("records_size"), f"{resource.resource_id}.records_size", minimum=1)
    records_address = _integer(resource.parameters.get("records_address"), f"{resource.resource_id}.records_address")
    try:
        decoded = decode_doraemon_metasprites(
            image[offset:offset + size], image[records_offset:records_offset + records_size], records_address)
    except AuthoringError as exc:
        raise NesResourceError(str(exc)) from exc
    return {"id": resource.resource_id, "kind": resource.kind, "codec": resource.codec, **decoded}


def _parse_metasprite(raw: dict[str, Any], spec: ResourceSpec) -> Encoded:
    payload: bytes | None = None
    encoded_spans: tuple[_EncodedSpan, ...] | None = None
    expected_fields = {"id", "kind", "codec", "entries", "records"}
    offset, size = _span(spec, _integer(spec.parameters.get("size"), f"{spec.resource_id}.size", minimum=2))
    records_offset = _integer(spec.parameters.get("records_file_offset"), f"{spec.resource_id}.records_file_offset")
    records_size = _integer(spec.parameters.get("records_size"), f"{spec.resource_id}.records_size", minimum=1)
    records_address = _integer(spec.parameters.get("records_address"), f"{spec.resource_id}.records_address")
    try:
        index, records = encode_doraemon_metasprites(
            {"entries": raw.get("entries"), "records": raw.get("records")}, records_address, size, records_size)
    except AuthoringError as exc:
        raise NesResourceError(str(exc)) from exc
    encoded_spans = (_EncodedSpan(offset, index), _EncodedSpan(records_offset, records))
    return expected_fields, payload, encoded_spans


def _decode_world2_metasprite(image: bytes, resource: ResourceSpec) -> dict[str, object]:
    offset, size = _span(resource, DORAEMON_WORLD2_METASPRITE_RECORDS * 4)
    descriptors = _integer(resource.parameters.get("descriptors_file_offset"), f"{resource.resource_id}.descriptors_file_offset")
    attributes = _integer(resource.parameters.get("attributes_file_offset"), f"{resource.resource_id}.attributes_file_offset")
    try:
        decoded = decode_doraemon_world2_metasprites(
            image[offset:offset + size],
            image[descriptors:descriptors + DORAEMON_WORLD2_METASPRITE_RECORDS],
            image[attributes:attributes + DORAEMON_WORLD2_METASPRITE_ATTRIBUTES])
    except AuthoringError as exc:
        raise NesResourceError(str(exc)) from exc
    return {"id": resource.resource_id, "kind": resource.kind, "codec": resource.codec, **decoded}


def _parse_world2_metasprite(raw: dict[str, Any], spec: ResourceSpec) -> Encoded:
    payload: bytes | None = None
    encoded_spans: tuple[_EncodedSpan, ...] | None = None
    expected_fields = {"id", "kind", "codec", "tiles", "descriptors", "attributes"}
    offset, _ = _span(spec, DORAEMON_WORLD2_METASPRITE_RECORDS * 4)
    descriptors = _integer(spec.parameters.get("descriptors_file_offset"), f"{spec.resource_id}.descriptors_file_offset")
    attributes = _integer(spec.parameters.get("attributes_file_offset"), f"{spec.resource_id}.attributes_file_offset")
    try:
        tiles, descriptor_bytes, attribute_bytes = encode_doraemon_world2_metasprites(
            {key: raw.get(key) for key in ("tiles", "descriptors", "attributes")})
    except AuthoringError as exc:
        raise NesResourceError(str(exc)) from exc
    encoded_spans = (_EncodedSpan(offset, tiles), _EncodedSpan(descriptors, descriptor_bytes),
                     _EncodedSpan(attributes, attribute_bytes))
    return expected_fields, payload, encoded_spans


def _decode_world2_metatile(image: bytes, resource: ResourceSpec) -> dict[str, object]:
    offset, size = _span(resource, DORAEMON_WORLD2_VISUAL_SIZE)
    collision_offset = _integer(resource.parameters.get("collision_file_offset"),
                                f"{resource.resource_id}.collision_file_offset")
    collision_size = _integer(resource.parameters.get("collision_size"),
                              f"{resource.resource_id}.collision_size", minimum=1)
    if collision_size != DORAEMON_WORLD2_COLLISION_SIZE:
        raise NesResourceError("World 2 collision table needs 26 bytes")
    try:
        decoded = decode_doraemon_world2_metatiles(
            image[offset:offset + size],
            image[collision_offset:collision_offset + collision_size],
        )
    except AuthoringError as exc:
        raise NesResourceError(str(exc)) from exc
    return {"id": resource.resource_id, "kind": resource.kind,
            "codec": resource.codec, **decoded}


def _parse_world2_metatile(raw: dict[str, Any], spec: ResourceSpec) -> Encoded:
    payload: bytes | None = None
    encoded_spans: tuple[_EncodedSpan, ...] | None = None
    expected_fields = {"id", "kind", "codec", "palettes", "tiles", "solid"}
    offset, _ = _span(spec, DORAEMON_WORLD2_VISUAL_SIZE)
    collision_offset = _integer(spec.parameters.get("collision_file_offset"),
                                f"{spec.resource_id}.collision_file_offset")
    collision_size = _integer(spec.parameters.get("collision_size"),
                              f"{spec.resource_id}.collision_size", minimum=1)
    if collision_size != DORAEMON_WORLD2_COLLISION_SIZE:
        raise NesResourceError("World 2 collision table needs 26 bytes")
    try:
        visual, collision = encode_doraemon_world2_metatiles(
            {key: raw.get(key) for key in ("palettes", "tiles", "solid")}
        )
    except AuthoringError as exc:
        raise NesResourceError(str(exc)) from exc
    encoded_spans = (
        _EncodedSpan(offset, visual),
        _EncodedSpan(collision_offset, collision),
    )
    return expected_fields, payload, encoded_spans


def _decode_tables(image: bytes, resource: ResourceSpec) -> dict[str, object]:
    size = _integer(resource.parameters.get("size"), f"{resource.resource_id}.size", minimum=4)
    offset, _ = _span(resource, size)
    payload = image[offset:offset + size]
    try:
        if resource.codec == DORAEMON_PLACEMENTS_CODEC:
            fields: dict[str, object] = {"records": decode_doraemon_placements(payload)}
        elif resource.codec == DORAEMON_ENEMY_PROPERTIES_CODEC:
            fields = decode_doraemon_enemy_properties(payload)
        elif resource.codec == DORAEMON_REGISTRY_CODEC:
            fields = {"records": decode_doraemon_registry(payload)}
        elif resource.codec == DORAEMON_SECRET_SCREENS_CODEC:
            fields = {"screens": decode_doraemon_secret_screens(payload)}
        elif resource.codec == DORAEMON_SCHEDULE_CODEC:
            fields = {"rooms": decode_doraemon_schedule(payload)}
        else:
            fields = {"records": decode_doraemon_descriptors(payload)}
    except AuthoringError as exc:
        raise NesResourceError(str(exc)) from exc
    return {"id": resource.resource_id, "kind": resource.kind,
            "codec": resource.codec, **fields}


def _parse_tables(raw: dict[str, Any], spec: ResourceSpec) -> Encoded:
    payload: bytes | None = None
    encoded_spans: tuple[_EncodedSpan, ...] | None = None
    size = _integer(spec.parameters.get("size"), f"{spec.resource_id}.size", minimum=4)
    _span(spec, size)
    try:
        if spec.codec == DORAEMON_PLACEMENTS_CODEC:
            expected_fields = {"id", "kind", "codec", "records"}
            payload = encode_doraemon_placements(
                raw.get("records"), size, sorted_tail=spec.parameters.get("sorted_tail") is True)
        elif spec.codec == DORAEMON_REGISTRY_CODEC:
            expected_fields = {"id", "kind", "codec", "records"}
            payload = encode_doraemon_registry(raw.get("records"))
        elif spec.codec == DORAEMON_SECRET_SCREENS_CODEC:
            expected_fields = {"id", "kind", "codec", "screens"}
            payload = encode_doraemon_secret_screens(raw.get("screens"))
        elif spec.codec == DORAEMON_SCHEDULE_CODEC:
            expected_fields = {"id", "kind", "codec", "rooms"}
            payload = encode_doraemon_schedule(raw.get("rooms"))
        elif spec.codec == DORAEMON_ENEMY_PROPERTIES_CODEC:
            expected_fields = {"id", "kind", "codec", "metasprites", "render_flags", "health"}
            payload = encode_doraemon_enemy_properties(
                {name: raw.get(name) for name in ("metasprites", "render_flags", "health")})
        else:
            expected_fields = {"id", "kind", "codec", "records"}
            payload = encode_doraemon_descriptors(raw.get("records"))
    except AuthoringError as exc:
        raise NesResourceError(str(exc)) from exc
    return expected_fields, payload, encoded_spans


def _decode_oam_stream(image: bytes, resource: ResourceSpec) -> dict[str, object]:
    size = _integer(resource.parameters.get("size"), f"{resource.resource_id}.size", minimum=5)
    offset, _ = _span(resource, size)
    try:
        sprites = decode_doraemon_oam_stream(image[offset:offset + size])
    except AuthoringError as exc:
        raise NesResourceError(str(exc)) from exc
    return {"id": resource.resource_id, "kind": resource.kind,
            "codec": resource.codec, "sprites": sprites}


def _parse_oam_stream(raw: dict[str, Any], spec: ResourceSpec) -> Encoded:
    payload: bytes | None = None
    encoded_spans: tuple[_EncodedSpan, ...] | None = None
    expected_fields = {"id", "kind", "codec", "sprites"}
    size = _integer(spec.parameters.get("size"), f"{spec.resource_id}.size", minimum=5)
    _span(spec, size)
    try:
        payload = encode_doraemon_oam_stream(raw.get("sprites"), size)
    except AuthoringError as exc:
        raise NesResourceError(str(exc)) from exc
    return expected_fields, payload, encoded_spans


def _decode_room_selector(image: bytes, resource: ResourceSpec) -> dict[str, object]:
    offset, size = _span(resource, 64)
    try:
        selectors = decode_doraemon_room_selectors(image[offset:offset + size])
    except AuthoringError as exc:
        raise NesResourceError(str(exc)) from exc
    return {
        "id": resource.resource_id,
        "kind": resource.kind,
        "codec": resource.codec,
        "selectors": selectors,
    }


def _parse_room_selector(raw: dict[str, Any], spec: ResourceSpec) -> Encoded:
    payload: bytes | None = None
    encoded_spans: tuple[_EncodedSpan, ...] | None = None
    expected_fields = {"id", "kind", "codec", "selectors"}
    _span(spec, 64)
    try:
        payload = encode_doraemon_room_selectors(raw.get("selectors"))
    except AuthoringError as exc:
        raise NesResourceError(str(exc)) from exc
    return expected_fields, payload, encoded_spans


def _stream_sizes(resource: ResourceSpec) -> list[int]:
    sizes = resource.parameters.get("stream_sizes")
    if not isinstance(sizes, list) or not sizes or any(type(size) is not int or size < 1 for size in sizes):
        raise NesResourceError(f"{resource.resource_id}.stream_sizes must list positive sizes")
    return sizes


def _decode_behavior(image: bytes, resource: ResourceSpec) -> dict[str, object]:
    sizes = _stream_sizes(resource)
    offset, size = _span(resource, sum(sizes))
    try:
        streams = decode_doraemon_behavior(image[offset:offset + size], sizes)
    except AuthoringError as exc:
        raise NesResourceError(str(exc)) from exc
    return {"id": resource.resource_id, "kind": resource.kind, "codec": resource.codec, "streams": streams}


def _parse_behavior(raw: dict[str, Any], spec: ResourceSpec) -> Encoded:
    sizes = _stream_sizes(spec)
    _span(spec, sum(sizes))
    try:
        payload = encode_doraemon_behavior(raw.get("streams"), sizes)
    except AuthoringError as exc:
        raise NesResourceError(str(exc)) from exc
    return {"id", "kind", "codec", "streams"}, payload, None


def _decode_hierarchy(image: bytes, resource: ResourceSpec) -> dict[str, object]:
    world = resource.parameters.get("world")
    try:
        size = doraemon_hierarchy_size(world)
        offset, _ = _span(resource, size)
        payload = image[offset : offset + size]
        decoded = decode_doraemon_hierarchy(payload, world)
    except AuthoringError as exc:
        raise NesResourceError(str(exc)) from exc
    return {
        "id": resource.resource_id,
        "kind": resource.kind,
        "codec": resource.codec,
        **decoded,
    }


def _parse_hierarchy(raw: dict[str, Any], spec: ResourceSpec) -> Encoded:
    payload: bytes | None = None
    encoded_spans: tuple[_EncodedSpan, ...] | None = None
    expected_fields = {
        "id", "kind", "codec", "world", "attributes",
        "small_blocks", "big_blocks", "maps"
    }
    world = spec.parameters.get("world")
    try:
        size = doraemon_hierarchy_size(world)
        _span(spec, size)
        payload = encode_doraemon_hierarchy(
            {key: raw.get(key) for key in expected_fields - {
                "id", "kind", "codec"
            }}, world
        )
    except AuthoringError as exc:
        raise NesResourceError(str(exc)) from exc
    return expected_fields, payload, encoded_spans


register(NesCodec(DORAEMON_WORLD2_CHAPTER_CODEC, decode=_decode_world2_chapter, parse=_parse_world2_chapter))
register(NesCodec(DORAEMON_WORLD2_BRANCH_CODEC, decode=_decode_world2_branch, parse=_parse_world2_branch))
register(NesCodec(DORAEMON_WORLD2_STAGE_CODEC, decode=_decode_world2_stage, parse=_parse_world2_stage))
register(NesCodec(DORAEMON_WORLD2_SCREEN_CODEC, decode=_decode_world2_screen, parse=_parse_world2_screen))
register(NesCodec(DORAEMON_METASPRITE_CODEC, decode=_decode_metasprite, parse=_parse_metasprite))
register(NesCodec(DORAEMON_WORLD2_METASPRITE_CODEC, decode=_decode_world2_metasprite, parse=_parse_world2_metasprite))
register(NesCodec(DORAEMON_WORLD2_METATILE_CODEC, decode=_decode_world2_metatile, parse=_parse_world2_metatile))
register(NesCodec(DORAEMON_PLACEMENTS_CODEC, decode=_decode_tables, parse=_parse_tables))
register(NesCodec(DORAEMON_ENEMY_PROPERTIES_CODEC, decode=_decode_tables, parse=_parse_tables))
register(NesCodec(DORAEMON_DESCRIPTORS_CODEC, decode=_decode_tables, parse=_parse_tables))
register(NesCodec(DORAEMON_REGISTRY_CODEC, decode=_decode_tables, parse=_parse_tables))
register(NesCodec(DORAEMON_SECRET_SCREENS_CODEC, decode=_decode_tables, parse=_parse_tables))
register(NesCodec(DORAEMON_SCHEDULE_CODEC, decode=_decode_tables, parse=_parse_tables))
register(NesCodec(DORAEMON_OAM_STREAM_CODEC, decode=_decode_oam_stream, parse=_parse_oam_stream))
register(NesCodec(DORAEMON_ROOM_SELECTOR_CODEC, decode=_decode_room_selector, parse=_parse_room_selector))
register(NesCodec(DORAEMON_HIERARCHY_CODEC, decode=_decode_hierarchy, parse=_parse_hierarchy))
register(NesCodec(DORAEMON_BEHAVIOR_CODEC, decode=_decode_behavior, parse=_parse_behavior))
