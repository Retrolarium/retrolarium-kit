"""Alien Soldier's sound envelopes: the pitch envelopes
(`Sound_ApplyPitchEffects`) and the PSG volume envelopes
(`Sound_UpdatePSGVolumeEnvelope`).

A channel selects an envelope through a one-based pointer table and reads it
a byte per update. A byte is a signed value (a pitch offset scaled by the
channel, or attenuation added to the volume), unless it is a command: $80
restarts the envelope, $81 repeats the last value, $82 jumps to the index
in the next byte, $83 ends it (with a rest or a mute), and for pitch $84 adds
the next byte to the channel's scale. The tables are in physical order here,
not pointer order: envelope 6 sits before envelope 5 and runs on into it.

The document keeps each envelope's bytes with their roles: `v` a value the
editor may change (to anything the reader will not take for a command),
`c` a command or its operand.
"""

from __future__ import annotations

from typing import Any

from retrolarium_kit.games.alien_soldier.assets import pointer_table
from retrolarium_kit.platform.megadrive.asl import SourceTree


SCHEMA = "alien-soldier.envelopes"
DOCUMENT = "sound/envelopes.json"
FAMILIES = {
    "pitch": ("Sound_PitchEnvelopePointerTable", {0x80, 0x81, 0x82, 0x83, 0x84}, {0x82, 0x84}),
    "volume": ("Sound_PSGVolumeEnvelopePointerTable", {0x80, 0x81, 0x82, 0x83}, {0x82}),
}


class EnvelopeError(ValueError):
    """An envelope document that does not fit the source."""


def _roles(data: bytes, commands: set[int], with_operand: set[int]) -> str:
    roles, at = [], 0
    while at < len(data):
        if data[at] in commands:
            roles.append("c")
            if data[at] in with_operand and at + 1 < len(data):
                roles.append("c")
                at += 1
        else:
            roles.append("v")
        at += 1
    return "".join(roles)


def envelopes(tree: SourceTree) -> list[dict[str, Any]]:
    found = []
    for family, (table, commands, with_operand) in FAMILIES.items():
        labels = pointer_table(tree, table)
        for label in sorted(labels, key=tree.order.index):
            data = tree.data(label)
            found.append(
                {
                    "family": family,
                    "label": label,
                    "number": labels.index(label) + 1,
                    "values": [value - 256 if value >= 0x80 else value for value in data],
                    "roles": _roles(data, commands, with_operand),
                }
            )
    return found


def export(tree: SourceTree) -> dict[str, Any]:
    return {"schema": SCHEMA, "schema_version": 1, "envelopes": envelopes(tree)}


def apply(tree: SourceTree, document: dict[str, Any]) -> None:
    if document.get("schema") != SCHEMA or document.get("schema_version") != 1:
        raise EnvelopeError("not an Alien Soldier envelope document")
    source = envelopes(tree)
    items = document.get("envelopes")
    if not isinstance(items, list) or [item.get("label") for item in items] != [item["label"] for item in source]:
        raise EnvelopeError("the envelopes differ from the source")
    for item, old in zip(items, source):
        values, roles = item.get("values"), old["roles"]
        commands = FAMILIES[old["family"]][1]
        if item.get("roles") != roles or not isinstance(values, list) or len(values) != len(roles):
            raise EnvelopeError(f"{old['label']}: the layout is the source's")
        for value, role, before in zip(values, roles, old["values"]):
            if isinstance(value, bool) or not isinstance(value, int) or not -128 <= value <= 127:
                raise EnvelopeError(f"{old['label']}: {value!r} is not a signed byte")
            if role == "c" and value != before:
                raise EnvelopeError(f"{old['label']}: a command changed")
            if role == "v" and value & 0xFF in commands:
                raise EnvelopeError(f"{old['label']}: {value} would read as a command")
        tree.set_data(old["label"], bytes(value & 0xFF for value in values))
