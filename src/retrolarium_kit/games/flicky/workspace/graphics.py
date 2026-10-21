"""Headless codecs and fixed-region build support for Flicky graphics."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Nemesis and Enigma are the core's C++, run as rlcore.
from retrolarium_core.tool import enigma_decode, enigma_encode, nemesis_decode, nemesis_encode


ASSETS = (
    ("sega_tiles", "tiles_4bpp", "data/artnem/data_SegaTiles.bin", 828),
    ("level_tiles", "tiles_4bpp", "data/artnem/data_LevelTiles.bin", 6052),
    ("exit_tiles", "tiles_4bpp", "data/artnem/data_ExitTiles.bin", 128),
    ("sprite_tiles", "tiles_4bpp", "data/artnem/data_SpritesTiles.bin", 4458),
    ("score_tiles", "tiles_4bpp", "data/artnem/data_ScoresTiles.bin", 1456),
    ("logo_tiles", "tiles_4bpp", "data/artnem/data_FlickyLogoTiles.bin", 680),
    ("japanese_font", "tiles_1bpp", "data/artunc/data_Jap1BPPTiles.bin", 1432),
    ("latin_font", "tiles_1bpp", "data/artunc/data_Latin1BPPTiles.bin", 344),
    ("sega_palette", "palette_compact", "data/other/data_SegaPalette.bin", 20),
    ("sega_tilemap", "enigma_tilemap", "data/arteni/data_SegaEnigma.bin", 10),
)

INCLUDES = {
    "sega_tiles": ("system/startup.s", "data/artnem/data_SegaTiles.bin"),
    "level_tiles": ("data/art.s", "data/artnem/data_LevelTiles.bin"),
    "exit_tiles": ("data/art.s", "data/artnem/data_ExitTiles.bin"),
    "sprite_tiles": ("data/art.s", "data/artnem/data_SpritesTiles.bin"),
    "score_tiles": ("data/art.s", "data/artnem/data_ScoresTiles.bin"),
    "logo_tiles": ("data/art.s", "data/artnem/data_FlickyLogoTiles.bin"),
    "japanese_font": ("data/bank0.s", "data/artunc/data_Jap1BPPTiles.bin"),
    "latin_font": ("data/art.s", "data/artunc/data_Latin1BPPTiles.bin"),
    "sega_palette": ("system/startup.s", "data/other/data_SegaPalette.bin"),
    "sega_tilemap": ("system/startup.s", "data/arteni/data_SegaEnigma.bin"),
}


def decode_palette_compact(data: bytes) -> list[dict]:
    """Unpack the compact palette form read by Gfx_LoadPaletteCompact.

    One word per entry: the colour lives in bits $EEE, the CRAM index is
    assembled from bit 4, the top nibble and bit 8, and bit 0 ends the list.
    """
    entries = []
    for offset in range(0, len(data), 2):
        word = int.from_bytes(data[offset:offset + 2], "big")
        index = (word & 0x10) | ((word >> 12) & 0xF) | ((word & 0x100) >> 3)
        entries.append({"index": index, "colour": word & 0xEEE, "last": bool(word & 1)})
        if word & 1:
            break
    return entries


def encode_palette_compact(entries: list[dict]) -> bytes:
    out = bytearray()
    for entry in entries:
        index = entry["index"]
        word = entry["colour"] & 0xEEE
        word |= index & 0x10
        word |= (index & 0xF) << 12
        word |= (index & 0x20) << 3
        word |= 1 if entry["last"] else 0
        out += word.to_bytes(2, "big")
    return bytes(out)


def fail(message: str) -> None:
    raise ValueError(message)


def packed_4bpp_to_tiles(data: bytes) -> list[list[list[int]]]:
    if len(data) % 32:
        raise ValueError("4bpp graphics do not contain whole 32-byte tiles")
    tiles = []
    for start in range(0, len(data), 32):
        pixels = []
        for byte in data[start:start + 32]:
            pixels.extend((byte >> 4, byte & 0x0F))
        tiles.append([pixels[row * 8:(row + 1) * 8] for row in range(8)])
    return tiles


def tiles_to_packed_4bpp(tiles: Any) -> bytes:
    validate_tiles(tiles, 15)
    result = bytearray()
    for tile in tiles:
        for row in tile:
            for column in range(0, 8, 2):
                result.append((row[column] << 4) | row[column + 1])
    return bytes(result)


def font_to_tiles(data: bytes) -> list[list[list[int]]]:
    if len(data) % 8:
        raise ValueError("1bpp font does not contain whole eight-row tiles")
    tiles = []
    for start in range(0, len(data), 8):
        tile = []
        for byte in data[start:start + 8]:
            tile.append([(byte >> shift) & 1 for shift in range(7, -1, -1)])
        tiles.append(tile)
    return tiles


def tiles_to_font(tiles: Any) -> bytes:
    validate_tiles(tiles, 1)
    result = bytearray()
    for tile in tiles:
        for row in tile:
            byte = 0
            for pixel in row:
                byte = (byte << 1) | pixel
            result.append(byte)
    return bytes(result)


def validate_tiles(tiles: Any, maximum: int) -> None:
    if not isinstance(tiles, list) or not tiles:
        raise ValueError("tile asset must contain at least one tile")
    for tile in tiles:
        if not isinstance(tile, list) or len(tile) != 8:
            raise ValueError("each tile must contain eight rows")
        for row in tile:
            if (
                not isinstance(row, list)
                or len(row) != 8
                or any(not isinstance(pixel, int) or not 0 <= pixel <= maximum for pixel in row)
            ):
                raise ValueError(f"tile pixels must be integers from 0 through {maximum}")


def asset_table(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    assets = document.get("assets")
    if not isinstance(assets, list):
        raise ValueError("graphics document has no asset list")
    result = {asset.get("id"): asset for asset in assets}
    expected = {identifier for identifier, _kind, _path, _capacity in ASSETS}
    if set(result) != expected or len(result) != len(assets):
        raise ValueError("graphics asset identity differs from the manifest")
    return result


def export_document(root: Path) -> dict[str, Any]:
    assets = []
    for identifier, kind, relative, capacity in ASSETS:
        data = (root / relative).read_bytes()
        if len(data) != capacity:
            raise ValueError(f"{identifier} is {len(data)} bytes, expected {capacity}")
        entry: dict[str, Any] = {"id": identifier, "kind": kind, "capacity": capacity}
        if kind == "tiles_4bpp":
            entry["tiles"] = packed_4bpp_to_tiles(nemesis_decode(data))
        elif kind == "tiles_1bpp":
            entry["tiles"] = font_to_tiles(data)
        elif kind == "palette_compact":
            entry["entries"] = decode_palette_compact(data)
        elif kind == "enigma_tilemap":
            plain = enigma_decode(data)
            entry["width"] = 12
            entry["height"] = 4
            entry["words"] = [
                int.from_bytes(plain[offset:offset + 2], "big")
                for offset in range(0, len(plain), 2)
            ]
        assets.append(entry)
    return {"schema_version": 1, "profile": "canonical", "assets": assets}


def encode_asset(asset: dict[str, Any], root: Path) -> bytes:
    by_id = {identifier: (kind, relative, capacity) for identifier, kind, relative, capacity in ASSETS}
    identifier = asset["id"]
    expected_kind, relative, capacity = by_id[identifier]
    if asset.get("kind") != expected_kind or asset.get("capacity") != capacity:
        raise ValueError(f"{identifier}: kind or capacity differs")
    reference = (root / relative).read_bytes()
    if expected_kind == "tiles_4bpp":
        plain = tiles_to_packed_4bpp(asset.get("tiles"))
        original_plain = nemesis_decode(reference)
        if len(plain) != len(original_plain):
            raise ValueError(f"{identifier}: tile count cannot change")
        packed = reference if plain == original_plain else nemesis_encode(plain, reference)
    elif expected_kind == "tiles_1bpp":
        packed = tiles_to_font(asset.get("tiles"))
    elif expected_kind == "palette_compact":
        entries = asset.get("entries")
        if not isinstance(entries, list) or len(entries) != 10:
            raise ValueError("sega_palette must contain ten compact entries")
        for index, entry in enumerate(entries):
            if not isinstance(entry.get("index"), int) or not 0 <= entry["index"] <= 63:
                raise ValueError("sega_palette contains an invalid CRAM index")
            if not isinstance(entry.get("colour"), int) or entry["colour"] & ~0xEEE:
                raise ValueError("Mega Drive palette channels use only bits $EEE")
            if bool(entry.get("last")) != (index == len(entries) - 1):
                raise ValueError("only the final compact palette entry may terminate the list")
        packed = encode_palette_compact(entries)
    else:
        words = asset.get("words")
        if (
            asset.get("width") != 12
            or asset.get("height") != 4
            or not isinstance(words, list)
            or len(words) != 48
            or any(not isinstance(word, int) or not 0 <= word <= 0xFFFF for word in words)
        ):
            raise ValueError("sega_tilemap must contain 12x4 valid words")
        plain = b"".join(word.to_bytes(2, "big") for word in words)
        original_plain = enigma_decode(reference)
        packed = reference if plain == original_plain else enigma_encode(plain)
    if len(packed) > capacity:
        raise ValueError(f"{identifier}: encoded size {len(packed)} exceeds {capacity}-byte slot")
    return packed + bytes((0xFF,)) * (capacity - len(packed))


def validate_document(document: dict[str, Any], root: Path) -> dict[str, bytes]:
    if document.get("schema_version") != 1 or document.get("profile") != "canonical":
        raise ValueError("unsupported graphics document schema or profile")
    assets = asset_table(document)
    return {identifier: encode_asset(assets[identifier], root) for identifier, *_rest in ASSETS}


def atomic_write_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8", newline="\n")
    temporary.replace(path)


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(data)
    temporary.replace(path)


def load_document(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_assets(document: dict[str, Any], root: Path, build_dir: Path) -> dict[str, Path]:
    encoded = validate_document(document, root)
    outputs = {}
    for identifier, data in encoded.items():
        path = build_dir / "graphics" / f"{identifier}.bin"
        atomic_write(path, data)
        outputs[identifier] = path.relative_to(root)
    return outputs


def redirect_includes(staged_source: Path, outputs: dict[str, Path]) -> None:
    for identifier, (owner, original) in INCLUDES.items():
        path = staged_source / owner
        text = path.read_text(encoding="utf-8")
        old = f'binclude "{original}"'
        new = f'binclude "{outputs[identifier].as_posix()}"'
        if text.count(old) != 1:
            raise ValueError(f"expected one graphics include in {path}: {old}")
        path.write_text(text.replace(old, new), encoding="utf-8", newline="\n")
