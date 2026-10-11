"""Source-bound initial enemy and player sprite frames for Solomon's Key."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Mapping

from retrolarium_kit.core.authoring import (
    Address, AddressRange, AuthoringError, BuildPlan, Change, ChangeSet,
    GameProfile, Provenance, ResourceSpec, RevisionSpec,
)
from retrolarium_core.ines import parse_ines


CODEC = "solomon.sprite-frames.v1"
SCHEMA = "retrolarium.solomon-sprite-frames"
VERSION = 1
ENEMY_TYPE_FIRST = 0x18
ENEMY_TYPE_COUNT = 108
POINTER_COUNT = 33
CONFIGURATION_COUNT = 27
PLAYER_ACTIONS = {"right": 0x16, "left": 0x17}


class SolomonSpriteFrameError(AuthoringError):
    """A Solomon animation pointer or edited sprite frame is invalid."""


@dataclass(frozen=True)
class SpriteFrame:
    prg_offset: int
    left_tile: int
    right_tile: int
    flags: int
    enemy_types: tuple[int, ...]
    player_facings: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "prg_offset": self.prg_offset,
            "left_tile": self.left_tile,
            "right_tile": self.right_tile,
            "flags": self.flags,
            "enemy_types": list(self.enemy_types),
            "player_facings": list(self.player_facings),
        }


@dataclass(frozen=True)
class SolomonSpriteFrameDocument:
    game_id: str
    revision_id: str
    source_sha256: str
    frames: tuple[SpriteFrame, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "schema": SCHEMA,
            "schema_version": VERSION,
            "game": self.game_id,
            "revision": self.revision_id,
            "source_sha256": self.source_sha256,
            "frames": [frame.as_dict() for frame in self.frames],
        }


def _resource(profile: GameProfile, revision: RevisionSpec) -> ResourceSpec:
    matches = [
        item for item in profile.resources_for(revision) if item.codec == CODEC
    ]
    if len(matches) != 1:
        raise SolomonSpriteFrameError("Solomon profile needs one sprite-frame resource")
    return matches[0]


def _table_offsets(resource: ResourceSpec) -> tuple[int, int]:
    result = []
    for name, size in (
        ("animation_pointers_prg", POINTER_COUNT * 2),
        ("enemy_configuration_prg", CONFIGURATION_COUNT),
    ):
        offset = resource.parameters.get(name)
        if type(offset) is not int or not 0 <= offset <= 0x8000 - size:
            raise SolomonSpriteFrameError(f"{name} escapes the 32 KiB PRG")
        result.append(offset)
    return result[0], result[1]


def _source(source: bytes, revision: RevisionSpec):
    if hashlib.sha256(source).hexdigest() != revision.sha256:
        raise SolomonSpriteFrameError("sprite-frame source image identity mismatch")
    image = parse_ines(source)
    if image.mapper != revision.mapper or len(image.prg) != 0x8000:
        raise SolomonSpriteFrameError("Solomon sprite frames need a 32 KiB mapper-3 PRG")
    return image


def _byte(prg: bytes, address: int) -> int:
    offset = address - 0x8000
    if not 0 <= offset < len(prg):
        raise SolomonSpriteFrameError(
            f"sprite animation address ${address:04X} is outside PRG"
        )
    return prg[offset]


def _word(prg: bytes, address: int) -> int:
    return _byte(prg, address) | (_byte(prg, address + 1) << 8)


def _initial_frame_offset(initial_phase: int) -> int:
    phase_sum = initial_phase + 0xF0
    phase = phase_sum & 0xFF
    if phase_sum <= 0xFF:
        phase = (phase & 0x0F) * 0x11
    return (phase >> 3) + (phase >> 4)


def _frame_offset(
    prg: bytes, pointer_table: int, pointer_index: int,
    action: int, variant: int,
) -> int:
    group = _word(prg, pointer_table + pointer_index * 2)
    descriptor = group + action * 4
    initial_phase = _byte(prg, descriptor)
    descriptor_flags = _byte(prg, descriptor + 1)
    frame_pointer = _word(prg, descriptor + 2)
    if descriptor_flags & 1:
        frame_pointer = _word(prg, frame_pointer + variant * 2)
    frame_address = frame_pointer + _initial_frame_offset(initial_phase)
    offset = frame_address - 0x8000
    if not 0 <= offset <= len(prg) - 3:
        raise SolomonSpriteFrameError(
            f"sprite frame at ${frame_address:04X} escapes PRG"
        )
    return offset


def _bindings(
    prg: bytes, pointer_offset: int, configuration_offset: int,
) -> dict[int, tuple[list[int], list[str]]]:
    pointer_table = pointer_offset + 0x8000
    configuration = configuration_offset + 0x8000
    result: dict[int, tuple[list[int], list[str]]] = {}
    for enemy_type in range(ENEMY_TYPE_FIRST, ENEMY_TYPE_FIRST + ENEMY_TYPE_COUNT):
        flags = _byte(
            prg, configuration + ((enemy_type - ENEMY_TYPE_FIRST) >> 2)
        )
        action = enemy_type & 3
        if not flags & 0x08:
            action |= 0x18
        offset = _frame_offset(
            prg, pointer_table, enemy_type >> 2, action, enemy_type & 3
        )
        result.setdefault(offset, ([], []))[0].append(enemy_type)
    for facing, action in PLAYER_ACTIONS.items():
        offset = _frame_offset(prg, pointer_table, 0, action, 0)
        result.setdefault(offset, ([], []))[1].append(facing)
    offsets = sorted(result)
    if any(left + 3 > right for left, right in zip(offsets, offsets[1:])):
        raise SolomonSpriteFrameError("sprite-frame source records overlap")
    return result


def decode_sprite_frames(
    source: bytes, profile: GameProfile, revision: RevisionSpec
) -> SolomonSpriteFrameDocument:
    image = _source(source, revision)
    pointer_offset, configuration_offset = _table_offsets(
        _resource(profile, revision)
    )
    bindings = _bindings(image.prg, pointer_offset, configuration_offset)
    frames = []
    for offset, (enemy_types, facings) in sorted(bindings.items()):
        left, right, flags = image.prg[offset:offset + 3]
        frames.append(SpriteFrame(
            offset, left, right, flags,
            tuple(enemy_types), tuple(facings),
        ))
    return SolomonSpriteFrameDocument(
        profile.game_id, revision.revision_id, revision.sha256, tuple(frames),
    )


def _byte_field(value: object, context: str) -> int:
    if type(value) is not int or not 0 <= value <= 0xFF:
        raise SolomonSpriteFrameError(f"{context} must be a byte")
    return value


def parse_sprite_frame_document(
    value: object, profile: GameProfile, revision: RevisionSpec
) -> SolomonSpriteFrameDocument:
    _table_offsets(_resource(profile, revision))
    if not isinstance(value, Mapping) or set(value) != {
        "schema", "schema_version", "game", "revision",
        "source_sha256", "frames",
    }:
        raise SolomonSpriteFrameError("sprite-frame document fields differ")
    for name, expected in (
        ("schema", SCHEMA), ("schema_version", VERSION),
        ("game", profile.game_id), ("revision", revision.revision_id),
        ("source_sha256", revision.sha256),
    ):
        if value.get(name) != expected:
            raise SolomonSpriteFrameError(f"sprite-frame {name} differs from profile")
    raw_frames = value["frames"]
    if not isinstance(raw_frames, list) or not raw_frames:
        raise SolomonSpriteFrameError("sprite-frame document needs frame records")
    frames = []
    seen_enemies: set[int] = set()
    seen_facings: set[str] = set()
    previous_end = -1
    required = {
        "prg_offset", "left_tile", "right_tile", "flags",
        "enemy_types", "player_facings",
    }
    for index, raw in enumerate(raw_frames):
        if not isinstance(raw, Mapping) or set(raw) != required:
            raise SolomonSpriteFrameError(f"sprite frame {index} fields differ")
        offset = raw["prg_offset"]
        if type(offset) is not int or not 0 <= offset <= 0x8000 - 3:
            raise SolomonSpriteFrameError(f"sprite frame {index} PRG offset is invalid")
        if offset < previous_end:
            raise SolomonSpriteFrameError("sprite-frame records overlap or are unordered")
        previous_end = offset + 3
        enemy_types = raw["enemy_types"]
        facings = raw["player_facings"]
        if not isinstance(enemy_types, list) or not isinstance(facings, list):
            raise SolomonSpriteFrameError("sprite-frame bindings must be lists")
        if not enemy_types and not facings:
            raise SolomonSpriteFrameError("sprite frame has no enemy or player binding")
        if any(
            type(item) is not int
            or not ENEMY_TYPE_FIRST <= item < ENEMY_TYPE_FIRST + ENEMY_TYPE_COUNT
            for item in enemy_types
        ) or enemy_types != sorted(set(enemy_types)):
            raise SolomonSpriteFrameError("sprite-frame enemy bindings are invalid")
        if (
            any(type(item) is not str or item not in PLAYER_ACTIONS
                for item in facings)
            or facings != [item for item in PLAYER_ACTIONS if item in facings]
        ):
            raise SolomonSpriteFrameError("sprite-frame player bindings are invalid")
        if seen_enemies.intersection(enemy_types) or seen_facings.intersection(facings):
            raise SolomonSpriteFrameError("sprite-frame bindings are duplicated")
        seen_enemies.update(enemy_types)
        seen_facings.update(facings)
        frames.append(SpriteFrame(
            offset,
            _byte_field(raw["left_tile"], f"frame {index}.left_tile"),
            _byte_field(raw["right_tile"], f"frame {index}.right_tile"),
            _byte_field(raw["flags"], f"frame {index}.flags"),
            tuple(enemy_types), tuple(facings),
        ))
    if (
        seen_enemies != set(range(
            ENEMY_TYPE_FIRST, ENEMY_TYPE_FIRST + ENEMY_TYPE_COUNT
        ))
        or seen_facings != set(PLAYER_ACTIONS)
    ):
        raise SolomonSpriteFrameError("sprite-frame bindings do not cover all types")
    return SolomonSpriteFrameDocument(
        profile.game_id, revision.revision_id, revision.sha256, tuple(frames),
    )


def build_sprite_frames(
    source: bytes, document: SolomonSpriteFrameDocument,
    profile: GameProfile, revision: RevisionSpec,
) -> tuple[bytes, dict[str, object]]:
    image = _source(source, revision)
    checked = parse_sprite_frame_document(document.as_dict(), profile, revision)
    original = decode_sprite_frames(source, profile, revision)
    if len(checked.frames) != len(original.frames):
        raise SolomonSpriteFrameError("sprite-frame layout differs from source")
    for index, (edited, baseline) in enumerate(zip(checked.frames, original.frames)):
        if (edited.prg_offset, edited.enemy_types, edited.player_facings) != (
            baseline.prg_offset, baseline.enemy_types, baseline.player_facings
        ):
            raise SolomonSpriteFrameError(
                f"sprite frame {index} binding differs from source"
            )
    base = len(image.header) + len(image.trainer)
    changes = []
    for index, frame in enumerate(checked.frames):
        file_offset = base + frame.prg_offset
        changes.append(Change(
            AddressRange(Address("file", file_offset), 3),
            source[file_offset:file_offset + 3],
            bytes((frame.left_tile, frame.right_tile, frame.flags)),
            Provenance(revision.sha256, f"sprite-frame-{index}", "encode"),
        ))
    output = BuildPlan(revision.sha256, ChangeSet(tuple(changes))).apply(source)
    return output, {
        "schema": "retrolarium.solomon-sprite-frames-build",
        "schema_version": 1,
        "game": profile.game_id,
        "revision": revision.revision_id,
        "source_sha256": revision.sha256,
        "output_sha256": hashlib.sha256(output).hexdigest(),
        "size": len(output),
        "changed_bytes": sum(a != b for a, b in zip(source, output)),
        "frames": len(checked.frames),
    }
