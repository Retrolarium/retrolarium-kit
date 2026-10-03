"""Super Mario Bros. title demo: the controller steps the title screen plays.

``sub_demo_engine`` walks two parallel tables once a frame: a step's buttons
(``tbl_demo_joypad_actions``) are held for its number of frames
(``tbl_demo_action_durations``, 1-255) and a duration of 0 ends the demo.
Both tables stay where the engine reads them (``LDA table-1,X``), so a demo
holds at most as many steps as the game's own: 21.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any, Mapping

from .authoring import (
    Address, AddressRange, AuthoringError, BuildPlan, Change, ChangeSet,
    GameProfile, Provenance, RevisionSpec,
)
from .ines import parse_ines


CODEC = "smb.demo-input.v1"
SCHEMA = "retrolarium.smb-demo"
VERSION = 1
BUTTON_BITS = (
    (0x80, "A"), (0x40, "B"), (0x20, "SELECT"), (0x10, "START"),
    (0x08, "UP"), (0x04, "DOWN"), (0x02, "LEFT"), (0x01, "RIGHT"),
)
FIELDS = ("actions_cpu", "durations_cpu", "engine_cpu", "capacity")
# Offsets into sub_demo_engine of the operands of LDA durations-1,X and
# LDA actions-1,X.
DURATIONS_OPERAND = 14
ACTIONS_OPERAND = 22


class SmbDemoError(AuthoringError):
    """A demo document is invalid or does not fit the game's tables."""


@dataclass(frozen=True)
class SmbDemoDocument:
    game_id: str
    revision_id: str
    source_sha256: str
    layout: dict[str, int]
    steps: tuple[dict[str, Any], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA, "schema_version": VERSION,
            "game": self.game_id, "revision": self.revision_id,
            "source_sha256": self.source_sha256,
            "layout": dict(self.layout),
            "steps": [
                {"index": step["index"], "frames": step["frames"], "buttons": list(step["buttons"])}
                for step in self.steps
            ],
        }


def _layout(profile: GameProfile, revision: RevisionSpec) -> dict[str, int]:
    resources = [
        resource for resource in profile.resources_for(revision) if resource.codec == CODEC
    ]
    if len(resources) != 1:
        raise SmbDemoError("SMB profile needs one demo resource")
    parameters = resources[0].parameters
    if any(type(parameters.get(name)) is not int for name in FIELDS):
        raise SmbDemoError("demo resource needs its addresses and capacity")
    layout = {name: parameters[name] for name in FIELDS}
    if not 1 <= layout["capacity"] <= 255 \
            or layout["durations_cpu"] - layout["actions_cpu"] < layout["capacity"]:
        raise SmbDemoError("demo resource ranges differ")
    return layout


def _source(source: bytes, revision: RevisionSpec, *, verify: bool = True):
    if verify and hashlib.sha256(source).hexdigest() != revision.sha256:
        raise SmbDemoError("demo source identity mismatch")
    image = parse_ines(source)
    if len(image.prg) != 0x8000:
        raise SmbDemoError("SMB demo needs 32 KiB PRG")
    return image


def _read(prg: bytes, address: int, count: int) -> bytes:
    if not 0x8000 <= address <= 0x10000 - count:
        raise SmbDemoError("demo range escapes PRG")
    return prg[address - 0x8000:address - 0x8000 + count]


def _check_engine(prg: bytes, layout: Mapping[str, int]) -> None:
    def word(offset: int) -> int:
        raw = _read(prg, layout["engine_cpu"] + offset, 2)
        return raw[0] | (raw[1] << 8)
    if _read(prg, layout["engine_cpu"] + DURATIONS_OPERAND - 1, 1)[0] != 0xBD \
            or word(DURATIONS_OPERAND) != layout["durations_cpu"] - 1 \
            or _read(prg, layout["engine_cpu"] + ACTIONS_OPERAND - 1, 1)[0] != 0xBD \
            or word(ACTIONS_OPERAND) != layout["actions_cpu"] - 1:
        raise SmbDemoError("the demo engine reads other tables than the profile names")


def decode_demo(
    source: bytes, profile: GameProfile, revision: RevisionSpec,
    *, _verify_identity: bool = True,
) -> SmbDemoDocument:
    layout = _layout(profile, revision)
    prg = _source(source, revision, verify=_verify_identity).prg
    _check_engine(prg, layout)
    capacity = layout["capacity"]
    actions = _read(prg, layout["actions_cpu"], capacity)
    durations = _read(prg, layout["durations_cpu"], capacity + 1)
    steps = []
    for index in range(capacity + 1):
        if durations[index] == 0:
            break
        if index == capacity:
            raise SmbDemoError("the demo durations run past the tables")
        steps.append({"index": index, "frames": durations[index], "buttons": [
            name for mask, name in BUTTON_BITS if actions[index] & mask
        ]})
    result = SmbDemoDocument(profile.game_id, revision.revision_id, revision.sha256, layout, tuple(steps))
    _encode(result.as_dict(), layout, prg)
    return result


