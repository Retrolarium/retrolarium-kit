"""The NES resource bundle: a profile's fixed resources as one JSON document.

A profile lists the resources of a revision, each with a codec. This module
reads them out of the image (decode_resources), checks an edited document
(parse_resource_document) and writes it back (build_resources). The console's
own codecs live here: nametables, palette tables, CHR, metatiles, palette
packets, fixed text, PPU screen packets and FDS synthesis and music. A game's
own codecs register themselves from games/<game>/codecs.py; the provider
module named in CODEC_PROVIDERS is imported the first time a codec is looked
up, so the platform never imports a game.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib
import json
import operator
from typing import Any, Callable, Mapping, Sequence

from retrolarium_kit.core.authoring import (
    Address,
    AddressRange,
    AuthoringError,
    BuildPlan,
    Change,
    ChangeSet,
    GameProfile,
    PayloadSpec,
    Provenance,
    ResourceSpec,
    RevisionSpec,
)
from retrolarium_core.fds import FdsError, fds_payload, fds_payload_ranges, parse_fds_side
from retrolarium_kit.platform.fds.synthesis import (
    CODEC as FDS_SYNTHESIS_CODEC,
    decode_synthesis as decode_fds_synthesis,
    encode_synthesis as encode_fds_synthesis,
)
from retrolarium_kit.platform.fds.music import (
    CODEC as FDS_MUSIC_CODEC,
    decode_music as decode_fds_music,
    encode_music as encode_fds_music,
)
from retrolarium_core.ines import parse_ines, parse_ines_prefix
from retrolarium_kit.platform.nes.text import (
    CODEC as FIXED_TEXT_CODEC,
    decode_text_fields,
    encode_text_fields,
)
from retrolarium_kit.platform.nes.records import (
    CODEC as RECORD_TABLE_CODEC,
    decode_records,
    encode_records,
)
from retrolarium_kit.platform.nes.screen_packets import (
    CODEC as PPU_SCREEN_PACKET_CODEC,
    decode_screen_packets,
    encode_screen_packets,
)
from retrolarium_kit.platform.nes.visuals import (
    METATILE_CODEC,
    PALETTE_PACKET_CODEC,
    decode_metatiles,
    decode_palette_packets,
    encode_metatiles,
    encode_palette_packets,
)


DOCUMENT_SCHEMA = "retrolarium.nes-resource-bundle"
DOCUMENT_VERSION = 1
ADAPTER_CODEC = "nes.resource-bundle.v1"
NAMETABLE_CODEC = "nes.nametable.v1"
PALETTE_CODEC = "nes.palette-table.v1"
CHR_CODEC = "nes.chr-2bpp.v1"


class NesResourceError(AuthoringError):
    """A fixed NES resource profile or document is invalid."""


@dataclass(frozen=True)
class _ResourceSpan:
    offset: int
    size: int
    payload_id: str | None = None


@dataclass(frozen=True)
class _EncodedSpan:
    offset: int
    replacement: bytes
    payload_id: str | None = None


# Modules that register game codecs; imported the first time a codec is needed.
CODEC_PROVIDERS = ("retrolarium_kit.games.nes_codecs",)

# What a codec's parse step returns: the document fields its contract allows,
# and either one payload written at the resource's file_offset or explicit
# encoded spans.
Encoded = tuple[set[str], "bytes | None", "tuple[_EncodedSpan, ...] | None"]


@dataclass(frozen=True)
class NesCodec:
    """How one resource codec reads a resource and writes it back.

    decode reads the resource from its payload (the image, or the named
    payload it lives in); decode_source reads it from the whole source when
    it spans several places. parse checks a document entry and encodes it.
    check validates the profile entry against the other selected resources.
    """

    codec: str
    parse: Callable[[dict[str, Any], ResourceSpec], Encoded]
    decode: Callable[[bytes, ResourceSpec], dict[str, object]] | None = None
    decode_source: Callable[[bytes, GameProfile, RevisionSpec, ResourceSpec],
                            dict[str, object]] | None = None
    check: Callable[[ResourceSpec, Mapping[str, ResourceSpec]], None] | None = None


_CODECS: dict[str, NesCodec] = {}
_COUNTERS: list[Callable[[Sequence[Mapping[str, Any]]], Mapping[str, int]]] = []
_providers_loaded = False


def register(codec: NesCodec) -> None:
    existing = _CODECS.get(codec.codec)
    if existing is not None and existing != codec:
        raise NesResourceError(f"NES codec {codec.codec} is registered twice")
    _CODECS[codec.codec] = codec


def register_counters(counter: Callable[[Sequence[Mapping[str, Any]]], Mapping[str, int]]) -> None:
    """Add a game's figures to inspection_details."""
    if counter not in _COUNTERS:
        _COUNTERS.append(counter)


def _codecs() -> Mapping[str, NesCodec]:
    global _providers_loaded
    if not _providers_loaded:
        for name in CODEC_PROVIDERS:
            importlib.import_module(name)
        _providers_loaded = True
    return _CODECS


def _codec(name: str) -> NesCodec:
    codec = _codecs().get(name)
    if codec is None:
        raise NesResourceError(f"unsupported NES resource codec {name}")
    return codec


def _integer(value: object, name: str, *, minimum: int = 0) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise NesResourceError(f"{name} must be an integer >= {minimum}")
    return value


def _byte(value: object, name: str, *, maximum: int = 0xFF) -> int:
    result = _integer(value, name)
    if result > maximum:
        raise NesResourceError(f"{name} must be <= {maximum}")
    return result


