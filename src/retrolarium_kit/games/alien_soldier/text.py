"""Alien Soldier's text, in every form its readers take.

The game never stores characters: text is glyph indices into a font, and
each reader has its own layout. Most text uses the shared font
(`data/artunc/font.bin`, 64 bytes a glyph: the top and bottom tile of a
double-height character); the Japanese story uses its own font of 128-byte
glyphs (top-left, bottom-left, top-right, bottom-right tiles), indexed by
words less $20.

The document cuts each text payload into records whose values, put back
together, are the payload: every value has a role, `g` a glyph the editor
may change, `d` a delay (credits), `f` a fixed byte the reader needs as it
is (terminators, row positions, counts, attributes, command words and
padding). A record keeps its length and its roles; only `g` and `d` values
change.

| Group     | Payloads                                   | Records                                       |
| --------- | ------------------------------------------ | --------------------------------------------- |
| story     | StoryText_PrimaryRows, StoryText_AccentRows | 34-byte rows ending $FF, then the stream's $FE |
| frontend  | every `Text_` string ending $FF            | one string                                    |
| messages  | `StageTransitionMessageSequence_` payloads | row byte, glyphs, $FF; $FD ends a sequence     |
| scripts   | the boss and ship message scripts          | addressed records: attribute word, glyphs, $FF |
| credits   | CreditsGlyphSequenceData                   | two rows of delay, count and glyphs           |
| lists     | the four glyph source lists                | glyphs to $FF, word-packed                    |
| japanese  | JapaneseFontMappingData                    | glyph words, then $FFFF                       |
| results   | Results_StageDetailTextTable, the labels   | 22-byte rows ending $FF; 22-glyph labels       |
"""

from __future__ import annotations

from typing import Any, Callable

from retrolarium_kit.platform.megadrive.asl import AslSourceError, SourceTree


SCHEMA = "alien-soldier.text"
DOCUMENT = "text/text.json"
SHARED_FONT = "data/artunc/font.bin"
JAPANESE_FONT = "data/artunc/font_japanese.bin"
SHARED_GLYPH_BYTES = 64
JAPANESE_GLYPH_BYTES = 128
JAPANESE_FIRST = 0x20
SCRIPT_TABLES = ("BossMessageScriptPointerTable", "ShipNameScriptPointerTable")
GLYPH_LISTS = (
    "StageIntro_GlyphSourceList",
    "BattleBanner_GlyphSourceList",
    "Results_TimeBonusGlyphSourceList",
    "StageIntro_EmergencyGlyphSourceList",
)
RESULT_LABELS = ("Results_TotalTimeLabel", "Results_TotalClearTimeLabel", "Results_TotalContinueLabel")


class TextError(ValueError):
    """A text document that does not fit the source."""


Record = dict[str, Any]


def _record(identifier: str, values: list[int], roles: str) -> Record:
    return {"id": identifier, "values": list(values), "roles": roles}


def _rows(label: str, data: bytes, size: int, end: bytes = b"") -> list[Record]:
    body = data[: len(data) - len(end)]
    if data[len(body) :] != end or len(body) % size:
        raise TextError(f"{label} is not {size}-byte rows")
    records = [
        _record(
            f"{label}#{index}",
            list(body[at : at + size]),
            "".join("f" if value == 0xFF else "g" for value in body[at : at + size]),
        )
        for index, at in enumerate(range(0, len(body), size))
    ]
    if any(record["values"][-1] != 0xFF for record in records):
        raise TextError(f"{label} has a row without its $FF")
    if end:
        records.append(_record(f"{label}#end", list(end), "f" * len(end)))
    return records


def _story(tree: SourceTree, label: str) -> list[Record]:
    return _rows(label, tree.data(label), 34, b"\xfe")


def _frontend(data: bytes, label: str) -> list[Record]:
    return [_record(label, list(data), "g" * (len(data) - 1) + "f")]


