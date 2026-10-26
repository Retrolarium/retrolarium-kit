"""Alien Soldier's compressed tile art: every payload an asset list or a
stage's visual loader sends to VRAM through LZSS (command seven, directly or
through the shared stage tile table) or through the tile codec (commands two
and three).

Each payload keeps its slot: an edited one is compressed again and must fit
the original payload's size, padded after its stream so nothing behind it
moves. One command-seven payload, `Credits_SegaMappingData`, decodes to
mapping words rather than whole tiles and is not part of the document.
"""

from __future__ import annotations

from typing import Any

from retrolarium_kit.games.alien_soldier import lzss, tile_codec
from retrolarium_kit.games.alien_soldier.assets import (
    LZSS_TO_VRAM,
    SHARED_TILE_SOURCES,
    TILE_CODEC_COMMANDS,
    asset_lists,
    pointer_table,
)
from retrolarium_kit.games.alien_soldier.stages import visual_tile_commands
from retrolarium_kit.platform.megadrive.asl import SourceTree
from retrolarium_kit.platform.megadrive.vdp import TILE_BYTES, tile_bytes, tiles_hex


SCHEMA = "alien-soldier.compressed-tiles"
DOCUMENT = "graphics/compressed_tiles.json"


class CompressedTileError(ValueError):
    """A compressed tile document that does not fit the source."""


def vram_sources(tree: SourceTree) -> set[str]:
    """Every LZSS payload the VRAM loader reads: its matches stay within
    $400 blocks (see lzss.py)."""
    lists = asset_lists(tree).values()
    found = {command.source for commands in lists for command in commands if command.command == LZSS_TO_VRAM}
    found |= set(pointer_table(tree, SHARED_TILE_SOURCES))
    found |= {command.source for command in visual_tile_commands(tree) if command.command == LZSS_TO_VRAM}
    return found


def payloads(tree: SourceTree) -> dict[str, str]:
    """Label -> codec ("lzss" or "tile-codec") of every tile payload, in
    source order."""
    lists = asset_lists(tree).values()
    lzss_sources = vram_sources(tree)
    codec_sources = {
        command.source for commands in lists for command in commands if command.command in TILE_CODEC_COMMANDS
    }
    found = {}
    for label in tree.order:
        if label in codec_sources:
            found[label] = "tile-codec"
        elif label in lzss_sources and len(lzss.decompress(tree.data(label), blocks=True)) % TILE_BYTES == 0:
            found[label] = "lzss"
    return found


def decode(codec: str, payload: bytes) -> bytes:
    """A payload's tile bytes."""
    return tile_codec.decompress(payload) if codec == "tile-codec" else lzss.decompress(payload, blocks=True)


def export(tree: SourceTree) -> dict[str, Any]:
    items = []
    for label, codec in payloads(tree).items():
        payload = tree.data(label)
        items.append(
            {"label": label, "codec": codec, "capacity": len(payload), "tiles": tiles_hex(decode(codec, payload))}
        )
    return {"schema": SCHEMA, "schema_version": 1, "payloads": items}


def apply(tree: SourceTree, document: dict[str, Any]) -> None:
    if document.get("schema") != SCHEMA or document.get("schema_version") != 1:
        raise CompressedTileError("not an Alien Soldier compressed tile document")
    source = payloads(tree)
    items = document.get("payloads")
    if not isinstance(items, list) or [(item.get("label"), item.get("codec")) for item in items] != list(
        source.items()
    ):
        raise CompressedTileError("the compressed payloads differ from the source")
    for item in items:
        label, codec, original = item["label"], item["codec"], tree.data(item["label"])
        tiles = item.get("tiles")
        if not isinstance(tiles, list) or len(tiles) * TILE_BYTES != len(decode(codec, original)):
            raise CompressedTileError(f"{label}: the tile count differs from the source")
        try:
            data = b"".join(tile_bytes(tile) for tile in tiles)
            fitted = tile_codec.fit(data, original) if codec == "tile-codec" else lzss.fit(data, original)
        except ValueError as error:
            raise CompressedTileError(f"{label}: {error}") from None
        tree.set_data(label, fitted)
