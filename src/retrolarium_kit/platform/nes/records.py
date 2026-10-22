"""Fixed tables of byte records (codec nes.record-table.v1).

Games keep many small tables at fixed PRG addresses: a stat per enemy, an
offset per weapon direction, a flag per room. A resource of this codec
describes such records once and the kit reads and writes them as rows of
named fields, so every game edits them the same way.

The profile entry lists the record count and one or more tables. A table
lies at a file offset and holds some of the fields: record by record (the
fields of a record side by side), or with "columns" field by field (each
field's bytes for all records in a row). A field is a byte with an optional
range, signed reading, named choices, or a "none" byte the document holds as
null; a "unique" field gives every record a different value (with as many
choices as records, the records share out the choices). Records a field lists in "locked" are shown but never written: the
byte is code, or belongs to something else. Two tables may share a byte
(the game reads the last record of one as the first of the next); an edit
that gives such a byte two values is refused.

The document repeats the field descriptions and record labels so an editor
needs nothing but the document; parsing checks them against the profile.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from retrolarium_kit.core.authoring import AuthoringError


CODEC = "nes.record-table.v1"
FIELD_KEYS = frozenset(
    {"name", "label", "minimum", "maximum", "signed", "choices", "names", "none", "locked", "unique"}
)


class RecordTableError(AuthoringError):
    """A record table profile or document is invalid."""


def _integer(value: object, context: str, *, minimum: int = 0, maximum: int | None = None) -> int:
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        bound = f"{minimum}-{maximum}" if maximum is not None else f">= {minimum}"
        raise RecordTableError(f"{context} must be an integer {bound}")
    return value


def _field(raw: object, records: int, context: str) -> dict[str, Any]:
    """One field description, normalized: name, label, minimum, maximum,
    signed, and choices, names, none and locked when given. `names` is the
    category of the game's names (its repository's config/names.json) the
    studio shows for the values in place of the choices' neutral labels."""
    if not isinstance(raw, Mapping) or not set(raw) <= FIELD_KEYS or "name" not in raw:
        raise RecordTableError(f"{context} needs a name and only {sorted(FIELD_KEYS)}")
    name = raw["name"]
    if not isinstance(name, str) or not name.isidentifier():
        raise RecordTableError(f"{context}.name must be an identifier")
    signed = raw.get("signed", False)
    if type(signed) is not bool:
        raise RecordTableError(f"{context}.signed must be true or false")
    low, high = (-128, 127) if signed else (0, 255)
    minimum = _integer(raw.get("minimum", low), f"{context}.minimum", minimum=low, maximum=high)
    maximum = _integer(raw.get("maximum", high), f"{context}.maximum", minimum=minimum, maximum=high)
    field: dict[str, Any] = {
        "name": name,
        "label": raw.get("label", name.replace("_", " ").capitalize()),
        "minimum": minimum,
        "maximum": maximum,
        "signed": signed,
    }
    if not isinstance(field["label"], str) or not field["label"]:
        raise RecordTableError(f"{context}.label must be a non-empty string")
    if "choices" in raw:
        choices = raw["choices"]
        if not isinstance(choices, list) or not choices:
            raise RecordTableError(f"{context}.choices must be a non-empty list")
        seen = set()
        for i, choice in enumerate(choices):
            if (
                not isinstance(choice, Mapping)
                or set(choice) != {"value", "label"}
                or not isinstance(choice["label"], str)
                or not choice["label"]
            ):
                raise RecordTableError(f"{context}.choices[{i}] needs a value and a label")
            value = _integer(choice["value"], f"{context}.choices[{i}].value", minimum=minimum, maximum=maximum)
            if value in seen:
                raise RecordTableError(f"{context}.choices repeats {value}")
            seen.add(value)
        field["choices"] = [dict(choice) for choice in choices]
    if "names" in raw:
        if not isinstance(raw["names"], str) or not raw["names"].isidentifier():
            raise RecordTableError(f"{context}.names must name a category")
        field["names"] = raw["names"]
    if "none" in raw:
        field["none"] = _integer(raw["none"], f"{context}.none", minimum=0, maximum=255)
    if raw.get("unique", False) is not False:
        if raw["unique"] is not True:
            raise RecordTableError(f"{context}.unique must be true or false")
        field["unique"] = True
    if "locked" in raw:
        locked = raw["locked"]
        if (
            not isinstance(locked, list)
            or any(type(r) is not int or not 0 <= r < records for r in locked)
            or len(set(locked)) != len(locked)
        ):
            raise RecordTableError(f"{context}.locked must list distinct record indexes")
        field["locked"] = sorted(locked)
    return field


