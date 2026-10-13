"""Super Mario Bros. sound effect frequency tables.

SoundEngine's effects read four byte tables between their code: the 1-up
and power-up grab sequences, the power-up reveal / vine sequence (the
reveal plays its first eight entries) and the brick shatter noise
periods. A note table holds offsets into the music's note period table, so
its values are even; a noise byte is the $400E value (mode bit and period
index). Each table keeps its place and size.

Where the effects can be played, the resource also names where SoundEngine
starts (engine_file_offset) and where the note period table lies
(note_periods_file_offset); the kit finds them by these offsets, never by
the game's code.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from retrolarium_kit.core.authoring import AuthoringError


CODEC = "smb.sfx-tables.v1"
KINDS = ("note", "noise")


class SmbSfxTableError(AuthoringError):
    """A sound effect table does not fit its place."""


OFFSETS = ("engine_file_offset", "note_periods_file_offset")


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
    for key in OFFSETS:
        if key in parameters and (type(parameters[key]) is not int or parameters[key] < 0):
            raise SmbSfxTableError(f"sfx tables {key} must be a file offset")
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


def _source_resources(source: bytes):
    """(parameters, base) of each sfx-tables resource an opened profile
    declares for this image; base turns a file offset into a CPU address in
    the 32 KiB window."""
    import hashlib

    from retrolarium_kit.core.game_profiles import game_profiles
    from retrolarium_kit.platform.nes.resources import _resource_payload, _resource_specs

    sha = hashlib.sha256(source).hexdigest()
    registry = game_profiles()
    for identifier in registry.identifiers():
        profile = registry.get(identifier)
        for revision in profile.revisions:
            if revision.sha256 != sha:
                continue
            result = []
            for spec in _resource_specs(profile, revision):
                if spec.codec != CODEC:
                    continue
                payload = _resource_payload(profile, revision, spec)
                # An iNES image's PRG follows its 16-byte header.
                base = 0x8000 - 16 if payload is None else payload.load_address
                if base is not None:
                    result.append((spec.parameters, base))
            return result
    return []


def source_tables(source: bytes) -> tuple["Table", ...]:
    """The effect tables an opened profile declares for this image
    (smb.sfx-tables.v1), at their CPU addresses in the 32 KiB window."""
    from retrolarium_kit.platform.nes.sound_effects import Table

    result = []
    for parameters, base in _source_resources(source):
        for name, offset, size, kind in table_specs(parameters):
            if 0x8000 <= base + offset and base + offset + size <= 0x10000:
                result.append(Table(name, base + offset, size, kind))
    return tuple(result)


def source_sound(source: bytes) -> tuple[int | None, int | None]:
    """The CPU addresses of SoundEngine and of the note period table the
    profile names for this image; None where it names none."""
    for parameters, base in _source_resources(source):
        engine, periods = (parameters.get(key) for key in OFFSETS)
        return (base + engine if engine is not None else None,
                base + periods if periods is not None else None)
    return None, None