def _resource_specs(
    profile: GameProfile, revision: RevisionSpec
) -> tuple[ResourceSpec, ...]:
    supported = _codecs()
    profile_resources = profile.resources_for(revision)
    markers = [item for item in profile_resources if item.codec == ADAPTER_CODEC]
    if len(markers) != 1:
        raise NesResourceError(
            "NES resource profile needs exactly one bundle marker"
        )
    marker = markers[0]
    selected_ids = marker.parameters.get("resource_ids")
    if selected_ids is None:
        resources = [
            item for item in profile_resources if item.codec in supported
        ]
    else:
        if (
            not isinstance(selected_ids, list)
            or not selected_ids
            or any(not isinstance(item, str) or not item for item in selected_ids)
            or len(set(selected_ids)) != len(selected_ids)
        ):
            raise NesResourceError(
                "NES resource bundle resource_ids must be a non-empty array "
                "of unique resource ids"
            )
        declared_by_id = {item.resource_id: item for item in profile_resources}
        missing = [item for item in selected_ids if item not in declared_by_id]
        invalid = [
            item for item in selected_ids
            if item in declared_by_id
            and declared_by_id[item].codec not in supported
        ]
        if missing or invalid:
            raise NesResourceError(
                "NES resource bundle resource_ids must name supported resources; "
                f"missing={missing}, unsupported={invalid}"
            )
        resources = [declared_by_id[item] for item in selected_ids]
    unsupported = [
        item.resource_id
        for item in profile_resources
        if item.codec not in supported and item.codec != ADAPTER_CODEC
    ]
    if not resources or (selected_ids is None and unsupported):
        raise NesResourceError(
            "NES resource profile needs one bundle marker, at least one supported "
            f"resource and no unsupported codecs; unsupported={unsupported}"
        )
    nonfixed = [
        item.resource_id
        for item in (*markers, *resources)
        if item.capacity_policy != "fixed"
    ]
    if nonfixed:
        raise NesResourceError(
            f"NES resource bundle v1 requires fixed capacities: {nonfixed}"
        )
    resources_by_id = {item.resource_id: item for item in resources}
    for item in resources:
        check = supported[item.codec].check
        if check is not None:
            check(item, resources_by_id)
    return tuple(resources)


def _revision_image(source: bytes, revision: RevisionSpec):
    if revision.container == "ines":
        return parse_ines(source)
    if revision.container == "ines-pc10":
        image, trailing = parse_ines_prefix(source)
        if len(trailing) != 0x2000:
            raise NesResourceError(
                "PlayChoice-10 container must have an 8192-byte instruction ROM"
            )
        return image
    if revision.container == "ines-vs":
        if len(source) != revision.size or source[:4] != b"NES\x1a":
            raise NesResourceError(
                "Vs. container size or iNES signature differs from its profile"
            )
        return None
    if revision.container == "fds":
        try:
            parse_fds_side(source)
        except FdsError as exc:
            raise NesResourceError(str(exc)) from exc
        return None
    raise NesResourceError(
        f"NES resource bundle does not support container {revision.container!r}"
    )


def _resource_payload(
    profile: GameProfile, revision: RevisionSpec, resource: ResourceSpec
) -> PayloadSpec | None:
    if resource.payload is None:
        if revision.container == "fds":
            raise NesResourceError(
                f"FDS resource {resource.resource_id} must select a named payload"
            )
        return None
    try:
        return profile.payload_for(revision, resource.payload)
    except AuthoringError as exc:
        raise NesResourceError(str(exc)) from exc


def _payload_source(source: bytes, payload: PayloadSpec) -> bytes:
    if payload.storage == "range":
        assert payload.file_offset is not None
        result = source[payload.file_offset:payload.file_offset + payload.size]
    elif payload.storage == "fds-records":
        try:
            result = fds_payload(source, payload.record_ids)
        except FdsError as exc:
            raise NesResourceError(str(exc)) from exc
    else:
        raise NesResourceError(f"unsupported payload storage {payload.storage!r}")
    if len(result) != payload.size:
        raise NesResourceError(
            f"payload {payload.payload_id} is {len(result)} bytes, expected {payload.size}"
        )
    if hashlib.sha1(result).hexdigest() != payload.sha1:
        raise NesResourceError(f"payload {payload.payload_id} SHA-1 differs from profile")
    return result


def _resource_source(
    source: bytes,
    profile: GameProfile,
    revision: RevisionSpec,
    resource: ResourceSpec,
) -> bytes:
    payload = _resource_payload(profile, revision, resource)
    if payload is None:
        return source
    return _payload_source(source, payload)


def _physical_replacements(
    source: bytes,
    profile: GameProfile,
    revision: RevisionSpec,
    resource: ResourceSpec,
    replacements: tuple[_EncodedSpan, ...],
) -> tuple[tuple[int, bytes], ...]:
    result = []
    for item in replacements:
        offset = item.offset
        replacement = item.replacement
        if item.payload_id is None:
            payload = _resource_payload(profile, revision, resource)
        else:
            try:
                payload = profile.payload_for(revision, item.payload_id)
            except AuthoringError as exc:
                raise NesResourceError(str(exc)) from exc
        if payload is None:
            result.append((offset, replacement))
            continue
        if offset < 0 or offset + len(replacement) > payload.size:
            raise NesResourceError(
                f"resource {resource.resource_id} exceeds payload {payload.payload_id}"
            )
        if payload.storage == "range":
            assert payload.file_offset is not None
            result.append((payload.file_offset + offset, replacement))
        elif payload.storage == "fds-records":
            try:
                for item in fds_payload_ranges(
                    source, payload.record_ids, offset, len(replacement)
                ):
                    start = item.payload_offset
                    result.append(
                        (
                            item.file_offset,
                            replacement[start:start + item.size],
                        )
                    )
            except FdsError as exc:
                raise NesResourceError(str(exc)) from exc
        else:
            raise NesResourceError(
                f"unsupported payload storage {payload.storage!r}"
            )
    return tuple(result)


