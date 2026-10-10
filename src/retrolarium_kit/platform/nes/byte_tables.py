"""Named byte tables a codec reads from the game (codec nes.byte-tables.v1).

Some editors need what the game draws with but never edits it: the
scenery patterns a level renderer lays out, the metatile an instruction
loads for a bridge. The kit keeps none of these values; a profile entry of
this codec only says where each lies in the image built from the game's
disassembly, and the project gets them from there when it is made.

The profile lists the tables by name, each at a file offset with a size (a
constant an instruction loads is a table of one byte at its operand). The
document holds every table as a list of bytes. Writing it back puts the
bytes where they came from, so a project still builds byte for byte; the
studio offers no editor for them.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from retrolarium_kit.core.authoring import AuthoringError


CODEC = "nes.byte-tables.v1"


class ByteTableError(AuthoringError):
    """A byte-table profile entry or document is invalid."""


def layout(parameters: Mapping[str, Any], context: str) -> dict[str, tuple[int, int]]:
    """The tables a profile entry lists, as name -> (file offset, size)."""
    tables = parameters.get("tables")
    if not isinstance(tables, Mapping) or not tables:
        raise ByteTableError(f"{context}.tables must name at least one table")
    result = {}
    for name, place in tables.items():
        where = f"{context}.tables.{name}"
        if not isinstance(name, str) or not name.isidentifier():
            raise ByteTableError(f"{where} must be named by an identifier")
        if not isinstance(place, Mapping) or set(place) != {"file_offset", "size"}:
            raise ByteTableError(f"{where} needs a file_offset and a size")
        offset, size = place["file_offset"], place["size"]
        if type(offset) is not int or offset < 0 or type(size) is not int or size < 1:
            raise ByteTableError(f"{where} needs a file_offset >= 0 and a size >= 1")
        result[name] = (offset, size)
    return result


def decode_tables(image: bytes, parameters: Mapping[str, Any], context: str) -> dict[str, object]:
    tables = {}
    for name, (offset, size) in layout(parameters, context).items():
        chunk = image[offset:offset + size]
        if len(chunk) != size:
            raise ByteTableError(f"{context}.tables.{name} escapes the image")
        tables[name] = list(chunk)
    return {"tables": tables}


def encode_tables(raw: Mapping[str, Any], parameters: Mapping[str, Any],
                  context: str) -> list[tuple[int, bytes]]:
    """The bytes a document writes back, as (file offset, bytes) runs."""
    expected = layout(parameters, context)
    tables = raw.get("tables")
    if not isinstance(tables, Mapping) or set(tables) != set(expected):
        raise ByteTableError(f"{context} must hold the tables {sorted(expected)}")
    runs = []
    for name, (offset, size) in expected.items():
        values = tables[name]
        if (not isinstance(values, list) or len(values) != size
                or any(type(value) is not int or not 0 <= value <= 0xFF for value in values)):
            raise ByteTableError(f"{context}.tables.{name} must be {size} bytes")
        runs.append((offset, bytes(values)))
    return runs
