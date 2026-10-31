"""Alien Soldier's attract demos.

The title starts a demo when it has idled $700 frames: `Demo_PlaybackSystem`
seeds the random numbers, takes the stage from `Demo_StageIndexTable` (by
`DemoRotationIndex`, which moves on after each demo) and plays the input
stream `Demo_InputStreamPointers` names for $1000 frames, or until Start.

A stream is a word, how many frames the pad stays idle first, then pairs of
words: the pad (held buttons in the high byte, newly pressed ones in the
low) and how many frames it stays so. The document keeps each demo's stage
and its steps; a stream keeps its file's size, so a new recording must fit
it, and its steps must last the demo's $1000 frames (the last one is
stretched to do so). The bytes after the steps are zero.
"""

from __future__ import annotations

from typing import Any

from retrolarium_kit.games.alien_soldier.assets import pointer_table
from retrolarium_kit.platform.megadrive.asl import SourceTree


SCHEMA = "alien-soldier.demos"
DOCUMENT = "levels/demos.json"
STAGES = "Demo_StageIndexTable"
STREAMS = "Demo_InputStreamPointers"
DEMO_FRAMES = 0x1000


class DemoError(ValueError):
    """A demo document that does not fit the source."""


def read_steps(data: bytes) -> list[dict[str, int]]:
    """A stream as steps, up to the step that ends the demo."""
    steps = [{"held": 0, "pressed": 0, "frames": int.from_bytes(data[:2], "big")}]
    total, at = steps[0]["frames"], 2
    while total < DEMO_FRAMES and at + 4 <= len(data):
        frames = int.from_bytes(data[at + 2 : at + 4], "big")
        steps.append({"held": data[at], "pressed": data[at + 1], "frames": frames})
        total += frames
        at += 4
    return steps


def encode(steps: list[dict[str, int]], capacity: int) -> bytes:
    out = bytearray(steps[0]["frames"].to_bytes(2, "big"))
    for step in steps[1:]:
        out += bytes((step["held"], step["pressed"])) + step["frames"].to_bytes(2, "big")
    if len(out) > capacity:
        raise DemoError(f"the steps take {len(out)} bytes, the stream holds {capacity}")
    return bytes(out) + bytes(capacity - len(out))


def export(tree: SourceTree) -> dict[str, Any]:
    stages = tree.block(STAGES).values()
    demos = []
    for index, label in enumerate(pointer_table(tree, STREAMS)):
        data = tree.data(label)
        demos.append({"label": label, "stage": stages[index], "capacity": len(data), "steps": read_steps(data)})
    return {"schema": SCHEMA, "schema_version": 1, "frames": DEMO_FRAMES, "demos": demos}


def encoded(document: dict[str, Any], labels: list[str], originals: dict[str, bytes]):
    """Validate and encode identically for source writes and fixed ROM slots."""
    if document.get("schema") != SCHEMA or document.get("schema_version") != 1:
        raise DemoError("not an Alien Soldier demo document")
    items = document.get("demos")
    if not isinstance(items, list) or [item.get("label") for item in items] != labels:
        raise DemoError("the demos differ from the source")
    stages = []
    updates = {}
    for item in items:
        label, steps, stage = item["label"], item.get("steps"), item.get("stage")
        if isinstance(stage, bool) or not isinstance(stage, int) or not 0 <= stage <= 0x30 or stage % 2:
            raise DemoError(f"{label}: the stage is an even StageTableIndex")
        stages.append(stage)
        if not isinstance(steps, list) or not steps:
            raise DemoError(f"{label}: a demo needs steps")
        for step in steps:
            if any(
                isinstance(step.get(key), bool) or not isinstance(step.get(key), int)
                for key in ("held", "pressed", "frames")
            ):
                raise DemoError(f"{label}: a step is held, pressed and frames")
            if not (0 <= step["held"] <= 0xFF and 0 <= step["pressed"] <= 0xFF and 1 <= step["frames"] <= 0xFFFF):
                raise DemoError(f"{label}: a step's buttons are bytes and it lasts 1..65535 frames")
        steps = [dict(step) for step in steps]
        total = sum(step["frames"] for step in steps)
        if total < DEMO_FRAMES:
            steps[-1]["frames"] += DEMO_FRAMES - total
        original = originals[label]
        data = encode(steps, len(original))
        # An unchanged demo keeps its stream as it was, bytes after its end included.
        if read_steps(original) != steps:
            updates[label] = data
    return stages, updates


