"""Source-owned, shared bonus-round actor positions for canonical Flicky UE.

The six X immediates and three Y immediates have fixed instruction sizes.
All twelve bonus rounds execute the same code, so these are shared placements,
not per-round level layouts.
"""

from __future__ import annotations

from pathlib import Path
import re


SOURCE = "game/bonus/objects.s"
GROUPS = (
    ("seesaw", "Obj_BonusSeesaw:", "Obj_BonusTiger:"),
    ("tiger", "Obj_BonusTiger:", "Obj_BonusHeldChick:"),
    ("chick_spawn", "BonusChick_StateWait:", "BonusChick_StateFly:"),
)
X_WORD = re.compile(r"(?m)^\s*move\.w\s+#\$([0-9A-Fa-f]+),\$30\(a0\)\s*$")
Y_WORD = re.compile(r"(?m)^\s*move\.w\s+#\$([0-9A-Fa-f]+),\$24\(a0\)\s*$")


def _group(source: str, start: str, end: str) -> str:
    if source.count(start) != 1 or source.count(end) != 1:
        raise ValueError(f"ambiguous Flicky bonus source boundary: {start}")
    begin = source.index(start)
    finish = source.index(end)
    if finish <= begin:
        raise ValueError(f"invalid Flicky bonus source boundary: {start}")
    return source[begin:finish]


def export_bonus_placements(source_root: Path) -> list[dict[str, object]]:
    source = (source_root / "src" / SOURCE).read_text(encoding="utf-8")
    records = []
    for identifier, start, end in GROUPS:
        block = _group(source, start, end)
        xs = [int(match, 16) for match in X_WORD.findall(block)]
        ys = [int(match, 16) for match in Y_WORD.findall(block)]
        if len(xs) != 2 or len(ys) != 1:
            raise ValueError(f"unexpected Flicky bonus placement code: {identifier}")
        records.append({"id": identifier, "source": SOURCE,
                        "x": xs, "y": ys[0],
                        "baseline_x": xs.copy(), "baseline_y": ys[0]})
    return records


def validate_bonus_placements(document: dict, source_root: Path) -> None:
    """Legacy workspaces may omit this section; new ones have pinned owners."""
    if "bonus_placements" not in document:
        return
    records = document["bonus_placements"]
    baseline = export_bonus_placements(source_root)
    if not isinstance(records, list) or len(records) != len(baseline):
        raise ValueError("Flicky bonus placements need exactly three shared groups")
    for record, expected in zip(records, baseline):
        if not isinstance(record, dict) or any(
            record.get(key) != expected[key]
            for key in ("id", "source", "baseline_x", "baseline_y")
        ):
            raise ValueError("Flicky bonus placement identity/baseline changed")
        xs = record.get("x")
        y = record.get("y")
        if (not isinstance(xs, list) or len(xs) != 2
            or any(type(value) is not int or not 0x80 <= value <= 0x17F
                   for value in xs)
            or type(y) is not int or not 0x80 <= y <= 0x17F):
            raise ValueError(f"invalid Flicky bonus placement: {record['id']}")
