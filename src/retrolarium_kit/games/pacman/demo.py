"""Pac-Man attract demo: the direction script the demo's Pac-Man follows.

``loc_demo_direction_script`` runs once per movement update while
``ram_flag_demo`` is set. It counts ``ram_demo_direction_timer`` down and,
when it reaches zero, steps ``ram_demo_direction_index`` to the next
[duration, direction] pair; the pair's direction is the joypad the demo
holds. The timer starts at zero, so the first pair's direction lasts 255
updates and its duration byte is never read; a later pair lasts its
duration (0 meaning 256). The table holds 28 pairs and code follows it, so
a demo keeps 28 steps (the demo's single life usually ends well before).

Directions use the engine's order: 0 up, 1 left, 2 down, 3 right.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any, Mapping

from retrolarium_kit.core.authoring import (
    Address, AddressRange, AuthoringError, BuildPlan, Change, ChangeSet,
    GameProfile, Provenance, RevisionSpec,
)
from retrolarium_core.ines import parse_ines


CODEC = "pacman.attract-demo.v1"
SCHEMA = "retrolarium.pacman-demo"
VERSION = 1
DIRECTIONS = ("up", "left", "down", "right")
FIRST_STEP_UPDATES = 255
FIELDS = ("script_cpu", "table_cpu", "pairs")
PRG_BASE = 0xC000


class PacmanDemoError(AuthoringError):
    """A demo document is invalid or does not fit the game's table."""


@dataclass(frozen=True)
class PacmanDemoDocument:
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
                {"index": step["index"], "direction": step["direction"], "frames": step["frames"]}
                for step in self.steps
            ],
        }


def _layout(profile: GameProfile, revision: RevisionSpec) -> dict[str, int]:
    resources = [resource for resource in profile.resources_for(revision) if resource.codec == CODEC]
    if len(resources) != 1:
        raise PacmanDemoError("Pac-Man profile needs one attract demo resource")
    parameters = resources[0].parameters
    if any(type(parameters.get(name)) is not int for name in FIELDS):
        raise PacmanDemoError("attract demo resource needs its addresses and pair count")
    layout = {name: parameters[name] for name in FIELDS}
    if not 1 <= layout["pairs"] <= 127:
        raise PacmanDemoError("attract demo pair count is outside 1-127")
    return layout


def _source(source: bytes, revision: RevisionSpec, *, verify: bool = True):
    if verify and hashlib.sha256(source).hexdigest() != revision.sha256:
        raise PacmanDemoError("demo source identity mismatch")
    image = parse_ines(source)
    if len(image.prg) != 0x4000:
        raise PacmanDemoError("Pac-Man demo needs 16 KiB PRG")
    return image


def _read(prg: bytes, address: int, count: int) -> bytes:
    if not PRG_BASE <= address <= 0x10000 - count:
        raise PacmanDemoError("demo range escapes PRG")
    return prg[address - PRG_BASE:address - PRG_BASE + count]


def _check_engine(prg: bytes, layout: Mapping[str, int]) -> None:
    table = layout["table_cpu"]
    after = table + 1
    # LDY index / DEC timer / BNE / INC index x2 / LDY index /
    # LDA table,Y / STA timer / LDA table+1,Y / AND #3
    expected = bytes((
        0xA4, 0xE4, 0xC6, 0xE3, 0xD0, 0x0B, 0xE6, 0xE4, 0xE6, 0xE4, 0xA4, 0xE4,
        0xB9, table & 0xFF, table >> 8, 0x85, 0xE3, 0xB9, after & 0xFF, after >> 8, 0x29, 0x03,
    ))
    if _read(prg, layout["script_cpu"], len(expected)) != expected:
        raise PacmanDemoError("the demo engine reads another table than the profile names")


def decode_demo(
    source: bytes, profile: GameProfile, revision: RevisionSpec,
    *, _verify_identity: bool = True,
) -> PacmanDemoDocument:
    layout = _layout(profile, revision)
    prg = _source(source, revision, verify=_verify_identity).prg
    _check_engine(prg, layout)
    table = _read(prg, layout["table_cpu"], layout["pairs"] * 2)
    steps = []
    for index in range(layout["pairs"]):
        duration, direction = table[index * 2], table[index * 2 + 1]
        if direction > 3:
            raise PacmanDemoError("the demo table holds a direction the editor cannot name")
        frames = FIRST_STEP_UPDATES if index == 0 else (duration or 256)
        steps.append({"index": index, "direction": DIRECTIONS[direction], "frames": frames})
    # The first pair's duration byte is never read; keep it as the source has it.
    result = PacmanDemoDocument(profile.game_id, revision.revision_id, revision.sha256, layout, tuple(steps))
    _encode(result.as_dict(), layout, first_duration=table[0])
    return result


