"""Expand a Solomon's Key build to 64 KiB PRG (mapper 66) for larger rooms.

The original CNROM cartridge keeps every room's item and enemy stream in two
shared arenas of 1342 and 726 bytes. The expanded layout adds a second 32 KiB
PRG bank and uses GxROM (mapper 66), whose single register selects the PRG
bank in bits 4-5 and the CHR bank in bits 0-1:

* bank 1 (the second 32 KiB) is the game. Its CHR writes already carry $10-$13
  (``ChrBankSelectValues``), so under GxROM they keep bank 1 mapped.
* bank 0 is a copy of bank 1 whose room-stream, block, and audio areas hold
  the item and enemy streams instead, up to 256 bytes per stream.
* the room-load thread calls the item and enemy decoders through two
  wrappers. A wrapper disables NMI, maps bank 0 (the decoders, and everything
  they call, are identical there), runs the decoder, maps bank 1 back and
  restores PPUCTRL. The decoders never wait for a frame, so nothing else runs
  meanwhile; NMI and the audio engine never see bank 0.
* ``UpdateNmiChrBank`` keeps the consumed CHR bank in ``ChrBankRequest``
  (``ORA #$80`` instead of ``LDA #$80``) so a wrapper can keep the CHR bank.
* bank 0's reset vector maps bank 1 before the normal reset runs.

In bank 1 the old stream arenas are free; the Spawn Mirror enemy sets, which
gameplay reads from bank 1, move into the old enemy-stream arena.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .authoring import AuthoringError


class SolomonExpansionError(AuthoringError):
    """The build does not match the guarded layout the expansion patches."""


MAPPER = 66
PRG_BANK = 0x8000
STREAM_LIMIT = 256          # the decoders index a stream with Y
PPU_CTRL_SHADOW = 0x0300
PPU_CTRL = 0x2000
CHR_BANK_REQUEST = 0x7D


@dataclass(frozen=True)
class ExpansionLayout:
    chr_consume: int            # LDA #$80 in UpdateNmiChrBank
    item_call: int              # JSR LoadRoomItemsAndMetadata in RoomLoadThread
    enemy_call: int             # JSR LoadRoomEnemies
    item_decoder: int
    enemy_decoder: int
    wrapper: int                # free filler, the same address in both banks
    wrapper_end: int
    reset: int
    item_pointers: int          # low table; the high table follows 53 bytes later
    enemy_pointers: int
    mirror_set_pointers: int    # low table; the high table follows 17 bytes later
    mirror_set_region: tuple[int, int]          # bank 1, inclusive
    data_regions: tuple[tuple[int, int], ...]   # bank 0, inclusive


LAYOUTS = {
    "usa": ExpansionLayout(
        chr_consume=0x8051, item_call=0x9071, enemy_call=0x9091,
        item_decoder=0x97C8, enemy_decoder=0x961B,
        # The end of the filler: the attract demo may grow into its start.
        wrapper=0xCFAB, wrapper_end=0xCFFF, reset=0x8C00,
        item_pointers=0xEA1C, enemy_pointers=0xDCEC, mirror_set_pointers=0xDC20,
        mirror_set_region=(0xDD56, 0xE02B),
        data_regions=((0xDD56, 0xEA1B), (0xEA86, 0xFFF9)),
    ),
    "europe": ExpansionLayout(
        chr_consume=0x8051, item_call=0x9080, enemy_call=0x90A0,
        item_decoder=0x97D0, enemy_decoder=0x9623,
        wrapper=0xCF2B, wrapper_end=0xCF7F, reset=0x8C00,
        item_pointers=0xE99C, enemy_pointers=0xDC6C, mirror_set_pointers=0xDBA0,
        mirror_set_region=(0xDCD6, 0xDFAB),
        data_regions=((0xDCD6, 0xE99B), (0xEA06, 0xFFF9)),
    ),
}


def layout_for(revision_id: str) -> ExpansionLayout:
    try:
        return LAYOUTS[revision_id]
    except KeyError as exc:
        raise SolomonExpansionError(
            f"Solomon's Key {revision_id} has no expanded-ROM layout"
        ) from exc


def data_capacity(revision_id: str) -> int:
    """Bytes bank 0 holds for every room's item and enemy streams."""
    return sum(end - start + 1 for start, end in layout_for(revision_id).data_regions)


