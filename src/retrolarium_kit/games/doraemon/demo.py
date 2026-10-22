"""Doraemon attract demo: the controller stream World 1's demo plays.

``World1_DemoEntry`` points $A6/$A7 at the stream and clears the random
states, so the demo replays the same way each time. Once per update
``World1_UpdateInputOrDemoStream`` counts $A8 down; when it reaches zero it
loads the next [duration, buttons] pair, and a pair whose two bytes are $FF
ends the demo. A pair holds its buttons for ``duration`` updates, 0 meaning
256. The buttons are masked with $CF, so a demo never presses Start or
Select (Start on the real controller ends it). The stream sits in bank 0's
free space, which runs in $FF bytes to the vectors, so a demo may grow to
the end of it.

Worlds 2 and 3 run their demos without a stream; only World 1's is edited.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any, Mapping

from retrolarium_kit.core.authoring import (
    Address,
    AddressRange,
    AuthoringError,
    BuildPlan,
    Change,
    ChangeSet,
    GameProfile,
    Provenance,
    RevisionSpec,
)
from retrolarium_core.ines import parse_ines


CODEC = "doraemon.world1-demo.v1"
SCHEMA = "retrolarium.doraemon-demo"
VERSION = 1
# Joypad bits other than Start and Select, from the high bit.
BUTTONS = (("a", 0x80), ("b", 0x40), ("up", 0x08), ("down", 0x04), ("left", 0x02), ("right", 0x01))
FIELDS = ("bank", "entry_cpu", "reader_cpu", "stream_cpu", "limit_cpu")
BANK_SIZE = 0x8000


class DoraemonDemoError(AuthoringError):
    """A demo document is invalid or does not fit the stream's space."""


@dataclass(frozen=True)
class DoraemonDemoDocument:
    game_id: str
    revision_id: str
    source_sha256: str
    layout: dict[str, int]
    steps: tuple[dict[str, Any], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "schema_version": VERSION,
            "game": self.game_id,
            "revision": self.revision_id,
            "source_sha256": self.source_sha256,
            "layout": dict(self.layout),
            "steps": [
                {"index": step["index"], "frames": step["frames"], "buttons": list(step["buttons"])}
                for step in self.steps
            ],
        }


def capacity(layout: Mapping[str, int]) -> int:
    """The most steps the stream's space holds beside its end pair."""
    return (layout["limit_cpu"] - layout["stream_cpu"]) // 2 - 1


def _layout(profile: GameProfile, revision: RevisionSpec) -> dict[str, int]:
    resources = [resource for resource in profile.resources_for(revision) if resource.codec == CODEC]
    if len(resources) != 1:
        raise DoraemonDemoError("Doraemon profile needs one World 1 demo resource")
    parameters = resources[0].parameters
    if any(type(parameters.get(name)) is not int for name in FIELDS):
        raise DoraemonDemoError("World 1 demo resource needs its bank and addresses")
    layout = {name: parameters[name] for name in FIELDS}
    if not (0x8000 <= layout["stream_cpu"] < layout["limit_cpu"] <= 0xFFFA) or capacity(layout) < 1:
        raise DoraemonDemoError("World 1 demo space is outside the bank")
    return layout


def _source(source: bytes, revision: RevisionSpec, *, verify: bool = True):
    if verify and hashlib.sha256(source).hexdigest() != revision.sha256:
        raise DoraemonDemoError("demo source identity mismatch")
    image = parse_ines(source)
    if len(image.prg) != 4 * BANK_SIZE:
        raise DoraemonDemoError("Doraemon demo needs 128 KiB PRG")
    return image


def _offset(layout: Mapping[str, int], cpu: int) -> int:
    """The PRG offset of a bank-0 CPU address."""
    return layout["bank"] * BANK_SIZE + cpu - 0x8000


def _check_engine(prg: bytes, layout: Mapping[str, int]) -> None:
    stream = layout["stream_cpu"]
    # LDA #<stream / STA $A6 / LDA #>stream / STA $A7
    entry = bytes((0xA9, stream & 0xFF, 0x85, 0xA6, 0xA9, stream >> 8, 0x85, 0xA7))
    # LDA $21 / AND #$30 / BNE / LDA $A8 / BNE / LDY #0 / LDA ($A6),Y / STA $A8
    reader = bytes((0xA5, 0x21, 0x29, 0x30, 0xD0, 0x2D, 0xA5, 0xA8, 0xD0, 0x1E, 0xA0, 0x00, 0xB1, 0xA6, 0x85, 0xA8))
    at = _offset(layout, layout["entry_cpu"])
    if prg[at : at + len(entry)] != entry:
        raise DoraemonDemoError("the demo entry points at another stream than the profile names")
    at = _offset(layout, layout["reader_cpu"])
    if prg[at : at + len(reader)] != reader:
        raise DoraemonDemoError("the demo reader differs from the one the profile names")


def _space(prg: bytes, layout: Mapping[str, int]) -> bytes:
    start = _offset(layout, layout["stream_cpu"])
    return prg[start : start + layout["limit_cpu"] - layout["stream_cpu"]]


