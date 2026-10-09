"""NES sound effects heard through the games' own drivers.

Sound effects in Super Mario Bros., Castle Excellent and Doraemon are code,
not note streams: each one is a start routine plus a per-frame update that
writes the APU registers. To hear one, a small 6502 interpreter runs the
game's own routines from the cartridge image frame by frame and records the
APU register writes; an APU model turns them into PCM and into per-channel
notes for the studio's piano roll.

An effect key names the game and the request:

- ``smb:square1:N`` / ``smb:square2:N`` / ``smb:noise:N``: the bit value N
  written to that sound queue ($FF, $FE, $FD) before SoundEngine runs.
- ``castle-excellent:N``: Castle Excellent's start routine N (1-5), then
  UpdateSoundEffects every frame.
- ``doraemon:D:N``: request N queued through driver D's QueueEffect (World 1,
  World 2, World 3, shell banks 0-3), then its UpdateEffects every frame.
"""

from __future__ import annotations

from dataclasses import dataclass
import io
import json
import math
from pathlib import Path
import struct
from typing import Callable
import wave

from retrolarium_kit.core.authoring import AuthoringError


class SoundEffectError(AuthoringError):
    """A sound effect cannot be found or played."""


CPU_CLOCK = 1_789_773.0
FRAME_CYCLES = CPU_CLOCK / 60.0988
SAMPLE_RATE = 44_100
MAX_FRAMES = 300            # five seconds: some effects repeat until replaced
SILENT_TAIL = 45            # frames of silence that end an effect
STEP_LIMIT = 200_000        # instructions per routine call
CHANNEL_NAMES = ("Pulse 1", "Pulse 2", "Triangle", "Noise")


# --- 6502 -------------------------------------------------------------------

C, Z, I, D, B, U, V, N = 1, 2, 4, 8, 16, 32, 64, 128
_RETURN = 0x0001            # where a call's sentinel RTS lands

_ALU = {0x00: "ORA", 0x20: "AND", 0x40: "EOR", 0x60: "ADC", 0x80: "STA", 0xA0: "LDA", 0xC0: "CMP", 0xE0: "SBC"}
_ALU_MODES = {0x09: "imm", 0x05: "zp", 0x15: "zpx", 0x0D: "abs", 0x1D: "absx", 0x19: "absy", 0x01: "indx", 0x11: "indy"}
_SHIFT = {0x00: "ASL", 0x20: "ROL", 0x40: "LSR", 0x60: "ROR"}
_SHIFT_MODES = {0x0A: "acc", 0x06: "zp", 0x16: "zpx", 0x0E: "abs", 0x1E: "absx"}


def _opcodes() -> dict[int, tuple[str, str]]:
    table: dict[int, tuple[str, str]] = {}
    for base, name in _ALU.items():
        for offset, mode in _ALU_MODES.items():
            if name == "STA" and mode == "imm":
                continue
            table[base + offset] = (name, mode)
    for base, name in _SHIFT.items():
        for offset, mode in _SHIFT_MODES.items():
            table[base + offset] = (name, mode)
    for base, name in ((0xC0, "DEC"), (0xE0, "INC")):
        for offset, mode in ((0x06, "zp"), (0x16, "zpx"), (0x0E, "abs"), (0x1E, "absx")):
            table[base + offset] = (name, mode)
    table.update({
        0x10: ("BPL", "rel"), 0x30: ("BMI", "rel"), 0x50: ("BVC", "rel"), 0x70: ("BVS", "rel"),
        0x90: ("BCC", "rel"), 0xB0: ("BCS", "rel"), 0xD0: ("BNE", "rel"), 0xF0: ("BEQ", "rel"),
        0x24: ("BIT", "zp"), 0x2C: ("BIT", "abs"),
        0x00: ("BRK", "imp"), 0x18: ("CLC", "imp"), 0xD8: ("CLD", "imp"), 0x58: ("CLI", "imp"),
        0xB8: ("CLV", "imp"), 0x38: ("SEC", "imp"), 0xF8: ("SED", "imp"), 0x78: ("SEI", "imp"),
        0xE0: ("CPX", "imm"), 0xE4: ("CPX", "zp"), 0xEC: ("CPX", "abs"),
        0xC0: ("CPY", "imm"), 0xC4: ("CPY", "zp"), 0xCC: ("CPY", "abs"),
        0xCA: ("DEX", "imp"), 0x88: ("DEY", "imp"), 0xE8: ("INX", "imp"), 0xC8: ("INY", "imp"),
        0x4C: ("JMP", "abs"), 0x6C: ("JMP", "ind"), 0x20: ("JSR", "abs"),
        0xA2: ("LDX", "imm"), 0xA6: ("LDX", "zp"), 0xB6: ("LDX", "zpy"), 0xAE: ("LDX", "abs"), 0xBE: ("LDX", "absy"),
        0xA0: ("LDY", "imm"), 0xA4: ("LDY", "zp"), 0xB4: ("LDY", "zpx"), 0xAC: ("LDY", "abs"), 0xBC: ("LDY", "absx"),
        0xEA: ("NOP", "imp"), 0x48: ("PHA", "imp"), 0x08: ("PHP", "imp"), 0x68: ("PLA", "imp"), 0x28: ("PLP", "imp"),
        0x40: ("RTI", "imp"), 0x60: ("RTS", "imp"),
        0x86: ("STX", "zp"), 0x96: ("STX", "zpy"), 0x8E: ("STX", "abs"),
        0x84: ("STY", "zp"), 0x94: ("STY", "zpx"), 0x8C: ("STY", "abs"),
        0xAA: ("TAX", "imp"), 0xA8: ("TAY", "imp"), 0xBA: ("TSX", "imp"), 0x8A: ("TXA", "imp"),
        0x9A: ("TXS", "imp"), 0x98: ("TYA", "imp"),
    })
    return table