def _read_resource_spans(
    source: bytes,
    profile: GameProfile,
    revision: RevisionSpec,
    resource: ResourceSpec,
    spans: tuple[_ResourceSpan, ...],
) -> bytes:
    cached_payloads: dict[str | None, bytes] = {}
    output = bytearray()
    for span in spans:
        if span.payload_id not in cached_payloads:
            if span.payload_id is None:
                cached_payloads[None] = _resource_source(
                    source, profile, revision, resource
                )
            else:
                try:
                    payload = profile.payload_for(revision, span.payload_id)
                except AuthoringError as exc:
                    raise NesResourceError(str(exc)) from exc
                cached_payloads[span.payload_id] = _payload_source(source, payload)
        payload_source = cached_payloads[span.payload_id]
        chunk = payload_source[span.offset:span.offset + span.size]
        if len(chunk) != span.size:
            target = span.payload_id or "source image"
            raise NesResourceError(
                f"resource {resource.resource_id} exceeds {target}"
            )
        output.extend(chunk)
    return bytes(output)


def _encoded_resource_spans(
    payload: bytes, spans: tuple[_ResourceSpan, ...]
) -> tuple[_EncodedSpan, ...]:
    if len(payload) != sum(span.size for span in spans):
        raise NesResourceError("encoded stream size differs from its span layout")
    result = []
    position = 0
    for span in spans:
        end = position + span.size
        result.append(_EncodedSpan(
            span.offset, payload[position:end], span.payload_id
        ))
        position = end
    return tuple(result)


def _span(resource: ResourceSpec, expected_size: int) -> tuple[int, int]:
    offset = _integer(
        resource.parameters.get("file_offset"),
        f"{resource.resource_id}.file_offset",
    )
    size = _integer(
        resource.parameters.get("size"),
        f"{resource.resource_id}.size",
        minimum=1,
    )
    if size != expected_size:
        raise NesResourceError(
            f"{resource.resource_id}.size is {size}, expected {expected_size}"
        )
    return offset, size


def _replicated_palette_offsets(
    resource: ResourceSpec, entries: int
) -> tuple[tuple[int, ...], ...] | None:
    raw = resource.parameters.get("replicated_file_offsets")
    if raw is None:
        return None
    if not isinstance(raw, list) or len(raw) != entries:
        raise NesResourceError(
            f"{resource.resource_id}.replicated_file_offsets must contain "
            f"{entries} entry lists"
        )
    result = []
    seen: set[int] = set()
    for entry, offsets in enumerate(raw):
        if not isinstance(offsets, list) or not offsets:
            raise NesResourceError(
                f"{resource.resource_id}.replicated_file_offsets[{entry}] "
                "must be a non-empty list"
            )
        normalized = tuple(
            _integer(offset, f"{resource.resource_id}.replicated_file_offsets[{entry}]")
            for offset in offsets
        )
        if len(set(normalized)) != len(normalized) or seen.intersection(normalized):
            raise NesResourceError(
                f"{resource.resource_id}.replicated_file_offsets overlap or repeat"
            )
        seen.update(normalized)
        result.append(normalized)
    return tuple(result)


def _matrix(
    value: object,
    rows: int,
    columns: int,
    name: str,
    *,
    maximum: int = 0xFF,
) -> tuple[tuple[int, ...], ...]:
    # A well-formed matrix is checked row by row in C; anything else takes the
    # cell-by-cell path, which names the first bad cell.
    if (type(value) is list and len(value) == rows
            and all(type(row) is list and len(row) == columns
                    and set(map(type, row)) == _INTEGER_TYPE
                    and min(row) >= 0 and max(row) <= maximum
                    for row in value)):
        return tuple(map(tuple, value))
    if not isinstance(value, list) or len(value) != rows:
        raise NesResourceError(f"{name} must contain {rows} rows")
    result = []
    for row_index, row in enumerate(value):
        if not isinstance(row, list) or len(row) != columns:
            raise NesResourceError(
                f"{name}[{row_index}] must contain {columns} columns"
            )
        result.append(
            tuple(
                _byte(item, f"{name}[{row_index}][{column}]", maximum=maximum)
                for column, item in enumerate(row)
            )
        )
    return tuple(result)


_INTEGER_TYPE = {int}
# A CHR pixel row as two bit planes: each plane byte read as the binary
# number its pixels' low (high) bits spell.
_LOW_PLANE = bytes.maketrans(bytes(range(4)), b"0101")
_HIGH_PLANE = bytes.maketrans(bytes(range(4)), b"0011")
# One plane byte as eight pixel values (the high plane already doubled).
_LOW_PIXELS = tuple(tuple((value >> (7 - x)) & 1 for x in range(8)) for value in range(256))
_HIGH_PIXELS = tuple(tuple(((value >> (7 - x)) & 1) << 1 for x in range(8)) for value in range(256))


