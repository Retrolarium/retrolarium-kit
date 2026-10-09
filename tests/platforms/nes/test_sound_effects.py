from __future__ import annotations

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from retrolarium_kit.platform.nes.sound_effects import (
    Apu, Bus, Cpu6502, SoundEffectError, Table, effect_plan, play, source_tables,
)


def _cpu(program: bytes, at: int = 0x8000) -> tuple[Cpu6502, Bus]:
    prg = bytearray(0x8000)
    prg[at - 0x8000:at - 0x8000 + len(program)] = program
    bus = Bus(bytes(prg))
    return Cpu6502(bus.read, bus.write), bus


class Cpu6502Tests(unittest.TestCase):
    def test_adc_sets_carry_and_overflow(self) -> None:
        # CLC / LDA #$50 / ADC #$50 / RTS: $A0, overflow, no carry.
        cpu, _bus = _cpu(bytes((0x18, 0xA9, 0x50, 0x69, 0x50, 0x60)))
        cpu.call(0x8000)
        self.assertEqual(0xA0, cpu.a)
        self.assertTrue(cpu.p & 0x40)
        self.assertFalse(cpu.p & 0x01)

    def test_sbc_borrows(self) -> None:
        # SEC / LDA #$10 / SBC #$20 / RTS: $F0 with the carry clear.
        cpu, _bus = _cpu(bytes((0x38, 0xA9, 0x10, 0xE9, 0x20, 0x60)))
        cpu.call(0x8000)
        self.assertEqual(0xF0, cpu.a)
        self.assertFalse(cpu.p & 0x01)

    def test_loop_subroutine_and_indirect_store(self) -> None:
        # LDX #3 / loop: JSR inc / DEX / BNE loop / LDA #$00 / STA $10 /
        # LDA #$02 / STA $11 / LDY #1 / LDA $0300 / STA ($10),Y / RTS;
        # inc: INC $0300 / RTS
        program = bytes((0xA2, 0x03, 0x20, 0x20, 0x80, 0xCA, 0xD0, 0xFA,
                         0xA9, 0x00, 0x85, 0x10, 0xA9, 0x02, 0x85, 0x11,
                         0xA0, 0x01, 0xAD, 0x00, 0x03, 0x91, 0x10, 0x60))
        program += bytes(0x20 - len(program)) + bytes((0xEE, 0x00, 0x03, 0x60))
        cpu, bus = _cpu(program)
        cpu.call(0x8000)
        self.assertEqual(3, bus.ram[0x300])
        self.assertEqual(3, bus.ram[0x201])

    def test_indirect_jump_wraps_within_its_page(self) -> None:
        # JMP ($02FF) reads the high byte from $0200, not $0300.
        cpu, bus = _cpu(bytes((0x6C, 0xFF, 0x02)))
        bus.ram[0x2FF], bus.ram[0x200], bus.ram[0x300] = 0x10, 0x90, 0x80
        cpu.pc = 0x8000
        cpu.step()
        self.assertEqual(0x9010, cpu.pc)

    def test_apu_writes_are_recorded(self) -> None:
        cpu, bus = _cpu(bytes((0xA9, 0x0F, 0x8D, 0x15, 0x40, 0x60)))
        cpu.call(0x8000)
        self.assertEqual([(0x4015, 0x0F)], bus.writes)

    def test_unknown_effects_are_refused(self) -> None:
        for key in ("smb:square3:1", "smb:square1:3", "castle:6", "doraemon:1:15", "pacman:1"):
            with self.assertRaises(SoundEffectError):
                effect_plan(key, bytes(0x8000))


class ApuTests(unittest.TestCase):
    def test_a_pulse_note_sounds_until_its_length_runs_out(self) -> None:
        apu = Apu()
        for address, value in ((0x4015, 0x01), (0x4000, 0x9F), (0x4002, 0xFD), (0x4003, 0x18)):
            apu.write(address, value)
        samples = apu.render(29780.5)
        audible, frequency, volume = apu.channel_state()[0]
        self.assertTrue(audible)
        self.assertEqual(15, volume)
        self.assertAlmostEqual(440.0, frequency, delta=1.0)
        self.assertGreater(max(samples) - min(samples), 0.05)
        # Length index 3 is two half frames: silent after the next frame.
        apu.render(29780.5)
        apu.render(29780.5)
        self.assertFalse(apu.channel_state()[0][0])


ROOT = Path("D:/Projects/oranguthang")
SMB = ROOT / "smb1_src/Super Mario Bros. (JU) [!].nes"
CASTLE = ROOT / "castle_excellent_src/Castle Excellent (J).nes"
DORAEMON = ROOT / "doraemon_src/Doraemon (J) (PRG0) [!].nes"


def _notes(result: dict, channel: str) -> list[tuple[int, int, int]]:
    for c in result["channels"]:
        if c["name"] == channel:
            return [(n["start"], n["length"], n["pitch"]) for n in c["notes"]]
    return []


class RomEffectTests(unittest.TestCase):
    @unittest.skipUnless(SMB.is_file(), "the Super Mario Bros. ROM is not available")
    def test_smb_coin_and_extra_life(self) -> None:
        prg = SMB.read_bytes()[16:16 + 0x8000]
        # The coin: B5 then a held E6; the 1-up: E G E' C' D' G'.
        self.assertEqual([83, 88], [p for _s, _l, p in _notes(play("smb:square2:1", prg), "Pulse 2")])
        self.assertEqual([88, 91, 100, 96, 98, 103], [p for _s, _l, p in _notes(play("smb:square2:64", prg), "Pulse 2")])

    @unittest.skipUnless(SMB.is_file(), "the Super Mario Bros. ROM is not available")
    def test_smb_notes_name_their_table_bytes(self) -> None:
        data = SMB.read_bytes()
        tables = source_tables(data)
        self.assertIn(Table("extra_life", 0xF4D4, 6, "note"), tables)
        result = play("smb:square2:64", data[16:16 + 0x8000], tables=tables)
        notes = result["channels"][1]["notes"]
        # The 1-up reads its table from the end; each byte is one note.
        self.assertEqual([5, 4, 3, 2, 1, 0], [n["source"]["index"] for n in notes])
        extra_life = next(t for t in result["tables"] if t["id"] == "extra_life")
        choices = {c["value"]: c["pitch"] for c in extra_life["choices"]}
        self.assertEqual(notes[0]["pitch"], choices[notes[0]["source"]["value"]])

    @unittest.skipUnless(CASTLE.is_file(), "the Castle Excellent ROM is not available")
    def test_castle_effects_sound(self) -> None:
        prg = CASTLE.read_bytes()[16:16 + 0x8000]
        for number in range(1, 6):
            result = play(f"castle:{number}", prg)
            self.assertGreater(result["frames"], 0, number)

    @unittest.skipUnless(DORAEMON.is_file(), "the Doraemon ROM is not available")
    def test_doraemon_triangle_descent_retriggers_every_frame(self) -> None:
        prg = DORAEMON.read_bytes()[16:16 + 0x20000]
        # $4008 = $01 with a $400B write each frame: the triangle sounds a
        # quarter frame per frame while its period falls.
        notes = _notes(play("doraemon:0:17", prg), "Triangle")
        self.assertEqual((0, 4, 81), notes[0])
        self.assertEqual(sorted(p for _s, _l, p in notes), [p for _s, _l, p in notes])


if __name__ == "__main__":
    unittest.main()
