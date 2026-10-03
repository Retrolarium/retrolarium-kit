"""Castle Excellent title demo: the controller script the title screen plays.

``ReadDemoController1`` returns one script byte per 12 frames (the first
for 11) in place of controller 1, masked with $F3 so Select and Start never
reach the game; the byte $FF ends the demo with a jump to Reset. The script
stays where ``InitializeTitleDemoPlayback`` points (``LDA #<script``), so a
demo holds at most as many commands as the game's own: 72, about 14 s.

The document groups runs of equal commands into steps.
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


CODEC = "castle.title-demo.v1"
SCHEMA = "retrolarium.castle-demo"
VERSION = 1
TERMINATOR = 0xFF
COMMAND_FRAMES = 12
BUTTON_BITS = (
    (0x01, "A"), (0x02, "B"), (0x10, "UP"), (0x20, "DOWN"), (0x40, "LEFT"), (0x80, "RIGHT"),
)
FIELDS = ("script_cpu", "init_cpu", "reader_cpu", "capacity")


class CastleDemoError(AuthoringError):
    """A demo document is invalid or does not fit the game's script."""


@dataclass(frozen=True)
class CastleDemoDocument:
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
                {"index": step["index"], "commands": step["commands"], "buttons": list(step["buttons"])}
                for step in self.steps
            ],
        }


def _layout(profile: GameProfile, revision: RevisionSpec) -> dict[str, int]:
    resources = [
        resource for resource in profile.resources_for(revision) if resource.codec == CODEC
    ]
    if len(resources) != 1:
        raise CastleDemoError("Castle profile needs one title demo resource")
    parameters = resources[0].parameters
    if any(type(parameters.get(name)) is not int for name in FIELDS):
        raise CastleDemoError("title demo resource needs its addresses and capacity")
    layout = {name: parameters[name] for name in FIELDS}
    if not 1 <= layout["capacity"] <= 255:
        raise CastleDemoError("title demo capacity is outside 1-255")
    return layout


def _source(source: bytes, revision: RevisionSpec, *, verify: bool = True):
    if verify and hashlib.sha256(source).hexdigest() != revision.sha256:
        raise CastleDemoError("demo source identity mismatch")
    image = parse_ines(source)
    if len(image.prg) != 0x8000:
        raise CastleDemoError("Castle demo needs 32 KiB PRG")
    return image


def _read(prg: bytes, address: int, count: int) -> bytes:
    if not 0x8000 <= address <= 0x10000 - count:
        raise CastleDemoError("demo range escapes PRG")
    return prg[address - 0x8000:address - 0x8000 + count]


def _check_engine(prg: bytes, layout: Mapping[str, int]) -> None:
    script = layout["script_cpu"]
    # LDA #<script / STA $FD / LDA #>script / STA $FE, and the reader's
    # INC $FF / LDA $FF / CMP #12.
    if _read(prg, layout["init_cpu"], 8) != bytes((0xA9, script & 0xFF, 0x85, 0xFD, 0xA9, script >> 8, 0x85, 0xFE)) \
            or _read(prg, layout["reader_cpu"], 6) != bytes((0xE6, 0xFF, 0xA5, 0xFF, 0xC9, COMMAND_FRAMES)):
        raise CastleDemoError("the demo engine reads another script than the profile names")


def _mask(buttons: list[str]) -> int:
    return sum(bit for bit, name in BUTTON_BITS if name in buttons)


def decode_demo(
    source: bytes, profile: GameProfile, revision: RevisionSpec,
    *, _verify_identity: bool = True,
) -> CastleDemoDocument:
    layout = _layout(profile, revision)
    prg = _source(source, revision, verify=_verify_identity).prg
    _check_engine(prg, layout)
    script = _read(prg, layout["script_cpu"], layout["capacity"] + 1)
    if TERMINATOR not in script:
        raise CastleDemoError("the demo script runs past its capacity")
    commands = script[:script.index(TERMINATOR)]
    known = sum(bit for bit, _name in BUTTON_BITS)
    if not commands or any(command & ~known for command in commands):
        raise CastleDemoError("the demo script holds commands the editor cannot name")
    steps: list[dict[str, Any]] = []
    for command in commands:
        names = [name for bit, name in BUTTON_BITS if command & bit]
        if steps and steps[-1]["buttons"] == names:
            steps[-1]["commands"] += 1
        else:
            steps.append({"index": len(steps), "commands": 1, "buttons": names})
    result = CastleDemoDocument(profile.game_id, revision.revision_id, revision.sha256, layout, tuple(steps))
    _encode(result.as_dict(), layout)
    return result