OPCODES = _opcodes()
_BRANCHES = {"BPL": (N, False), "BMI": (N, True), "BVC": (V, False), "BVS": (V, True),
             "BCC": (C, False), "BCS": (C, True), "BNE": (Z, False), "BEQ": (Z, True)}


class Cpu6502:
    """The documented 6502 instruction set (no decimal mode, as on the NES)."""

    def __init__(self, read: Callable[[int], int], write: Callable[[int, int], None]) -> None:
        self.read = read
        self.write = write
        self.a = self.x = self.y = 0
        self.s = 0xFD
        self.p = I | U
        self.pc = 0

    def _word(self, address: int) -> int:
        return self.read(address & 0xFFFF) | self.read((address + 1) & 0xFFFF) << 8

    def _push(self, value: int) -> None:
        self.write(0x100 | self.s, value & 0xFF)
        self.s = (self.s - 1) & 0xFF

    def _pull(self) -> int:
        self.s = (self.s + 1) & 0xFF
        return self.read(0x100 | self.s)

    def _nz(self, value: int) -> int:
        value &= 0xFF
        self.p = (self.p & ~(N | Z)) | (value & N) | (Z if value == 0 else 0)
        return value

    def _address(self, mode: str) -> int:
        pc = self.pc
        if mode in ("imm", "rel"):
            self.pc = (pc + 1) & 0xFFFF
            return pc
        if mode == "zp":
            self.pc = (pc + 1) & 0xFFFF
            return self.read(pc)
        if mode == "zpx":
            self.pc = (pc + 1) & 0xFFFF
            return (self.read(pc) + self.x) & 0xFF
        if mode == "zpy":
            self.pc = (pc + 1) & 0xFFFF
            return (self.read(pc) + self.y) & 0xFF
        if mode == "indx":
            self.pc = (pc + 1) & 0xFFFF
            zp = (self.read(pc) + self.x) & 0xFF
            return self.read(zp) | self.read((zp + 1) & 0xFF) << 8
        if mode == "indy":
            self.pc = (pc + 1) & 0xFFFF
            zp = self.read(pc)
            return ((self.read(zp) | self.read((zp + 1) & 0xFF) << 8) + self.y) & 0xFFFF
        self.pc = (pc + 2) & 0xFFFF
        base = self._word(pc)
        if mode == "abs":
            return base
        if mode == "absx":
            return (base + self.x) & 0xFFFF
        if mode == "absy":
            return (base + self.y) & 0xFFFF
        if mode == "ind":
            # The page-wrap bug of JMP ($xxFF).
            return self.read(base) | self.read((base & 0xFF00) | ((base + 1) & 0xFF)) << 8
        raise SoundEffectError(f"unknown addressing mode {mode}")

    def _compare(self, register: int, value: int) -> None:
        result = (register - value) & 0x1FF
        self.p = (self.p & ~C) | (C if register >= value else 0)
        self._nz(result)

    def _adc(self, value: int) -> None:
        total = self.a + value + (self.p & C)
        overflow = (~(self.a ^ value) & (self.a ^ total) & 0x80) != 0
        self.p = (self.p & ~(C | V)) | (C if total > 0xFF else 0) | (V if overflow else 0)
        self.a = self._nz(total)

    def step(self) -> None:
        opcode = self.read(self.pc)
        entry = OPCODES.get(opcode)
        if entry is None:
            raise SoundEffectError(f"unsupported opcode ${opcode:02X} at ${self.pc:04X}")
        name, mode = entry
        self.pc = (self.pc + 1) & 0xFFFF
        if mode in ("imp", "acc"):
            address = -1
        else:
            address = self._address(mode)
        if name in _BRANCHES:
            flag, wanted = _BRANCHES[name]
            offset = self.read(address)
            if bool(self.p & flag) == wanted:
                self.pc = (self.pc + (offset - 256 if offset & 0x80 else offset)) & 0xFFFF
            return
        if name == "LDA":
            self.a = self._nz(self.read(address))
        elif name == "LDX":
            self.x = self._nz(self.read(address))
        elif name == "LDY":
            self.y = self._nz(self.read(address))
        elif name == "STA":
            self.write(address, self.a)
        elif name == "STX":
            self.write(address, self.x)
        elif name == "STY":
            self.write(address, self.y)
        elif name == "ORA":
            self.a = self._nz(self.a | self.read(address))
        elif name == "AND":
            self.a = self._nz(self.a & self.read(address))
        elif name == "EOR":
            self.a = self._nz(self.a ^ self.read(address))
        elif name == "ADC":
            self._adc(self.read(address))
        elif name == "SBC":
            self._adc(self.read(address) ^ 0xFF)
        elif name == "CMP":
            self._compare(self.a, self.read(address))
        elif name == "CPX":
            self._compare(self.x, self.read(address))
        elif name == "CPY":
            self._compare(self.y, self.read(address))
        elif name == "BIT":
            value = self.read(address)
            self.p = (self.p & ~(N | V | Z)) | (value & (N | V)) | (Z if (self.a & value) == 0 else 0)
        elif name in ("ASL", "LSR", "ROL", "ROR"):
            value = self.a if mode == "acc" else self.read(address)
            carry = self.p & C
            if name == "ASL":
                out, value = value >> 7, value << 1
            elif name == "LSR":
                out, value = value & 1, value >> 1
            elif name == "ROL":
                out, value = value >> 7, (value << 1) | carry
            else:
                out, value = value & 1, (value >> 1) | (carry << 7)
            self.p = (self.p & ~C) | (C if out else 0)
            value = self._nz(value)
            if mode == "acc":
                self.a = value
            else:
                self.write(address, value)
        elif name == "INC":
            self.write(address, self._nz(self.read(address) + 1))
        elif name == "DEC":
            self.write(address, self._nz(self.read(address) - 1))
        elif name == "INX":
            self.x = self._nz(self.x + 1)
        elif name == "INY":
            self.y = self._nz(self.y + 1)
        elif name == "DEX":
            self.x = self._nz(self.x - 1)
        elif name == "DEY":
            self.y = self._nz(self.y - 1)
        elif name == "TAX":
            self.x = self._nz(self.a)
        elif name == "TAY":
            self.y = self._nz(self.a)
        elif name == "TXA":
            self.a = self._nz(self.x)
        elif name == "TYA":
            self.a = self._nz(self.y)
        elif name == "TSX":
            self.x = self._nz(self.s)
        elif name == "TXS":
            self.s = self.x
        elif name == "PHA":
            self._push(self.a)
        elif name == "PHP":
            self._push(self.p | B | U)
        elif name == "PLA":
            self.a = self._nz(self._pull())
        elif name == "PLP":
            self.p = (self._pull() & ~B) | U
        elif name == "JMP":
            self.pc = address
        elif name == "JSR":
            return_to = (self.pc - 1) & 0xFFFF
            self._push(return_to >> 8)
            self._push(return_to)
            self.pc = address
        elif name == "RTS":
            low = self._pull()
            self.pc = ((self._pull() << 8 | low) + 1) & 0xFFFF
        elif name == "RTI":
            self.p = (self._pull() & ~B) | U
            low = self._pull()
            self.pc = self._pull() << 8 | low
        elif name in ("CLC", "SEC", "CLD", "SED", "CLI", "SEI", "CLV"):
            flag = {"CLC": C, "SEC": C, "CLD": D, "SED": D, "CLI": I, "SEI": I, "CLV": V}[name]
            self.p = self.p | flag if name.startswith("SE") else self.p & ~flag
        elif name == "NOP":
            pass
        elif name == "BRK":
            raise SoundEffectError(f"BRK at ${(self.pc - 1) & 0xFFFF:04X}")

    def call(self, address: int, *, a: int = 0, x: int = 0, y: int = 0) -> None:
        """Runs a subroutine until it returns to the caller."""
        self.a, self.x, self.y = a & 0xFF, x & 0xFF, y & 0xFF
        self._push((_RETURN - 1) >> 8)
        self._push(_RETURN - 1)
        self.pc = address
        for _ in range(STEP_LIMIT):
            if self.pc == _RETURN:
                return
            self.step()
        raise SoundEffectError(f"the routine at ${address:04X} did not return")


