from __future__ import annotations

from pathlib import Path
import sys


# The kit from this checkout, for every test package that imports helpers
# first (each tests/<folder>/__init__.py does); the kit finds the core.
SOURCE = Path(__file__).resolve().parents[1] / "src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

def nrom16_image() -> bytes:
    header = b"NES\x1a" + bytes((1, 1, 0, 0)) + bytes(8)
    prg = bytearray(0x4000)
    prg[0:6] = bytes.fromhex("2C 02 20 10 FB 60")
    prg[-6:] = bytes.fromhex("00 C0 00 C0 00 00")
    chr_data = bytes((index & 0xFF for index in range(0x2000)))
    return header + prg + chr_data


def write_facts(path: Path) -> None:
    path.write_text(
        "block\tspace\taddress\tlength\tbytes\tmnemonic\toperands\tflows\tflow_type\tsymbol\tfunction\n"
        "PRG0\tPRG0\tC000\t3\t2C 02 20\tBIT\t0x2002\t\tFALL_THROUGH\t\tFUN_PRG0__c000\n"
        "PRG0\tPRG0\tC003\t2\t10 FB\tBPL\tPRG0::0xc000\tPRG0::c000\tCONDITIONAL_JUMP\t\t\n"
        "PRG0\tPRG0\tC005\t1\t60\tRTS\t\t\tTERMINATOR\t\t\n",
        encoding="utf-8",
        newline="\n",
    )


# A SNES cartridge image for the analysis tests.
def cartridge(
    mapping: str,
    *,
    size: int = 0x20000,
    copier_header: bool = False,
    map_mode_byte: int | None = None,
    chipset: int = 0,
    ram_size_code: int = 0,
    reset: int = 0x9000,
) -> bytes:
    from retrolarium_core.snes import HIROM_HEADER_OFFSET, LOROM_HEADER_OFFSET, cartridge_checksum

    data = bytearray([0xFF] * size)
    header_offset = (
        LOROM_HEADER_OFFSET if mapping == "lorom" else HIROM_HEADER_OFFSET
    )
    header = memoryview(data)[header_offset : header_offset + 0x40]
    header[:] = bytes(0x40)
    header[0:21] = b"RETROLARIUM SNES TEST".ljust(21)
    header[0x15] = map_mode_byte if map_mode_byte is not None else (
        0x30 if mapping == "lorom" else 0x21
    )
    header[0x16] = chipset
    header[0x17] = size.bit_length() - 1 - 10
    header[0x18] = ram_size_code
    header[0x19] = 1
    header[0x1A] = 0x33
    header[0x1B] = 0
    for offset, value in (
        (0x24, 0x9020),
        (0x2A, 0x9010),
        (0x2E, 0x9030),
        (0x34, 0x9020),
        (0x3A, 0x9010),
        (0x3C, reset),
        (0x3E, 0x9030),
    ):
        header[offset : offset + 2] = value.to_bytes(2, "little")
    header.release()
    checksum = (cartridge_checksum(data) + 0x1FE) & 0xFFFF
    data[header_offset + 0x1C : header_offset + 0x1E] = (
        checksum ^ 0xFFFF
    ).to_bytes(2, "little")
    data[header_offset + 0x1E : header_offset + 0x20] = checksum.to_bytes(
        2, "little"
    )
    container = bytes(data)
    if copier_header:
        container = bytes([0x5A] * 0x200) + container
    return container


def patched_manifest(value):
    """Stand in for the disassembly manifest a Studio project names, where the
    project is created (studio.create) and where it is loaded (studio.project)."""
    from contextlib import ExitStack
    from unittest.mock import patch

    stack = ExitStack()
    for module in ("create", "project"):
        stack.enter_context(patch(
            f"retrolarium_kit.studio.{module}.load_disassembly_manifest", return_value=value))
    return stack


def game_repository(name: str) -> Path:
    """A game disassembly (smb1_src, ...) for the tests that read its data.

    RETROLARIUM_GAME_SOURCES names the folder holding them; otherwise an
    oranguthang folder beside one of the folders above the kit is used. The
    path may not exist: the tests that need it skip.
    """
    import os

    configured = os.environ.get("RETROLARIUM_GAME_SOURCES")
    if configured:
        return Path(configured) / name
    for folder in Path(__file__).resolve().parents[1:]:
        candidate = folder.parent / "oranguthang"
        if candidate.is_dir():
            return candidate / name
    return Path(__file__).resolve().parents[1] / "oranguthang-not-found" / name


def fds_side() -> bytes:
    """A Disk System side with two files, for the FDS and project tests."""
    image = bytearray(65_500)
    image[0] = 1
    image[56:58] = bytes((2, 2))
    position = 58
    for number, identifier, name, address, payload in (
        (0, 3, b"SMMAIN3 ", 0x6000, b"AB"),
        (1, 4, b"SMMAIN4 ", 0xA000, b"CDE"),
    ):
        header = bytearray(16)
        header[0:3] = bytes((3, number, identifier))
        header[3:11] = name
        header[11:13] = address.to_bytes(2, "little")
        header[13:15] = len(payload).to_bytes(2, "little")
        image[position:position + 16] = header
        position += 16
        image[position] = 4
        position += 1
        image[position:position + len(payload)] = payload
        position += len(payload)
    return bytes(image)