def decode_chr_2bpp(payload: bytes) -> tuple[tuple[tuple[int, ...], ...], ...]:
    if len(payload) % 16:
        raise NesResourceError("CHR 2bpp payload size must be divisible by 16")
    tiles = []
    for start in range(0, len(payload), 16):
        low = payload[start:start + 8]
        high = payload[start + 8:start + 16]
        tiles.append(tuple(
            tuple(map(operator.add, _LOW_PIXELS[low[y]], _HIGH_PIXELS[high[y]]))
            for y in range(8)
        ))
    return tuple(tiles)


def encode_chr_2bpp(
    tiles: Sequence[Sequence[Sequence[int]]], expected_tiles: int
) -> bytes:
    if len(tiles) != expected_tiles:
        raise NesResourceError(f"CHR resource must contain {expected_tiles} tiles")
    output = bytearray()
    for tile_index, tile in enumerate(tiles):
        matrix = _matrix(
            [list(row) for row in tile],
            8,
            8,
            f"tiles[{tile_index}]",
            maximum=3,
        )
        rows = [bytes(row) for row in matrix]
        output.extend(int(row.translate(_LOW_PLANE), 2) for row in rows)
        output.extend(int(row.translate(_HIGH_PLANE), 2) for row in rows)
    return bytes(output)


@dataclass(frozen=True)
class NesResourceBundleDocument:
    game_id: str
    revision_id: str
    source_sha256: str
    resources: tuple[Mapping[str, object], ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "schema": DOCUMENT_SCHEMA,
            "schema_version": DOCUMENT_VERSION,
            "game": self.game_id,
            "revision": self.revision_id,
            "source_sha256": self.source_sha256,
            "resources": [dict(item) for item in self.resources],
        }


def _decode_nametable(image: bytes, resource: ResourceSpec) -> dict[str, object]:
    width = _integer(
        resource.parameters.get("width"), f"{resource.resource_id}.width", minimum=1
    )
    height = _integer(
        resource.parameters.get("height"),
        f"{resource.resource_id}.height",
        minimum=1,
    )
    attributes = _integer(
        resource.parameters.get("attribute_bytes"),
        f"{resource.resource_id}.attribute_bytes",
    )
    offset, size = _span(resource, width * height + attributes)
    payload = image[offset : offset + size]
    if len(payload) != size:
        raise NesResourceError(f"{resource.resource_id} exceeds the source image")
    return {
        "id": resource.resource_id,
        "kind": resource.kind,
        "codec": resource.codec,
        "width": width,
        "height": height,
        "tiles": [
            list(payload[row * width : (row + 1) * width])
            for row in range(height)
        ],
        "attributes": list(payload[width * height :]),
    }


def _parse_nametable(raw: dict[str, Any], spec: ResourceSpec) -> Encoded:
    payload: bytes | None = None
    encoded_spans: tuple[_EncodedSpan, ...] | None = None
    expected_fields = {
        "id", "kind", "codec", "width", "height", "tiles", "attributes"
    }
    width = _integer(spec.parameters.get("width"), f"{spec.resource_id}.width", minimum=1)
    height = _integer(
        spec.parameters.get("height"), f"{spec.resource_id}.height", minimum=1
    )
    attribute_count = _integer(
        spec.parameters.get("attribute_bytes"),
        f"{spec.resource_id}.attribute_bytes",
    )
    if raw.get("width") != width or raw.get("height") != height:
        raise NesResourceError(f"resource {spec.resource_id} geometry differs from profile")
    tiles = _matrix(raw.get("tiles"), height, width, f"{spec.resource_id}.tiles")
    values = raw.get("attributes")
    if not isinstance(values, list) or len(values) != attribute_count:
        raise NesResourceError(
            f"{spec.resource_id}.attributes must contain {attribute_count} bytes"
        )
    attributes = bytes(
        _byte(item, f"{spec.resource_id}.attributes[{index}]")
        for index, item in enumerate(values)
    )
    payload = bytes(cell for row in tiles for cell in row) + attributes
    return expected_fields, payload, encoded_spans


def decode_palette(image: bytes, resource: ResourceSpec, *, mirror_pairs: bool = False) -> dict[str, object]:
    entries = _integer(
        resource.parameters.get("entries"),
        f"{resource.resource_id}.entries",
        minimum=1,
    )
    replicated = _replicated_palette_offsets(resource, entries)
    if replicated is None:
        offset, size = _span(resource, entries)
        payload = image[offset : offset + size]
        if len(payload) != size:
            raise NesResourceError(f"{resource.resource_id} exceeds the source image")
    else:
        decoded = []
        for entry, offsets in enumerate(replicated):
            copies = []
            for offset in offsets:
                if offset >= len(image):
                    raise NesResourceError(f"{resource.resource_id} exceeds the source image")
                copies.append(image[offset])
            if len(set(copies)) != 1:
                raise NesResourceError(
                    f"{resource.resource_id}.values[{entry}] replicas differ"
                )
            decoded.append(copies[0])
        payload = bytes(decoded)
    for index, value in enumerate(payload):
        _byte(value, f"{resource.resource_id}.values[{index}]", maximum=0x3F)
    result = {
        "id": resource.resource_id,
        "kind": resource.kind,
        "codec": resource.codec,
        "values": list(payload),
    }
    palette_model = resource.parameters.get("palette_model")
    if palette_model is not None:
        if palette_model not in ("nes", "rp2c04_0004"):
            raise NesResourceError(
                f"{resource.resource_id}.palette_model is unsupported"
            )
        result["palette_model"] = palette_model
    if mirror_pairs:
        result["mirror_pairs"] = resource.parameters.get("mirror_pairs", [])
    return result


