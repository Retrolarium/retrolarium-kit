"""Super Mario Bros. sound effect frequency tables.

SoundEngine's effects read four byte tables between their code: the 1-up
and power-up grab sequences, the power-up reveal / vine sequence (the
reveal plays its first eight entries) and the brick shatter noise
periods. A note table holds offsets into tbl_music_note_periods, the
period table music uses too, so its values are even; a noise byte is the
$400E value (mode bit and period index). Each table keeps its place and
size.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from retrolarium_kit.core.authoring import AuthoringError


CODEC = "smb.sfx-tables.v1"
KINDS = ("note", "noise")


class SmbSfxTableError(AuthoringError):
    """A sound effect table does not fit its place."""


def table_specs(parameters: Mapping[str, Any]) -> list[tuple[str, int, int, str]]:
    """(id, file offset, size, kind) of each table."""
    records = parameters.get("records")
    if not isinstance(records, list) or not records:
        raise SmbSfxTableError("sfx tables need records")
    result = []
    for i, record in enumerate(records):
        if not isinstance(record, Mapping) or set(record) != {"id", "file_offset", "size", "kind"}:
            raise SmbSfxTableError(f"sfx table {i} fields differ")
        name, offset, size, kind = record["id"], record["file_offset"], record["size"], record["kind"]
        if not isinstance(name, str) or not name or type(offset) is not int or type(size) is not int or size < 1:
            raise SmbSfxTableError(f"sfx table {i} needs an id, a file offset and a size")
        if kind not in KINDS:
            raise SmbSfxTableError(f"sfx table {name} kind is note or noise")
        result.append((name, offset, size, kind))
    if len({name for name, *_ in result}) != len(result):
        raise SmbSfxTableError("sfx table ids repeat")
    return result


def decode(image: bytes, parameters: Mapping[str, Any]) -> dict[str, object]:
    records = []
    for name, offset, size, _kind in table_specs(parameters):
        payload = image[offset:offset + size]
        if len(payload) != size:
            raise SmbSfxTableError(f"sfx table {name} exceeds the image")
        records.append({"id": name, "values": list(payload)})
    return {"records": records}


def encode(value: Any, parameters: Mapping[str, Any]) -> list[tuple[int, bytes]]:
    specs = table_specs(parameters)
    if not isinstance(value, list) or len(value) != len(specs):
        raise SmbSfxTableError(f"the sfx tables are {len(specs)} records")
    spans = []
    for record, (name, offset, size, kind) in zip(value, specs):
        if not isinstance(record, Mapping) or set(record) != {"id", "values"} or record["id"] != name:
            raise SmbSfxTableError(f"sfx table {name} fields differ")
        values = record["values"]
        if not isinstance(values, list) or len(values) != size:
            raise SmbSfxTableError(f"sfx table {name} holds {size} bytes")
        for i, v in enumerate(values):
            if type(v) is not int or not 0 <= v <= 255:
                raise SmbSfxTableError(f"sfx table {name}[{i}] must be a byte")
            if kind == "note" and v % 2:
                raise SmbSfxTableError(f"sfx table {name}[{i}] is a period table offset and must be even")
        spans.append((offset, bytes(values)))
    return spans
