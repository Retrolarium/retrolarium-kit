"""Alien Soldier's sprite mappings: the pieces an object is drawn from.

A mapping is a run of pieces ending at the piece whose attribute word has
bit 15 set (the end mark, not priority). `Sprite_RenderMapping` reads
six-byte static pieces: the attribute word, an OAM size byte, a link byte
the renderer overwrites, a signed Y and a signed X offset.
`Sprite_RenderDynamicMapping` reads eight-byte pieces, written in the source
as `dc.w attributes`, `dc.l Art+$SS000000`, `dc.w YYXX`: the long is both the
size byte (its top byte) and the art the piece uploads, so the art travels
with the piece.

The attribute word is relative to the object's: its tile index is added to
the object's base tile and its flip bits are exclusive-ored with the
object's. The size byte is `(width - 1) << 2 | (height - 1)` in tiles; its
high nibble is ignored by the hardware and kept as the source has it.

Blocks are found by name (labels with SpriteMapping, SpriteFrame or Mapping,
but not metasprite descriptor tables or what an asset list loads, the maps)
and kept when they read as whole mappings; a document from a project saved
when maps were read as mappings applies with those blocks left alone. The document lets positions, attributes, sizes and links change;
end marks, piece counts and a dynamic piece's art, tile and size stay.
"""

from __future__ import annotations

import re
from typing import Any

from retrolarium_kit.platform.megadrive.asl import AslSourceError, SourceTree, parse_number


SCHEMA = "alien-soldier.mappings"
DOCUMENT = "graphics/mappings.json"
LAST_PIECE = 0x8000
NAME_MARKERS = ("SpriteMapping", "SpriteFrame", "Mapping")
ART_OPERAND = re.compile(r"^(?P<art>[A-Za-z_][A-Za-z0-9_]*)(?:\+\$(?P<addend>[0-9A-Fa-f]+))?$")


class MappingError(ValueError):
    """A mapping document that does not fit the source."""


def _signed(value: int) -> int:
    return value - 0x100 if value >= 0x80 else value


def _group(pieces: list[dict[str, Any]], width: int) -> list[dict[str, Any]]:
    mappings, current = [], []
    for index, piece in enumerate(pieces):
        current.append(piece)
        if piece["attributes"] & LAST_PIECE:
            mappings.append({"offset": (index + 1 - len(current)) * width, "pieces": current})
            current = []
    if current:
        raise MappingError("the last piece does not end its mapping")
    return mappings


def read_static(data: bytes) -> list[dict[str, Any]]:
    if len(data) % 6 or not data:
        raise MappingError(f"{len(data)} bytes are not whole six-byte pieces")
    pieces = [
        {
            "attributes": int.from_bytes(data[at : at + 2], "big"),
            "size": data[at + 2],
            "link": data[at + 3],
            "y": _signed(data[at + 4]),
            "x": _signed(data[at + 5]),
        }
        for at in range(0, len(data), 6)
    ]
    return _group(pieces, 6)


def read_dynamic(tree: SourceTree, label: str) -> list[dict[str, Any]]:
    items = [
        (directive.kind, operand.text) for directive in tree.block(label).directives for operand in directive.operands
    ]
    if len(items) % 3 or not items:
        raise MappingError("not whole word/long/word pieces")
    pieces = []
    for at in range(0, len(items), 3):
        if tuple(kind for kind, _ in items[at : at + 3]) != ("dc.w", "dc.l", "dc.w"):
            raise MappingError("not whole word/long/word pieces")
        attributes, position = parse_number(items[at][1]), parse_number(items[at + 2][1])
        operand = ART_OPERAND.match(items[at + 1][1])
        if attributes is None or position is None or operand is None:
            raise MappingError("a dynamic piece needs numbers and an art label")
        addend = int(operand["addend"] or "0", 16)
        if addend & 0xFFFFFF:
            raise MappingError("a dynamic piece's art has an offset")
        pieces.append(
            {
                "attributes": attributes,
                "size": addend >> 24,
                "y": _signed((position >> 8) & 0xFF),
                "x": _signed(position & 0xFF),
                "art": operand["art"],
            }
        )
    return _group(pieces, 8)