def parse_palette(raw: dict[str, Any], spec: ResourceSpec, *, mirror_pairs: bool = False) -> Encoded:
    payload: bytes | None = None
    encoded_spans: tuple[_EncodedSpan, ...] | None = None
    expected_fields = {"id", "kind", "codec", "values"}
    palette_model = spec.parameters.get("palette_model")
    if palette_model is not None:
        expected_fields.add("palette_model")
        if palette_model not in ("nes", "rp2c04_0004"):
            raise NesResourceError(
                f"{spec.resource_id}.palette_model is unsupported"
            )
        if raw.get("palette_model") != palette_model:
            raise NesResourceError(
                f"{spec.resource_id}.palette_model differs from the profile"
            )
    if mirror_pairs:
        expected_fields.add("mirror_pairs")
    count = _integer(
        spec.parameters.get("entries"), f"{spec.resource_id}.entries", minimum=1
    )
    values = raw.get("values")
    if not isinstance(values, list) or len(values) != count:
        raise NesResourceError(f"{spec.resource_id}.values must contain {count} bytes")
    payload = bytes(
        _byte(
            item,
            f"{spec.resource_id}.values[{index}]",
            maximum=0x3F,
        )
        for index, item in enumerate(values)
    )
    replicated = _replicated_palette_offsets(spec, count)
    if replicated is not None:
        encoded_spans = tuple(
            _EncodedSpan(offset, bytes((payload[entry],)))
            for entry, offsets in enumerate(replicated)
            for offset in offsets
        )
    if mirror_pairs:
        configured_pairs = spec.parameters.get("mirror_pairs", [])
        raw_pairs = raw.get("mirror_pairs")
        if raw_pairs != configured_pairs:
            raise NesResourceError(
                f"{spec.resource_id}.mirror_pairs differ from the profile"
            )
        for pair_index, pair in enumerate(configured_pairs):
            if (not isinstance(pair, list) or len(pair) != 2
                    or any(not isinstance(item, int) for item in pair)):
                raise NesResourceError(
                    f"{spec.resource_id}.mirror_pairs[{pair_index}] is invalid"
                )
            left, right = pair
            if not 0 <= left < count or not 0 <= right < count or left == right:
                raise NesResourceError(
                    f"{spec.resource_id}.mirror_pairs[{pair_index}] is out of range"
                )
            if values[left] != values[right]:
                raise NesResourceError(
                    f"{spec.resource_id} mirrored colors {left} and {right} differ"
                )
    return expected_fields, payload, encoded_spans


def _decode_chr(image: bytes, resource: ResourceSpec) -> dict[str, object]:
    offset = _integer(
        resource.parameters.get("file_offset"),
        f"{resource.resource_id}.file_offset",
    )
    size = _integer(
        resource.parameters.get("size"),
        f"{resource.resource_id}.size",
        minimum=16,
    )
    if size % 16:
        raise NesResourceError(f"{resource.resource_id}.size must be divisible by 16")
    payload = image[offset : offset + size]
    if len(payload) != size:
        raise NesResourceError(f"{resource.resource_id} exceeds the source image")
    return {
        "id": resource.resource_id,
        "kind": resource.kind,
        "codec": resource.codec,
        "tiles": [[list(row) for row in tile] for tile in decode_chr_2bpp(payload)],
    }


