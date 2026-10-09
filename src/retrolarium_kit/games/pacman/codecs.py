"""Pac-Man's resources in the NES resource bundle: the maze, actor mappings,
sound, screens and the mirrored palette."""

from __future__ import annotations

import functools
from typing import Any, Mapping, Sequence

from retrolarium_kit.core.authoring import AuthoringError, ResourceSpec
from retrolarium_kit.games.pacman.maze import (
    CODEC as PACMAN_MAZE_CODEC,
    decode_maze_rle,
    encode_maze_grid,
    inspect_maze,
)
from retrolarium_kit.games.pacman.actor import (
    CODEC as PACMAN_ACTOR_CODEC,
    decode_actor_mapping,
    encode_actor_mapping,
)
from retrolarium_kit.games.pacman.sound import (
    CODEC as PACMAN_SOUND_CODEC,
    decode_sound_bundle,
    encode_sound_bundle,
    encode_stream as encode_sound_stream,
)
from retrolarium_kit.games.pacman.screen import (
    CODEC as PACMAN_SCREEN_CODEC,
    decode_screen_collection,
    encode_screen_collection,
)
from retrolarium_kit.platform.nes.resources import (
    Encoded,
    NesCodec,
    NesResourceError,
    _EncodedSpan,
    _byte,
    _integer,
    _matrix,
    _span,
    decode_palette,
    parse_palette,
    register,
    register_counters,
)

PACMAN_PALETTE_CODEC = "pacman.palette-table.v1"


def _decode_maze(image: bytes, resource: ResourceSpec) -> dict[str, object]:
    width = _integer(
        resource.parameters.get("width"), f"{resource.resource_id}.width", minimum=1
    )
    height = _integer(
        resource.parameters.get("height"), f"{resource.resource_id}.height", minimum=1
    )
    offset, size = _span(resource, _integer(
        resource.parameters.get("tokens"), f"{resource.resource_id}.tokens", minimum=1
    ))
    payload = image[offset : offset + size]
    if len(payload) != size:
        raise NesResourceError(f"{resource.resource_id} exceeds the source image")
    return {
        "id": resource.resource_id,
        "kind": resource.kind,
        "codec": resource.codec,
        "width": width,
        "height": height,
        "tokens": list(payload),
        "tiles": [list(row) for row in decode_maze_rle(
            payload, rows=height, columns=width
        )],
    }


def _parse_maze(raw: dict[str, Any], spec: ResourceSpec) -> Encoded:
    payload: bytes | None = None
    encoded_spans: tuple[_EncodedSpan, ...] | None = None
    expected_fields = {
        "id", "kind", "codec", "width", "height", "tokens", "tiles"
    }
    width = _integer(spec.parameters.get("width"), f"{spec.resource_id}.width", minimum=1)
    height = _integer(spec.parameters.get("height"), f"{spec.resource_id}.height", minimum=1)
    tokens = _integer(spec.parameters.get("tokens"), f"{spec.resource_id}.tokens", minimum=1)
    if raw.get("width") != width or raw.get("height") != height:
        raise NesResourceError(f"resource {spec.resource_id} geometry differs from profile")
    raw_tokens = raw.get("tokens")
    if not isinstance(raw_tokens, list) or len(raw_tokens) != tokens:
        raise NesResourceError(
            f"{spec.resource_id}.tokens must contain {tokens} bytes"
        )
    preserved = bytes(
        _byte(item, f"{spec.resource_id}.tokens[{index}]")
        for index, item in enumerate(raw_tokens)
    )
    try:
        preserved_grid = decode_maze_rle(
            preserved, rows=height, columns=width
        )
    except AuthoringError as exc:
        raise NesResourceError(str(exc)) from exc
    normalized_grid = _matrix(
        raw.get("tiles"), height, width, f"{spec.resource_id}.tiles", maximum=0x3F
    )
    payload = preserved if preserved_grid == normalized_grid else encode_maze_grid(
        raw.get("tiles"), rows=height, columns=width, token_count=tokens
    )
    # The round counter's start and the two "pellets eaten" sums are
    # immediate operands; the build writes the maze's own count there.
    count_offsets = spec.parameters.get("pellet_count_offsets")
    report = inspect_maze(
        raw.get("tiles"),
        power_pellets=spec.parameters.get("power_pellets"),
        counted=count_offsets is not None,
    )
    if report["errors"]:
        raise NesResourceError("invalid Pac-Man maze: " + "; ".join(report["errors"]))
    if count_offsets is not None:
        offset, _size = _span(spec, len(payload))
        encoded_spans = (_EncodedSpan(offset, payload),) + tuple(
            _EncodedSpan(
                _integer(item, f"{spec.resource_id}.pellet_count_offsets", minimum=0),
                bytes((int(report["pellets"]),)),
            )
            for item in count_offsets
        )
    return expected_fields, payload, encoded_spans