def _encode(value: Mapping[str, Any], layout: Mapping[str, int], *, first_duration: int) -> bytes:
    """The table's pairs; a shorter demo repeats its last step's direction
    in 256-update pairs to the end of the table."""
    if value.get("layout") != dict(layout):
        raise PacmanDemoError("demo layout differs from profile")
    steps = value.get("steps")
    pairs = layout["pairs"]
    if not isinstance(steps, list) or not 1 <= len(steps) <= pairs:
        raise PacmanDemoError(f"the demo holds 1-{pairs} steps")
    table = bytearray()
    for index, step in enumerate(steps):
        if not isinstance(step, Mapping) or set(step) != {"index", "direction", "frames"} \
                or step["index"] != index:
            raise PacmanDemoError("demo step fields or indices differ")
        if step["direction"] not in DIRECTIONS:
            raise PacmanDemoError(f"demo step {index + 1} direction is not one of {', '.join(DIRECTIONS)}")
        frames = step["frames"]
        if index == 0:
            if frames != FIRST_STEP_UPDATES:
                raise PacmanDemoError(f"the first demo step always lasts {FIRST_STEP_UPDATES} frames")
            duration = first_duration
        else:
            if type(frames) is not int or not 1 <= frames <= 256:
                raise PacmanDemoError(f"demo step {index + 1} lasts 1-256 frames")
            duration = frames & 0xFF
        table += bytes((duration, DIRECTIONS.index(step["direction"])))
    last = table[-1]
    while len(table) < pairs * 2:
        table += bytes((0, last))
    return bytes(table)


def parse_demo_document(value: object, profile: GameProfile, revision: RevisionSpec) -> PacmanDemoDocument:
    layout = _layout(profile, revision)
    if not isinstance(value, Mapping) or set(value) != {
        "schema", "schema_version", "game", "revision", "source_sha256", "layout", "steps",
    }:
        raise PacmanDemoError("demo document fields differ")
    for key, expected in (
        ("schema", SCHEMA), ("schema_version", VERSION), ("game", profile.game_id),
        ("revision", revision.revision_id), ("source_sha256", revision.sha256),
    ):
        if value[key] != expected:
            raise PacmanDemoError(f"demo {key} differs from profile")
    _encode(value, layout, first_duration=0)
    return PacmanDemoDocument(
        profile.game_id, revision.revision_id, revision.sha256, layout,
        tuple({"index": s["index"], "direction": s["direction"], "frames": s["frames"]}
              for s in value["steps"]),
    )


def build_demo(
    source: bytes, document: PacmanDemoDocument, profile: GameProfile, revision: RevisionSpec,
) -> tuple[bytes, dict[str, object]]:
    image = _source(source, revision)
    layout = _layout(profile, revision)
    _check_engine(image.prg, layout)
    checked = parse_demo_document(document.as_dict(), profile, revision)
    start = len(image.header) + len(image.trainer) + layout["table_cpu"] - PRG_BASE
    table = _encode(checked.as_dict(), layout, first_duration=source[start])
    output = source
    if source[start:start + len(table)] != table:
        change = Change(
            AddressRange(Address("file", start), len(table)),
            source[start:start + len(table)], table,
            Provenance(revision.sha256, "attract-demo", "pairs"),
        )
        output = BuildPlan(revision.sha256, ChangeSet((change,))).apply(source)
    rebuilt = decode_demo(output, profile, revision, _verify_identity=False)
    if _encode(rebuilt.as_dict(), layout, first_duration=source[start]) != table:
        raise PacmanDemoError("rebuilt demo does not decode to the same table")
    return output, {
        "schema": "retrolarium.pacman-demo-build", "schema_version": 1,
        "game": profile.game_id, "revision": revision.revision_id,
        "source_sha256": revision.sha256,
        "output_sha256": hashlib.sha256(output).hexdigest(),
        "size": len(output),
        "changed_bytes": sum(a != b for a, b in zip(source, output)),
        "steps": len(checked.steps),
    }
