"""Alien Soldier's asset lists: what a screen or stage loads, in order.

An asset list is a run of eight-byte records, `dc.w command`, `dc.l source`,
`dc.w destination`, ended by a negative word; `LoadObjData` reads them and
the command indexes `LoadObjDataHandlers`. A label whose directives do not
read as a whole list is something else and is left alone.
"""

from __future__ import annotations

from dataclasses import dataclass

from retrolarium_kit.platform.megadrive.asl import SourceTree, parse_number


# LoadObjDataHandlers, by command.
HANDLERS = (
    "LoadFuncToRAM",
    "Gfx_LoadDataToVRAM",
    "Gfx_LoadAndDecompTiles",
    "Gfx_LoadCompressedGfx",
    "LoadFuncToRAM",
    "Gfx_LoadDataToVRAM",
    "LoadCompressedToRAM",
    "LoadCompressedToVRAM",
)
TILE_CODEC_COMMANDS = (2, 3)
LZSS_TO_RAM = 6
LZSS_TO_VRAM = 7
# Stage_ExpandAndSubmitTileAssetCommands turns a compact index into an entry
# of this table and submits it as an LZSS_TO_VRAM command.
SHARED_TILE_SOURCES = "Stage_SharedTileSourceTable"


@dataclass(frozen=True)
class AssetCommand:
    command: int
    source: str  # a label
    destination: int  # a VRAM address, or a RAM offset sign-extended to $FFFFxxxx

    @property
    def ram_offset(self) -> int:
        return self.destination & 0xFFFF


def _read_list(tree: SourceTree, label: str) -> list[AssetCommand] | None:
    items = [
        (directive.kind, operand.text) for directive in tree.block(label).directives for operand in directive.operands
    ]
    commands, at = [], 0
    while at < len(items):
        kind, text = items[at]
        value = parse_number(text)
        if kind != "dc.w" or value is None:
            return None
        if value & 0x8000 or value < 0:
            return commands if commands and at + 1 == len(items) else None
        if value >= len(HANDLERS) or at + 2 >= len(items) or items[at + 1][0] != "dc.l" or items[at + 2][0] != "dc.w":
            return None
        destination = parse_number(items[at + 2][1])
        if destination is None:
            return None
        commands.append(AssetCommand(value, items[at + 1][1], destination))
        at += 3
    # A list may end on the first word of the label after it, which is also
    # that label's own data (Options_AssetLoadDescriptors does).
    following = tree.order.index(label) + 1
    if commands and following < len(tree.order):
        operands = tree.block(tree.order[following]).operands
        value = parse_number(operands[0].text) if operands else None
        if value is not None and (value & 0x8000 or value < 0):
            return commands
    return None


def asset_lists(tree: SourceTree) -> dict[str, list[AssetCommand]]:
    """Every label that reads as a whole asset list."""
    found = {}
    for label in tree.order:
        if tree.block(label).kind == "mixed":
            commands = _read_list(tree, label)
            if commands:
                found[label] = commands
    return found


def pointer_table(tree: SourceTree, label: str) -> list[str]:
    """The labels a `dc.l` table names, in order."""
    block = tree.block(label)
    return [
        operand.text for directive in block.directives if directive.kind == "dc.l" for operand in directive.operands
    ]
