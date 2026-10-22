"""Fixed-size bonus-motion tables from the pinned Flicky source snapshot.

The neighboring source owns the pointer topology. Retrolarium exposes only
the 30 word arrays, preserving every label, word count and ROM address.
"""

from __future__ import annotations

from pathlib import Path
import re


BONUS_SOURCE = "game/bonus/objects.s"
TABLE_GROUPS = (
    ("delay", "Bonus_ChickDelays", 3),
    ("velocity", "Bonus_ChickVelocities", 9),
    ("trajectory_index", "Bonus_ChickTrajIndex", 9),
    ("trajectory", "Bonus_Trajectory", 9),
)
TABLE_SPECS = tuple((f"{prefix}{index}", kind) for kind, prefix, count in TABLE_GROUPS for index in range(count))
ROUND_POINTERS = (
    ("delay", "Bonus_ChickDelayPointers"),
    ("velocity", "Bonus_ChickVelocityPointers"),
    ("trajectory_index", "Bonus_ChickTrajIndexPointers"),
)
LABEL_RE = re.compile(r"(?m)^([A-Za-z_][A-Za-z0-9_]*):")
WORD_RE = re.compile(r"^\s*dc\.w\s+([^;]+)", re.IGNORECASE)
LONG_RE = re.compile(r"^\s*dc\.l\s+([^;]+)", re.IGNORECASE)


def _number(token: str) -> int:
    token = token.strip()
    if re.fullmatch(r"\$[0-9A-Fa-f]+", token):
        return int(token[1:], 16)
    if re.fullmatch(r"[0-9]+", token):
        return int(token)
    raise ValueError(f"invalid Flicky bonus word: {token}")


def _spans(source: str) -> dict[str, tuple[int, int]]:
    labels = list(LABEL_RE.finditer(source))
    return {
        match.group(1): (match.start(), labels[index + 1].start() if index + 1 < len(labels) else len(source))
        for index, match in enumerate(labels)
    }


def _words(block: str) -> list[int]:
    lines = block.splitlines()
    lines[0] = lines[0].split(":", 1)[1]
    result: list[int] = []
    for line in lines:
        match = WORD_RE.match(line)
        if match:
            result.extend(_number(token) for token in match.group(1).split(","))
        elif line.strip() and not line.lstrip().startswith(";"):
            raise ValueError("non-word directive inside Flicky bonus table")
    if not result or any(not 0 <= value <= 0xFFFF for value in result):
        raise ValueError("invalid Flicky bonus table words")
    return result


def _pointers(block: str) -> list[str]:
    lines = block.splitlines()
    lines[0] = lines[0].split(":", 1)[1]
    result: list[str] = []
    for line in lines:
        match = LONG_RE.match(line)
        if match:
            result.extend(token.strip() for token in match.group(1).split(","))
        elif line.strip() and not line.lstrip().startswith(";"):
            raise ValueError("non-pointer directive inside Flicky bonus round aliases")
    if len(result) != 12 or any(
        not re.fullmatch(r"Bonus_Chick(?:Delays|Velocities|TrajIndex)[0-9]+", value) for value in result
    ):
        raise ValueError("Flicky bonus round aliases need 12 table pointers")
    return result


def export_bonus_motion(source_root: Path) -> list[dict[str, object]]:
    source = (source_root / "src" / BONUS_SOURCE).read_text(encoding="utf-8")
    spans = _spans(source)
    aliases: dict[str, list[str]] = {}
    for kind, label in ROUND_POINTERS:
        if label not in spans:
            raise ValueError(f"missing Flicky bonus round aliases: {label}")
        start, end = spans[label]
        aliases[kind] = _pointers(source[start:end])
        allowed = {identifier for identifier, table_kind in TABLE_SPECS if table_kind == kind}
        if any(target not in allowed for target in aliases[kind]):
            raise ValueError(f"invalid Flicky bonus round aliases: {label}")
    records = []
    for identifier, kind in TABLE_SPECS:
        if identifier not in spans:
            raise ValueError(f"missing Flicky bonus table: {identifier}")
        start, end = spans[identifier]
        record: dict[str, object] = {
            "id": identifier,
            "source": BONUS_SOURCE,
            "kind": kind,
            "words": _words(source[start:end]),
        }
        if kind in aliases:
            record["rounds"] = [3 + 4 * index for index, target in enumerate(aliases[kind]) if target == identifier]
        records.append(record)
    return records


def validate_bonus_motion(document: dict, source_root: Path) -> None:
    """Allow legacy workspaces without the section; validate new ones strictly."""
    if "bonus_motion" not in document:
        return
    records = document["bonus_motion"]
    baseline = export_bonus_motion(source_root)
    if not isinstance(records, list) or len(records) != len(baseline):
        raise ValueError("Flicky bonus motion needs exactly 30 source tables")
    for record, expected in zip(records, baseline):
        if not isinstance(record, dict) or any(record.get(key) != expected[key] for key in ("id", "source", "kind")):
            raise ValueError("Flicky bonus motion identity or order changed")
        if "rounds" in record and record["rounds"] != expected.get("rounds"):
            raise ValueError(f"Flicky bonus round aliases changed: {record['id']}")
        words = record.get("words")
        baseline_words = expected["words"]
        if (
            not isinstance(words, list)
            or len(words) != len(baseline_words)
            or any(type(value) is not int or not 0 <= value <= 0xFFFF for value in words)
        ):
            raise ValueError(f"invalid Flicky bonus words: {record['id']}")
        if record["kind"] == "trajectory" and words[-1] != 0xFFFF:
            raise ValueError(f"Flicky bonus trajectory sentinel changed: {record['id']}")
        if record["kind"] == "trajectory_index" and any(value >> 8 > 8 or value & 0xFF > 8 for value in words):
            raise ValueError(f"Flicky bonus trajectory index is outside 0..8: {record['id']}")


def stage_bonus_motion(document: dict, source_root: Path, staged_source: Path) -> None:
    validate_bonus_motion(document, source_root)
    if "bonus_motion" not in document:
        return
    baseline = export_bonus_motion(source_root)
    changes = [
        (record, expected)
        for record, expected in zip(document["bonus_motion"], baseline)
        if record["words"] != expected["words"]
    ]
    if not changes:
        return
    path = staged_source / BONUS_SOURCE
    source = path.read_text(encoding="utf-8")
    spans = _spans(source)
    patches = []
    for record, _ in changes:
        start, end = spans[record["id"]]
        if _words(source[start:end]) != next(item["words"] for item in baseline if item["id"] == record["id"]):
            raise ValueError(f"staged Flicky bonus table changed: {record['id']}")
        words = record["words"]
        lines = [
            f"{record['id']}: dc.w    " + ", ".join(f"${word:X}" for word in words[index : index + 10])
            for index in (0,)
        ]
        for index in range(10, len(words), 10):
            lines.append("                dc.w    " + ", ".join(f"${word:X}" for word in words[index : index + 10]))
        trailing = "\n" if source[start:end].endswith("\n") else ""
        patches.append((start, end, "\n".join(lines) + trailing))
    for start, end, replacement in sorted(patches, reverse=True):
        source = source[:start] + replacement + source[end:]
    path.write_text(source, encoding="utf-8", newline="\n")
