"""Alien Soldier's sprite animation scripts.

A script is a run of word pairs, `dc.w Target-*, duration`, read by
`Anim_ResolveTimedMappingFrame`: the first word is a displacement from
itself to a mapping, the second how many reader calls the frame lasts. A
duration of zero makes the entry a jump: the displacement leads to another
script entry instead of a mapping, which is how a script loops.

The source keeps the displacements symbolic, so a frame is edited by naming
another mapping and the assembler works out the displacement. An entry stays
a frame or a jump: a frame may name any mapping some script already shows
and last 1..$7FFF calls (a negative low byte holds the frame for good, which
the reader also accepts), a jump may name any script.
"""

from __future__ import annotations

import re
from typing import Any

from retrolarium_kit.platform.megadrive.asl import SourceTree, parse_number


SCHEMA = "alien-soldier.animations"
DOCUMENT = "graphics/animations.json"
RELATIVE = re.compile(r"^(?P<target>[A-Za-z_][A-Za-z0-9_]*)-\*$")


class AnimationError(ValueError):
    """An animation document that does not fit the source."""


def _read(tree: SourceTree, label: str) -> list[dict[str, Any]] | None:
    block = tree.block(label)
    if block.kind != "dc.w":
        return None
    operands = block.operands
    if not operands or len(operands) % 2:
        return None
    entries = []
    for at in range(0, len(operands), 2):
        target, duration = RELATIVE.match(operands[at].text), parse_number(operands[at + 1].text)
        if target is None or duration is None:
            return None
        entries.append({"target": target["target"], "duration": duration})
    return entries


def scripts(tree: SourceTree) -> dict[str, list[dict[str, Any]]]:
    found = {}
    for label in tree.order:
        entries = _read(tree, label)
        if entries:
            found[label] = entries
    return found


def export(tree: SourceTree) -> dict[str, Any]:
    found = scripts(tree)
    frames = sorted({entry["target"] for entries in found.values() for entry in entries if entry["duration"]})
    return {
        "schema": SCHEMA,
        "schema_version": 1,
        "frames": frames,
        "scripts": [{"label": label, "entries": entries} for label, entries in found.items()],
    }


def apply(tree: SourceTree, document: dict[str, Any]) -> None:
    if document.get("schema") != SCHEMA or document.get("schema_version") != 1:
        raise AnimationError("not an Alien Soldier animation document")
    source = scripts(tree)
    frames = {entry["target"] for entries in source.values() for entry in entries if entry["duration"]}
    if document.get("frames") != sorted(frames):
        raise AnimationError("the frame list is the source's")
    items = document.get("scripts")
    if not isinstance(items, list) or [item.get("label") for item in items] != list(source):
        raise AnimationError("the animation scripts differ from the source")
    for item in items:
        label, entries = item["label"], item.get("entries")
        original = source[label]
        if not isinstance(entries, list) or len(entries) != len(original):
            raise AnimationError(f"{label}: the entry count is the source's")
        for index, (entry, old) in enumerate(zip(entries, original)):
            where = f"{label} entry {index}"
            duration, target = entry.get("duration"), entry.get("target")
            if isinstance(duration, bool) or not isinstance(duration, int) or not 0 <= duration <= 0xFFFF:
                raise AnimationError(f"{where}: the duration is a word")
            if (duration == 0) != (old["duration"] == 0):
                raise AnimationError(f"{where}: a frame stays a frame and a jump a jump")
            if duration and target not in frames or not duration and target not in source:
                raise AnimationError(f"{where}: {target!r} is not a {'mapping' if duration else 'script'}")
            tree.set_text(label, 2 * index, f"{target}-*")
        tree.set_values(
            label, [None if at % 2 == 0 else entries[at // 2]["duration"] for at in range(2 * len(entries))]
        )
