"""Alien Soldier's uncompressed tile art (the files under data/artunc/).

A payload is either a chain of blocks, each a word counting its tile bytes
followed by those tiles, or bare tiles without a count. The count is the DMA
length: `Sprite_RenderDynamicMapping` takes a piece's art pointer and uploads
that many bytes, which is why most payloads carry it. Every payload reads as
one shape or the other; the document keeps the shape and lets only the
pixels change.
"""

from __future__ import annotations

from typing import Any

from retrolarium_kit.platform.megadrive.asl import SourceTree
from retrolarium_kit.platform.megadrive.vdp import TILE_BYTES, tile_bytes, tiles_hex


SCHEMA = "alien-soldier.tiles"
DOCUMENT = "graphics/tiles.json"
ART = "data/artunc/"


class TileArtError(ValueError):
    """A tile document that does not fit the source's art."""


def read_blocks(data: bytes) -> list[tuple[bool, bytes]]:
    """A payload as (counted, tile bytes) blocks."""
    blocks, at = [], 0
    while at < len(data):
        count = int.from_bytes(data[at : at + 2], "big") if at + 2 <= len(data) else 0
        if not count or count % TILE_BYTES or at + 2 + count > len(data):
            break
        blocks.append((True, data[at + 2 : at + 2 + count]))
        at += 2 + count
    else:
        if blocks:
            return blocks
    if len(data) % TILE_BYTES:
        raise TileArtError(f"{len(data)} bytes are neither counted blocks nor whole tiles")
    return [(False, data)]


def payload_labels(tree: SourceTree) -> list[str]:
    return [
        label
        for label in tree.order
        if tree.block(label).kind == "binclude" and tree.block(label).directives[0].path.startswith(ART)
    ]


def export(tree: SourceTree) -> dict[str, Any]:
    payloads = []
    for label in payload_labels(tree):
        blocks = read_blocks(tree.data(label))
        payloads.append(
            {
                "label": label,
                "path": tree.block(label).directives[0].path,
                "blocks": [{"counted": counted, "tiles": tiles_hex(tiles)} for counted, tiles in blocks],
            }
        )
    return {"schema": SCHEMA, "schema_version": 1, "payloads": payloads}


def apply(tree: SourceTree, document: dict[str, Any]) -> None:
    if document.get("schema") != SCHEMA or document.get("schema_version") != 1:
        raise TileArtError("not an Alien Soldier tile document")
    labels = payload_labels(tree)
    payloads = document.get("payloads")
    if not isinstance(payloads, list) or [item.get("label") for item in payloads] != labels:
        raise TileArtError("the tile payloads differ from the source")
    for payload in payloads:
        label = payload["label"]
        original = read_blocks(tree.data(label))
        blocks = payload.get("blocks")
        if not isinstance(blocks, list) or len(blocks) != len(original):
            raise TileArtError(f"{label}: the blocks differ from the source")
        data = bytearray()
        for block, (counted, tiles) in zip(blocks, original):
            art = block.get("tiles")
            if block.get("counted") != counted or not isinstance(art, list) or len(art) * TILE_BYTES != len(tiles):
                raise TileArtError(f"{label}: only pixels may change")
            if counted:
                data += len(tiles).to_bytes(2, "big")
            try:
                data += b"".join(tile_bytes(tile) for tile in art)
            except ValueError as error:
                raise TileArtError(f"{label}: {error}") from None
        tree.set_data(label, bytes(data))