def _encode(value: Mapping[str, Any], layout: Mapping[str, int], prg: bytes) -> tuple[bytes, bytes]:
    """The action and duration tables; bytes past the demo keep their values."""
    if value.get("layout") != dict(layout):
        raise SmbDemoError("demo layout differs from profile")
    steps = value.get("steps")
    capacity = layout["capacity"]
    if not isinstance(steps, list) or not 1 <= len(steps) <= capacity:
        raise SmbDemoError(f"the demo needs 1-{capacity} steps")
    names = [name for _mask, name in BUTTON_BITS]
    actions = bytearray(_read(prg, layout["actions_cpu"], capacity))
    durations = bytearray(_read(prg, layout["durations_cpu"], capacity + 1))
    for index, step in enumerate(steps):
        if not isinstance(step, Mapping) or set(step) != {"index", "frames", "buttons"} \
                or step["index"] != index:
            raise SmbDemoError("demo step fields or indices differ")
        frames = step["frames"]
        if type(frames) is not int or not 1 <= frames <= 255:
            raise SmbDemoError(f"demo step {index + 1} lasts 1-255 frames")
        buttons = step["buttons"]
        if not isinstance(buttons, list) or buttons != [name for name in names if name in buttons]:
            raise SmbDemoError("demo buttons are unknown or unordered")
        actions[index] = sum(mask for mask, name in BUTTON_BITS if name in buttons)
        durations[index] = frames
    durations[len(steps)] = 0
    return bytes(actions), bytes(durations)


def parse_demo_document(value: object, profile: GameProfile, revision: RevisionSpec) -> SmbDemoDocument:
    layout = _layout(profile, revision)
    if not isinstance(value, Mapping) or set(value) != {
        "schema", "schema_version", "game", "revision", "source_sha256", "layout", "steps",
    }:
        raise SmbDemoError("demo document fields differ")
    for key, expected in (
        ("schema", SCHEMA), ("schema_version", VERSION), ("game", profile.game_id),
        ("revision", revision.revision_id), ("source_sha256", revision.sha256),
    ):
        if value[key] != expected:
            raise SmbDemoError(f"demo {key} differs from profile")
    # The tables' other bytes come from the ROM at build time; any stand-in
    # checks the steps.
    _encode(value, layout, bytes(0x8000))
    return SmbDemoDocument(
        profile.game_id, revision.revision_id, revision.sha256, layout,
        tuple({"index": s["index"], "frames": s["frames"], "buttons": list(s["buttons"])} for s in value["steps"]),
    )


def build_demo(
    source: bytes, document: SmbDemoDocument, profile: GameProfile, revision: RevisionSpec,
) -> tuple[bytes, dict[str, object]]:
    image = _source(source, revision)
    layout = _layout(profile, revision)
    _check_engine(image.prg, layout)
    checked = parse_demo_document(document.as_dict(), profile, revision)
    actions, durations = _encode(checked.as_dict(), layout, image.prg)
    base = len(image.header) + len(image.trainer)
    changes = []
    for address, payload, field in (
        (layout["actions_cpu"], actions, "actions"), (layout["durations_cpu"], durations, "durations"),
    ):
        start = base + address - 0x8000
        if source[start:start + len(payload)] != payload:
            changes.append(Change(
                AddressRange(Address("file", start), len(payload)),
                source[start:start + len(payload)], payload,
                Provenance(revision.sha256, "demo", field),
            ))
    output = BuildPlan(revision.sha256, ChangeSet(tuple(changes))).apply(source) if changes else source
    rebuilt = decode_demo(output, profile, revision, _verify_identity=False)
    if rebuilt.as_dict() != checked.as_dict():
        raise SmbDemoError("rebuilt demo does not decode to document")
    return output, {
        "schema": "retrolarium.smb-demo-build", "schema_version": 1,
        "game": profile.game_id, "revision": revision.revision_id,
        "source_sha256": revision.sha256,
        "output_sha256": hashlib.sha256(output).hexdigest(),
        "size": len(output),
        "changed_bytes": sum(a != b for a, b in zip(source, output)),
        "steps": len(checked.steps),
    }
