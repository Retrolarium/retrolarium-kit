"""Profile-bound Solomon room-group colors and 58 shared RoomMap records."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Mapping

from retrolarium_kit.core.authoring import (
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
from retrolarium_core.ines import parse_ines


CODEC = "solomon.room-art.v1"
SCHEMA = "retrolarium.solomon-room-art"
VERSION = 1
GROUP_COUNT = 14
PATTERN_COUNT = 58
PATTERN_SIZE = 4


class SolomonRoomArtError(AuthoringError):
    """The regional room-art table or its edited document is invalid."""


@dataclass(frozen=True)
class RoomMapPattern:
    index: int
    palette: int
    top_left: int
    top_right: int
    bottom_left: int
    bottom_right: int

    def as_dict(self) -> dict[str, int]:
        return {
            "index": self.index,
            "palette": self.palette,
            "top_left": self.top_left,
            "top_right": self.top_right,
            "bottom_left": self.bottom_left,
            "bottom_right": self.bottom_right,
        }


@dataclass(frozen=True)
class SolomonRoomArtDocument:
    game_id: str
    revision_id: str
    source_sha256: str
    group_colors: tuple[int, ...]
    patterns: tuple[RoomMapPattern, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "schema": SCHEMA,
            "schema_version": VERSION,
            "game": self.game_id,
            "revision": self.revision_id,
            "source_sha256": self.source_sha256,
            "room_group_colors": list(self.group_colors),
            "room_map_patterns": [item.as_dict() for item in self.patterns],
        }


def _resource(profile: GameProfile, revision: RevisionSpec) -> ResourceSpec:
    matches = [item for item in profile.resources_for(revision) if item.codec == CODEC]
    if len(matches) != 1:
        raise SolomonRoomArtError("Solomon profile needs one room-art resource")
    return matches[0]


def _offsets(resource: ResourceSpec) -> tuple[int, int]:
    result = []
    for name, size in (
        ("room_group_colors_prg", GROUP_COUNT),
        ("room_map_patterns_prg", PATTERN_COUNT * PATTERN_SIZE),
    ):
        raw = resource.parameters.get(name)
        if type(raw) is not int or not 0 <= raw <= 0x8000 - size:
            raise SolomonRoomArtError(f"{name} escapes the 32 KiB PRG")
        result.append(raw)
    group, patterns = result
    if group < patterns + PATTERN_COUNT * PATTERN_SIZE and patterns < group + GROUP_COUNT:
        raise SolomonRoomArtError("Solomon room-art tables overlap")
    return group, patterns


def _source(source: bytes, revision: RevisionSpec):
    if hashlib.sha256(source).hexdigest() != revision.sha256:
        raise SolomonRoomArtError("room-art source image identity mismatch")
    image = parse_ines(source)
    if image.mapper != revision.mapper or len(image.prg) != 0x8000:
        raise SolomonRoomArtError("room art requires a 32 KiB mapper-3 PRG")
    return image


def _color(value: object, context: str) -> int:
    if type(value) is not int or (value != 0x80 and not 0 <= value <= 0x3F):
        raise SolomonRoomArtError(f"{context} must be 0..63 or $80")
    return value


def _tile(value: object, context: str, *, first: bool = False) -> int:
    maximum = 0xFC if first else 0xFF
    if type(value) is not int or not 0 <= value <= maximum:
        raise SolomonRoomArtError(f"{context} must be 0..{maximum}")
    if first and value & 3:
        raise SolomonRoomArtError(f"{context} reserves its low two palette bits")
    return value


def decode_room_art(source: bytes, profile: GameProfile, revision: RevisionSpec) -> SolomonRoomArtDocument:
    image = _source(source, revision)
    group_offset, pattern_offset = _offsets(_resource(profile, revision))
    colors = tuple(image.prg[group_offset : group_offset + GROUP_COUNT])
    if len(colors) != GROUP_COUNT:
        raise SolomonRoomArtError("room-group color table is truncated")
    for index, value in enumerate(colors):
        _color(value, f"room_group_colors[{index}]")
    patterns = []
    for index in range(PATTERN_COUNT):
        first, right, left_bottom, right_bottom = image.prg[
            pattern_offset + index * PATTERN_SIZE : pattern_offset + (index + 1) * PATTERN_SIZE
        ]
        patterns.append(RoomMapPattern(index, first & 3, first & 0xFC, right, left_bottom, right_bottom))
    return SolomonRoomArtDocument(
        profile.game_id,
        revision.revision_id,
        revision.sha256,
        colors,
        tuple(patterns),
    )


def parse_room_art_document(value: object, profile: GameProfile, revision: RevisionSpec) -> SolomonRoomArtDocument:
    _offsets(_resource(profile, revision))
    if not isinstance(value, Mapping) or set(value) != {
        "schema",
        "schema_version",
        "game",
        "revision",
        "source_sha256",
        "room_group_colors",
        "room_map_patterns",
    }:
        raise SolomonRoomArtError("room-art document fields differ")
    for name, expected in (
        ("schema", SCHEMA),
        ("schema_version", VERSION),
        ("game", profile.game_id),
        ("revision", revision.revision_id),
        ("source_sha256", revision.sha256),
    ):
        if value.get(name) != expected:
            raise SolomonRoomArtError(f"room-art {name} differs from the profile")
    colors = value["room_group_colors"]
    if not isinstance(colors, list) or len(colors) != GROUP_COUNT:
        raise SolomonRoomArtError("room art needs 14 group colors")
    checked_colors = tuple(_color(item, f"room_group_colors[{index}]") for index, item in enumerate(colors))
    raw_patterns = value["room_map_patterns"]
    if not isinstance(raw_patterns, list) or len(raw_patterns) != PATTERN_COUNT:
        raise SolomonRoomArtError("room art needs 58 RoomMap patterns")
    patterns = []
    required = {
        "index",
        "palette",
        "top_left",
        "top_right",
        "bottom_left",
        "bottom_right",
    }
    for index, raw in enumerate(raw_patterns):
        if (
            not isinstance(raw, Mapping)
            or set(raw) != required
            or type(raw["index"]) is not int
            or raw["index"] != index
        ):
            raise SolomonRoomArtError(f"RoomMap pattern {index} has invalid fields")
        palette = raw["palette"]
        if type(palette) is not int or not 0 <= palette <= 3:
            raise SolomonRoomArtError(f"RoomMap pattern {index} palette must be 0..3")
        patterns.append(
            RoomMapPattern(
                index,
                palette,
                _tile(raw["top_left"], f"pattern {index}.top_left", first=True),
                _tile(raw["top_right"], f"pattern {index}.top_right"),
                _tile(raw["bottom_left"], f"pattern {index}.bottom_left"),
                _tile(raw["bottom_right"], f"pattern {index}.bottom_right"),
            )
        )
    return SolomonRoomArtDocument(
        profile.game_id,
        revision.revision_id,
        revision.sha256,
        checked_colors,
        tuple(patterns),
    )


def build_room_art(
    source: bytes,
    document: SolomonRoomArtDocument,
    profile: GameProfile,
    revision: RevisionSpec,
) -> tuple[bytes, dict[str, object]]:
    image = _source(source, revision)
    checked = parse_room_art_document(document.as_dict(), profile, revision)
    group_offset, pattern_offset = _offsets(_resource(profile, revision))
    encoded_patterns = bytearray()
    for pattern in checked.patterns:
        encoded_patterns.extend(
            (
                pattern.top_left | pattern.palette,
                pattern.top_right,
                pattern.bottom_left,
                pattern.bottom_right,
            )
        )
    base = len(image.header) + len(image.trainer)
    changes = []
    for name, offset, payload in (
        ("room-group-colors", group_offset, bytes(checked.group_colors)),
        ("room-map-patterns", pattern_offset, bytes(encoded_patterns)),
    ):
        file_offset = base + offset
        changes.append(
            Change(
                AddressRange(Address("file", file_offset), len(payload)),
                source[file_offset : file_offset + len(payload)],
                payload,
                Provenance(revision.sha256, name, "encode"),
            )
        )
    output = BuildPlan(revision.sha256, ChangeSet(tuple(changes))).apply(source)
    return output, {
        "schema": "retrolarium.solomon-room-art-build",
        "schema_version": 1,
        "game": profile.game_id,
        "revision": revision.revision_id,
        "source_sha256": revision.sha256,
        "output_sha256": hashlib.sha256(output).hexdigest(),
        "size": len(output),
        "changed_bytes": sum(a != b for a, b in zip(source, output)),
        "room_group_color_file_offset": base + group_offset,
        "room_map_pattern_file_offset": base + pattern_offset,
    }
