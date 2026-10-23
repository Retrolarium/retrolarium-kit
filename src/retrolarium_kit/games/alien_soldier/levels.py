"""Alien Soldier's stages: the object placements and the stage records.

A spawn list is a run of twelve-byte placements read by
`Sys_ProcessSpawnList`, ended by the word $7FFF: X and Y in the stage, the
object type (a byte offset into `Entity_UpdateHandlerTable`, whose low two
bits mark easy-only and hard-only objects), a parameter the handler reads,
and the first and last object slot it may take.

A stage record is thirty bytes that `Stage_ApplyConfigurationRecord` copies
into the stage state: the state offset, the spawn list, the enemy spawn
director, two palette effect offsets, the sprite priority bit, the primary
and secondary camera positions, the two planes' entry modes, the player's
start (biased by $80) and the palette offset list. `Stage_InitializerOffsets`
gives each stage slot the routine that applies one of them.

The document lets placements move and change type, parameter and slots, and
lets a record's numbers change within what their readers index; the number
of placements and which list or palette list a record names stay.
"""

from __future__ import annotations

import re
from typing import Any

from retrolarium_kit.platform.megadrive.asl import SourceTree, parse_number


SCHEMA = "alien-soldier.levels"
DOCUMENT = "levels/levels.json"
TERMINATOR = 0x7FFF
PLACEMENT_FIELDS = ("x", "y", "type", "parameter", "slot_first", "slot_limit")
RECORD_FIELDS = (
    ("state_offset", "dc.w"),
    ("spawn_list", "dc.l"),
    ("spawn_director", "dc.w"),
    ("palette_primary", "dc.b"),
    ("palette_secondary", "dc.b"),
    ("sprite_priority", "dc.w"),
    ("camera_x", "dc.w"),
    ("camera_y", "dc.w"),
    ("secondary_camera_x", "dc.w"),
    ("secondary_camera_y", "dc.w"),
    ("plane_a_mode", "dc.w"),
    ("plane_b_mode", "dc.w"),
    ("player_x", "dc.b"),
    ("player_y", "dc.b"),
    ("palette_list", "dc.l"),
)
LABEL_FIELDS = {"spawn_list", "palette_list"}
RECORD_SOURCE = "src/stages/configuration_records.s"
HANDLERS = "Entity_UpdateHandlerTable"
INITIALIZERS = "Stage_InitializerOffsets"
RECORD_LEA = re.compile(r"^\s+lea\s+(?P<record>[A-Za-z_][A-Za-z0-9_]*)\(pc\),a0\b")
# The tables a record's numbers index, and how: plane modes are offsets of
# four from 4 (zero skips the table, and the primary plane's reader masks bit
# 15); the director and palette effects index word tables from zero.
PLANE_TABLES = {
    "plane_a_mode": "Stage_EntryPrimaryPlaneVRAMParameterPointers",
    "plane_b_mode": "Stage_EntrySecondaryPlaneVRAMParameterPointers",
}
WORD_TABLES = {
    "spawn_director": "EnemySpawn_DirectorHandlers",
    "palette_primary": "Palette_PrimaryEffectOffsets",
    "palette_secondary": "Palette_SecondaryEffectOffsets",
}


class LevelError(ValueError):
    """A level document that does not fit the source."""


def _placements(data: bytes) -> tuple[list[dict[str, int]], bool]:
    """A list's placements, and whether it ends with the terminator (eight
    lists do not: the spawner runs on into the list that follows)."""
    terminated = len(data) % 12 == 2 and int.from_bytes(data[-2:], "big") == TERMINATOR
    body = data[:-2] if terminated else data
    if len(body) % 12:
        raise LevelError("not a spawn list")
    placements = [
        dict(
            zip(
                PLACEMENT_FIELDS,
                (int.from_bytes(body[at + 2 * index : at + 2 * index + 2], "big") for index in range(6)),
            )
        )
        for at in range(0, len(body), 12)
    ]
    return placements, terminated


def records(tree: SourceTree) -> dict[str, dict[str, Any]]:
    """The stage records `Stage_ApplyConfigurationRecord` reads (one
    stage's record lies elsewhere, has its own reader and stays as it is)."""
    expected = [kind for _, kind in RECORD_FIELDS]
    found = {}
    for label in tree.labels_in(RECORD_SOURCE):
        block = tree.block(label)
        if block.kind != "mixed":
            continue
        operands = [(directive.kind, operand.text) for directive in block.directives for operand in directive.operands]
        if [kind for kind, _ in operands] != expected:
            continue
        fields = {}
        for (name, _), (_, text) in zip(RECORD_FIELDS, operands):
            fields[name] = text if name in LABEL_FIELDS else parse_number(text)
        if all(value is not None for value in fields.values()):
            found[label] = fields
    return found


