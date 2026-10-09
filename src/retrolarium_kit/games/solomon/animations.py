"""Fixed-layout, source-bound authoring for Solomon's Key object animations.

The ROM keeps the 33 type pointers, 340 action descriptors, four variant
selectors and 275 three-byte frames in one contiguous PRG region.  Addresses
in this document are CPU addresses; the region is relocated by 0x80 in PAL.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Mapping

from retrolarium_kit.core.authoring import (
    Address, AddressRange, AuthoringError, BuildPlan, Change, ChangeSet,
    GameProfile, Provenance, RevisionSpec,
)
from retrolarium_core.ines import parse_ines


CODEC = "solomon.animations.v1"
SCHEMA = "retrolarium.solomon-animations"
POINTER_COUNT = 33
FRAME_COUNT = 275
CONFIGURATION_COUNT = 27
GROUP_COUNTS = (
    36, 16, 8, 8, 16, 4, 28, 12, 8, 4, 16, 16, 28, 28, 28, 28, 28, 28,
)
# Offsets from the first descriptor. The four selectors occupy gaps between
# descriptor groups; they do not belong to any action.
GROUP_OFFSETS = (
    0, 144, 208, 240, 272, 336, 352, 480, 528, 560,
    576, 640, 704, 816, 928, 1040, 1152, 1264,
)
SELECTOR_OFFSETS = (464, 472, 1376, 1384)
DEFINITION_SIZE = 1392
REGION_SIZE = POINTER_COUNT * 2 + DEFINITION_SIZE + FRAME_COUNT * 3


class SolomonAnimationError(AuthoringError):
    """An animation document or pointer violates the fixed ROM layout."""


@dataclass(frozen=True)
class SolomonAnimationDocument:
    game_id: str
    revision_id: str
    source_sha256: str
    enemy_configuration: tuple[int, ...]
    group_pointers: tuple[int, ...]
    descriptors: tuple[dict[str, object], ...]
    selectors: tuple[dict[str, object], ...]
    frames: tuple[dict[str, int], ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "schema": SCHEMA,
            "schema_version": 1,
            "game": self.game_id,
            "revision": self.revision_id,
            "source_sha256": self.source_sha256,
            "enemy_configuration": list(self.enemy_configuration),
            "group_pointers": list(self.group_pointers),
            "descriptors": [dict(item) for item in self.descriptors],
            "selectors": [
                {"cpu_address": item["cpu_address"],
                 "pointers": list(item["pointers"])}
                for item in self.selectors
            ],
            "frames": [dict(item) for item in self.frames],
        }


def _layout(profile: GameProfile, revision: RevisionSpec) -> tuple[int, int, int]:
    resources = [
        item for item in profile.resources_for(revision) if item.codec == CODEC
    ]
    if len(resources) != 1:
        raise SolomonAnimationError("profile needs one animation resource")
    pointer_offset = resources[0].parameters.get("pointer_table_prg")
    if type(pointer_offset) is not int or not 0 <= pointer_offset <= 0x8000 - REGION_SIZE:
        raise SolomonAnimationError("animation pointer table escapes PRG")
    pointer_cpu = 0x8000 + pointer_offset
    definition_cpu = pointer_cpu + POINTER_COUNT * 2
    frame_cpu = definition_cpu + DEFINITION_SIZE
    return pointer_offset, definition_cpu, frame_cpu


def _configuration_offset(profile: GameProfile, revision: RevisionSpec) -> int:
    resource = next(
        item for item in profile.resources_for(revision) if item.codec == CODEC
    )
    offset = resource.parameters.get("enemy_configuration_prg")
    if type(offset) is not int or not 0 <= offset <= 0x8000 - CONFIGURATION_COUNT:
        raise SolomonAnimationError("enemy configuration escapes PRG")
    return offset


def _image(source: bytes, revision: RevisionSpec):
    if hashlib.sha256(source).hexdigest() != revision.sha256:
        raise SolomonAnimationError("animation source image identity mismatch")
    image = parse_ines(source)
    if image.mapper != revision.mapper or len(image.prg) != 0x8000:
        raise SolomonAnimationError("Solomon animations need a 32 KiB mapper-3 PRG")
    return image


def _word(data: bytes, offset: int) -> int:
    return data[offset] | data[offset + 1] << 8


def _put_word(data: bytearray, offset: int, value: int) -> None:
    data[offset:offset + 2] = bytes((value & 255, value >> 8))


def _byte(value: object, label: str) -> int:
    if type(value) is not int or not 0 <= value <= 255:
        raise SolomonAnimationError(f"{label} must be a byte")
    return value


def _word_value(value: object, label: str) -> int:
    if type(value) is not int or not 0x8000 <= value <= 0xFFFF:
        raise SolomonAnimationError(f"{label} must be a PRG CPU address")
    return value


def decode_animations(
    source: bytes, profile: GameProfile, revision: RevisionSpec,
) -> SolomonAnimationDocument:
    image = _image(source, revision)
    pointer_offset, definition_cpu, frame_cpu = _layout(profile, revision)
    prg = image.prg
    enemy_configuration = tuple(
        prg[_configuration_offset(profile, revision):
            _configuration_offset(profile, revision) + CONFIGURATION_COUNT]
    )
    group_pointers = tuple(
        _word(prg, pointer_offset + index * 2)
        for index in range(POINTER_COUNT)
    )
    descriptors = []
    for group_offset, count in zip(GROUP_OFFSETS, GROUP_COUNTS):
        for action in range(count):
            address = definition_cpu + group_offset + action * 4
            offset = address - 0x8000
            descriptors.append({
                "cpu_address": address,
                "initial_phase": prg[offset],
                "delay": prg[offset + 1] >> 1,
                "uses_variants": bool(prg[offset + 1] & 1),
                "data_pointer": _word(prg, offset + 2),
            })
    selectors = []
    for selector_offset in SELECTOR_OFFSETS:
        address = definition_cpu + selector_offset
        offset = address - 0x8000
        selectors.append({
            "cpu_address": address,
            "pointers": tuple(_word(prg, offset + index * 2)
                              for index in range(4)),
        })
    frames = []
    for index in range(FRAME_COUNT):
        address = frame_cpu + index * 3
        offset = address - 0x8000
        frames.append({
            "cpu_address": address,
            "left_tile": prg[offset],
            "right_tile": prg[offset + 1],
            "flags": prg[offset + 2],
        })
    return SolomonAnimationDocument(
        profile.game_id, revision.revision_id, revision.sha256,
        enemy_configuration,
        group_pointers, tuple(descriptors), tuple(selectors), tuple(frames),
    )


def parse_animation_document(
    value: object, profile: GameProfile, revision: RevisionSpec,
) -> SolomonAnimationDocument:
    _, definition_cpu, frame_cpu = _layout(profile, revision)
    required = {
        "schema", "schema_version", "game", "revision", "source_sha256",
        "enemy_configuration",
        "group_pointers", "descriptors", "selectors", "frames",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise SolomonAnimationError("animation document fields differ")
    for key, expected in (
        ("schema", SCHEMA), ("schema_version", 1),
        ("game", profile.game_id), ("revision", revision.revision_id),
        ("source_sha256", revision.sha256),
    ):
        if value[key] != expected:
            raise SolomonAnimationError(f"animation {key} differs from profile")
    raw_configuration = value["enemy_configuration"]
    if not isinstance(raw_configuration, list) or len(raw_configuration) != CONFIGURATION_COUNT:
        raise SolomonAnimationError("animation needs 27 enemy configuration bytes")
    configuration = tuple(
        _byte(item, f"enemy configuration {index}")
        for index, item in enumerate(raw_configuration)
    )
    group_addresses = {
        definition_cpu + offset for offset in GROUP_OFFSETS
    }
    selector_addresses = {
        definition_cpu + offset for offset in SELECTOR_OFFSETS
    }
    frame_addresses = {
        frame_cpu + index * 3 for index in range(FRAME_COUNT)
    }
    raw_pointers = value["group_pointers"]
    if not isinstance(raw_pointers, list) or len(raw_pointers) != POINTER_COUNT:
        raise SolomonAnimationError("animation needs 33 object-type pointers")
    group_pointers = tuple(
        _word_value(item, f"group pointer {index}")
        for index, item in enumerate(raw_pointers)
    )
    if any(item not in group_addresses for item in group_pointers):
        raise SolomonAnimationError("object-type pointer leaves descriptor groups")
    group_capacity = {
        definition_cpu + offset: count
        for offset, count in zip(GROUP_OFFSETS, GROUP_COUNTS)
    }
    if group_capacity[group_pointers[0]] <= 0x17:
        raise SolomonAnimationError("Wizard animation group lacks both facings")
    for enemy_type in range(0x18, 0x84):
        flags = configuration[(enemy_type - 0x18) >> 2]
        action = (enemy_type & 3) | (0 if flags & 8 else 0x18)
        if action >= group_capacity[group_pointers[enemy_type >> 2]]:
            raise SolomonAnimationError(
                f"enemy type {enemy_type:02X} action leaves its descriptor group"
            )
    raw_descriptors = value["descriptors"]
    if not isinstance(raw_descriptors, list) or len(raw_descriptors) != sum(GROUP_COUNTS):
        raise SolomonAnimationError("animation needs 340 action descriptors")
    descriptors = []
    index = 0
    for group_offset, count in zip(GROUP_OFFSETS, GROUP_COUNTS):
        for action in range(count):
            item = raw_descriptors[index]
            address = definition_cpu + group_offset + action * 4
            if not isinstance(item, Mapping) or set(item) != {
                "cpu_address", "initial_phase", "delay",
                "uses_variants", "data_pointer",
            } or type(item["cpu_address"]) is not int or item["cpu_address"] != address:
                raise SolomonAnimationError(f"descriptor {index} layout differs")
            phase = _byte(item["initial_phase"], f"descriptor {index} phase")
            delay = _byte(item["delay"], f"descriptor {index} delay")
            if delay > 127 or type(item["uses_variants"]) is not bool:
                raise SolomonAnimationError(f"descriptor {index} timing is invalid")
            pointer = _word_value(item["data_pointer"], f"descriptor {index} pointer")
            valid = selector_addresses if item["uses_variants"] else frame_addresses
            if pointer not in valid:
                raise SolomonAnimationError(f"descriptor {index} pointer is invalid")
            descriptors.append({
                "cpu_address": address, "initial_phase": phase,
                "delay": delay, "uses_variants": item["uses_variants"],
                "data_pointer": pointer,
            })
            index += 1
    raw_selectors = value["selectors"]
    if not isinstance(raw_selectors, list) or len(raw_selectors) != len(SELECTOR_OFFSETS):
        raise SolomonAnimationError("animation needs four variant selectors")
    selectors = []
    for index, (item, selector_offset) in enumerate(zip(raw_selectors, SELECTOR_OFFSETS)):
        address = definition_cpu + selector_offset
        if not isinstance(item, Mapping) or set(item) != {
            "cpu_address", "pointers",
        } or type(item["cpu_address"]) is not int or item["cpu_address"] != address:
            raise SolomonAnimationError(f"selector {index} layout differs")
        raw_values = item["pointers"]
        if not isinstance(raw_values, list) or len(raw_values) != 4:
            raise SolomonAnimationError(f"selector {index} needs four pointers")
        pointers = tuple(
            _word_value(pointer, f"selector {index} pointer {variant}")
            for variant, pointer in enumerate(raw_values)
        )
        if any(pointer not in frame_addresses for pointer in pointers):
            raise SolomonAnimationError(f"selector {index} pointer leaves frames")
        selectors.append({"cpu_address": address, "pointers": pointers})
    raw_frames = value["frames"]
    if not isinstance(raw_frames, list) or len(raw_frames) != FRAME_COUNT:
        raise SolomonAnimationError("animation needs 275 frame records")
    frames = []
    for index, item in enumerate(raw_frames):
        address = frame_cpu + index * 3
        if not isinstance(item, Mapping) or set(item) != {
            "cpu_address", "left_tile", "right_tile", "flags",
        } or type(item["cpu_address"]) is not int or item["cpu_address"] != address:
            raise SolomonAnimationError(f"frame {index} layout differs")
        frames.append({
            "cpu_address": address,
            "left_tile": _byte(item["left_tile"], f"frame {index} left tile"),
            "right_tile": _byte(item["right_tile"], f"frame {index} right tile"),
            "flags": _byte(item["flags"], f"frame {index} flags"),
        })
    return SolomonAnimationDocument(
        profile.game_id, revision.revision_id, revision.sha256,
        configuration,
        group_pointers, tuple(descriptors), tuple(selectors), tuple(frames),
    )


def build_animations(
    source: bytes, document: SolomonAnimationDocument,
    profile: GameProfile, revision: RevisionSpec,
) -> tuple[bytes, dict[str, object]]:
    image = _image(source, revision)
    checked = parse_animation_document(document.as_dict(), profile, revision)
    baseline_configuration = tuple(image.prg[
        _configuration_offset(profile, revision):
        _configuration_offset(profile, revision) + CONFIGURATION_COUNT
    ])
    if checked.enemy_configuration != baseline_configuration:
        raise SolomonAnimationError("enemy configuration is source-owned")
    pointer_offset, definition_cpu, frame_cpu = _layout(profile, revision)
    payload = bytearray(image.prg[pointer_offset:pointer_offset + REGION_SIZE])
    for index, pointer in enumerate(checked.group_pointers):
        _put_word(payload, index * 2, pointer)
    for item in checked.descriptors:
        offset = item["cpu_address"] - definition_cpu + POINTER_COUNT * 2
        payload[offset] = item["initial_phase"]
        payload[offset + 1] = item["delay"] * 2 + item["uses_variants"]
        _put_word(payload, offset + 2, item["data_pointer"])
    for item in checked.selectors:
        offset = item["cpu_address"] - definition_cpu + POINTER_COUNT * 2
        for index, pointer in enumerate(item["pointers"]):
            _put_word(payload, offset + index * 2, pointer)
    for item in checked.frames:
        offset = item["cpu_address"] - frame_cpu + POINTER_COUNT * 2 + DEFINITION_SIZE
        payload[offset:offset + 3] = bytes((
            item["left_tile"], item["right_tile"], item["flags"],
        ))
    base = len(image.header) + len(image.trainer) + pointer_offset
    output = BuildPlan(revision.sha256, ChangeSet((Change(
        AddressRange(Address("file", base), REGION_SIZE),
        source[base:base + REGION_SIZE], bytes(payload),
        Provenance(revision.sha256, "object-animations", "encode"),
    ),))).apply(source)
    return output, {
        "schema": "retrolarium.solomon-animations-build",
        "schema_version": 1,
        "game": profile.game_id,
        "revision": revision.revision_id,
        "source_sha256": revision.sha256,
        "output_sha256": hashlib.sha256(output).hexdigest(),
        "size": len(output),
        "changed_bytes": sum(left != right for left, right in zip(source, output)),
        "descriptors": len(checked.descriptors),
        "frames": len(checked.frames),
    }


def animation_frame_for_action(
    document: SolomonAnimationDocument, object_type: int, action: int,
    tick: int,
) -> dict[str, int]:
    """Resolve the sprite record visible at a zero-based engine frame.

    This mirrors AdvanceObjectAnimation: the zero counter triggers at tick 0,
    reloads a delay, and subsequently underflows once every delay + 1 ticks.
    """
    if type(object_type) is not int or not 0 <= object_type < POINTER_COUNT * 4:
        raise SolomonAnimationError("animation object type is invalid")
    if type(action) is not int or not 0 <= action <= 0xFF:
        raise SolomonAnimationError("animation action is invalid")
    if type(tick) is not int or not 0 <= tick <= 100000:
        raise SolomonAnimationError("animation preview tick is invalid")
    descriptor_address = document.group_pointers[object_type >> 2] + action * 4
    descriptors = {
        item["cpu_address"]: item for item in document.descriptors
    }
    descriptor = descriptors.get(descriptor_address)
    if descriptor is None:
        raise SolomonAnimationError("animation action leaves descriptor data")
    pointer = descriptor["data_pointer"]
    if descriptor["uses_variants"]:
        selectors = {
            item["cpu_address"]: item for item in document.selectors
        }
        pointer = selectors[pointer]["pointers"][object_type & 3]
    phase = descriptor["initial_phase"]
    advances = tick // (descriptor["delay"] + 1) + 1
    for _ in range(advances):
        total = phase + 0xF0
        phase = total & 0xFF
        if total <= 0xFF:
            phase = (phase & 0x0F) * 0x11
    frame_address = pointer + (phase >> 3) + (phase >> 4)
    frames = {item["cpu_address"]: item for item in document.frames}
    frame = frames.get(frame_address)
    if frame is None:
        raise SolomonAnimationError(
            f"animation frame at ${frame_address:04X} leaves frame data"
        )
    return frame