class Bus:
    """CPU memory: 2 KiB RAM, work RAM at $6000 and one 32 KiB PRG window;
    APU register writes are recorded."""

    def __init__(self, prg: bytes) -> None:
        if len(prg) == 0x4000:
            prg = prg * 2
        if len(prg) != 0x8000:
            raise SoundEffectError("a PRG window is 16 or 32 KiB")
        self.prg = prg
        self.ram = bytearray(0x800)
        self.work = bytearray(0x2000)
        self.writes: list[tuple[int, int]] = []
        # Effect tables whose reads are recorded: (id, CPU address, size).
        self.watch: tuple[tuple[str, int, int], ...] = ()
        self.reads: list[tuple[str, int]] = []

    def read(self, address: int) -> int:
        if address < 0x2000:
            return self.ram[address & 0x7FF]
        if address >= 0x8000:
            for name, start, size in self.watch:
                if start <= address < start + size:
                    self.reads.append((name, address - start))
            return self.prg[address - 0x8000]
        if address >= 0x6000:
            return self.work[address - 0x6000]
        return 0

    def write(self, address: int, value: int) -> None:
        if address < 0x2000:
            self.ram[address & 0x7FF] = value
        elif 0x6000 <= address < 0x8000:
            self.work[address - 0x6000] = value
        elif 0x4000 <= address <= 0x4017 and address not in (0x4014, 0x4016):
            self.writes.append((address, value))


