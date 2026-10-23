"""Alien Soldier's palettes: the commands that load colours into CRAM.

A palette is stored as commands, never as sixty-four colours.
`Gfx_LoadPaletteCommand` reads a destination byte (a byte offset into the
palette buffer, so $62 is colour $31: line three, colour one), one less than
the colour count, then that many CRAM words. A label in
`src/rendering/palettes.s` holds one command or a bank of them, written as
`dc.b` or included from a split file.

The offset lists in the same file name the commands a screen plays, in order,
as `(Bank+$disp)-Gfx_LoadPalettePreservingSharedColor`: each entry is a
command by its label and offset, and `Gfx_LoadMultiplePalettes` plays the
common command first. The document carries them for previews; the editor
changes only colours, never destinations, counts or lists.
"""

from __future__ import annotations

import re
from typing import Any

from retrolarium_kit.platform.megadrive.asl import SourceTree


SCHEMA = "alien-soldier.palettes"
DOCUMENT = "graphics/palettes.json"
SOURCE = "src/rendering/palettes.s"
LOADER = "Gfx_LoadPalettePreservingSharedColor"
PRELUDE = "CommonPaletteCommand"
COLOURS = 64
LIST_ENTRY = re.compile(r"^\(?(?P<label>[A-Za-z_][A-Za-z0-9_]*)(?:\+\$(?P<offset>[0-9A-Fa-f]+))?\)?-" + LOADER + "$")


class PaletteError(ValueError):
    """A palette document that does not fit the source's commands."""


def read_commands(data: bytes, label: str) -> list[dict[str, Any]]:
    """A block as its chain of commands; PaletteError when it is not one."""
    commands, at = [], 0
    while at < len(data):
        if at + 2 > len(data):
            raise PaletteError(f"{label}: a command header is cut off at ${at:X}")
        destination, count = data[at], data[at + 1] + 1
        if destination % 2 or destination + 2 * count > 2 * COLOURS or at + 2 + 2 * count > len(data):
            raise PaletteError(f"{label}+${at:X} is not a palette command")
        colours = [int.from_bytes(data[at + 2 + 2 * index : at + 4 + 2 * index], "big") for index in range(count)]
        commands.append({"offset": at, "destination": destination, "colours": colours})
        at += 2 + 2 * count
    return commands


def _blocks(tree: SourceTree) -> dict[str, list[dict[str, Any]]]:
    blocks = {}
    for label in tree.labels_in(SOURCE):
        if tree.block(label).kind in {"dc.b", "binclude"}:
            blocks[label] = read_commands(tree.data(label), label)
    return blocks


def _lists(tree: SourceTree) -> list[dict[str, Any]]:
    """The offset lists: each label of offsets, split at its zero words."""
    lists = []
    for label in tree.labels_in(SOURCE):
        block = tree.block(label)
        if block.kind != "dc.w":
            continue
        runs: list[list[list[Any]]] = [[]]
        for operand in block.operands:
            entry = LIST_ENTRY.match(operand.text)
            if entry is None:
                runs.append([])
                continue
            runs[-1].append([entry["label"], int(entry["offset"] or "0", 16)])
        for index, run in enumerate(run for run in runs if run):
            lists.append({"label": label if index == 0 else f"{label}[{index}]", "commands": run})
    return lists


def export(tree: SourceTree) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "schema_version": 1,
        "prelude": PRELUDE,
        "blocks": [{"label": label, "commands": commands} for label, commands in _blocks(tree).items()],
        "lists": _lists(tree),
    }


def encode(commands: list[dict[str, Any]]) -> bytes:
    out = bytearray()
    for command in commands:
        colours = command["colours"]
        out += bytes((command["destination"], len(colours) - 1))
        for word in colours:
            out += int(word).to_bytes(2, "big")
    return bytes(out)


def apply(tree: SourceTree, document: dict[str, Any]) -> None:
    """Write the document's colours into the tree; destinations, counts and
    the lists must be the source's."""
    if document.get("schema") != SCHEMA or document.get("schema_version") != 1:
        raise PaletteError("not an Alien Soldier palette document")
    source = _blocks(tree)
    blocks = document.get("blocks")
    if not isinstance(blocks, list) or [block.get("label") for block in blocks] != list(source):
        raise PaletteError("the palette blocks differ from the source")
    if document.get("lists") != _lists(tree):
        raise PaletteError("the palette lists differ from the source")
    for block in blocks:
        label, commands = block["label"], block.get("commands")
        original = source[label]
        if not isinstance(commands, list) or len(commands) != len(original):
            raise PaletteError(f"{label}: the commands differ from the source")
        for command, old in zip(commands, original):
            colours = command.get("colours")
            if (
                command.get("offset") != old["offset"]
                or command.get("destination") != old["destination"]
                or not isinstance(colours, list)
                or len(colours) != len(old["colours"])
            ):
                raise PaletteError(f"{label}+${old['offset']:X}: only colours may change")
            if any(isinstance(word, bool) or not isinstance(word, int) or not 0 <= word <= 0xFFFF for word in colours):
                raise PaletteError(f"{label}+${old['offset']:X}: a colour is not a CRAM word")
        tree.set_data(label, encode(commands))