def _messages(data: bytes, label: str) -> list[Record]:
    records, at, sequence = [], 0, 0
    while at < len(data) and data[at] != 0xFF:
        entry = 0
        while at < len(data) and data[at] != 0xFD:
            end = data.index(0xFF, at + 1)
            records.append(
                _record(f"{label}#{sequence}.{entry}", list(data[at : end + 1]), "f" + "g" * (end - at - 1) + "f")
            )
            at, entry = end + 1, entry + 1
        if at >= len(data):
            raise TextError(f"{label} has no $FD")
        records.append(_record(f"{label}#{sequence}.end", [0xFD], "f"))
        at, sequence = at + 1, sequence + 1
    if at < len(data):
        records.append(_record(f"{label}#padding", list(data[at:]), "f" * (len(data) - at)))
    return records


def _script(data: bytes, label: str) -> list[Record]:
    """Commands are words: $FFFF ends them, $FFFE and a long queue tiles, any
    other is a displacement from the word to a record. A record is an
    attribute word, glyphs and $FF. Bytes no command reaches stay fixed."""
    targets, at = [], 0
    while True:
        word = int.from_bytes(data[at : at + 2], "big")
        if word == 0xFFFF:
            at += 2
            break
        if word == 0xFFFE:
            at += 6
            continue
        targets.append(at + (word - 0x10000 if word & 0x8000 else word))
        at += 2
    roles = ["f"] * len(data)
    for target in targets:
        end = data.index(0xFF, target + 2)
        for position in range(target + 2, end):
            roles[position] = "g"
    return [_record(label, list(data), "".join(roles))]


def _credits(data: bytes, label: str) -> list[Record]:
    records, at, index = [], 0, 0
    while not data[at] & 0x80:
        values, roles = [], ""
        for _row in range(2):
            count = data[at + 1]
            values += list(data[at : at + 2 + count])
            roles += "df" + "g" * count
            at += 2 + count
        records.append(_record(f"{label}#{index}", values, roles))
        index += 1
    records.append(_record(f"{label}#end", list(data[at:]), "f" * (len(data) - at)))
    return records


def _glyph_list(data: bytes, label: str) -> list[Record]:
    end = data.index(0xFF)
    return [_record(label, list(data), "g" * end + "f" * (len(data) - end))]


def _japanese(data: bytes, label: str) -> list[Record]:
    words = [int.from_bytes(data[at : at + 2], "big") for at in range(0, len(data), 2)]
    if words[-1] != 0xFFFF:
        raise TextError(f"{label} has no final $FFFF")
    return [_record(label, words, "g" * (len(words) - 1) + "f")]


def _result_label(data: bytes, label: str) -> list[Record]:
    return [_record(label, list(data), "g" * 22 + "f" * (len(data) - 22))]


def _font_label(tree: SourceTree, path: str) -> str:
    for label in tree.order:
        block = tree.block(label)
        if block.kind == "binclude" and block.directives[0].path == path:
            return label
    raise TextError(f"the source includes no {path}")


def payloads(tree: SourceTree) -> list[tuple[str, str, int, Callable[[bytes, str], list[Record]]]]:
    """(group, label, value width, reader) of every text payload."""
    found: list[tuple[str, str, int, Callable[[bytes, str], list[Record]]]] = [
        ("story", "StoryText_PrimaryRows", 1, lambda data, label: _rows(label, data, 34, b"\xfe")),
        ("story", "StoryText_AccentRows", 1, lambda data, label: _rows(label, data, 34, b"\xfe")),
    ]
    for label in tree.order:
        block = tree.block(label)
        if label.startswith("Text_") and block.kind == "dc.b":
            data = tree.data(label)
            if data and data[-1] == 0xFF:
                found.append(("frontend", label, 1, _frontend))
    found += [
        ("messages", label, 1, _messages)
        for label in tree.order
        if label.startswith("StageTransitionMessageSequence_") and tree.block(label).kind in {"dc.b", "binclude"}
    ]
    scripts = dict.fromkeys(
        target for table in SCRIPT_TABLES for target in (o.text for o in tree.block(table).operands)
    )
    found += [("scripts", label, 1, _script) for label in tree.order if label in scripts]
    found.append(("credits", "CreditsGlyphSequenceData", 1, _credits))
    found += [("lists", label, 1, _glyph_list) for label in GLYPH_LISTS]
    found.append(("japanese", "JapaneseFontMappingData", 2, _japanese))
    found.append(("results", "Results_StageDetailTextTable", 1, lambda data, label: _rows(label, data, 22)))
    found += [("results", label, 1, _result_label) for label in RESULT_LABELS]
    return found