# --- the games' effects ------------------------------------------------------

@dataclass(frozen=True)
class Action:
    kind: str               # "poke" or "call"
    address: int
    value: int = 0          # poke value / call A


@dataclass(frozen=True)
class EffectPlan:
    bank: int               # 32 KiB PRG window
    start: tuple[Action, ...]
    service: tuple[int, ...]
    service_on_start: bool


SMB_QUEUES = {"square1": 0xFF, "square2": 0xFE, "noise": 0xFD}
SMB_OPER_MODE = 0x0770
# SoundEngine: LDA OperMode / BNE SndOn / STA SND_MASTERCTRL_REG / RTS.
SMB_ENGINE_SIGNATURE = bytes((0xAD, 0x70, 0x07, 0xD0, 0x04, 0x8D, 0x15, 0x40, 0x60))
CASTLE_EXCELLENT_STARTS = (0xC20D, 0xC233, 0xC272, 0xC29A, 0xC2BD)
CASTLE_EXCELLENT_UPDATE = 0xC2F9
# Doraemon's four bank-local drivers: QueueEffect, UpdateEffects, requests.
DORAEMON_DRIVERS = ((0xE3BC, 0xE3CD, 26), (0xA87A, 0xA88B, 15), (0xBEB4, 0xBEC5, 26), (0x982A, 0x983B, 26))


def _key_parts(key: str, game: str, count: int) -> list[str]:
    parts = key.split(":")
    if len(parts) != count or parts[0] != game:
        raise SoundEffectError(f"unknown sound effect {key!r}")
    return parts[1:]


def _number(text: str, key: str) -> int:
    try:
        return int(text, 0)
    except ValueError:
        raise SoundEffectError(f"unknown sound effect {key!r}") from None


def effect_plan(key: str, prg: bytes) -> EffectPlan:
    game = key.split(":", 1)[0]
    if game == "smb":
        channel, value = _key_parts(key, "smb", 3)
        bit = _number(value, key)
        if channel not in SMB_QUEUES or bit not in (1, 2, 4, 8, 16, 32, 64, 128):
            raise SoundEffectError(f"unknown sound effect {key!r}")
        window = prg[:0x8000] if len(prg) >= 0x8000 else prg * 2
        at = window.find(SMB_ENGINE_SIGNATURE)
        if at < 0:
            raise SoundEffectError("SoundEngine was not found in this Super Mario Bros. image")
        return EffectPlan(0, (Action("poke", SMB_OPER_MODE, 1), Action("poke", SMB_QUEUES[channel], bit)),
                          (0x8000 + at,), True)
    if game == "castle-excellent":
        (index,) = _key_parts(key, "castle-excellent", 2)
        number = _number(index, key)
        if not 1 <= number <= len(CASTLE_EXCELLENT_STARTS):
            raise SoundEffectError(f"unknown sound effect {key!r}")
        return EffectPlan(0, (Action("call", CASTLE_EXCELLENT_STARTS[number - 1]),), (CASTLE_EXCELLENT_UPDATE,), False)
    if game == "doraemon":
        driver_text, request_text = _key_parts(key, "doraemon", 3)
        driver = _number(driver_text, key)
        request = _number(request_text, key)
        if not 0 <= driver < len(DORAEMON_DRIVERS) or not 1 <= request < DORAEMON_DRIVERS[driver][2]:
            raise SoundEffectError(f"unknown sound effect {key!r}")
        queue, update, _count = DORAEMON_DRIVERS[driver]
        # The boot enables the four channels once ($4015 = $0F).
        return EffectPlan(driver, (Action("poke", 0x4015, 0x0F), Action("call", queue, request)), (update,), True)
    raise SoundEffectError(f"unknown sound effect {key!r}")


