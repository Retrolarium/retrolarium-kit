"""Doraemon metasprite catalogs.

World 1 (bank 0) and World 3 (bank 2) share one variable-length format:
an index of two-byte entries, each a pointer to a record (high byte $04 and
up) or an alias (low byte: another index, high byte: flip mode 0-3), and
contiguous records of a three-byte header (piece count, X and Y mirror
extents) followed by Y-offset, X-offset, tile triples. Bit 7 of an offset
is the piece's OAM flip. Records keep their order; an edit may move pieces
between records while the catalog keeps its size, and the index pointers
are recomputed from the record order.

World 2 (bank 1) uses fixed 2x2 records in three tables: four tiles per
record, a descriptor per record (palette in bits 0-1, an OAM-attribute
base in bits 2-5), and the 36 per-piece attribute bytes the bases select.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from retrolarium_kit.core.authoring import AuthoringError


VARIABLE_CODEC = "doraemon.metasprites.v1"
WORLD2_CODEC = "doraemon.world2-metasprites.v1"
WORLD2_RECORDS = 58
WORLD2_ATTRIBUTES = 36


class DoraemonMetaspriteError(AuthoringError):
    """A metasprite catalog does not fit its fixed ROM ranges."""


def _byte(value: object, context: str, maximum: int = 255) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= maximum:
        raise DoraemonMetaspriteError(f"{context} must be 0..{maximum}")
    return value


def decode_variable(index: bytes, records: bytes, records_address: int) -> dict[str, Any]:
    if len(index) % 2:
        raise DoraemonMetaspriteError("metasprite index has an odd size")
    parsed: list[dict[str, Any]] = []
    starts: dict[int, int] = {}
    offset = 0
    while offset < len(records):
        count = records[offset]
        size = 3 + count * 3
        if count == 0 or offset + size > len(records):
            raise DoraemonMetaspriteError("metasprite record is truncated")
        starts[records_address + offset] = len(parsed)
        parsed.append({
            "x_extent": records[offset + 1],
            "y_extent": records[offset + 2],
            "pieces": [list(records[offset + 3 + p * 3:offset + 6 + p * 3]) for p in range(count)],
        })
        offset += size
    entries: list[dict[str, int]] = []
    for i in range(len(index) // 2):
        low, high = index[i * 2], index[i * 2 + 1]
        if high < 4:
            entries.append({"alias": low, "flip": high})
        else:
            address = low | high << 8
            if address not in starts:
                raise DoraemonMetaspriteError(f"metasprite index {i} does not point at a record")
            entries.append({"record": starts[address]})
    result = {"entries": entries, "records": parsed}
    if encode_variable(result, records_address, len(index), len(records)) != (index, records):
        raise DoraemonMetaspriteError("metasprite catalog does not re-encode to its bytes")
    return result


def encode_variable(value: Any, records_address: int, index_size: int, records_size: int) -> tuple[bytes, bytes]:
    if not isinstance(value, Mapping) or set(value) != {"entries", "records"}:
        raise DoraemonMetaspriteError("metasprite catalog fields differ")
    entries = value["entries"]
    records = value["records"]
    if not isinstance(entries, list) or len(entries) * 2 != index_size:
        raise DoraemonMetaspriteError(f"the metasprite index holds {index_size // 2} entries")
    if not isinstance(records, list) or not records:
        raise DoraemonMetaspriteError("the catalog needs records")
    body = bytearray()
    addresses = []
    for r, record in enumerate(records):
        if not isinstance(record, Mapping) or set(record) != {"x_extent", "y_extent", "pieces"}:
            raise DoraemonMetaspriteError(f"metasprite record {r} fields differ")
        pieces = record["pieces"]
        if not isinstance(pieces, list) or not 1 <= len(pieces) <= 64:
            raise DoraemonMetaspriteError(f"metasprite record {r} holds 1-64 pieces")
        addresses.append(records_address + len(body))
        body += bytes((len(pieces), _byte(record["x_extent"], f"record {r} X extent"),
                       _byte(record["y_extent"], f"record {r} Y extent")))
        for p, piece in enumerate(pieces):
            if not isinstance(piece, list) or len(piece) != 3:
                raise DoraemonMetaspriteError(f"record {r} piece {p} is [y, x, tile]")
            body += bytes(_byte(v, f"record {r} piece {p}") for v in piece)
    if len(body) != records_size:
        raise DoraemonMetaspriteError(
            f"the catalog keeps {records_size} bytes of records; these take {len(body)}")
    index = bytearray()
    for i, entry in enumerate(entries):
        if isinstance(entry, Mapping) and set(entry) == {"record"}:
            record = _byte(entry["record"], f"index {i} record", len(records) - 1)
            index += addresses[record].to_bytes(2, "little")
        elif isinstance(entry, Mapping) and set(entry) == {"alias", "flip"}:
            index += bytes((_byte(entry["alias"], f"index {i} alias", len(entries) - 1),
                            _byte(entry["flip"], f"index {i} flip", 3)))
        else:
            raise DoraemonMetaspriteError(f"index {i} is a record or an alias")
    return bytes(index), bytes(body)


OAM_STREAM_CODEC = "doraemon.oam-stream.v1"


def decode_oam_stream(payload: bytes) -> list[dict[str, int]]:
    """A terminated OAM stream (Shell_CopyTerminatedOamStream): Y, tile,
    attributes, X per sprite, ended by a zero Y. The sprite count is fixed."""
    if len(payload) % 4 != 1 or payload[-1] != 0:
        raise DoraemonMetaspriteError("an OAM stream is whole sprites and a zero terminator")
    sprites = []
    for at in range(0, len(payload) - 1, 4):
        y, tile, attributes, x = payload[at:at + 4]
        if y == 0:
            raise DoraemonMetaspriteError("an OAM stream ends before its span")
        sprites.append({"x": x, "y": y, "tile": tile, "attributes": attributes})
    return sprites


def encode_oam_stream(value: Any, size: int) -> bytes:
    if not isinstance(value, list) or len(value) * 4 + 1 != size:
        raise DoraemonMetaspriteError(f"the OAM stream holds {(size - 1) // 4} sprites")
    payload = bytearray()
    for i, sprite in enumerate(value):
        if not isinstance(sprite, Mapping) or set(sprite) != {"x", "y", "tile", "attributes"}:
            raise DoraemonMetaspriteError(f"OAM sprite {i} fields differ")
        y = _byte(sprite["y"], f"OAM sprite {i} y")
        if y == 0:
            raise DoraemonMetaspriteError(f"OAM sprite {i} y 0 would end the stream")
        payload += bytes((y, _byte(sprite["tile"], f"OAM sprite {i} tile"),
                          _byte(sprite["attributes"], f"OAM sprite {i} attributes"),
                          _byte(sprite["x"], f"OAM sprite {i} x")))
    return bytes(payload) + b"\x00"


def decode_world2(tiles: bytes, descriptors: bytes, attributes: bytes) -> dict[str, Any]:
    if len(tiles) != WORLD2_RECORDS * 4 or len(descriptors) != WORLD2_RECORDS or len(attributes) != WORLD2_ATTRIBUTES:
        raise DoraemonMetaspriteError("World 2 metasprite table sizes differ")
    result = {
        "tiles": [list(tiles[i * 4:i * 4 + 4]) for i in range(WORLD2_RECORDS)],
        "descriptors": list(descriptors),
        "attributes": list(attributes),
    }
    encode_world2(result)
    return result


def encode_world2(value: Any) -> tuple[bytes, bytes, bytes]:
    if not isinstance(value, Mapping) or set(value) != {"tiles", "descriptors", "attributes"}:
        raise DoraemonMetaspriteError("World 2 metasprite fields differ")
    tiles = value["tiles"]
    descriptors = value["descriptors"]
    attributes = value["attributes"]
    if not isinstance(tiles, list) or len(tiles) != WORLD2_RECORDS \
            or any(not isinstance(q, list) or len(q) != 4 for q in tiles):
        raise DoraemonMetaspriteError("World 2 has 58 metasprites of four tiles")
    if not isinstance(descriptors, list) or len(descriptors) != WORLD2_RECORDS:
        raise DoraemonMetaspriteError("World 2 has 58 metasprite descriptors")
    if not isinstance(attributes, list) or len(attributes) != WORLD2_ATTRIBUTES:
        raise DoraemonMetaspriteError("World 2 has 36 metasprite attribute bytes")
    for i, d in enumerate(descriptors):
        _byte(d, f"descriptor {i}")
        # The last descriptor is also the enemy attack-period table's first
        # byte, so its value is not limited to an attribute group.
        if i < WORLD2_RECORDS - 1 and (d >> 2) & 0x0F > WORLD2_ATTRIBUTES // 4 - 1:
            raise DoraemonMetaspriteError(f"descriptor {i} selects attributes past the table")
    return (
        bytes(_byte(t, "World 2 tile") for q in tiles for t in q),
        bytes(descriptors),
        bytes(_byte(a, "World 2 attribute") for a in attributes),
    )