def layout(parameters: Mapping[str, Any], context: str) -> dict[str, Any]:
    """The profile parameters checked and normalized: records, record labels,
    fields (in table order) and tables (file offset, field names, columns)."""
    records = _integer(parameters.get("records"), f"{context}.records", minimum=1, maximum=4096)
    raw_tables = parameters.get("tables")
    if not isinstance(raw_tables, list) or not raw_tables:
        raise RecordTableError(f"{context}.tables must be a non-empty list")
    fields: list[dict[str, Any]] = []
    tables = []
    for t, table in enumerate(raw_tables):
        where = f"{context}.tables[{t}]"
        if not isinstance(table, Mapping) or not set(table) <= {"file_offset", "fields", "columns"}:
            raise RecordTableError(f"{where} has file_offset, fields and columns only")
        offset = _integer(table.get("file_offset"), f"{where}.file_offset")
        columns = table.get("columns", False)
        if type(columns) is not bool:
            raise RecordTableError(f"{where}.columns must be true or false")
        raw_fields = table.get("fields")
        if not isinstance(raw_fields, list) or not raw_fields:
            raise RecordTableError(f"{where}.fields must be a non-empty list")
        names = []
        for f, raw in enumerate(raw_fields):
            field = _field(raw, records, f"{where}.fields[{f}]")
            if any(other["name"] == field["name"] for other in fields):
                raise RecordTableError(f"{where} repeats field {field['name']}")
            fields.append(field)
            names.append(field["name"])
        tables.append({"file_offset": offset, "fields": names, "columns": columns})
    labels = parameters.get("record_labels")
    if labels is None:
        # {n} counts from 1, {i} from 0, {hex} is the index as $XX.
        template = parameters.get("record_label", "Record {n}")
        if not isinstance(template, str) or not any(key in template for key in ("{n}", "{i}", "{hex}")):
            raise RecordTableError(f"{context}.record_label must hold {{n}}, {{i}} or {{hex}}")
        labels = [
            template.replace("{n}", str(i + 1)).replace("{i}", str(i)).replace("{hex}", f"${i:02X}")
            for i in range(records)
        ]
    if (
        not isinstance(labels, list)
        or len(labels) != records
        or any(not isinstance(label, str) or not label for label in labels)
    ):
        raise RecordTableError(f"{context}.record_labels must name every record")
    return {"records": records, "record_labels": list(labels), "fields": fields, "tables": tables}


def _cells(shape: Mapping[str, Any]):
    """Every stored byte: (file offset, record, field)."""
    by_name = {field["name"]: field for field in shape["fields"]}
    for table in shape["tables"]:
        width = len(table["fields"])
        for record in range(shape["records"]):
            for f, name in enumerate(table["fields"]):
                at = f * shape["records"] + record if table["columns"] else record * width + f
                yield table["file_offset"] + at, record, by_name[name]


def _decode_value(byte: int, field: Mapping[str, Any]) -> int | None:
    if "none" in field and byte == field["none"]:
        return None
    return byte - 256 if field["signed"] and byte >= 128 else byte


def decode_records(image: bytes, parameters: Mapping[str, Any], context: str) -> dict[str, object]:
    shape = layout(parameters, context)
    rows: list[dict[str, Any]] = [{} for _ in range(shape["records"])]
    for offset, record, field in _cells(shape):
        if offset >= len(image):
            raise RecordTableError(f"{context} escapes the image")
        rows[record][field["name"]] = _decode_value(image[offset], field)
    return {"fields": shape["fields"], "record_labels": shape["record_labels"], "records": rows}


def _encode_value(value: object, field: Mapping[str, Any], context: str) -> int:
    if value is None:
        if "none" not in field:
            raise RecordTableError(f"{context} cannot be empty")
        return field["none"]
    if type(value) is not int or not field["minimum"] <= value <= field["maximum"]:
        raise RecordTableError(f"{context} must be {field['minimum']}-{field['maximum']}")
    if "choices" in field and value not in {choice["value"] for choice in field["choices"]}:
        raise RecordTableError(f"{context} is not one of its choices")
    byte = value & 0xFF
    if "none" in field and byte == field["none"]:
        raise RecordTableError(f"{context} {value} is the byte that means none")
    return byte


def encode_records(raw: Mapping[str, Any], parameters: Mapping[str, Any], context: str) -> list[tuple[int, bytes]]:
    """The bytes an edited document writes, as (file offset, bytes) runs.
    Locked records are skipped; a shared byte must get one value."""
    shape = layout(parameters, context)
    if raw.get("fields") != shape["fields"] or raw.get("record_labels") != shape["record_labels"]:
        raise RecordTableError(f"{context} field descriptions or record labels differ from the profile")
    rows = raw.get("records")
    names = [field["name"] for field in shape["fields"]]
    if not isinstance(rows, list) or len(rows) != shape["records"]:
        raise RecordTableError(f"{context} holds {shape['records']} records")
    for r, row in enumerate(rows):
        if not isinstance(row, Mapping) or set(row) != set(names):
            raise RecordTableError(f"{context} record {r + 1} fields differ")
    for field in shape["fields"]:
        if field.get("unique"):
            values = [row[field["name"]] for row in rows]
            if len(set(map(repr, values))) != len(values):
                raise RecordTableError(f"{context} {field['label'].lower()} must differ for every record")
    written: dict[int, tuple[int, str]] = {}
    for offset, record, field in _cells(shape):
        if record in field.get("locked", ()):
            continue
        where = f"{shape['record_labels'][record]} {field['label'].lower()}"
        byte = _encode_value(rows[record][field["name"]], field, where)
        previous = written.get(offset)
        if previous is not None and previous[0] != byte:
            raise RecordTableError(f"{where} and {previous[1]} are one byte in the game; give them one value")
        written[offset] = (byte, where)
    runs: list[tuple[int, bytearray]] = []
    for offset in sorted(written):
        if runs and runs[-1][0] + len(runs[-1][1]) == offset:
            runs[-1][1].append(written[offset][0])
        else:
            runs.append((offset, bytearray((written[offset][0],))))
    return [(offset, bytes(data)) for offset, data in runs]