@dataclass(frozen=True)
class Table:
    """A data table an effect reads: note tables hold offsets into the
    game's period table, noise tables $400E values."""
    id: str
    address: int            # CPU address
    size: int
    kind: str               # "note" or "noise"


def capture(key: str, prg: bytes, *, frames: int = MAX_FRAMES, tables: tuple[Table, ...] = (),
            reads: list[list[tuple[str, int]]] | None = None) -> list[list[tuple[int, int]]]:
    """The APU writes of each frame while the effect plays; `reads` gets
    the table bytes each frame read."""
    plan = effect_plan(key, prg)
    start = plan.bank * 0x8000
    window = prg[start:start + 0x8000] if len(prg) > 0x4000 else prg
    bus = Bus(window)
    bus.watch = tuple((t.id, t.address, t.size) for t in tables)
    cpu = Cpu6502(bus.read, bus.write)
    result = []
    for frame in range(frames):
        bus.writes = []
        bus.reads = []
        if frame == 0:
            for action in plan.start:
                if action.kind == "poke":
                    bus.write(action.address, action.value)
                else:
                    cpu.call(action.address, a=action.value)
        if frame > 0 or plan.service_on_start:
            for routine in plan.service:
                cpu.call(routine)
        result.append(bus.writes)
        if reads is not None:
            reads.append(bus.reads)
    return result


# SetFreq: TAY / LDA periods+1,Y / BEQ / STA $4002,X / LDA periods,Y.
NOTE_PERIOD_SIGNATURE = bytes((0x9D, 0x02, 0x40, 0xB9))
SMB_NOTE_PERIODS = 51


def table_choices(table: Table, prg: bytes) -> list[dict[str, int]]:
    """The values a table byte can take and the pitch each one plays."""
    if table.kind == "noise":
        return [{"value": v, "pitch": _pitch(3, float(v))} for v in range(16)]
    window = prg[:0x8000] if len(prg) >= 0x8000 else prg * 2
    at = window.find(NOTE_PERIOD_SIGNATURE)
    if at < 0:
        return []
    base = window[at + 4] | window[at + 5] << 8
    choices = []
    for value in range(0, SMB_NOTE_PERIODS * 2, 2):
        high, low = window[base + value - 0x8000], window[base + value + 1 - 0x8000]
        if low == 0:
            continue
        period = (high & 7) << 8 | low
        choices.append({"value": value, "pitch": _pitch(0, CPU_CLOCK / (16.0 * (period + 1)))})
    return choices


# --- APU ---------------------------------------------------------------------

LENGTHS = (10, 254, 20, 2, 40, 4, 80, 6, 160, 8, 60, 10, 14, 12, 26, 14,
           12, 16, 24, 18, 48, 20, 96, 22, 192, 24, 72, 26, 16, 28, 32, 30)
NOISE_PERIODS = (4, 8, 16, 32, 64, 96, 128, 160, 202, 254, 380, 508, 762, 1016, 2034, 4068)
DUTIES = (0.125, 0.25, 0.5, 0.75)
TRIANGLE = tuple(range(15, -1, -1)) + tuple(range(16))


class _Envelope:
    def __init__(self) -> None:
        self.start = False
        self.divider = 0
        self.decay = 0
        self.loop = False
        self.constant = False
        self.volume = 0

    def control(self, value: int) -> None:
        self.loop = bool(value & 0x20)
        self.constant = bool(value & 0x10)
        self.volume = value & 0x0F

    def clock(self) -> None:
        if self.start:
            self.start = False
            self.decay = 15
            self.divider = self.volume
        elif self.divider == 0:
            self.divider = self.volume
            if self.decay > 0:
                self.decay -= 1
            elif self.loop:
                self.decay = 15
        else:
            self.divider -= 1

    def output(self) -> int:
        return self.volume if self.constant else self.decay


