"""Profile-driven fixed NES screen, palette and CHR resource authoring."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any, Mapping, Sequence

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
from .pacman_maze import (
    CODEC as PACMAN_MAZE_CODEC,
    decode_maze_rle,
    encode_maze_grid,
    inspect_maze,
)
from .pacman_actor import (
    CODEC as PACMAN_ACTOR_CODEC,
    decode_actor_mapping,
    encode_actor_mapping,
)
from .pacman_sound import (
    CODEC as PACMAN_SOUND_CODEC,
    decode_sound_bundle,
    encode_sound_bundle,
    encode_stream as encode_sound_stream,
)
from .pacman_screen import (
    CODEC as PACMAN_SCREEN_CODEC,
    decode_screen_collection,
    encode_screen_collection,
)
from .nes_text import (
    CODEC as FIXED_TEXT_CODEC,
    decode_text_fields,
    encode_text_fields,
)


DOCUMENT_SCHEMA = "retrolarium.nes-resource-bundle"
DOCUMENT_VERSION = 1
ADAPTER_CODEC = "nes.resource-bundle.v1"
NAMETABLE_CODEC = "nes.nametable.v1"
PALETTE_CODEC = "nes.palette-table.v1"
PACMAN_PALETTE_CODEC = "pacman.palette-table.v1"
CHR_CODEC = "nes.chr-2bpp.v1"
SUPPORTED_CODECS = (
    NAMETABLE_CODEC, PALETTE_CODEC, PACMAN_PALETTE_CODEC, CHR_CODEC, PACMAN_MAZE_CODEC,
    PACMAN_ACTOR_CODEC, FIXED_TEXT_CODEC,
    PACMAN_SOUND_CODEC,
    PACMAN_SCREEN_CODEC,
)


class NesResourceError(AuthoringError):
    """A fixed NES resource profile or document is invalid."""


def _integer(value: object, name: str, *, minimum: int = 0) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise NesResourceError(f"{name} must be an integer >= {minimum}")
    return value


def _byte(value: object, name: str, *, maximum: int = 0xFF) -> int:
    result = _integer(value, name)
    if result > maximum:
        raise NesResourceError(f"{name} must be <= {maximum}")
    return result


def _resource_specs(profile: GameProfile) -> tuple[ResourceSpec, ...]:
    markers = [item for item in profile.resources if item.codec == ADAPTER_CODEC]
    resources = [item for item in profile.resources if item.codec in SUPPORTED_CODECS]
    unsupported = [
        item.resource_id
        for item in profile.resources
        if item.codec not in (*SUPPORTED_CODECS, ADAPTER_CODEC)
    ]
    if len(markers) != 1 or not resources or unsupported:
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
    return tuple(resources)


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


def decode_chr_2bpp(payload: bytes) -> tuple[tuple[tuple[int, ...], ...], ...]:
    if len(payload) % 16:
        raise NesResourceError("CHR 2bpp payload size must be divisible by 16")
    tiles = []
    for start in range(0, len(payload), 16):
        tile = payload[start : start + 16]
        rows = []
        for y in range(8):
            rows.append(
                tuple(
                    ((tile[y] >> (7 - x)) & 1)
                    | (((tile[y + 8] >> (7 - x)) & 1) << 1)
                    for x in range(8)
                )
            )
        tiles.append(tuple(rows))
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
        low = bytearray(8)
        high = bytearray(8)
        for y, row in enumerate(matrix):
            for x, pixel in enumerate(row):
                low[y] |= (pixel & 1) << (7 - x)
                high[y] |= ((pixel >> 1) & 1) << (7 - x)
        output.extend(low)
        output.extend(high)
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


def _decode_resource(image: bytes, resource: ResourceSpec) -> dict[str, object]:
    if resource.codec == NAMETABLE_CODEC:
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
    if resource.codec in (PALETTE_CODEC, PACMAN_PALETTE_CODEC):
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
        if resource.codec == PACMAN_PALETTE_CODEC:
            result["mirror_pairs"] = resource.parameters.get("mirror_pairs", [])
        return result
    if resource.codec == CHR_CODEC:
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
    if resource.codec == PACMAN_MAZE_CODEC:
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
    if resource.codec == PACMAN_ACTOR_CODEC:
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
    if resource.codec == FIXED_TEXT_CODEC:
        return {
            "id": resource.resource_id,
            "kind": resource.kind,
            "codec": resource.codec,
            **decode_text_fields(image, resource.parameters),
        }
    if resource.codec == PACMAN_SOUND_CODEC:
        return {
            "id": resource.resource_id,
            "kind": resource.kind,
            "codec": resource.codec,
            **decode_sound_bundle(image, resource.parameters),
        }
    if resource.codec == PACMAN_SCREEN_CODEC:
        return {
            "id": resource.resource_id,
            "kind": resource.kind,
            "codec": resource.codec,
            **decode_screen_collection(image, resource.parameters),
        }
    raise NesResourceError(f"unsupported NES resource codec {resource.codec}")


def decode_resources(
    source: bytes, profile: GameProfile, revision: RevisionSpec
) -> NesResourceBundleDocument:
    if hashlib.sha256(source).hexdigest() != revision.sha256:
        raise NesResourceError("resource source does not match the selected revision")
    image = parse_ines(source)
    if revision.mapper is not None and image.mapper != revision.mapper:
        raise NesResourceError("resource profile mapper differs from the source image")
    resources = tuple(_decode_resource(source, item) for item in _resource_specs(profile))
    return NesResourceBundleDocument(
        profile.game_id, revision.revision_id, revision.sha256, resources
    )


def _parse_resource(
    raw: object, spec: ResourceSpec
) -> tuple[Mapping[str, object], tuple[tuple[int, bytes], ...]]:
    if not isinstance(raw, dict):
        raise NesResourceError(f"resource {spec.resource_id} must be an object")
    if raw.get("id") != spec.resource_id or raw.get("codec") != spec.codec:
        raise NesResourceError(f"resource {spec.resource_id} identity differs from profile")
    if raw.get("kind") != spec.kind:
        raise NesResourceError(f"resource {spec.resource_id} kind differs from profile")
    encoded_spans: tuple[tuple[int, bytes], ...] | None = None
    if spec.codec == NAMETABLE_CODEC:
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
    elif spec.codec in (PALETTE_CODEC, PACMAN_PALETTE_CODEC):
        expected_fields = {"id", "kind", "codec", "values"}
        if spec.codec == PACMAN_PALETTE_CODEC:
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
                (offset, bytes((payload[entry],)))
                for entry, offsets in enumerate(replicated)
                for offset in offsets
            )
        if spec.codec == PACMAN_PALETTE_CODEC:
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
    elif spec.codec == CHR_CODEC:
        expected_fields = {"id", "kind", "codec", "tiles"}
        size = _integer(
            spec.parameters.get("size"), f"{spec.resource_id}.size", minimum=16
        )
        values = raw.get("tiles")
        if not isinstance(values, list):
            raise NesResourceError(f"{spec.resource_id}.tiles must be an array")
        payload = encode_chr_2bpp(values, size // 16)
    elif spec.codec == PACMAN_MAZE_CODEC:
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
        report = inspect_maze(raw.get("tiles"))
        if report["errors"]:
            raise NesResourceError("invalid Pac-Man maze: " + "; ".join(report["errors"]))
    elif spec.codec == PACMAN_ACTOR_CODEC:
        expected_fields = {
            "id", "kind", "codec", "standard_tile_quads",
            "alternate_tile_quads", "standard_attribute_quads",
            "alternate_attribute_quads", "oam_offsets_yx",
        }
        payload = encode_actor_mapping({
            key: raw.get(key) for key in expected_fields - {"id", "kind", "codec"}
        })
    elif spec.codec == FIXED_TEXT_CODEC:
        expected_fields = {"id", "kind", "codec", "encoding", "messages"}
        encoded_spans = encode_text_fields(raw, spec.parameters)
    elif spec.codec == PACMAN_SOUND_CODEC:
        expected_fields = {"id", "kind", "codec", "capacity", "slots"}
        encoded_spans = encode_sound_bundle(raw, spec.parameters)
    elif spec.codec == PACMAN_SCREEN_CODEC:
        expected_fields = {
            "id", "kind", "codec", "title_logo", "title_packets",
            "title_attributes", "fixed_blocks", "attract_packets",
            "intermission_visual", "read_only_code_tables", "encoding",
            "text_fields",
        }
        encoded_spans = encode_screen_collection(
            {key: raw.get(key) for key in expected_fields - {"id", "kind", "codec"}},
            spec.parameters,
        )
    else:
        raise NesResourceError(f"unsupported NES resource codec {spec.codec}")
    if set(raw) != expected_fields:
        raise NesResourceError(
            f"resource {spec.resource_id} fields differ from its codec contract"
        )
    if encoded_spans is None:
        offset, _size = _span(spec, len(payload))
        encoded_spans = ((offset, payload),)
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
    specs = _resource_specs(profile)
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
    specs = _resource_specs(profile)
    changes = []
    reports = []
    for raw, spec in zip(document.resources, specs):
        _normalized, replacements = _parse_resource(dict(raw), spec)
        range_reports = []
        for span_index, (offset, replacement) in enumerate(replacements):
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
    screens = sum(
        item.get("codec") in (NAMETABLE_CODEC, PACMAN_SCREEN_CODEC)
        for item in document.resources
    )
    palettes = sum(
        len(item.get("values", []))
        for item in document.resources
        if item.get("codec") in (PALETTE_CODEC, PACMAN_PALETTE_CODEC)
    )
    chr_tiles = sum(
        len(item.get("tiles", []))
        for item in document.resources
        if item.get("codec") == CHR_CODEC
    )
    mazes = sum(item.get("codec") == PACMAN_MAZE_CODEC for item in document.resources)
    actor_frames = sum(
        len(item.get("standard_tile_quads", []))
        + len(item.get("alternate_tile_quads", []))
        for item in document.resources
        if item.get("codec") == PACMAN_ACTOR_CODEC
    )
    text_messages = sum(
        len(item.get("messages", []))
        for item in document.resources
        if item.get("codec") == FIXED_TEXT_CODEC
    )
    text_messages += sum(
        len(item.get("text_fields", []))
        for item in document.resources
        if item.get("codec") == PACMAN_SCREEN_CODEC
    )
    sound_slots = sum(
        len(item.get("slots", []))
        for item in document.resources
        if item.get("codec") == PACMAN_SOUND_CODEC
    )
    sound_notes = sum(
        command.get("kind") == "note"
        for item in document.resources
        if item.get("codec") == PACMAN_SOUND_CODEC
        for slot in item.get("slots", [])
        for command in slot.get("stream", {}).get("commands", [])
    )
    sound_used_bytes = sum(
        len(encode_sound_stream(slot["stream"]))
        for item in document.resources
        if item.get("codec") == PACMAN_SOUND_CODEC
        for slot in item.get("slots", [])
    )
    return {
        "resource_count": len(document.resources),
        "screens": screens,
        "palette_bytes": palettes,
        "chr_tiles": chr_tiles,
        "mazes": mazes,
        "actor_frames": actor_frames,
        "text_messages": text_messages,
        "sound_slots": sound_slots,
        "sound_notes": sound_notes,
        "sound_used_bytes": sound_used_bytes,
    }