def stages(tree: SourceTree) -> list[dict[str, str]]:
    """Each stage slot's initializer and the record it applies."""
    slots = []
    for operand in tree.block(INITIALIZERS).operands:
        routine = operand.text.split("-")[0]
        record = ""
        block = tree.block(routine)
        for line in block.file.lines[block.line : block.line + 8]:
            match = RECORD_LEA.match(line.split(";")[0])
            if match:
                record = match["record"]
                break
        slots.append({"initializer": routine, "record": record})
    return slots


def spawn_lists(tree: SourceTree, found: dict[str, dict[str, Any]]) -> dict[str, tuple[list[dict[str, int]], bool]]:
    """The lists the records name (a record may hold a number instead, such
    as $80000000, which names none)."""
    lists = {}
    for label in dict.fromkeys(fields["spawn_list"] for fields in found.values()):
        if parse_number(label) is None:
            lists[label] = _placements(tree.data(label))
    return lists


def handlers(tree: SourceTree) -> dict[str, str]:
    """Object type (as $hex) to the handler label it runs."""
    return {f"${4 * index:X}": operand.text for index, operand in enumerate(tree.block(HANDLERS).operands)}


def _table_length(tree: SourceTree, label: str, stops: set[str]) -> int:
    """How many entries a table has: its own and those of the labels that
    follow it with the same directive, up to the next table."""
    kind, count = tree.block(label).kind, 0
    for name in tree.order[tree.order.index(label) :]:
        block = tree.block(name)
        if (name in stops and name != label) or block.kind != kind:
            break
        count += len(block.operands)
    return count


def _domains(tree: SourceTree) -> dict[str, set[int]]:
    domains = {}
    tables = set(PLANE_TABLES.values()) | set(WORD_TABLES.values())
    for field, table in PLANE_TABLES.items():
        offsets = {4 * (index + 1) for index in range(_table_length(tree, table, tables))}
        if field == "plane_a_mode":
            offsets |= {0x8000 | offset for offset in offsets}
        domains[field] = {0} | offsets
    for field, table in WORD_TABLES.items():
        domains[field] = {2 * index for index in range(_table_length(tree, table, tables))}
    return domains


def export(tree: SourceTree) -> dict[str, Any]:
    found = records(tree)
    return {
        "schema": SCHEMA,
        "schema_version": 1,
        "handlers": handlers(tree),
        "stages": stages(tree),
        "records": [{"label": label, **fields} for label, fields in found.items()],
        "spawn_lists": [
            {"label": label, "terminated": terminated, "placements": items}
            for label, (items, terminated) in spawn_lists(tree, found).items()
        ],
    }


def _word(value: Any, where: str, low: int = 0, high: int = 0xFFFF) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise LevelError(f"{where} must be {low}..{high}")
    return value


def apply(tree: SourceTree, document: dict[str, Any]) -> None:
    if document.get("schema") != SCHEMA or document.get("schema_version") != 1:
        raise LevelError("not an Alien Soldier level document")
    found = records(tree)
    if document.get("handlers") != handlers(tree) or document.get("stages") != stages(tree):
        raise LevelError("the handler table and stage slots are the source's")
    items = document.get("records")
    if not isinstance(items, list) or [item.get("label") for item in items] != list(found):
        raise LevelError("the stage records differ from the source")
    domains = _domains(tree)
    for item in items:
        label, old = item["label"], found[item["label"]]
        values: list[int | None] = []
        for name, kind in RECORD_FIELDS:
            value = item.get(name)
            if name in LABEL_FIELDS:
                if value != old[name]:
                    raise LevelError(f"{label}: {name} is the source's")
                values.append(None)
                continue
            high = 0xFF if kind == "dc.b" else 0xFFFF
            _word(value, f"{label} {name}", 0, high)
            if name in domains and value not in domains[name]:
                raise LevelError(f"{label}: {name} ${value:X} is not an entry its reader indexes")
            values.append(value)
        tree.set_values(label, values)
    lists = spawn_lists(tree, found)
    items = document.get("spawn_lists")
    if not isinstance(items, list) or [item.get("label") for item in items] != list(lists):
        raise LevelError("the spawn lists differ from the source")
    for item in items:
        label, placements = item["label"], item.get("placements")
        old, terminated = lists[label]
        if not isinstance(placements, list) or len(placements) != len(old) or item.get("terminated") != terminated:
            raise LevelError(f"{label}: the number of placements is the source's")
        data = bytearray()
        for index, placement in enumerate(placements):
            for field in PLACEMENT_FIELDS:
                data += _word(placement.get(field), f"{label} placement {index} {field}").to_bytes(2, "big")
        tree.set_data(label, bytes(data) + (TERMINATOR.to_bytes(2, "big") if terminated else b""))