class _Pulse:
    def __init__(self, ones_complement: bool) -> None:
        self.ones = ones_complement
        self.envelope = _Envelope()
        self.duty = 0
        self.timer = 0
        self.length = 0
        self.enabled = False
        self.sweep_enabled = False
        self.sweep_period = 0
        self.sweep_negate = False
        self.sweep_shift = 0
        self.sweep_reload = False
        self.sweep_divider = 0
        self.phase = 0.0

    def target(self) -> int:
        change = self.timer >> self.sweep_shift
        if self.sweep_negate:
            return self.timer - change - (1 if self.ones else 0)
        return self.timer + change

    def muted(self) -> bool:
        return self.timer < 8 or self.target() > 0x7FF

    def write(self, register: int, value: int) -> None:
        if register == 0:
            self.duty = value >> 6
            self.envelope.control(value)
        elif register == 1:
            self.sweep_enabled = bool(value & 0x80)
            self.sweep_period = (value >> 4) & 7
            self.sweep_negate = bool(value & 0x08)
            self.sweep_shift = value & 7
            self.sweep_reload = True
        elif register == 2:
            self.timer = (self.timer & 0x700) | value
        else:
            self.timer = (self.timer & 0xFF) | (value & 7) << 8
            if self.enabled:
                self.length = LENGTHS[value >> 3]
            self.envelope.start = True
            self.phase = 0.0

    def half(self) -> None:
        if not self.envelope.loop and self.length > 0:
            self.length -= 1
        if self.sweep_divider == 0 and self.sweep_enabled and self.sweep_shift > 0 and not self.muted():
            self.timer = max(0, self.target())
        if self.sweep_divider == 0 or self.sweep_reload:
            self.sweep_divider = self.sweep_period
            self.sweep_reload = False
        else:
            self.sweep_divider -= 1

    def volume(self) -> int:
        if self.length == 0 or self.muted():
            return 0
        return self.envelope.output()

    def frequency(self) -> float:
        return CPU_CLOCK / (16.0 * (self.timer + 1))


class _Triangle:
    def __init__(self) -> None:
        self.control = False
        self.reload_value = 0
        self.reload = False
        self.linear = 0
        self.timer = 0
        self.length = 0
        self.enabled = False
        self.phase = 0.0

    def write(self, register: int, value: int) -> None:
        if register == 0:
            self.control = bool(value & 0x80)
            self.reload_value = value & 0x7F
        elif register == 2:
            self.timer = (self.timer & 0x700) | value
        elif register == 3:
            self.timer = (self.timer & 0xFF) | (value & 7) << 8
            if self.enabled:
                self.length = LENGTHS[value >> 3]
            self.reload = True

    def quarter(self) -> None:
        if self.reload:
            self.linear = self.reload_value
        elif self.linear > 0:
            self.linear -= 1
        if not self.control:
            self.reload = False

    def half(self) -> None:
        if not self.control and self.length > 0:
            self.length -= 1

    def audible(self) -> bool:
        return self.length > 0 and self.linear > 0 and self.timer >= 2

    def frequency(self) -> float:
        return CPU_CLOCK / (32.0 * (self.timer + 1))


class _Noise:
    def __init__(self) -> None:
        self.envelope = _Envelope()
        self.mode = False
        self.period = 0
        self.length = 0
        self.enabled = False
        self.shift = 1
        self.counter = 0.0

    def write(self, register: int, value: int) -> None:
        if register == 0:
            self.envelope.control(value)
        elif register == 2:
            self.mode = bool(value & 0x80)
            self.period = value & 0x0F
        elif register == 3:
            if self.enabled:
                self.length = LENGTHS[value >> 3]
            self.envelope.start = True

    def half(self) -> None:
        if not self.envelope.loop and self.length > 0:
            self.length -= 1

    def volume(self) -> int:
        return 0 if self.length == 0 else self.envelope.output()


def _blep(t: float, dt: float) -> float:
    if t < dt:
        t /= dt
        return t + t - t * t - 1.0
    if t > 1.0 - dt:
        t = (t - 1.0) / dt
        return t * t + t + t + 1.0
    return 0.0


