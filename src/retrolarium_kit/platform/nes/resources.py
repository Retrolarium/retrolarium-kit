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
    PayloadSpec,
    Provenance,
    ResourceSpec,
    RevisionSpec,
    stable_identifier,
)
from .castle_mml import (
    CODEC as CASTLE_MML_MUSIC_CODEC,
    decode_music as decode_castle_mml_music,
    encode_music as encode_castle_mml_music,
)
from .fds import FdsError, fds_payload, fds_payload_ranges, parse_fds_side
from .fds_synthesis import (
    CODEC as FDS_SYNTHESIS_CODEC,
    decode_synthesis as decode_fds_synthesis,
    encode_synthesis as encode_fds_synthesis,
)
from .fds_music import (
    CODEC as FDS_MUSIC_CODEC,
    decode_music as decode_fds_music,
    encode_music as encode_fds_music,
)
from .ines import parse_ines, parse_ines_prefix
from .doraemon_hierarchy import (
    CODEC as DORAEMON_HIERARCHY_CODEC,
    decode_hierarchy as decode_doraemon_hierarchy,
    encode_hierarchy as encode_doraemon_hierarchy,
    payload_size as doraemon_hierarchy_size,
)
from .doraemon_palettes import (
    ROOM_SELECTOR_CODEC as DORAEMON_ROOM_SELECTOR_CODEC,
    decode_room_selectors as decode_doraemon_room_selectors,
    encode_room_selectors as encode_doraemon_room_selectors,
)
from .doraemon_world2_metatiles import (
    CODEC as DORAEMON_WORLD2_METATILE_CODEC,
    COLLISION_SIZE as DORAEMON_WORLD2_COLLISION_SIZE,
    VISUAL_SIZE as DORAEMON_WORLD2_VISUAL_SIZE,
    decode_metatiles as decode_doraemon_world2_metatiles,
    encode_metatiles as encode_doraemon_world2_metatiles,
)
from .doraemon_world2_streams import (
    CODEC as DORAEMON_WORLD2_SCREEN_CODEC,
    PAYLOAD_SIZE as DORAEMON_WORLD2_SCREEN_SIZE,
    decode_screens as decode_doraemon_world2_screens,
    encode_screens as encode_doraemon_world2_screens,
)
from .doraemon_world2_stage import (
    CODEC as DORAEMON_WORLD2_STAGE_CODEC,
    SEQUENCE_SIZE as DORAEMON_WORLD2_STAGE_SEQUENCE_SIZE,
    START_SIZE as DORAEMON_WORLD2_STAGE_START_SIZE,
    decode_stage as decode_doraemon_world2_stage,
    encode_stage as encode_doraemon_world2_stage,
)
from .doraemon_world2_branches import (
    CODEC as DORAEMON_WORLD2_BRANCH_CODEC,
    PAYLOAD_SIZE as DORAEMON_WORLD2_BRANCH_SIZE,
    decode_branches as decode_doraemon_world2_branches,
    encode_branches as encode_doraemon_world2_branches,
)
from .doraemon_world2_chapters import (
    CODEC as DORAEMON_WORLD2_CHAPTER_CODEC,
    PAYLOAD_SIZE as DORAEMON_WORLD2_CHAPTER_SIZE,
    decode_chapters as decode_doraemon_world2_chapters,
    encode_chapters as encode_doraemon_world2_chapters,
)
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
from .nes_screen_packets import (
    CODEC as PPU_SCREEN_PACKET_CODEC,
    decode_screen_packets,
    encode_screen_packets,
)
from .smb_world import (
    CODEC as SMB_WORLD_CODEC,
    decode_world as decode_smb_world,
    encode_world as encode_smb_world,
)
from .smb_level import (
    CODEC as SMB_LEVEL_CODEC,
    decode_levels as decode_smb_levels,
    encode_level_pointers as encode_smb_level_pointers,
    encode_levels as encode_smb_levels,
    stream_pool as smb_level_stream_pool,
)
from .smb_player_frames import (
    CODEC as SMB_PLAYER_FRAME_CODEC,
    ENCODED_SIZE as SMB_PLAYER_FRAME_SIZE,
    decode_player_frames,
    encode_player_frames,
)
from .smb_apu import (
    CODEC as SMB_APU_CODEC,
    decode_music as decode_smb_music,
    encode_music as encode_smb_music,
    parse_layout as parse_smb_apu_layout,
)
from .smb_apu_envelope import (
    CODEC as SMB_APU_ENVELOPE_CODEC,
    decode_envelope as decode_smb_apu_envelope,
    encode_envelope as encode_smb_apu_envelope,
)
from .nes_visuals import (
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
PACMAN_PALETTE_CODEC = "pacman.palette-table.v1"
CHR_CODEC = "nes.chr-2bpp.v1"
CASTLE_ROOM_CELL_CODEC = "castle.room-cell-visuals.v1"
CASTLE_METASPRITE_CODEC = "castle.metasprite-descriptors.v1"
CASTLE_SUB_COMMAND_TEXT_CODEC = "castle.sub-command-text.v1"
CASTLE_SFX_PARAMETERS_CODEC = "castle.sfx-parameters.v1"
CASTLE_FIXED_RECORD_CODECS = (
    CASTLE_SUB_COMMAND_TEXT_CODEC, CASTLE_SFX_PARAMETERS_CODEC
)
SUPPORTED_CODECS = (
    NAMETABLE_CODEC, PALETTE_CODEC, PACMAN_PALETTE_CODEC, CHR_CODEC, PACMAN_MAZE_CODEC,
    PACMAN_ACTOR_CODEC, FIXED_TEXT_CODEC,
    PACMAN_SOUND_CODEC,
    PACMAN_SCREEN_CODEC,
    SMB_WORLD_CODEC,
    SMB_LEVEL_CODEC,
    SMB_PLAYER_FRAME_CODEC,
    SMB_APU_CODEC,
    SMB_APU_ENVELOPE_CODEC,
    FDS_SYNTHESIS_CODEC,
    FDS_MUSIC_CODEC,
    METATILE_CODEC,
    PALETTE_PACKET_CODEC, CASTLE_ROOM_CELL_CODEC, CASTLE_METASPRITE_CODEC,
    *CASTLE_FIXED_RECORD_CODECS, CASTLE_MML_MUSIC_CODEC,
    PPU_SCREEN_PACKET_CODEC, DORAEMON_HIERARCHY_CODEC,
    DORAEMON_ROOM_SELECTOR_CODEC, DORAEMON_WORLD2_METATILE_CODEC,
    DORAEMON_WORLD2_SCREEN_CODEC, DORAEMON_WORLD2_STAGE_CODEC,
    DORAEMON_WORLD2_BRANCH_CODEC,
    DORAEMON_WORLD2_CHAPTER_CODEC,
)


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
            item for item in profile_resources if item.codec in SUPPORTED_CODECS
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
            and declared_by_id[item].codec not in SUPPORTED_CODECS
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
        if item.codec not in (*SUPPORTED_CODECS, ADAPTER_CODEC)
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
        if item.codec != SMB_LEVEL_CODEC:
            continue
        world_resource_id = item.parameters.get("world_resource")
        if world_resource_id is None:
            continue
        if not isinstance(world_resource_id, str) or not world_resource_id:
            raise NesResourceError(
                f"{item.resource_id}.world_resource must be a non-empty string"
            )
        world_resource = resources_by_id.get(world_resource_id)
        if world_resource is None or world_resource.codec != SMB_WORLD_CODEC:
            raise NesResourceError(
                f"{item.resource_id}.world_resource must name an SMB world resource"
            )
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


def _level_stream_spans(
    resource: ResourceSpec, stream_kind: str
) -> tuple[_ResourceSpan, ...]:
    capacities_value = resource.parameters.get(f"{stream_kind}_capacities")
    if not isinstance(capacities_value, list) or not capacities_value:
        raise NesResourceError(
            f"{resource.resource_id}.{stream_kind}_capacities must be a non-empty array"
        )
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
            raise NesResourceError(
                f"{resource.resource_id}.{stream_kind}_size is {size}, "
                f"expected {sum(capacities)}"
            )
        return (_ResourceSpan(offset, size),)
    if not isinstance(spans_value, list) or len(spans_value) != len(capacities):
        raise NesResourceError(
            f"{resource.resource_id}.{stream_kind}_stream_spans must contain "
            f"{len(capacities)} entries"
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
            raise NesResourceError(
                f"{context}.size is {size}, expected stream capacity {capacity}"
            )
        result.append(_ResourceSpan(
            _integer(value.get("offset"), f"{context}.offset"),
            size,
            payload_id,
        ))
    return tuple(result)


def _level_rendering(resource: ResourceSpec) -> str:
    value = resource.parameters.get("rendering", "smb1")
    if value not in {"smb1", "ann", "smb2"}:
        raise NesResourceError(
            f"{resource.resource_id}.rendering must be smb1, ann or smb2"
        )
    return value


def _world_component_span(
    resource: ResourceSpec, component: str
) -> _ResourceSpan:
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
        raise NesResourceError(
            f"{context}.size is {size}, expected {expected_size}"
        )
    return _ResourceSpan(
        _integer(value.get("offset"), f"{context}.offset"),
        size,
        payload_id,
    )


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
    if resource.codec == DORAEMON_WORLD2_CHAPTER_CODEC:
        offset, size = _span(resource, DORAEMON_WORLD2_CHAPTER_SIZE)
        try:
            decoded = decode_doraemon_world2_chapters(image[offset:offset + size])
        except AuthoringError as exc:
            raise NesResourceError(str(exc)) from exc
        return {"id": resource.resource_id, "kind": resource.kind,
                "codec": resource.codec, **decoded}
    if resource.codec == DORAEMON_WORLD2_BRANCH_CODEC:
        offset, size = _span(resource, DORAEMON_WORLD2_BRANCH_SIZE)
        try:
            decoded = decode_doraemon_world2_branches(image[offset:offset + size])
        except AuthoringError as exc:
            raise NesResourceError(str(exc)) from exc
        return {"id": resource.resource_id, "kind": resource.kind,
                "codec": resource.codec, **decoded}
    if resource.codec == DORAEMON_WORLD2_STAGE_CODEC:
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
    if resource.codec == DORAEMON_WORLD2_SCREEN_CODEC:
        offset, size = _span(resource, DORAEMON_WORLD2_SCREEN_SIZE)
        try:
            decoded = decode_doraemon_world2_screens(image[offset:offset + size])
        except AuthoringError as exc:
            raise NesResourceError(str(exc)) from exc
        return {"id": resource.resource_id, "kind": resource.kind,
                "codec": resource.codec, **decoded}
    if resource.codec == DORAEMON_WORLD2_METATILE_CODEC:
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
    if resource.codec == DORAEMON_ROOM_SELECTOR_CODEC:
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
    if resource.codec == DORAEMON_HIERARCHY_CODEC:
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
        palette_model = resource.parameters.get("palette_model")
        if palette_model is not None:
            if palette_model not in ("nes", "rp2c04_0004"):
                raise NesResourceError(
                    f"{resource.resource_id}.palette_model is unsupported"
                )
            result["palette_model"] = palette_model
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
    if resource.codec == CASTLE_ROOM_CELL_CODEC:
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
    if resource.codec == CASTLE_METASPRITE_CODEC:
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
    if resource.codec in CASTLE_FIXED_RECORD_CODECS:
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
    if resource.codec == CASTLE_MML_MUSIC_CODEC:
        try:
            decoded = decode_castle_mml_music(image, resource.parameters)
        except AuthoringError as exc:
            raise NesResourceError(str(exc)) from exc
        return {
            "id": resource.resource_id,
            "kind": resource.kind,
            "codec": resource.codec,
            **decoded,
        }
    if resource.codec == METATILE_CODEC:
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
    if resource.codec == SMB_PLAYER_FRAME_CODEC:
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
    if resource.codec == SMB_APU_CODEC:
        try:
            expected = parse_smb_apu_layout(resource.parameters).encoded_size
            offset, size = _span(resource, expected)
            payload = image[offset : offset + size]
            if len(payload) != size:
                raise NesResourceError(
                    f"{resource.resource_id} exceeds the source image"
                )
            decoded = decode_smb_music(payload, resource.parameters)
        except AuthoringError as exc:
            raise NesResourceError(str(exc)) from exc
        return {
            "id": resource.resource_id,
            "kind": resource.kind,
            "codec": resource.codec,
            **decoded,
        }
    if resource.codec == SMB_APU_ENVELOPE_CODEC:
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
            decoded = decode_smb_apu_envelope(payload, resource.parameters)
        except AuthoringError as exc:
            raise NesResourceError(str(exc)) from exc
        return {
            "id": resource.resource_id,
            "kind": resource.kind,
            "codec": resource.codec,
            **decoded,
        }
    if resource.codec == FDS_SYNTHESIS_CODEC:
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
    if resource.codec == FDS_MUSIC_CODEC:
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
    if resource.codec == PALETTE_PACKET_CODEC:
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
    if resource.codec == PPU_SCREEN_PACKET_CODEC:
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


def _decode_smb_world_resource(
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


def _decode_smb_level_resource(
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


def _decode_profile_resource(
    source: bytes,
    profile: GameProfile,
    revision: RevisionSpec,
    resource: ResourceSpec,
) -> dict[str, object]:
    if resource.codec == SMB_LEVEL_CODEC:
        return _decode_smb_level_resource(source, profile, revision, resource)
    if resource.codec == SMB_WORLD_CODEC:
        return _decode_smb_world_resource(source, profile, revision, resource)
    return _decode_resource(
        _resource_source(source, profile, revision, resource), resource
    )


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


def _parse_resource(
    raw: object, spec: ResourceSpec
) -> tuple[Mapping[str, object], tuple[_EncodedSpan, ...]]:
    if not isinstance(raw, dict):
        raise NesResourceError(f"resource {spec.resource_id} must be an object")
    if raw.get("id") != spec.resource_id or raw.get("codec") != spec.codec:
        raise NesResourceError(f"resource {spec.resource_id} identity differs from profile")
    if raw.get("kind") != spec.kind:
        raise NesResourceError(f"resource {spec.resource_id} kind differs from profile")
    encoded_spans: tuple[_EncodedSpan, ...] | None = None
    if spec.codec == DORAEMON_WORLD2_CHAPTER_CODEC:
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
    elif spec.codec == DORAEMON_WORLD2_BRANCH_CODEC:
        expected_fields = {"id", "kind", "codec", "branches"}
        _span(spec, DORAEMON_WORLD2_BRANCH_SIZE)
        try:
            payload = encode_doraemon_world2_branches(
                {"branches": raw.get("branches")}
            )
        except AuthoringError as exc:
            raise NesResourceError(str(exc)) from exc
    elif spec.codec == DORAEMON_WORLD2_STAGE_CODEC:
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
    elif spec.codec == DORAEMON_WORLD2_SCREEN_CODEC:
        expected_fields = {"id", "kind", "codec", "raw_hex", "screens"}
        _span(spec, DORAEMON_WORLD2_SCREEN_SIZE)
        try:
            payload = encode_doraemon_world2_screens(
                {"raw_hex": raw.get("raw_hex"), "screens": raw.get("screens")}
            )
        except AuthoringError as exc:
            raise NesResourceError(str(exc)) from exc
    elif spec.codec == DORAEMON_WORLD2_METATILE_CODEC:
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
    elif spec.codec == DORAEMON_ROOM_SELECTOR_CODEC:
        expected_fields = {"id", "kind", "codec", "selectors"}
        _span(spec, 64)
        try:
            payload = encode_doraemon_room_selectors(raw.get("selectors"))
        except AuthoringError as exc:
            raise NesResourceError(str(exc)) from exc
    elif spec.codec == DORAEMON_HIERARCHY_CODEC:
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
    elif spec.codec == NAMETABLE_CODEC:
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
                _EncodedSpan(offset, bytes((payload[entry],)))
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
    elif spec.codec == CASTLE_ROOM_CELL_CODEC:
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
    elif spec.codec == CASTLE_METASPRITE_CODEC:
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
    elif spec.codec in CASTLE_FIXED_RECORD_CODECS:
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
    elif spec.codec == CASTLE_MML_MUSIC_CODEC:
        expected_fields = {
            "id", "kind", "codec", "capacity_bytes", "used_bytes",
            "mml_cpu_address", "playback_prefix_cpu_address", "channel_names",
            "playback_prefix", "track_sets", "streams",
        }
        try:
            encoded_spans = tuple(
                _EncodedSpan(offset, replacement)
                for offset, replacement in encode_castle_mml_music(
                    {
                        key: raw.get(key)
                        for key in expected_fields - {"id", "kind", "codec"}
                    },
                    spec.parameters,
                )
            )
        except AuthoringError as exc:
            raise NesResourceError(str(exc)) from exc
    elif spec.codec == METATILE_CODEC:
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
    elif spec.codec == SMB_PLAYER_FRAME_CODEC:
        expected_fields = {
            "id", "kind", "codec", "frame_offsets", "frame_names",
            "tile_base", "blank_tile", "frames", "swim_kick_tiles",
        }
        try:
            payload = encode_player_frames(
                {
                    key: raw.get(key)
                    for key in expected_fields - {"id", "kind", "codec"}
                },
                spec.parameters,
            )
        except AuthoringError as exc:
            raise NesResourceError(str(exc)) from exc
    elif spec.codec == SMB_APU_CODEC:
        expected_fields = {
            "id", "kind", "codec", "selectors", "ground_schedule",
            "headers", "shared_stream_arena", "note_periods", "note_lengths",
            "envelopes",
        }
        try:
            payload = encode_smb_music(
                {
                    key: raw.get(key)
                    for key in expected_fields - {"id", "kind", "codec"}
                },
                spec.parameters,
            )
        except AuthoringError as exc:
            raise NesResourceError(str(exc)) from exc
    elif spec.codec == SMB_APU_ENVELOPE_CODEC:
        expected_fields = {"id", "kind", "codec", "steps"}
        try:
            payload = encode_smb_apu_envelope(
                {"steps": raw.get("steps")}, spec.parameters
            )
        except AuthoringError as exc:
            raise NesResourceError(str(exc)) from exc
    elif spec.codec == FDS_SYNTHESIS_CODEC:
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
    elif spec.codec == FDS_MUSIC_CODEC:
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
    elif spec.codec == PALETTE_PACKET_CODEC:
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
        encoded_spans = tuple(
            _EncodedSpan(offset, replacement)
            for offset, replacement in encode_text_fields(raw, spec.parameters)
        )
    elif spec.codec == PPU_SCREEN_PACKET_CODEC:
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
    elif spec.codec == PACMAN_SOUND_CODEC:
        expected_fields = {"id", "kind", "codec", "capacity", "slots"}
        encoded_spans = tuple(
            _EncodedSpan(offset, replacement)
            for offset, replacement in encode_sound_bundle(raw, spec.parameters)
        )
    elif spec.codec == PACMAN_SCREEN_CODEC:
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
    elif spec.codec == SMB_WORLD_CODEC:
        expected_fields = {
            "id", "kind", "codec", "area_types", "worlds", "physics_tables"
        }
        try:
            routing, physics = encode_smb_world(
                {
                    key: raw.get(key)
                    for key in expected_fields - {"id", "kind", "codec"}
                },
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
            raise NesResourceError(
                f"{spec.resource_id} encoded sizes differ from the profile"
            )
        encoded_spans = (
            *_encoded_resource_spans(
                routing, (_world_component_span(spec, "routing"),)
            ),
            *_encoded_resource_spans(
                physics, (_world_component_span(spec, "physics"),)
            ),
        )
    elif spec.codec == SMB_LEVEL_CODEC:
        expected_fields = {
            "id", "kind", "codec", "rendering", "area_types", "areas"
        }
        rendering = _level_rendering(spec)
        if raw.get("rendering") != rendering:
            raise NesResourceError(
                f"{spec.resource_id}.rendering differs from the profile"
            )
        world_resource_id = spec.parameters.get("world_resource")
        if world_resource_id is not None:
            expected_fields.add("world_resource")
            if raw.get("world_resource") != world_resource_id:
                raise NesResourceError(
                    f"{spec.resource_id}.world_resource differs from the profile"
                )
        try:
            pooled = smb_level_stream_pool(spec.parameters) is not None
        except AuthoringError as exc:
            raise NesResourceError(str(exc)) from exc
        # Documents decoded before pooling lack the flag; it is optional.
        if "capacity_pool" in raw:
            expected_fields.add("capacity_pool")
            if raw.get("capacity_pool") is not pooled:
                raise NesResourceError(
                    f"{spec.resource_id}.capacity_pool differs from the profile"
                )
        level_value = {
            key: raw.get(key)
            for key in expected_fields
            - {"id", "kind", "codec", "rendering", "world_resource", "capacity_pool"}
        }
        try:
            area, enemy = encode_smb_levels(level_value, spec.parameters)
            pointers = encode_smb_level_pointers(level_value, spec.parameters)
        except AuthoringError as exc:
            raise NesResourceError(str(exc)) from exc
        encoded_spans = (
            *_encoded_resource_spans(
                area, _level_stream_spans(spec, "area")
            ),
            *_encoded_resource_spans(
                enemy, _level_stream_spans(spec, "enemy")
            ),
            *(_EncodedSpan(offset, table) for offset, table in pointers),
        )
    else:
        raise NesResourceError(f"unsupported NES resource codec {spec.codec}")
    if set(raw) != expected_fields:
        raise NesResourceError(
            f"resource {spec.resource_id} fields differ from its codec contract"
        )
    if encoded_spans is None:
        offset, _size = _span(spec, len(payload))
        encoded_spans = (_EncodedSpan(offset, payload),)
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
    screens = sum(
        (
            len(item.get("screens", []))
            if item.get("codec") == PPU_SCREEN_PACKET_CODEC
            else item.get("codec") in (NAMETABLE_CODEC, PACMAN_SCREEN_CODEC)
        )
        for item in document.resources
    )
    palettes = sum(
        len(item.get("values", []))
        for item in document.resources
        if item.get("codec") in (PALETTE_CODEC, PACMAN_PALETTE_CODEC)
    )
    palettes += sum(
        len(packet.get("values", []))
        for item in document.resources
        if item.get("codec") == PALETTE_PACKET_CODEC
        for packet in item.get("packets", [])
    )
    palette_packets = sum(
        len(item.get("packets", []))
        for item in document.resources
        if item.get("codec") == PALETTE_PACKET_CODEC
    )
    metatiles = sum(
        len(item.get("records", []))
        for item in document.resources
        if item.get("codec") == METATILE_CODEC
    )
    room_cells = sum(
        len(item.get("records", []))
        for item in document.resources
        if item.get("codec") == CASTLE_ROOM_CELL_CODEC
    )
    metasprite_descriptors = sum(
        len(item.get("descriptors", []))
        for item in document.resources
        if item.get("codec") == CASTLE_METASPRITE_CODEC
    )
    fixed_byte_records = sum(
        len(item.get("records", []))
        for item in document.resources
        if item.get("codec") in CASTLE_FIXED_RECORD_CODECS
    )
    fixed_byte_values = sum(
        len(record.get("values", []))
        for item in document.resources
        if item.get("codec") in CASTLE_FIXED_RECORD_CODECS
        for record in item.get("records", [])
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
    actor_frames += sum(
        len(item.get("frames", []))
        for item in document.resources
        if item.get("codec") == SMB_PLAYER_FRAME_CODEC
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
    text_messages += sum(
        len(item.get("text_fields", []))
        for item in document.resources
        if item.get("codec") == PPU_SCREEN_PACKET_CODEC
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
    smb_music_headers = sum(
        len(item.get("headers", []))
        for item in document.resources
        if item.get("codec") == SMB_APU_CODEC
    )
    smb_music_arena_bytes = sum(
        len(item.get("shared_stream_arena", []))
        for item in document.resources
        if item.get("codec") == SMB_APU_CODEC
    )
    castle_music_tracks = sum(
        len(item.get("track_sets", []))
        for item in document.resources
        if item.get("codec") == CASTLE_MML_MUSIC_CODEC
    )
    castle_mml_streams = sum(
        len(item.get("streams", []))
        for item in document.resources
        if item.get("codec") == CASTLE_MML_MUSIC_CODEC
    )
    castle_mml_used_bytes = sum(
        int(item.get("used_bytes", 0))
        for item in document.resources
        if item.get("codec") == CASTLE_MML_MUSIC_CODEC
    )
    smb_worlds = sum(
        len(item.get("worlds", []))
        for item in document.resources
        if item.get("codec") == SMB_WORLD_CODEC
    )
    smb_routes = sum(
        len(world.get("areas", []))
        for item in document.resources
        if item.get("codec") == SMB_WORLD_CODEC
        for world in item.get("worlds", [])
    )
    smb_physics_tables = sum(
        len(item.get("physics_tables", []))
        for item in document.resources
        if item.get("codec") == SMB_WORLD_CODEC
    )
    smb_levels = sum(
        len(item.get("areas", []))
        for item in document.resources
        if item.get("codec") == SMB_LEVEL_CODEC
    )
    smb_area_objects = sum(
        len(area.get("objects", []))
        for item in document.resources
        if item.get("codec") == SMB_LEVEL_CODEC
        for area in item.get("areas", [])
    )
    smb_enemy_records = sum(
        len(area.get("enemies", []))
        for item in document.resources
        if item.get("codec") == SMB_LEVEL_CODEC
        for area in item.get("areas", [])
    )
    return {
        "resource_count": len(document.resources),
        "screens": screens,
        "palette_bytes": palettes,
        "palette_packets": palette_packets,
        "metatiles": metatiles,
        "room_cells": room_cells,
        "metasprite_descriptors": metasprite_descriptors,
        "fixed_byte_records": fixed_byte_records,
        "fixed_byte_values": fixed_byte_values,
        "chr_tiles": chr_tiles,
        "mazes": mazes,
        "actor_frames": actor_frames,
        "text_messages": text_messages,
        "sound_slots": sound_slots,
        "sound_notes": sound_notes,
        "sound_used_bytes": sound_used_bytes,
        "smb_music_headers": smb_music_headers,
        "smb_music_arena_bytes": smb_music_arena_bytes,
        "castle_music_tracks": castle_music_tracks,
        "castle_mml_streams": castle_mml_streams,
        "castle_mml_used_bytes": castle_mml_used_bytes,
        "smb_worlds": smb_worlds,
        "smb_routes": smb_routes,
        "smb_physics_tables": smb_physics_tables,
        "smb_levels": smb_levels,
        "smb_area_objects": smb_area_objects,
        "smb_enemy_records": smb_enemy_records,
    }
