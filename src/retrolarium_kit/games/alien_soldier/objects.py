"""Alien Soldier's objects: the sprite mappings grouped by the source file
that holds them, each block with what it draws with — the object attribute
(base tile, palette line, priority), the art loaded where its tiles are and
the palette commands that colour its line — and the animation scripts that
show them.

A static piece takes its tiles from VRAM that the stage, a boss or the
object itself filled; the source does not say which for each mapping. The
game repository records what was seen in play (config/object_art.json, by
mapping block: the attribute, each art payload with the VRAM address it was
loaded at, the palette blocks). A block it does not list takes the context
of the nearest listed block of its file, marked as borrowed; a file with no
listed block has no context and its static pieces draw as outlines.
Dynamic pieces carry their own art and need none.

Nothing here edits anything: the document is read from the source when a
project opens.
"""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import Any

from retrolarium_kit.games.alien_soldier import animations, compressed_tiles, mappings, palettes, tiles
from retrolarium_kit.platform.megadrive.asl import SourceTree, parse_number


SCHEMA = "alien-soldier.objects"
DOCUMENT = "graphics/objects.json"
ART = Path("config/object_art.json")
ART_SCHEMA = "alien-soldier.object-art"
# Commands the studio loads art with: LZSS to VRAM, or an uncompressed block.
LZSS_TO_VRAM, UNCOMPRESSED = 7, 1


class ObjectError(ValueError):
    """An object art record that does not fit the source."""


def _number(value: Any) -> int | None:
    return parse_number(value) if isinstance(value, str) else value if isinstance(value, int) else None


def recorded(tree: SourceTree) -> dict[str, dict[str, Any]]:
    """config/object_art.json's records by mapping block, checked against the
    source; empty when the repository has none."""
    path = tree.root / ART
    if not path.is_file():
        return {}
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("schema") != ART_SCHEMA or document.get("schema_version") != 1:
        raise ObjectError(f"{ART.as_posix()} is not an Alien Soldier object art record")
    blocks = {block["label"] for block in mappings.export(tree)["blocks"]}
    compressed = set(compressed_tiles.payloads(tree))
    plain = set(tiles.payload_labels(tree))
    colours = palettes.blocks(tree)
    found = {}
    for label, record in document.get("mappings", {}).items():
        attribute = _number(record.get("attribute"))
        if label not in blocks or attribute is None or not 0 <= attribute <= 0xFFFF:
            raise ObjectError(f"{ART.as_posix()}: {label} is not a mapping block with an attribute")
        loads = []
        for source, address in record.get("art", []):
            vram = _number(address)
            if source not in compressed | plain or vram is None or not 0 <= vram < 0x10000 or vram % 32:
                raise ObjectError(f"{ART.as_posix()}: {label} loads {source} at {address}")
            loads.append(
                {"source": source, "command": LZSS_TO_VRAM if source in compressed else UNCOMPRESSED, "vram": vram}
            )
        commands = []
        for name, offset in record.get("palettes", []):
            if name not in colours or not any(command["offset"] == offset for command in colours[name]):
                raise ObjectError(f"{ART.as_posix()}: {label} names no palette command {name}+{offset}")
            commands.append([name, offset])
        found[label] = {"attribute": attribute, "loads": loads, "palettes": commands}
    return found


def export(tree: SourceTree) -> dict[str, Any]:
    blocks = mappings.export(tree)["blocks"]
    records = recorded(tree)
    groups: dict[str, list[str]] = {}
    for block in blocks:
        stem = PurePosixPath(tree.block(block["label"]).file.path).stem
        groups.setdefault(stem, []).append(block["label"])
    contexts = {}
    for labels in groups.values():
        listed = [index for index, label in enumerate(labels) if label in records]
        for index, label in enumerate(labels):
            if label in records:
                contexts[label] = {**records[label], "borrowed": False}
            elif listed:
                nearest = min(listed, key=lambda other: (abs(other - index), other))
                contexts[label] = {**records[labels[nearest]], "borrowed": True}
    shown: dict[str, list[str]] = {}
    for script in animations.export(tree)["scripts"]:
        for item in script["entries"]:
            if item["duration"] and script["label"] not in shown.setdefault(item["target"], []):
                shown[item["target"]].append(script["label"])
    return {
        "schema": SCHEMA,
        "schema_version": 1,
        "groups": [{"id": stem, "blocks": labels} for stem, labels in groups.items()],
        "contexts": contexts,
        "animations": shown,
    }