class Apu:
    """The four tone channels and the frame sequencer, sample by sample."""

    QUARTERS_4 = (7457, 14913, 22371, 29829)
    QUARTERS_5 = (7457, 14913, 22371, 37281)

    def __init__(self, sample_rate: int = SAMPLE_RATE) -> None:
        self.rate = sample_rate
        self.pulses = (_Pulse(True), _Pulse(False))
        self.triangle = _Triangle()
        self.noise = _Noise()
        self.five_step = False
        self.sequence_cycle = 0.0
        self.step = 0
        self.muted: set[int] = set()
        # What each channel played during the last render(): its loudest
        # volume and the frequency it had then.
        self.heard: list[tuple[int, float]] = [(0, 0.0)] * 4
        # Output filters: high-pass 90 Hz and 440 Hz, low-pass 14 kHz.
        self._hp = [(self._pole(90.0), 0.0, 0.0), (self._pole(440.0), 0.0, 0.0)]
        self._lp_alpha = 1.0 - math.exp(-2.0 * math.pi * 14_000.0 / sample_rate)
        self._lp = 0.0

    def _pole(self, cutoff: float) -> float:
        rc = 1.0 / (2.0 * math.pi * cutoff)
        return rc / (rc + 1.0 / self.rate)

    def write(self, address: int, value: int) -> None:
        register = address & 3
        if address < 0x4004:
            self.pulses[0].write(register, value)
        elif address < 0x4008:
            self.pulses[1].write(register, value)
        elif address < 0x400C:
            self.triangle.write(register, value)
        elif address < 0x4010:
            self.noise.write(register, value)
        elif address == 0x4015:
            for bit, channel in enumerate((*self.pulses, self.triangle, self.noise)):
                channel.enabled = bool(value & (1 << bit))
                if not channel.enabled:
                    channel.length = 0
        elif address == 0x4017:
            self.five_step = bool(value & 0x80)
            self.sequence_cycle = 0.0
            self.step = 0
            if self.five_step:
                self._quarter()
                self._half()

    def _quarter(self) -> None:
        for pulse in self.pulses:
            pulse.envelope.clock()
        self.noise.envelope.clock()
        self.triangle.quarter()

    def _half(self) -> None:
        for pulse in self.pulses:
            pulse.half()
        self.triangle.half()
        self.noise.half()

    def _advance_sequencer(self, cycles: float) -> None:
        self.sequence_cycle += cycles
        marks = self.QUARTERS_5 if self.five_step else self.QUARTERS_4
        while self.step < 4 and self.sequence_cycle >= marks[self.step]:
            step = self.step
            self.step += 1
            if self.five_step:
                if step in (0, 1, 2):
                    self._quarter()
                if step == 1:
                    self._half()
                if step == 3:
                    self._quarter()
                    self._half()
            else:
                self._quarter()
                if step in (1, 3):
                    self._half()
        period = 37282 if self.five_step else 29830
        if self.sequence_cycle >= period:
            self.sequence_cycle -= period
            self.step = 0

    def channel_state(self) -> list[tuple[bool, float, int]]:
        """(audible, frequency, volume) of each channel during the last
        render(); noise reports its period index as the frequency."""
        return [(volume > 0, frequency, volume) for volume, frequency in self.heard]

    def _hear(self, channel: int, volume: int, frequency: float) -> None:
        if volume >= self.heard[channel][0]:
            self.heard[channel] = (volume, frequency)

    def render(self, cycles: float) -> list[float]:
        count = int(round(cycles * self.rate / CPU_CLOCK))
        per_sample = cycles / max(1, count)
        out = []
        self.heard = [(0, 0.0)] * 4
        for _ in range(count):
            self._advance_sequencer(per_sample)
            levels = [0.0, 0.0, 0.0, 0.0]
            for index, pulse in enumerate(self.pulses):
                volume = pulse.volume()
                if volume:
                    self._hear(index, volume, pulse.frequency())
                if volume == 0 or index in self.muted:
                    continue
                dt = min(0.5, pulse.frequency() / self.rate)
                duty = DUTIES[pulse.duty]
                pulse.phase = (pulse.phase + dt) % 1.0
                level = 1.0 if pulse.phase < duty else 0.0
                level += _blep(pulse.phase, dt) - _blep((pulse.phase - duty) % 1.0, dt)
                levels[index] = max(0.0, min(1.0, level)) * volume
            tri = self.triangle
            if tri.audible():
                self._hear(2, 15, tri.frequency())
                tri.phase = (tri.phase + tri.frequency() / self.rate) % 1.0
            if 2 not in self.muted and tri.timer >= 2:
                levels[2] = TRIANGLE[int(tri.phase * 32) & 31]
            noise = self.noise
            noise.counter += per_sample
            period = NOISE_PERIODS[noise.period]
            while noise.counter >= period:
                noise.counter -= period
                tap = 6 if noise.mode else 1
                feedback = (noise.shift ^ (noise.shift >> tap)) & 1
                noise.shift = (noise.shift >> 1) | (feedback << 14)
            if noise.volume():
                self._hear(3, noise.volume(), float(noise.period))
            if 3 not in self.muted and not noise.shift & 1:
                levels[3] = noise.volume()
            pulse_sum = levels[0] + levels[1]
            pulse_out = 95.88 / (8128.0 / pulse_sum + 100.0) if pulse_sum > 0 else 0.0
            tnd_in = levels[2] / 8227.0 + levels[3] / 12241.0
            tnd_out = 159.79 / (1.0 / tnd_in + 100.0) if tnd_in > 0 else 0.0
            sample = pulse_out + tnd_out
            filtered = []
            for alpha, last_in, last_out in self._hp:
                value = alpha * (last_out + sample - last_in)
                filtered.append((alpha, sample, value))
                sample = value
            self._hp = filtered
            self._lp += self._lp_alpha * (sample - self._lp)
            out.append(self._lp)
        return out


# --- trace and render --------------------------------------------------------

def _pitch(channel: int, frequency: float) -> int:
    if channel == 3:
        # Noise: a higher period index sounds lower.
        return 60 - int(frequency) * 2
    return int(round(69 + 12 * math.log2(max(frequency, 1.0) / 440.0)))