def apply(tree: SourceTree, document: dict[str, Any]) -> None:
    labels = pointer_table(tree, STREAMS)
    stages, updates = encoded(document, labels, {label: tree.data(label) for label in labels})
    for label, data in updates.items():
        tree.set_data(label, data)
    tree.set_values(STAGES, stages)


def layout(tree: SourceTree, image: bytes, addresses: dict[str, int]) -> dict:
    """Assembler-proven slots, verified against the source's exact bytes."""
    labels = pointer_table(tree, STREAMS)
    slots = {}
    for label in [STAGES, STREAMS, *labels]:
        data = b"".join(addresses[name].to_bytes(4, "big") for name in labels) if label == STREAMS else tree.data(label)
        at = addresses[label]
        if at < 0 or image[at : at + len(data)] != data:
            raise DemoError(f"{label}: assembled slot differs from the source")
        slots[label] = {"offset": at, "size": len(data), "file": tree.block(label).file.path}
    _slot_ranges(image, slots)
    return {"labels": labels, "slots": slots}


def _slot_ranges(image: bytes, slots: dict) -> None:
    ranges = []
    for label, slot in slots.items():
        start, end = slot["offset"], slot["offset"] + slot["size"]
        if start < 0 or slot["size"] <= 0 or end > len(image) or any(start < b and a < end for a, b in ranges):
            raise DemoError(f"{label}: invalid or overlapping ROM slot")
        ranges.append((start, end))


def patch(image: bytes, document: dict[str, Any], layout: dict) -> tuple[bytes, list[str]]:
    """Replace only fixed-size demo slots in a verified baseline image."""
    slots, labels = layout["slots"], layout["labels"]
    _slot_ranges(image, slots)

    def original(label):
        slot = slots[label]
        return image[slot["offset"] : slot["offset"] + slot["size"]]

    stages, updates = encoded(document, labels, {label: original(label) for label in labels})
    updates[STAGES] = b"".join(value.to_bytes(2, "big") for value in stages)
    result = bytearray(image)
    changed = set()
    ranges = []
    for label, data in updates.items():
        slot = slots[label]
        start, end = slot["offset"], slot["offset"] + slot["size"]
        if len(data) != slot["size"] or start < 0 or end > len(image) or any(start < b and a < end for a, b in ranges):
            raise DemoError(f"{label}: invalid or overlapping ROM slot")
        ranges.append((start, end))
        if data != image[start:end]:
            result[start:end] = data
            changed.add(slot["file"])
    return bytes(result), sorted(changed)


def recorded_steps(steps: list[dict[str, int]], recording: str) -> list[dict[str, int]]:
    """A demo's steps after a recording (the demo script's file): the
    original steps for the recording's prefix frames, then the recorded ones."""
    lines = [line.split() for line in recording.splitlines() if line.strip()]
    if not lines or lines[0][0] != "prefix" or len(lines[0]) != 2:
        raise DemoError("the recording does not start with its prefix")
    prefix = int(lines[0][1])
    kept, total = [], 0
    for step in steps:
        if total >= prefix:
            break
        frames = min(step["frames"], prefix - total)
        kept.append({**step, "frames": frames})
        total += frames
    for line in lines[1:]:
        if line[0] == "end":
            break
        if line[0] != "step" or len(line) != 4:
            raise DemoError(f"the recording has an unknown line: {' '.join(line)}")
        frames, held, pressed = (int(value) for value in line[1:])
        kept.append({"held": held, "pressed": pressed, "frames": frames})
    if len(kept) <= 1:
        raise DemoError("the recording holds no steps")
    return kept