def decode_demo(
    source: bytes,
    profile: GameProfile,
    revision: RevisionSpec,
    *,
    _verify_identity: bool = True,
) -> DoraemonDemoDocument:
    layout = _layout(profile, revision)
    prg = _source(source, revision, verify=_verify_identity).prg
    _check_engine(prg, layout)
    space = _space(prg, layout)
    steps = []
    for index in range(capacity(layout) + 1):
        duration, buttons = space[index * 2], space[index * 2 + 1]
        if duration & buttons == 0xFF:
            break
        if buttons & 0x30:
            raise DoraemonDemoError("the demo stream presses Start or Select, which the game masks")
        steps.append(
            {
                "index": index,
                "frames": duration or 256,
                "buttons": [name for name, bit in BUTTONS if buttons & bit],
            }
        )
    else:
        raise DoraemonDemoError("the demo stream runs past its space")
    if not steps:
        raise DoraemonDemoError("the demo stream is empty")
    if any(byte != 0xFF for byte in space[len(steps) * 2 + 2 :]):
        raise DoraemonDemoError("the space after the demo stream is not free")
    return DoraemonDemoDocument(profile.game_id, revision.revision_id, revision.sha256, layout, tuple(steps))


def _encode(value: Mapping[str, Any], layout: Mapping[str, int]) -> bytes:
    """The whole space: the pairs, the end pair and free $FF bytes."""
    if value.get("layout") != dict(layout):
        raise DoraemonDemoError("demo layout differs from profile")
    steps = value.get("steps")
    most = capacity(layout)
    if not isinstance(steps, list) or not 1 <= len(steps) <= most:
        raise DoraemonDemoError(f"the demo holds 1-{most} steps")
    names = [name for name, _ in BUTTONS]
    stream = bytearray()
    for index, step in enumerate(steps):
        if not isinstance(step, Mapping) or set(step) != {"index", "frames", "buttons"} or step["index"] != index:
            raise DoraemonDemoError("demo step fields or indices differ")
        frames = step["frames"]
        if type(frames) is not int or not 1 <= frames <= 256:
            raise DoraemonDemoError(f"demo step {index + 1} lasts 1-256 frames")
        pressed = step["buttons"]
        if (
            not isinstance(pressed, list)
            or any(name not in names for name in pressed)
            or len(set(pressed)) != len(pressed)
        ):
            raise DoraemonDemoError(f"demo step {index + 1} buttons are not among {', '.join(names)}")
        buttons = 0
        for name, bit in BUTTONS:
            if name in pressed:
                buttons |= bit
        stream += bytes((frames & 0xFF, buttons))
    stream += b"\xff\xff"
    return bytes(stream) + b"\xff" * (layout["limit_cpu"] - layout["stream_cpu"] - len(stream))


def parse_demo_document(value: object, profile: GameProfile, revision: RevisionSpec) -> DoraemonDemoDocument:
    layout = _layout(profile, revision)
    if not isinstance(value, Mapping) or set(value) != {
        "schema",
        "schema_version",
        "game",
        "revision",
        "source_sha256",
        "layout",
        "steps",
    }:
        raise DoraemonDemoError("demo document fields differ")
    for key, expected in (
        ("schema", SCHEMA),
        ("schema_version", VERSION),
        ("game", profile.game_id),
        ("revision", revision.revision_id),
        ("source_sha256", revision.sha256),
    ):
        if value[key] != expected:
            raise DoraemonDemoError(f"demo {key} differs from profile")
    _encode(value, layout)
    # Buttons keep the joypad's order whatever order the document lists.
    return DoraemonDemoDocument(
        profile.game_id,
        revision.revision_id,
        revision.sha256,
        layout,
        tuple(
            {
                "index": s["index"],
                "frames": s["frames"],
                "buttons": [name for name, _ in BUTTONS if name in s["buttons"]],
            }
            for s in value["steps"]
        ),
    )


def build_demo(
    source: bytes,
    document: DoraemonDemoDocument,
    profile: GameProfile,
    revision: RevisionSpec,
) -> tuple[bytes, dict[str, object]]:
    image = _source(source, revision)
    layout = _layout(profile, revision)
    _check_engine(image.prg, layout)
    checked = parse_demo_document(document.as_dict(), profile, revision)
    space = _encode(checked.as_dict(), layout)
    start = len(image.header) + len(image.trainer) + _offset(layout, layout["stream_cpu"])
    output = source
    if source[start : start + len(space)] != space:
        change = Change(
            AddressRange(Address("file", start), len(space)),
            source[start : start + len(space)],
            space,
            Provenance(revision.sha256, "world1-demo", "stream"),
        )
        output = BuildPlan(revision.sha256, ChangeSet((change,))).apply(source)
    rebuilt = decode_demo(output, profile, revision, _verify_identity=False)
    if rebuilt.steps != checked.steps:
        raise DoraemonDemoError("rebuilt demo does not decode to the same stream")
    return output, {
        "schema": "retrolarium.doraemon-demo-build",
        "schema_version": 1,
        "game": profile.game_id,
        "revision": revision.revision_id,
        "source_sha256": revision.sha256,
        "output_sha256": hashlib.sha256(output).hexdigest(),
        "size": len(output),
        "changed_bytes": sum(a != b for a, b in zip(source, output)),
        "steps": len(checked.steps),
    }
