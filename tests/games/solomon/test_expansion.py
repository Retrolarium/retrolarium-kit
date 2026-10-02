from __future__ import annotations

from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from retrolarium_kit.authoring import AuthoringError
from retrolarium_kit.solomon_expansion import (
    LAYOUTS,
    data_capacity,
    expand_image,
    mirror_set_changes,
    wrapper_code,
)


def cnrom_image() -> bytes:
    """A 32 KiB PRG / 32 KiB CHR CNROM image with the USA patch sites."""
    layout = LAYOUTS["usa"]
    header = bytearray(b"NES\x1a" + bytes((2, 4, 0x30, 0)) + bytes(8))
    prg = bytearray(0x8000)

    def put(address: int, payload: bytes) -> None:
        prg[address - 0x8000:address - 0x8000 + len(payload)] = payload

    put(layout.chr_consume, b"\xA9\x80\x85\x7D")
    put(layout.item_call, b"\x20" + layout.item_decoder.to_bytes(2, "little"))
    put(layout.enemy_call, b"\x20" + layout.enemy_decoder.to_bytes(2, "little"))
    put(0xFFFC, layout.reset.to_bytes(2, "little"))
    chr_rom = bytes(range(256)) * 128
    return bytes(header) + bytes(prg) + chr_rom


def bank(output: bytes, index: int) -> bytes:
    return output[16 + index * 0x8000:16 + (index + 1) * 0x8000]


def at(prg: bytes, address: int, count: int) -> bytes:
    return prg[address - 0x8000:address - 0x8000 + count]


class SolomonExpansionTests(unittest.TestCase):
    def test_expanded_image_maps_the_streams_into_bank_zero(self) -> None:
        layout = LAYOUTS["usa"]
        items = [bytes((room, 0xE0)) * 3 for room in range(53)]
        enemies = [bytes((0x01, 0x70 + (room & 3), room, 0x00)) for room in range(53)]
        output, report = expand_image(cnrom_image(), "usa", items, enemies)

        # iNES: mapper 66, 64 KiB PRG, the CHR unchanged after both banks.
        self.assertEqual(len(output), 16 + 0x10000 + 0x8000)
        self.assertEqual((output[4], output[5], output[6], output[7]), (4, 4, 0x20, 0x40))
        self.assertEqual(output[16 + 0x10000:], cnrom_image()[16 + 0x8000:])

        data, game = bank(output, 0), bank(output, 1)
        code, labels = wrapper_code(layout)
        # The wrappers sit at the same address in both banks; the game calls
        # them and keeps the consumed CHR bank.
        self.assertEqual(at(data, layout.wrapper, len(code)), code)
        self.assertEqual(at(game, layout.wrapper, len(code)), code)
        self.assertEqual(at(game, layout.chr_consume, 2), b"\x09\x80")
        self.assertEqual(at(game, layout.item_call, 3), b"\x20" + labels["wrap_items"].to_bytes(2, "little"))
        self.assertEqual(at(game, layout.enemy_call, 3), b"\x20" + labels["wrap_enemies"].to_bytes(2, "little"))
        # Bank-select tables: writes from bank 1 carry $00-$03, from bank 0 $10-$13.
        self.assertEqual(at(game, labels["to_data"], 4), bytes((0, 1, 2, 3)))
        self.assertEqual(at(data, labels["to_main"], 4), bytes((0x10, 0x11, 0x12, 0x13)))
        # Bank 0 resets into the stub; bank 1 keeps the game's reset.
        self.assertEqual(at(data, 0xFFFC, 2), labels["reset_stub"].to_bytes(2, "little"))
        self.assertEqual(at(game, 0xFFFC, 2), layout.reset.to_bytes(2, "little"))

        # Every pointer of bank 0 reaches its stream.
        for room in range(53):
            for table, streams in ((layout.item_pointers, items), (layout.enemy_pointers, enemies)):
                address = at(data, table + room, 1)[0] | (at(data, table + 53 + room, 1)[0] << 8)
                self.assertEqual(at(data, address, len(streams[room])), streams[room])
                self.assertTrue(any(start <= address <= end for start, end in layout.data_regions))
        self.assertEqual(report["free_bytes"], data_capacity("usa") - 6 * 53 - 4 * 53)

    def test_limits_are_refused(self) -> None:
        empty = [b"\x00"] * 53
        with self.assertRaisesRegex(AuthoringError, "reads at most 256"):
            expand_image(cnrom_image(), "usa", [bytes(257)] + empty[1:], empty)
        with self.assertRaisesRegex(AuthoringError, "no room left"):
            expand_image(cnrom_image(), "usa", [bytes(250)] * 53, empty)
        with self.assertRaisesRegex(AuthoringError, "guarded"):
            broken = bytearray(cnrom_image())
            broken[16 + LAYOUTS["usa"].chr_consume - 0x8000] = 0xEA
            expand_image(bytes(broken), "usa", empty, empty)

    def test_mirror_sets_move_into_the_freed_bank_one_arena(self) -> None:
        layout = LAYOUTS["usa"]
        sets = [bytes((0x78, 0x79, 0x90))] * 17
        (start, payload), (table, pointers) = mirror_set_changes(sets, "usa")
        self.assertEqual((start, table), (layout.mirror_set_region[0], layout.mirror_set_pointers))
        self.assertEqual(payload, b"".join(sets))
        self.assertEqual(pointers[0] | (pointers[17] << 8), start)
        self.assertEqual(pointers[1] | (pointers[18] << 8), start + 3)


if __name__ == "__main__":
    unittest.main()