def _encode(value: Mapping[str, Any], layout: Mapping[str, int]) -> bytes:
    """The script's commands and its terminator."""
    if value.get("layout") != dict(layout):
        raise CastleDemoError("demo layout differs from profile")
    steps = value.get("steps")
    capacity = layout["capacity"]
    if not isinstance(steps, list) or not steps:
        raise CastleDemoError("the demo needs at least one step")
    names = [name for _bit, name in BUTTON_BITS]
    script = bytearray()
    for index, step in enumerate(steps):
        if not isinstance(step, Mapping) or set(step) != {"index", "commands", "buttons"} \
                or step["index"] != index:
            raise CastleDemoError("demo step fields or indices differ")
        commands = step["commands"]
        if type(commands) is not int or not 1 <= commands <= capacity:
            raise CastleDemoError(f"demo step {index + 1} holds 1-{capacity} commands")
        buttons = step["buttons"]
        if not isinstance(buttons, list) or buttons != [name for name in names if name in buttons]:
            raise CastleDemoError("demo buttons are unknown or unordered")
        script += bytes((_mask(buttons),)) * commands
    if len(script) > capacity:
        raise CastleDemoError(f"the demo holds {capacity} commands of 12 frames; this one has {len(script)}")
    return bytes(script) + bytes((TERMINATOR,))


def parse_demo_document(value: object, profile: GameProfile, revision: RevisionSpec) -> CastleDemoDocument:
    layout = _layout(profile, revision)
    if not isinstance(value, Mapping) or set(value) != {
        "schema", "schema_version", "game", "revision", "source_sha256", "layout", "steps",
    }:
        raise CastleDemoError("demo document fields differ")
    for key, expected in (
        ("schema", SCHEMA), ("schema_version", VERSION), ("game", profile.game_id),
        ("revision", revision.revision_id), ("source_sha256", revision.sha256),
    ):
        if value[key] != expected:
            raise CastleDemoError(f"demo {key} differs from profile")
    _encode(value, layout)
    return CastleDemoDocument(
        profile.game_id, revision.revision_id, revision.sha256, layout,
        tuple({"index": s["index"], "commands": s["commands"], "buttons": list(s["buttons"])}
              for s in value["steps"]),
    )


def build_demo(
    source: bytes, document: CastleDemoDocument, profile: GameProfile, revision: RevisionSpec,
) -> tuple[bytes, dict[str, object]]:
    image = _source(source, revision)
    layout = _layout(profile, revision)
    _check_engine(image.prg, layout)
    checked = parse_demo_document(document.as_dict(), profile, revision)
    script = _encode(checked.as_dict(), layout)
    start = len(image.header) + len(image.trainer) + layout["script_cpu"] - 0x8000
    output = source
    if source[start:start + len(script)] != script:
        change = Change(
            AddressRange(Address("file", start), len(script)),
            source[start:start + len(script)], script,
            Provenance(revision.sha256, "title-demo", "script"),
        )
        output = BuildPlan(revision.sha256, ChangeSet((change,))).apply(source)
    # Steps of equal buttons side by side decode as one; the commands match.
    rebuilt = decode_demo(output, profile, revision, _verify_identity=False)
    if _encode(rebuilt.as_dict(), layout) != script:
        raise CastleDemoError("rebuilt demo does not decode to document")
    return output, {
        "schema": "retrolarium.castle-demo-build", "schema_version": 1,
        "game": profile.game_id, "revision": revision.revision_id,
        "source_sha256": revision.sha256,
        "output_sha256": hashlib.sha256(output).hexdigest(),
        "size": len(output),
        "changed_bytes": sum(a != b for a, b in zip(source, output)),
        "commands": len(script) - 1,
    }