def _read(tree: SourceTree, label: str) -> tuple[str, list[dict[str, Any]]] | None:
    block = tree.block(label)
    kinds = {directive.kind for directive in block.directives}
    try:
        if kinds == {"dc.w", "dc.l"}:
            return "dynamic", read_dynamic(tree, label)
        if kinds in ({"dc.w"}, {"dc.b"}, {"binclude"}):
            return "static", read_static(tree.data(label))
    except (MappingError, AslSourceError):
        pass
    return None


def blocks(tree: SourceTree) -> dict[str, tuple[str, list[dict[str, Any]]]]:
    from retrolarium_kit.games.alien_soldier.assets import asset_lists

    # What an asset list loads is art or a map ("...MappingData4000"), even
    # where its bytes happen to read as pieces.
    loaded = {command.source for commands in asset_lists(tree).values() for command in commands}
    found = {}
    for label in tree.order:
        if not any(marker in label for marker in NAME_MARKERS) or ("Metasprite" in label and "Mapping" not in label):
            continue
        if label in loaded:
            continue
        read = _read(tree, label)
        if read is not None:
            found[label] = read
    return found


def export(tree: SourceTree) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "schema_version": 1,
        "blocks": [
            {"label": label, "kind": kind, "mappings": mappings} for label, (kind, mappings) in blocks(tree).items()
        ],
    }


def _number(value: Any, low: int, high: int, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise MappingError(f"{where} must be {low}..{high}")
    return value


def apply(tree: SourceTree, document: dict[str, Any]) -> None:
    if document.get("schema") != SCHEMA or document.get("schema_version") != 1:
        raise MappingError("not an Alien Soldier mapping document")
    from retrolarium_kit.games.alien_soldier.assets import asset_lists

    source = blocks(tree)
    items = document.get("blocks")
    if isinstance(items, list):
        # Projects saved by kits that read maps as mappings keep those blocks:
        # they are left as the source has them.
        loaded = {command.source for commands in asset_lists(tree).values() for command in commands}
        items = [item for item in items if not (isinstance(item, dict) and item.get("label") in loaded)]
    if not isinstance(items, list) or [(item.get("label"), item.get("kind")) for item in items] != [
        (label, kind) for label, (kind, _) in source.items()
    ]:
        raise MappingError("the mapping blocks differ from the source")
    for item in items:
        label, kind = item["label"], item["kind"]
        original = source[label][1]
        mappings = item.get("mappings")
        if not isinstance(mappings, list) or [len(mapping.get("pieces", [])) for mapping in mappings] != [
            len(mapping["pieces"]) for mapping in original
        ]:
            raise MappingError(f"{label}: the pieces differ from the source")
        pieces = [piece for mapping in mappings for piece in mapping["pieces"]]
        old_pieces = [piece for mapping in original for piece in mapping["pieces"]]
        for index, (piece, old) in enumerate(zip(pieces, old_pieces)):
            where = f"{label} piece {index}"
            attributes = _number(piece.get("attributes"), 0, 0xFFFF, f"{where} attributes")
            if attributes & LAST_PIECE != old["attributes"] & LAST_PIECE:
                raise MappingError(f"{where}: the end mark is the source's")
            _number(piece.get("y"), -128, 127, f"{where} y")
            _number(piece.get("x"), -128, 127, f"{where} x")
            if kind == "dynamic":
                if (
                    piece.get("art") != old["art"]
                    or piece.get("size") != old["size"]
                    or attributes & 0x7FF != old["attributes"] & 0x7FF
                ):
                    raise MappingError(f"{where}: a dynamic piece keeps its art, tile and size")
            else:
                _number(piece.get("size"), 0, 0xFF, f"{where} size")
                _number(piece.get("link"), 0, 0xFF, f"{where} link")
        if kind == "static":
            data = b"".join(
                piece["attributes"].to_bytes(2, "big")
                + bytes((piece["size"], piece["link"], piece["y"] & 0xFF, piece["x"] & 0xFF))
                for piece in pieces
            )
            tree.set_data(label, data)
        else:
            values: list[int | None] = []
            for piece in pieces:
                values += [piece["attributes"], None, ((piece["y"] & 0xFF) << 8) | (piece["x"] & 0xFF)]
            tree.set_values(label, values)
