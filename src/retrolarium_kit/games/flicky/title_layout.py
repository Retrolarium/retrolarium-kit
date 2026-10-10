"""Title screen sprite positions from the pinned Flicky source snapshot.

Title_Init places the six logo letters from Title_LogoLetterXTable and
the four cast birds from Title_BirdXTable. Each table is x, y word pairs
that run on through the next label (Title_LogoLetterYTable /
Title_BirdYTable), so a group keeps both labels and
its word count. Positions are VDP sprite coordinates ($80 is the screen's
left or top edge).
"""

from __future__ import annotations

from pathlib import Path
import re


SOURCE = "game/screens/front_end.s"
GROUPS = (
    ("statics", "Title_LogoLetterXTable", "Title_LogoLetterYTable", 6),
    ("cast", "Title_BirdXTable", "Title_BirdYTable", 4),
)
# Labels a source from before the 2026-10 object names uses instead.
LEGACY_LABELS = {"Title_LogoLetterXTable": "Title_StaticXTable",
                 "Title_LogoLetterYTable": "Title_StaticYTable"}
LINE_RE = r"(?m)^{label}:(?P<pad>[ \t]+)dc\.w(?P<gap>[ \t]+)(?P<words>[^;\r\n]*?)(?P<tail>[ \t]*(?:;[^\r\n]*)?)$"


def _number(token: str) -> int:
    token = token.strip()
    if re.fullmatch(r"\$[0-9A-Fa-f]+", token):
        return int(token[1:], 16)
    if re.fullmatch(r"[0-9]+", token):
        return int(token)
    raise ValueError(f"invalid Flicky title word: {token}")


def _line(source: str, label: str) -> re.Match[str]:
    if f"{label}:" not in source:
        label = LEGACY_LABELS.get(label, label)
    matches = list(re.finditer(LINE_RE.format(label=re.escape(label)), source))
    if len(matches) != 1:
        raise ValueError(f"Flicky title table {label} is not one dc.w line")
    return matches[0]


def _words(source: str, first: str, second: str) -> list[int]:
    return [_number(token) for label in (first, second)
            for token in _line(source, label).group("words").split(",")]


def export_title_layout(source_root: Path) -> list[dict[str, object]]:
    source = (source_root / "src" / SOURCE).read_text(encoding="utf-8")
    records = []
    for identifier, first, second, count in GROUPS:
        words = _words(source, first, second)
        if len(words) != count * 2 or len(_line(source, first).group("words").split(",")) != 1:
            raise ValueError(f"unexpected Flicky title table layout: {identifier}")
        positions = [[words[i], words[i + 1]] for i in range(0, len(words), 2)]
        records.append({"id": identifier, "source": SOURCE, "positions": positions,
                        "baseline": [list(p) for p in positions]})
    return records


def validate_title_layout(document: dict, source_root: Path) -> None:
    """Older workspaces may omit this section."""
    if "title_layout" not in document:
        return
    records = document["title_layout"]
    baseline = export_title_layout(source_root)
    if not isinstance(records, list) or len(records) != len(baseline):
        raise ValueError("Flicky title layout needs the statics and cast groups")
    for record, expected in zip(records, baseline):
        if not isinstance(record, dict) or set(record) != set(expected) or any(
                record.get(key) != expected[key] for key in ("id", "source", "baseline")):
            raise ValueError("Flicky title layout identity/baseline changed")
        positions = record.get("positions")
        if (not isinstance(positions, list) or len(positions) != len(expected["positions"])
                or any(not isinstance(p, list) or len(p) != 2
                       or any(type(v) is not int or not 0 <= v <= 0x1FF for v in p)
                       for p in positions)):
            raise ValueError(f"invalid Flicky title positions: {record['id']}")


def stage_title_layout(document: dict, source_root: Path, staged_source: Path) -> None:
    """Write edited positions into the staged front-end source."""
    validate_title_layout(document, source_root)
    if "title_layout" not in document:
        return
    path = staged_source / SOURCE
    source = path.read_text(encoding="utf-8")
    groups = {identifier: (first, second) for identifier, first, second, _count in GROUPS}
    for record in document["title_layout"]:
        if record["positions"] == record["baseline"]:
            continue
        first, second = groups[record["id"]]
        words = [value for position in record["positions"] for value in position]
        for label, values in ((first, words[:1]), (second, words[1:])):
            line = _line(source, label)
            text = ", ".join(f"${value:X}" for value in values)
            source = source[:line.start("words")] + text + source[line.end("words"):]
    path.write_text(source, encoding="utf-8", newline="\n")