def _parse_chr(raw: dict[str, Any], spec: ResourceSpec) -> Encoded:
    payload: bytes | None = None
    encoded_spans: tuple[_EncodedSpan, ...] | None = None
    expected_fields = {"id", "kind", "codec", "tiles"}
    size = _integer(
        spec.parameters.get("size"), f"{spec.resource_id}.size", minimum=16
    )
    values = raw.get("tiles")
    if not isinstance(values, list):
        raise NesResourceError(f"{spec.resource_id}.tiles must be an array")
    payload = encode_chr_2bpp(values, size // 16)
    return expected_fields, payload, encoded_spans


def _decode_metatile(image: bytes, resource: ResourceSpec) -> dict[str, object]:
    offset, size = _span(
        resource,
        sum(
            _integer(item, f"{resource.resource_id}.group_sizes[{index}]", minimum=1)
            for index, item in enumerate(resource.parameters.get("group_sizes", []))
        ) * 4,
    )
    payload = image[offset : offset + size]
    if len(payload) != size:
        raise NesResourceError(f"{resource.resource_id} exceeds the source image")
    try:
        decoded = decode_metatiles(payload, resource.parameters)
    except AuthoringError as exc:
        raise NesResourceError(str(exc)) from exc
    return {
        "id": resource.resource_id,
        "kind": resource.kind,
        "codec": resource.codec,
        **decoded,
    }


def _parse_metatile(raw: dict[str, Any], spec: ResourceSpec) -> Encoded:
    payload: bytes | None = None
    encoded_spans: tuple[_EncodedSpan, ...] | None = None
    expected_fields = {
        "id", "kind", "codec", "group_names", "group_sizes", "tile_base", "records"
    }
    try:
        payload = encode_metatiles(
            {key: raw.get(key) for key in expected_fields - {"id", "kind", "codec"}},
            spec.parameters,
        )
    except AuthoringError as exc:
        raise NesResourceError(str(exc)) from exc
    return expected_fields, payload, encoded_spans


def _decode_fds_synthesis(image: bytes, resource: ResourceSpec) -> dict[str, object]:
    try:
        size = _integer(
            resource.parameters.get("size"),
            f"{resource.resource_id}.size",
            minimum=1,
        )
        offset, _size = _span(resource, size)
        payload = image[offset:offset + size]
        if len(payload) != size:
            raise NesResourceError(
                f"{resource.resource_id} exceeds the source image"
            )
        decoded = decode_fds_synthesis(payload, resource.parameters)
    except AuthoringError as exc:
        raise NesResourceError(str(exc)) from exc
    return {
        "id": resource.resource_id,
        "kind": resource.kind,
        "codec": resource.codec,
        **decoded,
    }


def _parse_fds_synthesis(raw: dict[str, Any], spec: ResourceSpec) -> Encoded:
    payload: bytes | None = None
    encoded_spans: tuple[_EncodedSpan, ...] | None = None
    expected_fields = {
        "id", "kind", "codec", "arena", "waveforms", "volume_programs"
    }
    try:
        payload = encode_fds_synthesis(
            {
                key: raw.get(key)
                for key in expected_fields - {"id", "kind", "codec"}
            },
            spec.parameters,
        )
    except AuthoringError as exc:
        raise NesResourceError(str(exc)) from exc
    return expected_fields, payload, encoded_spans


def _decode_fds_music(image: bytes, resource: ResourceSpec) -> dict[str, object]:
    try:
        size = _integer(
            resource.parameters.get("size"),
            f"{resource.resource_id}.size",
            minimum=1,
        )
        offset, _size = _span(resource, size)
        payload = image[offset:offset + size]
        if len(payload) != size:
            raise NesResourceError(
                f"{resource.resource_id} exceeds the source image"
            )
        decoded = decode_fds_music(payload, resource.parameters)
    except AuthoringError as exc:
        raise NesResourceError(str(exc)) from exc
    return {
        "id": resource.resource_id,
        "kind": resource.kind,
        "codec": resource.codec,
        **decoded,
    }


def _parse_fds_music(raw: dict[str, Any], spec: ResourceSpec) -> Encoded:
    payload: bytes | None = None
    encoded_spans: tuple[_EncodedSpan, ...] | None = None
    expected_fields = {
        "id", "kind", "codec", "sequence", "headers", "prefix_padding",
        "shared_stream_arena", "synthesis", "suffix_padding",
        "note_lengths",
    }
    try:
        payload = encode_fds_music(
            {
                key: raw.get(key)
                for key in expected_fields - {"id", "kind", "codec"}
            },
            spec.parameters,
        )
    except AuthoringError as exc:
        raise NesResourceError(str(exc)) from exc
    return expected_fields, payload, encoded_spans


def _decode_palette_packet(image: bytes, resource: ResourceSpec) -> dict[str, object]:
    expected = sum(
        _integer(item, f"{resource.resource_id}.packet_capacities[{index}]", minimum=5)
        for index, item in enumerate(resource.parameters.get("packet_capacities", []))
    )
    offset, size = _span(resource, expected)
    payload = image[offset : offset + size]
    if len(payload) != size:
        raise NesResourceError(f"{resource.resource_id} exceeds the source image")
    try:
        decoded = decode_palette_packets(payload, resource.parameters)
    except AuthoringError as exc:
        raise NesResourceError(str(exc)) from exc
    result = {
        "id": resource.resource_id,
        "kind": resource.kind,
        "codec": resource.codec,
        **decoded,
    }
    palette_model = resource.parameters.get("palette_model")
    if palette_model is not None:
        if palette_model not in {"nes", "rp2c04_0004"}:
            raise NesResourceError(
                f"{resource.resource_id}.palette_model is unsupported"
            )
        result["palette_model"] = palette_model
    return result


def _parse_palette_packet(raw: dict[str, Any], spec: ResourceSpec) -> Encoded:
    payload: bytes | None = None
    encoded_spans: tuple[_EncodedSpan, ...] | None = None
    expected_fields = {
        "id", "kind", "codec", "day_background", "night_background", "packets"
    }
    palette_model = spec.parameters.get("palette_model")
    if palette_model is not None:
        expected_fields.add("palette_model")
        if raw.get("palette_model") != palette_model:
            raise NesResourceError(
                f"{spec.resource_id}.palette_model differs from the profile"
            )
    try:
        payload = encode_palette_packets(
            {
                key: raw.get(key)
                for key in expected_fields
                - {"id", "kind", "codec", "palette_model"}
            },
            spec.parameters,
        )
    except AuthoringError as exc:
        raise NesResourceError(str(exc)) from exc
    return expected_fields, payload, encoded_spans


def _decode_fixed_text(image: bytes, resource: ResourceSpec) -> dict[str, object]:
    return {
        "id": resource.resource_id,
        "kind": resource.kind,
        "codec": resource.codec,
        **decode_text_fields(image, resource.parameters),
    }


def _parse_fixed_text(raw: dict[str, Any], spec: ResourceSpec) -> Encoded:
    payload: bytes | None = None
    encoded_spans: tuple[_EncodedSpan, ...] | None = None
    expected_fields = {"id", "kind", "codec", "encoding", "messages"}
    encoded_spans = tuple(
        _EncodedSpan(offset, replacement)
        for offset, replacement in encode_text_fields(raw, spec.parameters)
    )
    return expected_fields, payload, encoded_spans


def _decode_record_table(image: bytes, resource: ResourceSpec) -> dict[str, object]:
    try:
        decoded = decode_records(image, resource.parameters, resource.resource_id)
    except AuthoringError as exc:
        raise NesResourceError(str(exc)) from exc
    return {"id": resource.resource_id, "kind": resource.kind, "codec": resource.codec, **decoded}


def _parse_record_table(raw: dict[str, Any], spec: ResourceSpec) -> Encoded:
    try:
        runs = encode_records(raw, spec.parameters, spec.resource_id)
    except AuthoringError as exc:
        raise NesResourceError(str(exc)) from exc
    return ({"id", "kind", "codec", "fields", "record_labels", "records"}, None,
            tuple(_EncodedSpan(offset, data) for offset, data in runs))


def _decode_ppu_screen_packet(image: bytes, resource: ResourceSpec) -> dict[str, object]:
    try:
        decoded = decode_screen_packets(image, resource.parameters)
    except AuthoringError as exc:
        raise NesResourceError(str(exc)) from exc
    return {
        "id": resource.resource_id,
        "kind": resource.kind,
        "codec": resource.codec,
        **decoded,
    }


def _parse_ppu_screen_packet(raw: dict[str, Any], spec: ResourceSpec) -> Encoded:
    payload: bytes | None = None
    encoded_spans: tuple[_EncodedSpan, ...] | None = None
    expected_fields = {"id", "kind", "codec", "screens"}
    if "encoding" in spec.parameters or "text_fields" in spec.parameters:
        expected_fields.update(("encoding", "text_fields"))
    # A pooled profile's documents may carry these; the codec checks them.
    if spec.parameters.get("packet_pool") is not None:
        expected_fields.update(key for key in ("capacity_pool", "growable_fields") if key in raw)
    try:
        encoded_spans = tuple(
            _EncodedSpan(offset, replacement)
            for offset, replacement in encode_screen_packets(
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


register(NesCodec(NAMETABLE_CODEC, decode=_decode_nametable, parse=_parse_nametable))
register(NesCodec(PALETTE_CODEC, decode=decode_palette, parse=parse_palette))
register(NesCodec(CHR_CODEC, decode=_decode_chr, parse=_parse_chr))
register(NesCodec(METATILE_CODEC, decode=_decode_metatile, parse=_parse_metatile))
register(NesCodec(FDS_SYNTHESIS_CODEC, decode=_decode_fds_synthesis, parse=_parse_fds_synthesis))
register(NesCodec(FDS_MUSIC_CODEC, decode=_decode_fds_music, parse=_parse_fds_music))
register(NesCodec(PALETTE_PACKET_CODEC, decode=_decode_palette_packet, parse=_parse_palette_packet))
register(NesCodec(FIXED_TEXT_CODEC, decode=_decode_fixed_text, parse=_parse_fixed_text))
register(NesCodec(PPU_SCREEN_PACKET_CODEC, decode=_decode_ppu_screen_packet, parse=_parse_ppu_screen_packet))
register(NesCodec(RECORD_TABLE_CODEC, decode=_decode_record_table, parse=_parse_record_table))


def _decode_profile_resource(
    source: bytes,
    profile: GameProfile,
    revision: RevisionSpec,
    resource: ResourceSpec,
) -> dict[str, object]:
    codec = _codec(resource.codec)
    if codec.decode_source is not None:
        return codec.decode_source(source, profile, revision, resource)
    assert codec.decode is not None, resource.codec
    return codec.decode(_resource_source(source, profile, revision, resource), resource)


def decode_resources(
    source: bytes, profile: GameProfile, revision: RevisionSpec
) -> NesResourceBundleDocument:
    if hashlib.sha256(source).hexdigest() != revision.sha256:
        raise NesResourceError("resource source does not match the selected revision")
    image = _revision_image(source, revision)
    if (
        revision.mapper is not None
        and image is not None
        and image.mapper != revision.mapper
    ):
        raise NesResourceError("resource profile mapper differs from the source image")
    resources = tuple(
        _decode_profile_resource(source, profile, revision, item)
        for item in _resource_specs(profile, revision)
    )
    return NesResourceBundleDocument(
        profile.game_id, revision.revision_id, revision.sha256, resources
    )


# Parsing a resource depends only on its document entry and its profile entry,
# and one Studio build parses each document up to three times (validation,
# the build's check, the encode). Encoded spans are kept by content for the
# life of the process.
_PARSED: dict[tuple[str, str, str], tuple[_EncodedSpan, ...]] = {}
_PARSED_LIMIT = 4096


def _parse_key(raw: dict[str, Any], spec: ResourceSpec) -> tuple[str, str, str] | None:
    try:
        return (
            spec.resource_id,
            json.dumps(raw, sort_keys=True, separators=(",", ":")),
            json.dumps([spec.codec, spec.kind, spec.parameters], sort_keys=True,
                       separators=(",", ":"), default=repr),
        )
    except (TypeError, ValueError):
        return None


def _parse_resource(
    raw: object, spec: ResourceSpec
) -> tuple[Mapping[str, object], tuple[_EncodedSpan, ...]]:
    if not isinstance(raw, dict):
        raise NesResourceError(f"resource {spec.resource_id} must be an object")
    if raw.get("id") != spec.resource_id or raw.get("codec") != spec.codec:
        raise NesResourceError(f"resource {spec.resource_id} identity differs from profile")
    if raw.get("kind") != spec.kind:
        raise NesResourceError(f"resource {spec.resource_id} kind differs from profile")
    key = _parse_key(raw, spec)
    cached = _PARSED.get(key) if key is not None else None
    if cached is not None:
        return raw, cached
    expected_fields, payload, encoded_spans = _codec(spec.codec).parse(raw, spec)
    if set(raw) != expected_fields:
        raise NesResourceError(
            f"resource {spec.resource_id} fields differ from its codec contract"
        )
    if encoded_spans is None:
        offset, _size = _span(spec, len(payload))
        encoded_spans = (_EncodedSpan(offset, payload),)
    if key is not None:
        if len(_PARSED) >= _PARSED_LIMIT:
            _PARSED.clear()
        _PARSED[key] = encoded_spans
    return raw, encoded_spans


def parse_resource_document(
    value: object, profile: GameProfile, revision: RevisionSpec
) -> NesResourceBundleDocument:
    if not isinstance(value, dict):
        raise NesResourceError("NES resource document must be an object")
    expected_fields = {
        "schema", "schema_version", "game", "revision", "source_sha256", "resources"
    }
    if set(value) != expected_fields:
        raise NesResourceError("NES resource document fields differ from schema v1")
    if value.get("schema") != DOCUMENT_SCHEMA or value.get("schema_version") != 1:
        raise NesResourceError("unsupported NES resource document schema")
    if value.get("game") != profile.game_id or value.get("revision") != revision.revision_id:
        raise NesResourceError("NES resource document game or revision mismatch")
    if value.get("source_sha256") != revision.sha256:
        raise NesResourceError("NES resource document source identity mismatch")
    raw_resources = value.get("resources")
    specs = _resource_specs(profile, revision)
    if not isinstance(raw_resources, list) or len(raw_resources) != len(specs):
        raise NesResourceError("NES resource document has the wrong resource count")
    parsed = []
    for raw, spec in zip(raw_resources, specs):
        normalized, _payload = _parse_resource(raw, spec)
        parsed.append(dict(normalized))
    return NesResourceBundleDocument(
        profile.game_id, revision.revision_id, revision.sha256, tuple(parsed)
    )


def build_resources(
    source: bytes,
    document: NesResourceBundleDocument,
    profile: GameProfile,
    revision: RevisionSpec,
) -> tuple[bytes, dict[str, object]]:
    source_sha256 = hashlib.sha256(source).hexdigest()
    if source_sha256 != revision.sha256 or document.source_sha256 != revision.sha256:
        raise NesResourceError("NES resource build source identity mismatch")
    _revision_image(source, revision)
    specs = _resource_specs(profile, revision)
    changes = []
    reports = []
    for raw, spec in zip(document.resources, specs):
        _normalized, replacements = _parse_resource(dict(raw), spec)
        range_reports = []
        physical_replacements = _physical_replacements(
            source, profile, revision, spec, replacements
        )
        for span_index, (offset, replacement) in enumerate(physical_replacements):
            size = len(replacement)
            expected = source[offset : offset + size]
            if len(expected) != size:
                raise NesResourceError(f"resource {spec.resource_id} exceeds the source image")
            changes.append(
                Change(
                    AddressRange(Address("file", offset), size),
                    expected,
                    replacement,
                    Provenance(source_sha256, spec.resource_id, f"encode-{span_index}"),
                )
            )
            range_reports.append({
                "file_offset": offset,
                "size": size,
                "changed_bytes": sum(a != b for a, b in zip(expected, replacement)),
            })
        report = {
            "id": spec.resource_id,
            "kind": spec.kind,
            "codec": spec.codec,
            "size": sum(item["size"] for item in range_reports),
            "changed_bytes": sum(item["changed_bytes"] for item in range_reports),
        }
        if len(range_reports) == 1:
            report["file_offset"] = range_reports[0]["file_offset"]
        else:
            report["ranges"] = range_reports
        reports.append(report)
    output = BuildPlan(source_sha256, ChangeSet.from_iterable(changes)).apply(source)
    return output, {
        "schema": "retrolarium.nes-resource-build",
        "schema_version": 1,
        "game": profile.game_id,
        "revision": revision.revision_id,
        "source_sha256": source_sha256,
        "output_sha256": hashlib.sha256(output).hexdigest(),
        "size": len(output),
        "changed_bytes": sum(a != b for a, b in zip(source, output)),
        "resources": reports,
    }


def inspection_details(document: NesResourceBundleDocument) -> dict[str, int]:
    resources = document.resources

    def of(*codecs: str) -> list[Mapping[str, Any]]:
        return [item for item in resources if item.get("codec") in codecs]

    result = {
        "resource_count": len(resources),
        "screens": sum(len(item.get("screens", [])) for item in of(PPU_SCREEN_PACKET_CODEC))
        + len(of(NAMETABLE_CODEC)),
        "palette_bytes": sum(len(item.get("values", [])) for item in of(PALETTE_CODEC))
        + sum(len(packet.get("values", []))
              for item in of(PALETTE_PACKET_CODEC) for packet in item.get("packets", [])),
        "palette_packets": sum(len(item.get("packets", [])) for item in of(PALETTE_PACKET_CODEC)),
        "metatiles": sum(len(item.get("records", [])) for item in of(METATILE_CODEC)),
        "chr_tiles": sum(len(item.get("tiles", [])) for item in of(CHR_CODEC)),
        "text_messages": sum(len(item.get("messages", [])) for item in of(FIXED_TEXT_CODEC))
        + sum(len(item.get("text_fields", [])) for item in of(PPU_SCREEN_PACKET_CODEC)),
    }
    _codecs()
    for counter in _COUNTERS:
        for key, value in counter(resources).items():
            result[key] = result.get(key, 0) + value
    return result