def _decode_actor(image: bytes, resource: ResourceSpec) -> dict[str, object]:
    offset, size = _span(resource, 624)
    payload = image[offset : offset + size]
    if len(payload) != size:
        raise NesResourceError(f"{resource.resource_id} exceeds the source image")
    return {
        "id": resource.resource_id,
        "kind": resource.kind,
        "codec": resource.codec,
        **decode_actor_mapping(payload),
    }


def _parse_actor(raw: dict[str, Any], spec: ResourceSpec) -> Encoded:
    payload: bytes | None = None
    encoded_spans: tuple[_EncodedSpan, ...] | None = None
    expected_fields = {
        "id", "kind", "codec", "standard_tile_quads",
        "alternate_tile_quads", "standard_attribute_quads",
        "alternate_attribute_quads", "oam_offsets_yx",
    }
    payload = encode_actor_mapping({
        key: raw.get(key) for key in expected_fields - {"id", "kind", "codec"}
    })
    return expected_fields, payload, encoded_spans


def _decode_sound(image: bytes, resource: ResourceSpec) -> dict[str, object]:
    return {
        "id": resource.resource_id,
        "kind": resource.kind,
        "codec": resource.codec,
        **decode_sound_bundle(image, resource.parameters),
    }


def _parse_sound(raw: dict[str, Any], spec: ResourceSpec) -> Encoded:
    payload: bytes | None = None
    encoded_spans: tuple[_EncodedSpan, ...] | None = None
    expected_fields = {"id", "kind", "codec", "capacity", "slots"}
    encoded_spans = tuple(
        _EncodedSpan(offset, replacement)
        for offset, replacement in encode_sound_bundle(raw, spec.parameters)
    )
    return expected_fields, payload, encoded_spans


def _decode_screen(image: bytes, resource: ResourceSpec) -> dict[str, object]:
    return {
        "id": resource.resource_id,
        "kind": resource.kind,
        "codec": resource.codec,
        **decode_screen_collection(image, resource.parameters),
    }


def _parse_screen(raw: dict[str, Any], spec: ResourceSpec) -> Encoded:
    payload: bytes | None = None
    encoded_spans: tuple[_EncodedSpan, ...] | None = None
    expected_fields = {
        "id", "kind", "codec", "title_logo", "title_packets",
        "title_attributes", "fixed_blocks", "attract_packets",
        "intermission_visual", "read_only_code_tables", "encoding",
        "text_fields",
    }
    encoded_spans = tuple(
        _EncodedSpan(offset, replacement)
        for offset, replacement in encode_screen_collection(
            {
                key: raw.get(key)
                for key in expected_fields - {"id", "kind", "codec"}
            },
            spec.parameters,
        )
    )
    return expected_fields, payload, encoded_spans


def _counters(resources: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    def of(*codecs: str) -> list[Mapping[str, Any]]:
        return [item for item in resources if item.get("codec") in codecs]

    sound = of(PACMAN_SOUND_CODEC)
    return {
        "screens": len(of(PACMAN_SCREEN_CODEC)),
        "palette_bytes": sum(len(item.get("values", [])) for item in of(PACMAN_PALETTE_CODEC)),
        "mazes": len(of(PACMAN_MAZE_CODEC)),
        "actor_frames": sum(
            len(item.get("standard_tile_quads", [])) + len(item.get("alternate_tile_quads", []))
            for item in of(PACMAN_ACTOR_CODEC)),
        "text_messages": sum(len(item.get("text_fields", [])) for item in of(PACMAN_SCREEN_CODEC)),
        "sound_slots": sum(len(item.get("slots", [])) for item in sound),
        "sound_notes": sum(
            command.get("kind") == "note"
            for item in sound for slot in item.get("slots", [])
            for command in slot.get("stream", {}).get("commands", [])),
        "sound_used_bytes": sum(
            len(encode_sound_stream(slot["stream"])) for item in sound for slot in item.get("slots", [])),
    }


register(NesCodec(
    PACMAN_PALETTE_CODEC,
    decode=functools.partial(decode_palette, mirror_pairs=True),
    parse=functools.partial(parse_palette, mirror_pairs=True),
))
register(NesCodec(PACMAN_MAZE_CODEC, decode=_decode_maze, parse=_parse_maze))
register(NesCodec(PACMAN_ACTOR_CODEC, decode=_decode_actor, parse=_parse_actor))
register(NesCodec(PACMAN_SOUND_CODEC, decode=_decode_sound, parse=_parse_sound))
register(NesCodec(PACMAN_SCREEN_CODEC, decode=_decode_screen, parse=_parse_screen))
register_counters(_counters)