def mirror_set_capacity(revision_id: str) -> int:
    start, end = layout_for(revision_id).mirror_set_region
    return end - start + 1


def _word(value: int) -> bytes:
    return bytes((value & 0xFF, value >> 8))


def wrapper_code(layout: ExpansionLayout) -> tuple[bytes, dict[str, int]]:
    """The wrappers, their bank-select tables and the bank-0 reset stub."""
    base = layout.wrapper
    labels = {
        "wrap_items": base,
        "wrap_enemies": base + 25,
        "return": base + 50,
        "to_data": base + 68,
        "to_main": base + 72,
        "reset_stub": base + 76,
    }

    def enter(decoder: int) -> bytes:
        return b"".join((
            b"\xAD" + _word(PPU_CTRL_SHADOW),           # LDA PpuCtrlShadow
            b"\x29\x7F",                                # AND #$7F
            b"\x8D" + _word(PPU_CTRL),                  # STA PPUCTRL: NMI off
            bytes((0xA5, CHR_BANK_REQUEST)),            # LDA ChrBankRequest
            b"\x29\x03",                                # AND #$03
            b"\xA8",                                    # TAY
            b"\xB9" + _word(labels["to_data"]),         # LDA ToData,Y
            b"\x99" + _word(labels["to_data"]),         # STA ToData,Y: PRG bank 0
            b"\x20" + _word(decoder),                   # JSR decoder
            b"\x4C" + _word(labels["return"]),          # JMP Return
        ))

    leave = b"".join((
        bytes((0xA5, CHR_BANK_REQUEST)),                # LDA ChrBankRequest
        b"\x29\x03",                                    # AND #$03
        b"\xA8",                                        # TAY
        b"\xB9" + _word(labels["to_main"]),             # LDA ToMain,Y
        b"\x99" + _word(labels["to_main"]),             # STA ToMain,Y: PRG bank 1
        b"\xAD" + _word(PPU_CTRL_SHADOW),               # LDA PpuCtrlShadow
        b"\x8D" + _word(PPU_CTRL),                      # STA PPUCTRL: NMI as before
        b"\x60",                                        # RTS
    ))
    reset_stub = b"".join((
        b"\xAD" + _word(labels["to_main"]),             # LDA ToMain ($10)
        b"\x8D" + _word(labels["to_main"]),             # STA ToMain: PRG bank 1
        b"\x4C" + _word(layout.reset),                  # JMP Reset
    ))
    code = (enter(layout.item_decoder) + enter(layout.enemy_decoder) + leave
            + bytes((0x00, 0x01, 0x02, 0x03)) + bytes((0x10, 0x11, 0x12, 0x13))
            + reset_stub)
    if len(code) != 85 or labels["reset_stub"] + 9 != base + len(code):
        raise SolomonExpansionError("expansion wrapper layout drifted")
    if base + len(code) - 1 > layout.wrapper_end:
        raise SolomonExpansionError("expansion wrappers do not fit their filler")
    return code, labels


def _place(prg: bytearray, address: int, payload: bytes) -> None:
    offset = address - PRG_BANK
    prg[offset:offset + len(payload)] = payload


def _check(prg: bytes, address: int, expected: bytes, what: str) -> None:
    offset = address - PRG_BANK
    if prg[offset:offset + len(expected)] != expected:
        raise SolomonExpansionError(f"Solomon build does not match the guarded {what}")


def pack_streams(
    streams: Sequence[bytes], regions: Sequence[tuple[int, int]], label: str,
) -> list[int]:
    """CPU addresses for streams placed first-fit into the regions."""
    cursors = [start for start, _end in regions]
    addresses = []
    for index, stream in enumerate(streams):
        if len(stream) > STREAM_LIMIT:
            raise SolomonExpansionError(
                f"room {index + 1} {label} stream is {len(stream)} bytes; "
                f"the decoder reads at most {STREAM_LIMIT}"
            )
        for slot, (_start, end) in enumerate(regions):
            if cursors[slot] + len(stream) - 1 <= end:
                addresses.append(cursors[slot])
                cursors[slot] += len(stream)
                break
        else:
            raise SolomonExpansionError(
                f"the expanded ROM has no room left for room {index + 1}'s {label} stream"
            )
    return addresses


