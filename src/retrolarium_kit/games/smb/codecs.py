"""Super Mario Bros.' resources in the NES resource bundle: worlds, levels,
player frames, music, envelopes and sound-effect tables."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from retrolarium_kit.core.authoring import AuthoringError, GameProfile, ResourceSpec, RevisionSpec, stable_identifier
from retrolarium_kit.games.smb.world import (
    CODEC as SMB_WORLD_CODEC,
    decode_world as decode_smb_world,
    encode_world as encode_smb_world,
)
from retrolarium_kit.games.smb.level import (
    CODEC as SMB_LEVEL_CODEC,
    decode_levels as decode_smb_levels,
    encode_level_pointers as encode_smb_level_pointers,
    encode_levels as encode_smb_levels,
    stream_pool as smb_level_stream_pool,
)
from retrolarium_kit.games.smb.player_frames import (
    CODEC as SMB_PLAYER_FRAME_CODEC,
    ENCODED_SIZE as SMB_PLAYER_FRAME_SIZE,
    decode_player_frames,
    encode_player_frames,
)
from retrolarium_kit.games.smb.apu import (
    CODEC as SMB_APU_CODEC,
    decode_music as decode_smb_music,
    encode_music as encode_smb_music,
    parse_layout as parse_smb_apu_layout,
)
from retrolarium_kit.games.smb.apu_envelope import (
    CODEC as SMB_APU_ENVELOPE_CODEC,
    decode_envelope as decode_smb_apu_envelope,
    encode_envelope as encode_smb_apu_envelope,
)
from retrolarium_kit.games.smb.sfx_tables import (
    CODEC as SMB_SFX_TABLES_CODEC,
    decode as decode_smb_sfx_tables,
    encode as encode_smb_sfx_tables,
)
from retrolarium_kit.platform.nes.resources import (
    Encoded,
    NesCodec,
    NesResourceError,
    _EncodedSpan,
    _ResourceSpan,
    _encoded_resource_spans,
    _integer,
    _read_resource_spans,
    _span,
    register,
    register_counters,
)


def _level_stream_spans(resource: ResourceSpec, stream_kind: str) -> tuple[_ResourceSpan, ...]:
    capacities_value = resource.parameters.get(f"{stream_kind}_capacities")
    if not isinstance(capacities_value, list) or not capacities_value:
        raise NesResourceError(f"{resource.resource_id}.{stream_kind}_capacities must be a non-empty array")
    capacities = tuple(
        _integer(
            value,
            f"{resource.resource_id}.{stream_kind}_capacities[{index}]",
            minimum=1,
        )
        for index, value in enumerate(capacities_value)
    )
    spans_value = resource.parameters.get(f"{stream_kind}_stream_spans")
    if spans_value is None:
        offset = _integer(
            resource.parameters.get(f"{stream_kind}_file_offset"),
            f"{resource.resource_id}.{stream_kind}_file_offset",
        )
        size = _integer(
            resource.parameters.get(f"{stream_kind}_size"),
            f"{resource.resource_id}.{stream_kind}_size",
            minimum=1,
        )
        if size != sum(capacities):
            raise NesResourceError(f"{resource.resource_id}.{stream_kind}_size is {size}, expected {sum(capacities)}")
        return (_ResourceSpan(offset, size),)
    if not isinstance(spans_value, list) or len(spans_value) != len(capacities):
        raise NesResourceError(
            f"{resource.resource_id}.{stream_kind}_stream_spans must contain {len(capacities)} entries"
        )
    result = []
    for index, (value, capacity) in enumerate(zip(spans_value, capacities)):
        context = f"{resource.resource_id}.{stream_kind}_stream_spans[{index}]"
        if not isinstance(value, dict) or set(value) != {"payload", "offset", "size"}:
            raise NesResourceError(f"{context} fields differ from contract")
        try:
            payload_id = stable_identifier(value.get("payload"), f"{context}.payload")
        except AuthoringError as exc:
            raise NesResourceError(str(exc)) from exc
        size = _integer(value.get("size"), f"{context}.size", minimum=1)
        if size != capacity:
            raise NesResourceError(f"{context}.size is {size}, expected stream capacity {capacity}")
        result.append(
            _ResourceSpan(
                _integer(value.get("offset"), f"{context}.offset"),
                size,
                payload_id,
            )
        )
    return tuple(result)


def _level_rendering(resource: ResourceSpec) -> str:
    value = resource.parameters.get("rendering", "smb1")
    if value not in {"smb1", "ann", "smb2"}:
        raise NesResourceError(f"{resource.resource_id}.rendering must be smb1, ann or smb2")
    return value


def _world_component_span(resource: ResourceSpec, component: str) -> _ResourceSpan:
    expected_size = _integer(
        resource.parameters.get(f"{component}_size"),
        f"{resource.resource_id}.{component}_size",
        minimum=1,
    )
    value = resource.parameters.get(f"{component}_span")
    if value is None:
        return _ResourceSpan(
            _integer(
                resource.parameters.get(f"{component}_file_offset"),
                f"{resource.resource_id}.{component}_file_offset",
            ),
            expected_size,
        )
    context = f"{resource.resource_id}.{component}_span"
    if not isinstance(value, dict) or set(value) != {"payload", "offset", "size"}:
        raise NesResourceError(f"{context} fields differ from contract")
    try:
        payload_id = stable_identifier(value.get("payload"), f"{context}.payload")
    except AuthoringError as exc:
        raise NesResourceError(str(exc)) from exc
    size = _integer(value.get("size"), f"{context}.size", minimum=1)
    if size != expected_size:
        raise NesResourceError(f"{context}.size is {size}, expected {expected_size}")
    return _ResourceSpan(
        _integer(value.get("offset"), f"{context}.offset"),
        size,
        payload_id,
    )


def _decode_world(
    source: bytes,
    profile: GameProfile,
    revision: RevisionSpec,
    resource: ResourceSpec,
) -> dict[str, object]:
    routing = _read_resource_spans(
        source,
        profile,
        revision,
        resource,
        (_world_component_span(resource, "routing"),),
    )
    physics = _read_resource_spans(
        source,
        profile,
        revision,
        resource,
        (_world_component_span(resource, "physics"),),
    )
    try:
        decoded = decode_smb_world(routing, physics, resource.parameters)
    except AuthoringError as exc:
        raise NesResourceError(str(exc)) from exc
    return {
        "id": resource.resource_id,
        "kind": resource.kind,
        "codec": resource.codec,
        **decoded,
    }


def _decode_level(
    source: bytes,
    profile: GameProfile,
    revision: RevisionSpec,
    resource: ResourceSpec,
) -> dict[str, object]:
    area = _read_resource_spans(
        source,
        profile,
        revision,
        resource,
        _level_stream_spans(resource, "area"),
    )
    enemy = _read_resource_spans(
        source,
        profile,
        revision,
        resource,
        _level_stream_spans(resource, "enemy"),
    )
    try:
        decoded = decode_smb_levels(area, enemy, resource.parameters)
    except AuthoringError as exc:
        raise NesResourceError(str(exc)) from exc
    result = {
        "id": resource.resource_id,
        "kind": resource.kind,
        "codec": resource.codec,
        "rendering": _level_rendering(resource),
        **decoded,
    }
    world_resource_id = resource.parameters.get("world_resource")
    if world_resource_id is not None:
        result["world_resource"] = world_resource_id
    try:
        pooled = smb_level_stream_pool(resource.parameters) is not None
    except AuthoringError as exc:
        raise NesResourceError(str(exc)) from exc
    if pooled:
        # Editors size areas against the shared range, not each capacity.
        result["capacity_pool"] = True
    return result


def _decode_player_frame(image: bytes, resource: ResourceSpec) -> dict[str, object]:
    offset, size = _span(resource, SMB_PLAYER_FRAME_SIZE)
    payload = image[offset : offset + size]
    if len(payload) != size:
        raise NesResourceError(f"{resource.resource_id} exceeds the source image")
    try:
        decoded = decode_player_frames(payload, resource.parameters)
    except AuthoringError as exc:
        raise NesResourceError(str(exc)) from exc
    return {
        "id": resource.resource_id,
        "kind": resource.kind,
        "codec": resource.codec,
        **decoded,
    }


def _parse_player_frame(raw: dict[str, Any], spec: ResourceSpec) -> Encoded:
    payload: bytes | None = None
    encoded_spans: tuple[_EncodedSpan, ...] | None = None
    expected_fields = {
        "id",
        "kind",
        "codec",
        "frame_offsets",
        "frame_names",
        "tile_base",
        "blank_tile",
        "frames",
        "swim_kick_tiles",
    }
    try:
        payload = encode_player_frames(
            {key: raw.get(key) for key in expected_fields - {"id", "kind", "codec"}},
            spec.parameters,
        )
    except AuthoringError as exc:
        raise NesResourceError(str(exc)) from exc
    return expected_fields, payload, encoded_spans


def _decode_apu(image: bytes, resource: ResourceSpec) -> dict[str, object]:
    try:
        expected = parse_smb_apu_layout(resource.parameters).encoded_size
        offset, size = _span(resource, expected)
        payload = image[offset : offset + size]
        if len(payload) != size:
            raise NesResourceError(f"{resource.resource_id} exceeds the source image")
        decoded = decode_smb_music(payload, resource.parameters)
    except AuthoringError as exc:
        raise NesResourceError(str(exc)) from exc
    return {
        "id": resource.resource_id,
        "kind": resource.kind,
        "codec": resource.codec,
        **decoded,
    }


def _parse_apu(raw: dict[str, Any], spec: ResourceSpec) -> Encoded:
    payload: bytes | None = None
    encoded_spans: tuple[_EncodedSpan, ...] | None = None
    expected_fields = {
        "id",
        "kind",
        "codec",
        "selectors",
        "ground_schedule",
        "headers",
        "shared_stream_arena",
        "note_periods",
        "note_lengths",
        "envelopes",
    }
    try:
        payload = encode_smb_music(
            {key: raw.get(key) for key in expected_fields - {"id", "kind", "codec"}},
            spec.parameters,
        )
    except AuthoringError as exc:
        raise NesResourceError(str(exc)) from exc
    return expected_fields, payload, encoded_spans


def _decode_sfx_tables(image: bytes, resource: ResourceSpec) -> dict[str, object]:
    try:
        fields = decode_smb_sfx_tables(image, resource.parameters)
    except AuthoringError as exc:
        raise NesResourceError(str(exc)) from exc
    return {"id": resource.resource_id, "kind": resource.kind, "codec": resource.codec, **fields}


def _parse_sfx_tables(raw: dict[str, Any], spec: ResourceSpec) -> Encoded:
    payload: bytes | None = None
    encoded_spans: tuple[_EncodedSpan, ...] | None = None
    expected_fields = {"id", "kind", "codec", "records"}
    try:
        encoded_spans = tuple(
            _EncodedSpan(offset, table) for offset, table in encode_smb_sfx_tables(raw.get("records"), spec.parameters)
        )
    except AuthoringError as exc:
        raise NesResourceError(str(exc)) from exc
    return expected_fields, payload, encoded_spans


def _decode_apu_envelope(image: bytes, resource: ResourceSpec) -> dict[str, object]:
    try:
        size = _integer(
            resource.parameters.get("size"),
            f"{resource.resource_id}.size",
            minimum=1,
        )
        offset, _size = _span(resource, size)
        payload = image[offset : offset + size]
        if len(payload) != size:
            raise NesResourceError(f"{resource.resource_id} exceeds the source image")
        decoded = decode_smb_apu_envelope(payload, resource.parameters)
    except AuthoringError as exc:
        raise NesResourceError(str(exc)) from exc
    return {
        "id": resource.resource_id,
        "kind": resource.kind,
        "codec": resource.codec,
        **decoded,
    }


def _parse_apu_envelope(raw: dict[str, Any], spec: ResourceSpec) -> Encoded:
    payload: bytes | None = None
    encoded_spans: tuple[_EncodedSpan, ...] | None = None
    expected_fields = {"id", "kind", "codec", "steps"}
    try:
        payload = encode_smb_apu_envelope({"steps": raw.get("steps")}, spec.parameters)
    except AuthoringError as exc:
        raise NesResourceError(str(exc)) from exc
    return expected_fields, payload, encoded_spans


def _parse_world(raw: dict[str, Any], spec: ResourceSpec) -> Encoded:
    payload: bytes | None = None
    encoded_spans: tuple[_EncodedSpan, ...] | None = None
    expected_fields = {"id", "kind", "codec", "area_types", "worlds", "physics_tables"}
    try:
        routing, physics = encode_smb_world(
            {key: raw.get(key) for key in expected_fields - {"id", "kind", "codec"}},
            spec.parameters,
        )
    except AuthoringError as exc:
        raise NesResourceError(str(exc)) from exc
    routing_size = _integer(
        spec.parameters.get("routing_size"),
        f"{spec.resource_id}.routing_size",
        minimum=1,
    )
    physics_size = _integer(
        spec.parameters.get("physics_size"),
        f"{spec.resource_id}.physics_size",
        minimum=1,
    )
    if len(routing) != routing_size or len(physics) != physics_size:
        raise NesResourceError(f"{spec.resource_id} encoded sizes differ from the profile")
    encoded_spans = (
        *_encoded_resource_spans(routing, (_world_component_span(spec, "routing"),)),
        *_encoded_resource_spans(physics, (_world_component_span(spec, "physics"),)),
    )
    return expected_fields, payload, encoded_spans


def _parse_level(raw: dict[str, Any], spec: ResourceSpec) -> Encoded:
    payload: bytes | None = None
    encoded_spans: tuple[_EncodedSpan, ...] | None = None
    expected_fields = {"id", "kind", "codec", "rendering", "area_types", "areas"}
    rendering = _level_rendering(spec)
    if raw.get("rendering") != rendering:
        raise NesResourceError(f"{spec.resource_id}.rendering differs from the profile")
    world_resource_id = spec.parameters.get("world_resource")
    if world_resource_id is not None:
        expected_fields.add("world_resource")
        if raw.get("world_resource") != world_resource_id:
            raise NesResourceError(f"{spec.resource_id}.world_resource differs from the profile")
    try:
        pooled = smb_level_stream_pool(spec.parameters) is not None
    except AuthoringError as exc:
        raise NesResourceError(str(exc)) from exc
    # Documents decoded before pooling lack the flag; it is optional.
    if "capacity_pool" in raw:
        expected_fields.add("capacity_pool")
        if raw.get("capacity_pool") is not pooled:
            raise NesResourceError(f"{spec.resource_id}.capacity_pool differs from the profile")
    level_value = {
        key: raw.get(key)
        for key in expected_fields - {"id", "kind", "codec", "rendering", "world_resource", "capacity_pool"}
    }
    try:
        area, enemy = encode_smb_levels(level_value, spec.parameters)
        pointers = encode_smb_level_pointers(level_value, spec.parameters)
    except AuthoringError as exc:
        raise NesResourceError(str(exc)) from exc
    encoded_spans = (
        *_encoded_resource_spans(area, _level_stream_spans(spec, "area")),
        *_encoded_resource_spans(enemy, _level_stream_spans(spec, "enemy")),
        *(_EncodedSpan(offset, table) for offset, table in pointers),
    )
    return expected_fields, payload, encoded_spans


def _check_level(item: ResourceSpec, resources_by_id: Mapping[str, ResourceSpec]) -> None:
    world_resource_id = item.parameters.get("world_resource")
    if world_resource_id is None:
        return
    if not isinstance(world_resource_id, str) or not world_resource_id:
        raise NesResourceError(f"{item.resource_id}.world_resource must be a non-empty string")
    world_resource = resources_by_id.get(world_resource_id)
    if world_resource is None or world_resource.codec != SMB_WORLD_CODEC:
        raise NesResourceError(f"{item.resource_id}.world_resource must name an SMB world resource")


def _counters(resources: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    def of(*codecs: str) -> list[Mapping[str, Any]]:
        return [item for item in resources if item.get("codec") in codecs]

    worlds = of(SMB_WORLD_CODEC)
    levels = of(SMB_LEVEL_CODEC)
    return {
        "actor_frames": sum(len(item.get("frames", [])) for item in of(SMB_PLAYER_FRAME_CODEC)),
        "smb_music_headers": sum(len(item.get("headers", [])) for item in of(SMB_APU_CODEC)),
        "smb_music_arena_bytes": sum(len(item.get("shared_stream_arena", [])) for item in of(SMB_APU_CODEC)),
        "smb_worlds": sum(len(item.get("worlds", [])) for item in worlds),
        "smb_routes": sum(len(world.get("areas", [])) for item in worlds for world in item.get("worlds", [])),
        "smb_physics_tables": sum(len(item.get("physics_tables", [])) for item in worlds),
        "smb_levels": sum(len(item.get("areas", [])) for item in levels),
        "smb_area_objects": sum(len(area.get("objects", [])) for item in levels for area in item.get("areas", [])),
        "smb_enemy_records": sum(len(area.get("enemies", [])) for item in levels for area in item.get("areas", [])),
    }


register(NesCodec(SMB_PLAYER_FRAME_CODEC, decode=_decode_player_frame, parse=_parse_player_frame))
register(NesCodec(SMB_APU_CODEC, decode=_decode_apu, parse=_parse_apu))
register(NesCodec(SMB_SFX_TABLES_CODEC, decode=_decode_sfx_tables, parse=_parse_sfx_tables))
register(NesCodec(SMB_APU_ENVELOPE_CODEC, decode=_decode_apu_envelope, parse=_parse_apu_envelope))
register(NesCodec(SMB_WORLD_CODEC, decode_source=_decode_world, parse=_parse_world))
register(NesCodec(SMB_LEVEL_CODEC, decode_source=_decode_level, parse=_parse_level, check=_check_level))
register_counters(_counters)