def _sources(writes: list[list[tuple[int, int]]], reads: list[list[tuple[str, int]]],
             tables: tuple[Table, ...], prg_values: dict[str, list[int]]) -> list[dict[int, dict[str, object]]]:
    """Per frame and channel: the table byte that set the channel's period."""
    kinds = {t.id: t.kind for t in tables}
    result: list[dict[int, dict[str, object]]] = []
    for frame_writes, frame_reads in zip(writes, reads):
        sources: dict[int, dict[str, object]] = {}
        periods = [channel for channel, register in enumerate((0x4002, 0x4006, 0x400A))
                   if any(address == register for address, _v in frame_writes)]
        for name, index in frame_reads:
            channel = 3 if kinds[name] == "noise" else (periods[0] if periods else None)
            if channel is not None:
                sources[channel] = {"table": name, "index": index, "value": prg_values[name][index]}
        result.append(sources)
    return result


def play(key: str, prg: bytes, *, muted: set[int] | None = None, tables: tuple[Table, ...] = ()) -> dict[str, object]:
    """Runs the effect and returns its per-channel notes and PCM samples;
    notes set from a table byte name it as their source."""
    reads: list[list[tuple[str, int]]] = []
    writes = capture(key, prg, tables=tables, reads=reads)
    plan = effect_plan(key, prg)
    window = prg[plan.bank * 0x8000:plan.bank * 0x8000 + 0x8000] if len(prg) > 0x4000 else prg * 2
    values = {t.id: list(window[t.address - 0x8000:t.address - 0x8000 + t.size]) for t in tables}
    sources = _sources(writes, reads, tables, values)
    apu = Apu()
    apu.muted = set(muted or ())
    samples: list[float] = []
    states: list[list[tuple[bool, float, int]]] = []
    quiet = 0
    last_audible = -1
    clock = 0.0
    for frame, frame_writes in enumerate(writes):
        for address, value in frame_writes:
            apu.write(address, value)
        target = (frame + 1) * FRAME_CYCLES
        samples.extend(apu.render(target - clock))
        clock = target
        state = apu.channel_state()
        states.append(state)
        if any(audible for audible, _f, _v in state):
            last_audible = frame
            quiet = 0
        else:
            quiet += 1
            if frame >= 10 and quiet >= SILENT_TAIL:
                break
    frames = last_audible + 1
    keep = int(round(min(len(states), frames + 12) * FRAME_CYCLES * SAMPLE_RATE / CPU_CLOCK))
    channels = []
    for index, name in enumerate(CHANNEL_NAMES):
        notes: list[dict[str, int]] = []
        for frame, state in enumerate(states[:frames]):
            audible, frequency, volume = state[index]
            if not audible:
                continue
            pitch = _pitch(index, frequency)
            source = sources[frame].get(index)
            last = notes[-1] if notes else None
            same = source is None or (last is not None and last.get("source", {}).get("table") == source["table"]
                                      and last.get("source", {}).get("index") == source["index"])
            if last and last["pitch"] == pitch and last["start"] + last["length"] == frame and same:
                last["length"] += 1
                last["volume"] = max(last["volume"], volume)
            else:
                note: dict[str, object] = {"start": frame, "length": 1, "pitch": pitch, "volume": volume}
                if source is not None:
                    note["source"] = source
                notes.append(note)  # type: ignore[arg-type]
        channels.append({"name": name, "notes": notes})
    used = {n["source"]["table"] for c in channels for n in c["notes"] if "source" in n}  # type: ignore[index]
    described = [{"id": t.id, "kind": t.kind, "values": values[t.id], "choices": table_choices(t, window)}
                 for t in tables if t.id in used]
    return {"key": key, "frames": frames, "channels": channels, "tables": described, "samples": samples[:keep]}


def wav_bytes(samples: list[float]) -> bytes:
    peak = max((abs(s) for s in samples), default=0.0)
    gain = 0.9 / peak if peak > 0 else 0.0
    frames = b"".join(struct.pack("<h", int(max(-1.0, min(1.0, s * gain)) * 32767)) for s in samples)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        out.writeframes(frames)
    return buffer.getvalue()


def render_effect(key: str, prg: bytes, *, wav: Path | None, trace: Path | None,
                  muted: set[int] | None = None, tables: tuple[Table, ...] = ()) -> dict[str, object]:
    result = play(key, prg, muted=muted, tables=tables)
    report: dict[str, object] = {"schema": "retrolarium.sound-effect", "key": key, "frames": result["frames"]}
    if wav is not None:
        wav.parent.mkdir(parents=True, exist_ok=True)
        wav.write_bytes(wav_bytes(result["samples"]))  # type: ignore[arg-type]
        report["wav"] = str(wav)
    if trace is not None:
        trace.parent.mkdir(parents=True, exist_ok=True)
        trace.write_text(json.dumps({"key": key, "frames": result["frames"], "channels": result["channels"],
                                     "tables": result["tables"]}, separators=(",", ":")), encoding="utf-8")
        report["trace"] = str(trace)
    return report