def _limits(tree: SourceTree) -> dict[str, tuple[int, int]]:
    shared = len(tree.data(_font_label(tree, SHARED_FONT))) // SHARED_GLYPH_BYTES
    japanese = len(tree.data(_font_label(tree, JAPANESE_FONT))) // JAPANESE_GLYPH_BYTES
    return {"shared": (0, shared - 1), "japanese": (JAPANESE_FIRST, JAPANESE_FIRST + japanese - 1)}


def export(tree: SourceTree) -> dict[str, Any]:
    items = []
    for group, label, width, reader in payloads(tree):
        try:
            records = reader(tree.data(label), label)
        except (ValueError, IndexError, AslSourceError) as error:
            raise TextError(f"{label}: {error}") from None
        items.append({"group": group, "label": label, "width": width, "records": records})
    return {
        "schema": SCHEMA,
        "schema_version": 1,
        "fonts": {
            "shared": {"label": _font_label(tree, SHARED_FONT), "glyph_bytes": SHARED_GLYPH_BYTES},
            "japanese": {"label": _font_label(tree, JAPANESE_FONT), "glyph_bytes": JAPANESE_GLYPH_BYTES},
        },
        "limits": {font: list(limit) for font, limit in _limits(tree).items()},
        "payloads": items,
    }


def apply(tree: SourceTree, document: dict[str, Any]) -> None:
    if document.get("schema") != SCHEMA or document.get("schema_version") != 1:
        raise TextError("not an Alien Soldier text document")
    source = export(tree)
    if document.get("fonts") != source["fonts"] or document.get("limits") != source["limits"]:
        raise TextError("the fonts are the source's")
    limits = _limits(tree)
    items = document.get("payloads")
    if not isinstance(items, list) or [(item.get("group"), item.get("label")) for item in items] != [
        (item["group"], item["label"]) for item in source["payloads"]
    ]:
        raise TextError("the text payloads differ from the source")
    for item, old in zip(items, source["payloads"]):
        label, width = old["label"], old["width"]
        low, high = limits["japanese" if old["group"] == "japanese" else "shared"]
        records = item.get("records")
        if not isinstance(records, list) or len(records) != len(old["records"]):
            raise TextError(f"{label}: the records are the source's")
        data = bytearray()
        for record, original in zip(records, old["records"]):
            values, roles = record.get("values"), original["roles"]
            if record.get("roles") != roles or not isinstance(values, list) or len(values) != len(roles):
                raise TextError(f"{original['id']}: the layout is the source's")
            for value, role, before in zip(values, roles, original["values"]):
                if isinstance(value, bool) or not isinstance(value, int):
                    raise TextError(f"{original['id']}: {value!r} is not a number")
                if role == "f" and value != before:
                    raise TextError(f"{original['id']}: a fixed value changed")
                if role == "g" and not low <= value <= high:
                    raise TextError(f"{original['id']}: glyph {value} is not in the font ({low}..{high})")
                if role == "d" and not 0 <= value <= 0x7F:
                    raise TextError(f"{original['id']}: a delay is 0..127")
                data += value.to_bytes(width, "big")
        tree.set_data(label, bytes(data))
