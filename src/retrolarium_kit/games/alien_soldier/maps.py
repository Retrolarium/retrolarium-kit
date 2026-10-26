"""Alien Soldier's stage maps: the payloads a stage decompresses into the
RAM its planes read.

A payload belongs to the map whose region of a stage plane's descriptor it
lands in (see stages.py): patterns are 4x4 name-table words, 16-bit values;
chunks are 8x8 pattern indices and the coarse map 32x32 chunk indices, both
bytes. Every value may change within its width; the payload is compressed
again into its slot, so nothing after it moves. Only the RAM loaders read a
map (but one also sent to VRAM), so its stream is compressed whole, matches
crossing $400 boundaries.
"""

from __future__ import annotations

from typing import Any

from retrolarium_kit.games.alien_soldier import lzss
from retrolarium_kit.games.alien_soldier.compressed_tiles import payloads as tile_payloads, vram_sources
from retrolarium_kit.games.alien_soldier.stages import RAM_BASE, regions, scenes
from retrolarium_kit.platform.megadrive.asl import SourceTree


SCHEMA = "alien-soldier.maps"
DOCUMENT = "levels/maps.json"
WIDTHS = {"patterns": 2, "chunks": 1, "coarse": 1}


class MapError(ValueError):
    """A map document that does not fit the source."""


def payloads(tree: SourceTree) -> dict[str, dict[str, Any]]:
    """Label -> role and every stage that loads it, in source order."""
    found: dict[str, dict[str, Any]] = {}
    all_scenes = scenes(tree)
    # Plane A first. Plane B's descriptor may cut the same RAM differently
    # (its patterns start lower), so it only adds payloads that lie outside
    # every region of the stage's plane A.
    for which in (0, 1):
        for scene in all_scenes:
            plane = scene["planes"][which]
            if plane is None:
                continue
            spans = regions(plane)
            primary = regions(scene["planes"][0]) if which and scene["planes"][0] else {}
            for write in scene["map_writes"]:
                role = next((role for role, (low, high) in spans.items() if low <= write["ram"] < high), None)
                if role is None:
                    continue
                if which and (
                    write["source"] in found
                    and found[write["source"]]["role"] != role
                    or any(low < write["ram"] + write["size"] and write["ram"] < high for low, high in primary.values())
                ):
                    continue
                item = found.setdefault(write["source"], {"role": role, "stages": []})
                if item["role"] != role:
                    raise MapError(f"{write['source']} is both {item['role']} and {role}")
                if scene["index"] not in item["stages"]:
                    item["stages"].append(scene["index"])
    for item in found.values():
        item["stages"].sort()
    # Two payloads also go to VRAM as tiles; the tile document owns them.
    tiles = set(tile_payloads(tree))
    return {label: found[label] for label in tree.order if label in found and label not in tiles}


def _values(role: str, data: bytes) -> list[int]:
    width = WIDTHS[role]
    return [int.from_bytes(data[at : at + width], "big") for at in range(0, len(data) - len(data) % width, width)]


def export(tree: SourceTree) -> dict[str, Any]:
    items = []
    for label, item in payloads(tree).items():
        payload = tree.data(label)
        items.append(
            {
                "label": label,
                "role": item["role"],
                "stages": item["stages"],
                "capacity": len(payload),
                "values": _values(item["role"], lzss.decompress(payload)),
            }
        )
    return {"schema": SCHEMA, "schema_version": 1, "ram_base": RAM_BASE, "payloads": items}


def apply(tree: SourceTree, document: dict[str, Any]) -> None:
    if document.get("schema") != SCHEMA or document.get("schema_version") != 1:
        raise MapError("not an Alien Soldier map document")
    source = payloads(tree)
    to_vram = vram_sources(tree)
    items = document.get("payloads")
    if not isinstance(items, list) or [(item.get("label"), item.get("role")) for item in items] != [
        (label, item["role"]) for label, item in source.items()
    ]:
        raise MapError("the map payloads differ from the source")
    for item in items:
        label, role, original = item["label"], item["role"], tree.data(item["label"])
        plain = lzss.decompress(original)
        values = item.get("values")
        width = WIDTHS[role]
        if not isinstance(values, list) or len(values) != len(plain) // width:
            raise MapError(f"{label}: the number of values is the source's")
        if any(
            isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < 1 << (8 * width)
            for value in values
        ):
            raise MapError(f"{label}: a value does not fit its map")
        data = b"".join(value.to_bytes(width, "big") for value in values) + plain[len(values) * width :]
        try:
            tree.set_data(label, lzss.fit(data, original, blocks=label in to_vram))
        except lzss.LzssError as error:
            raise MapError(f"{label}: {error}") from None