def mirror_set_changes(
    sets: Sequence[bytes], revision_id: str,
) -> tuple[tuple[int, bytes], ...]:
    """Bank-1 (CPU address, bytes) writes for relocated mirror enemy sets."""
    layout = layout_for(revision_id)
    addresses = pack_streams(sets, (layout.mirror_set_region,), "mirror enemy set")
    encoded = b"".join(sets)
    low = bytes(address & 0xFF for address in addresses)
    high = bytes(address >> 8 for address in addresses)
    return (
        (layout.mirror_set_region[0], encoded),
        (layout.mirror_set_pointers, low + high),
    )


def expand_image(
    image: bytes, revision_id: str,
    item_streams: Sequence[bytes], enemy_streams: Sequence[bytes],
) -> tuple[bytes, dict[str, object]]:
    """Turn a built 40 KiB CNROM image into the expanded mapper-66 image."""
    layout = layout_for(revision_id)
    if len(image) != 16 + 0x8000 + 0x8000 or image[:4] != b"NES\x1a" \
            or image[4] != 2 or image[5] != 4 \
            or ((image[6] >> 4) | (image[7] & 0xF0)) != 3:
        raise SolomonExpansionError("expansion needs a 32 KiB PRG, 32 KiB CHR CNROM build")
    if len(item_streams) != 53 or len(enemy_streams) != 53:
        raise SolomonExpansionError("expansion needs 53 item and enemy streams")
    prg = bytearray(image[16:16 + 0x8000])
    chr_rom = image[16 + 0x8000:]
    _check(prg, layout.chr_consume, b"\xA9\x80\x85\x7D", "CHR-bank consume")
    _check(prg, layout.item_call, b"\x20" + _word(layout.item_decoder), "item decoder call")
    _check(prg, layout.enemy_call, b"\x20" + _word(layout.enemy_decoder), "enemy decoder call")
    _check(prg, 0xFFFC, _word(layout.reset), "reset vector")

    code, labels = wrapper_code(layout)
    _place(prg, layout.chr_consume, b"\x09\x80")                     # ORA #$80
    _place(prg, layout.item_call, b"\x20" + _word(labels["wrap_items"]))
    _place(prg, layout.enemy_call, b"\x20" + _word(labels["wrap_enemies"]))
    _place(prg, layout.wrapper, code)
    bank1 = bytes(prg)

    bank0 = bytearray(bank1)
    item_addresses = pack_streams(item_streams, layout.data_regions, "item")
    regions = list(layout.data_regions)
    # Items first, then enemies after them in the same regions.
    cursors = [start for start, _end in regions]
    for address, stream in zip(item_addresses, item_streams):
        _place(bank0, address, stream)
        for slot, (start, end) in enumerate(regions):
            if start <= address <= end:
                cursors[slot] = max(cursors[slot], address + len(stream))
    remaining = [(cursors[slot], end) for slot, (_start, end) in enumerate(regions)]
    enemy_addresses = pack_streams(enemy_streams, remaining, "enemy")
    for address, stream in zip(enemy_addresses, enemy_streams):
        _place(bank0, address, stream)
    for table, addresses in ((layout.item_pointers, item_addresses),
                             (layout.enemy_pointers, enemy_addresses)):
        _place(bank0, table, bytes(address & 0xFF for address in addresses))
        _place(bank0, table + 53, bytes(address >> 8 for address in addresses))
    _place(bank0, 0xFFFC, _word(labels["reset_stub"]))

    header = bytearray(image[:16])
    header[4] = 4                                   # 64 KiB PRG in 16 KiB units
    header[5] = 4                                   # 32 KiB CHR
    header[6] = ((MAPPER & 0x0F) << 4) | (header[6] & 0x0F)
    header[7] = (MAPPER & 0xF0) | (header[7] & 0x0F)
    output = bytes(header) + bytes(bank0) + bank1 + chr_rom
    item_bytes = sum(map(len, item_streams))
    enemy_bytes = sum(map(len, enemy_streams))
    return output, {
        "mapper": MAPPER,
        "prg_bytes": 0x10000,
        "item_stream_bytes": item_bytes,
        "enemy_stream_bytes": enemy_bytes,
        "data_capacity_bytes": data_capacity(revision_id),
        "free_bytes": data_capacity(revision_id) - item_bytes - enemy_bytes,
    }
