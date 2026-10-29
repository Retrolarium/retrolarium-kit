"""Alien Soldier's 68000 sound driver, offline: one song or sound effect
played frame by frame into YM2612, PSG and DAC writes for a VGM.

It follows `Sound_UpdateDriver` and the handlers it calls: a channel counts
its duration down each frame and reads its sequence when it runs out (the
commands, then a note, a rest or a duration), the tempo delays every channel
by a frame every `tempo` frames, and between events a channel keeps its
note-stop timer, vibrato, pitch envelope, pan animation and PSG volume
envelope going. The DAC channel names samples instead of notes; the Z80
plays them (`Dac_Reset` onwards), each byte two levels, at the rate the
sample's descriptor sets. The quirks are kept as the driver has them (FM
reads the second operand of $E5 and $F1, PSG the first; a pitch envelope's
values count only as far as its $84 commands scale them; a pan animation
reads past its end, pan mode 4 past its jump tables). A frame's writes take
the time the driver's do, one after another.

Checked against the game in MAME: every song and sound effect makes the
driver's FM and PSG writes frame by frame, and its samples the Z80's levels
at the Z80's average pace.

The tables the driver reads (note frequencies, PSG periods, pan animations,
FM3 offsets, carrier masks, the DAC descriptors' rates) come from the
project's source; the payloads, envelopes and samples from its documents.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from retrolarium_kit.games.alien_soldier import pcm
from retrolarium_kit.platform.megadrive.asl import SourceTree
from retrolarium_kit.platform.megadrive.sound import (
    DEFAULT_SAMPLE_RATE,
    LEVEL_REGISTERS,
    VOICE_REGISTERS,
    Z80_CLOCK,
    ChipWrite,
)


FM_FREQUENCIES = "Sound_FMNoteFrequencyTable"
PSG_PERIODS = "Sound_PSGNotePeriodTable"
PAN_ANIMATIONS = "Sound_PanAnimationPointerTable"
FM3_OFFSETS = "Sound_FM3SpecialFrequencyOffsetTable"
CARRIERS = "Sound_FMAlgorithmCarrierMasks"
FM3_REGISTERS = (0xAD, 0xA9, 0xAC, 0xA8, 0xAE, 0xAA, 0xA6, 0xA2)
SSG_REGISTERS = (0x90, 0x50, 0x98, 0x58, 0x94, 0x54, 0x9C, 0x5C)
AM_REGISTERS = (0x60, 0x68, 0x64, 0x6C)
# Z80 cycles per DAC level: the two halves of the playback loop, 178 and
# 183 cycles apart, plus 13 per count of the sample's delay loop.
DAC_BASE_CYCLES = 180.5
DAC_DELAY_CYCLES = 13
# The share of the time the 68000 holds the Z80 off the bus (the vertical
# blank's handler, the driver's own writes): in MAME every sample, whatever
# its delay, plays 2.5% slower than the playback loop's cycles alone.
DAC_BUS_SHARE = 0.025
# How long one chip write of the driver takes, with the code around it and
# the wait for the Z80 bus: 67 microseconds on average in the game (a frame's
# writes take 0.1 ms plus that each in MAME). A frame's writes follow each
# other at that pace, so the chip sees a key-off before the key-on after it
# and the note starts again.
WRITE_SECONDS = 67e-6
# SFX channel records the driver loads a sound effect into, by channel type.
SFX_SLOTS = {0x02: 0, 0x04: 1, 0x05: 2, 0x80: 3, 0xA0: 4, 0xC0: 5, 0xE0: 5}
POINTER_BASE = 0x100000


class SequencerError(ValueError):
    """A payload the sequencer cannot play."""


def dac_rate(delay: int) -> float:
    """The levels a second the Z80 plays a sample with this delay count."""
    return Z80_CLOCK * (1 - DAC_BUS_SHARE) / (DAC_BASE_CYCLES + DAC_DELAY_CYCLES * (delay or 256))


def _signed(value: int) -> int:
    value &= 0xFF
    return value - 0x100 if value & 0x80 else value


def _word(value: int) -> int:
    value &= 0xFFFF
    return value - 0x10000 if value & 0x8000 else value


@dataclass
class Channel:
    index: int
    type: int
    flags: int = 0x80  # bit 7 active, 4 hold, 2 overridden, 1 resting
    multiplier: int = 1
    envelope_scale: int = 0  # $03
    cursor: int = 0
    transpose: int = 0
    volume: int = 0
    modulation: int = 0  # $0A: bit 7 vibrato, the rest a pitch envelope
    instrument: int = 0  # $0B: FM voice or PSG volume envelope
    volume_cursor: int = 0
    stack: int = 0x30
    duration: int = 1
    reload: int = 0
    frequency: int = 0
    stop: int = 0
    stop_reload: int = 0
    vibrato: int = 0  # offset of the four vibrato bytes
    vibrato_delay: int = 0
    vibrato_period: int = 0
    vibrato_step: int = 0
    vibrato_steps: int = 0
    vibrato_offset: int = 0
    detune: int = 0
    pan_mode: int = 0
    pan_animation: int = 0
    pan_cursor: int = 0
    pan_length: int = 0
    pan_period: int = 0
    pan_counter: int = 0
    algorithm: int = 0  # $25: the voice's first byte, or the PSG noise byte
    pitch_cursor: int = 0
    pan: int = 0xC0
    sample: int = 0  # the DAC channel's sample byte
    resume: int = 0  # where the sequence was read from this frame
    memory: bytearray = field(default_factory=lambda: bytearray(0x30))  # loop counters and call stack

    @property
    def psg(self) -> bool:
        return bool(self.type & 0x80)


class Stopped(Exception):
    """A channel stopped (F2/E3): the rest of its frame is skipped."""


class StoppedFrame(Stopped):
    """The DAC channel stopped: its handler returns from the whole driver
    update, so no other channel runs this frame."""


class Sequencer:
    def __init__(
        self,
        tree: SourceTree,
        sound: dict[str, Any],
        envelopes: dict[str, Any],
        samples: dict[str, Any],
        *,
        frame_rate: int = 60,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        rom: bytes | None = None,
    ):
        self.payloads = {item["label"]: item for item in sound["payloads"]}
        self.pitch_envelopes = {
            item["number"]: [value & 0xFF for value in item["values"]]
            for item in envelopes["envelopes"]
            if item["family"] == "pitch"
        }
        self.volume_envelopes = {
            item["number"]: [value & 0xFF for value in item["values"]]
            for item in envelopes["envelopes"]
            if item["family"] != "pitch"
        }
        self.frequencies = self._words(tree, FM_FREQUENCIES)
        self.periods = self._periods(tree, rom)
        self.fm3_offsets = self._words(tree, FM3_OFFSETS)
        self.carriers = list(tree.data(CARRIERS))
        self.pan_memory, self.pan_starts = self._pan_animations(tree, rom)
        self.dac = self._dac(tree, samples)
        self.rom = rom
        self.frame_rate = frame_rate
        self.sample_rate = sample_rate
        self.write_samples = WRITE_SECONDS * sample_rate

    # -- source tables

    @staticmethod
    def _words(tree: SourceTree, label: str) -> list[int]:
        data = tree.data(label)
        return [int.from_bytes(data[at : at + 2], "big") for at in range(0, len(data) - 1, 2)]

    @classmethod
    def _periods(cls, tree: SourceTree, rom: bytes | None) -> list[int]:
        """The PSG periods, and past them (a note index reaches 127) the
        words that follow the table in the ROM, where one is given."""
        periods = cls._words(tree, PSG_PERIODS)
        table = tree.data(PSG_PERIODS)
        at = rom.find(table) if rom else -1
        if at >= 0:
            tail = rom[at + len(table) : at + 256]
            periods += [int.from_bytes(tail[index : index + 2], "big") for index in range(0, len(tail) - 1, 2)]
        return periods

    @classmethod
    def _pan_animations(cls, tree: SourceTree, rom: bytes | None) -> tuple[bytes, list[int]]:
        """The pan animations as the driver reads them, from one run of
        memory: a step past an animation's end reads on into the next one
        (and past the last into the code after it, where the ROM is given),
        step 0 the byte before. The memory starts one byte before the
        first animation; where each one starts in it."""
        sequences = [tree.data(label) for label in cls._pointers(tree, PAN_ANIMATIONS)]
        joined = b"".join(sequences)
        starts, at = [], 1
        for sequence in sequences:
            starts.append(at)
            at += len(sequence)
        found = rom.find(joined) if rom else -1
        if found >= 1:
            return rom[found - 1 : found + len(joined) + 0x100], starts
        return bytes(1) + joined + bytes(0x100), starts

    @staticmethod
    def _pointers(tree: SourceTree, label: str) -> list[str]:
        return [operand.text for operand in tree.block(label).operands]

    @staticmethod
    def _dac(tree: SourceTree, samples: dict[str, Any]) -> dict[int, tuple[list[int], int]]:
        """The music samples by note: their levels and delay count."""
        delays = pcm.delays(tree)
        deltas = samples["deltas"]
        banks = {item["label"]: item["samples"] for item in samples["banks"]}
        found = {}
        for item in samples["descriptors"]:
            if item["kind"] != "music":
                continue
            data = bytes.fromhex(banks[item["bank"]][item["sample"]])
            found[item["id"]] = (pcm.decode(data, deltas["high"], deltas["low"]), delays[("music", item["id"])])
        return found

    # -- chip writes

    def _write(self, channel: Channel | None, port: int, address: int, value: int) -> None:
        at = self._tick()
        if channel is not None and self.enabled is not None and channel.index not in self.enabled:
            return
        self.writes.append(
            ChipWrite(at, "ym2612", value & 0xFF, port, address & 0xFF, None if channel is None else channel.index)
        )

    def _psg(self, channel: Channel, value: int) -> None:
        at = self._tick()
        if self.enabled is not None and channel.index not in self.enabled:
            return
        self.writes.append(ChipWrite(at, "sn76489", value & 0xFF, channel=channel.index))

    def _tick(self) -> int:
        """The output sample of a write, and the driver's time past it (a
        channel left unheard takes its time all the same)."""
        at = int(self.now)
        self.now += self.write_samples
        return at

    def _fm(self, channel: Channel, address: int, value: int) -> None:
        """Sound_WriteCurrentFMChannelRegister."""
        number = channel.type & 0xFF
        port = 1 if number & 4 else 0
        self._write(channel, port, address + (number & ~4 & 0xFF), value)

    def _fm_unless_overridden(self, channel: Channel, address: int, value: int) -> None:
        if not channel.flags & 4:
            self._fm(channel, address, value)

    def _key_off(self, channel: Channel, allowed: bool = True) -> None:
        if allowed and channel.flags & 0x14:
            return
        self._write(channel, 0, 0x28, channel.type)

    def _key_on(self, channel: Channel) -> None:
        if channel.flags & 6:
            return
        self._write(channel, 0, 0x28, channel.type | 0xF0)

    def _mute(self, channel: Channel) -> None:
        if not channel.flags & 4:
            self._psg(channel, channel.type | 0x1F)

    def _pan(self, channel: Channel, value: int) -> None:
        """Sound_WriteChannelPanAndAMS (the DAC channel's goes to B6 either way)."""
        if not channel.flags & 4:
            self._fm(channel, 0xB4, value)

    # -- instruments and volume

    def _voice(self, index: int) -> bytes:
        start = self.instruments + 25 * index
        voice = self.data[start : start + 25]
        if len(voice) != 25:
            raise SequencerError(f"FM voice {index} lies outside the payload")
        return voice

    def _program(self, channel: Channel, index: int) -> None:
        """Sound_ProgramFMInstrument."""
        voice = self._voice(index)
        channel.algorithm = voice[0]
        self._fm(channel, 0xB0, voice[0])
        for address, value in zip(VOICE_REGISTERS, voice[1:21]):
            self._fm(channel, address, value)
        mask = self.carriers[voice[0] & 7]
        for address, value in zip(LEVEL_REGISTERS, voice[21:25]):
            carrier, mask = mask & 1, mask >> 1
            self._fm(channel, address, value + channel.volume if carrier else value)
        self._fm(channel, 0xB4, channel.pan)

    def _apply_volume(self, channel: Channel) -> None:
        """Sound_ApplyFMVolumeOffset."""
        if channel.flags & 4 or channel.volume & 0x80:
            return
        voice = self._voice(channel.instrument)
        mask = self.carriers[channel.algorithm & 7]
        for address, value in zip(LEVEL_REGISTERS, voice[21:25]):
            carrier, mask = mask & 1, mask >> 1
            if carrier and value + channel.volume <= 0xFF:
                self._fm(channel, address, value + channel.volume)

    # -- sequence reading

    def _byte(self, channel: Channel) -> int:
        if not 0 <= channel.cursor < len(self.data):
            raise SequencerError(f"channel {channel.index} reads outside its payload at ${channel.cursor:X}")
        value = self.data[channel.cursor]
        channel.cursor += 1
        return value

    def _relative_jump(self, channel: Channel) -> None:
        displacement = (self._byte(channel) << 8) | self._byte(channel)
        channel.cursor += _word(displacement) - 1

    def _command(self, channel: Channel, opcode: int) -> None:
        """Sound_DispatchSequenceCommand."""
        take = lambda: self._byte(channel)  # noqa: E731
        if opcode == 0xE0:
            value = take()
            if not channel.psg:
                channel.pan = (channel.pan & 0x37) | value
                self._pan(channel, channel.pan)
        elif opcode == 0xE1:
            channel.detune = _signed(take())
        elif opcode in (0xE2, 0xEB):
            take()
        elif opcode == 0xE3:
            for address in (0x40, 0x44, 0x48, 0x4C):
                self._fm(channel, address, 0x7F)
            for address in (0x80, 0x84, 0x88, 0x8C):
                self._fm(channel, address, 0x0F)
            self._stop(channel)
        elif opcode == 0xE4:
            channel.pan_mode = take()
            if channel.pan_mode:
                channel.pan_animation, channel.pan_cursor, channel.pan_length = take(), take(), take()
                channel.pan_period = channel.pan_counter = take()
            else:
                self._pan(channel, channel.pan)
        elif opcode == 0xE5:
            first = take()
            if channel.psg:
                channel.volume = (channel.volume + first) & 0xFF
                take()
            else:
                channel.volume = (channel.volume + take()) & 0xFF
                self._apply_volume(channel)
        elif opcode == 0xE6:
            channel.volume = (channel.volume + take()) & 0xFF
            self._apply_volume(channel)
        elif opcode == 0xE7:
            channel.flags |= 0x10
        elif opcode == 0xE8:
            channel.stop = channel.stop_reload = take()
        elif opcode == 0xE9:
            # The first operand's top four bits set AM on the operators'
            # decay registers, over bytes the driver reads through the long
            # at $20 of the channel record (see _am_bytes); then the same
            # operand goes to the LFO register.
            mask = self.data[channel.cursor] if channel.cursor < len(self.data) else 0
            for operator, (address, value) in enumerate(zip(AM_REGISTERS, self._am_bytes(channel))):
                if (mask << operator) & 0x80:
                    self._fm_unless_overridden(channel, address, value | 0x80)
            lfo = take()
            self._write(channel, 0, 0x22, lfo)
            channel.pan = (channel.pan & 0xC0) | take()
            self._pan(channel, channel.pan)
        elif opcode == 0xEA:
            self.tempo = self.tempo_counter = take()
        elif opcode == 0xEC:
            channel.volume = (channel.volume + take()) & 0xFF
        elif opcode == 0xED:
            address, value = take(), take()
            self._fm_unless_overridden(channel, address, value)
        elif opcode == 0xEE:
            address, value = take(), take()
            self._write(channel, 0, address, value)
        elif opcode == 0xEF:
            channel.instrument = take()
            if not channel.flags & 4:
                self._program(channel, channel.instrument)
        elif opcode == 0xF0:
            channel.modulation |= 0x80
            channel.vibrato = channel.cursor
            channel.vibrato_delay, channel.vibrato_period = take(), take()
            channel.vibrato_step, channel.vibrato_steps = _signed(take()), take() >> 1
            channel.vibrato_offset = 0
        elif opcode == 0xF1:
            first = take()
            if channel.psg:
                channel.modulation = first
                take()
            else:
                channel.modulation = take()
        elif opcode == 0xF2:
            self._stop(channel)
        elif opcode == 0xF3:
            channel.type = 0xE0
            channel.algorithm = take()
            if not channel.flags & 4:
                self._psg(channel, channel.algorithm)
        elif opcode == 0xF4:
            channel.modulation = take()
        elif opcode == 0xF5:
            channel.instrument = take()
        elif opcode == 0xF6:
            self._relative_jump(channel)
        elif opcode == 0xF7:
            index, count = take(), take()
            if index >= 8:
                raise SequencerError(f"loop counter {index} lies outside the channel record")
            slot = 0x28 + index
            if not channel.memory[slot]:
                channel.memory[slot] = count
            channel.memory[slot] = (channel.memory[slot] - 1) & 0xFF
            if channel.memory[slot]:
                self._relative_jump(channel)
            else:
                channel.cursor += 2
        elif opcode == 0xF8:
            channel.stack -= 4
            if channel.stack < 0:
                raise SequencerError("sequence calls nest too deep")
            channel.memory[channel.stack : channel.stack + 4] = (POINTER_BASE + channel.cursor).to_bytes(4, "big")
            self._relative_jump(channel)
        elif opcode == 0xF9:
            pointer = int.from_bytes(channel.memory[channel.stack : channel.stack + 4], "big")
            channel.cursor = pointer - POINTER_BASE + 2
            channel.stack += 4
        elif opcode == 0xFA:
            channel.multiplier = take()
        elif opcode == 0xFB:
            channel.transpose = (channel.transpose + take()) & 0xFF
        elif opcode == 0xFC:
            channel.modulation |= 0x80
        elif opcode == 0xFD:
            channel.modulation &= 0x7F
        elif opcode == 0xFE:
            offsets = [self.fm3_offsets[take()] for _ in range(4)]
            if self.group:
                self.sfx_fm3 = offsets
            else:
                self.bgm_fm3 = offsets
                self.fm3_mode = True
            self._write(channel, 0, 0x27, 0x40)
        elif opcode == 0xFF:
            self._extended(channel, take())
        else:
            raise SequencerError(f"unknown command ${opcode:02X}")

    def _am_bytes(self, channel: Channel) -> bytes:
        """The four bytes $E9 sets AM over: read through the long at $20 of
        the channel record (a sound effect's record holds its voice table
        there, a song's its pan animation bytes, which the driver takes as an
        address); until a song has been loaded the driver reads address 0
        instead, but here one has. Only the ROM is readable: elsewhere the
        bytes stand as 0."""
        if self.group:
            return self.data[self.instruments : self.instruments + 4]
        address = (
            (channel.pan_animation << 24) | (channel.pan_cursor << 16) | (channel.pan_length << 8) | channel.pan_period
        ) & 0xFFFFFF
        if self.rom is None or address + 4 > len(self.rom):
            return bytes(4)
        return self.rom[address : address + 4]

    def _extended(self, channel: Channel, selector: int) -> None:
        take = lambda: self._byte(channel)  # noqa: E731
        if selector == 0x00:
            for at in range(0, 8, 2):
                self._fm_unless_overridden(channel, SSG_REGISTERS[at], take())
                self._fm_unless_overridden(channel, SSG_REGISTERS[at + 1], 0x1F)
        elif selector == 0x01:
            take()
        elif selector == 0x02:
            value = take()
            for other in self.channels:
                other.multiplier = value
        elif selector == 0x03:
            take(), take()
        elif selector != 0x04:
            raise SequencerError(f"unknown extended command ${selector:02X}")

    def _stop(self, channel: Channel) -> None:
        """Sound_StopSequenceChannel."""
        channel.flags &= ~0x90
        if channel.psg:
            self._mute(channel)
        elif self.dac_event:
            raise StoppedFrame
        else:
            self._key_off(channel)
        raise Stopped

    def _duration(self, channel: Channel, value: int) -> None:
        """Sound_CalculateDuration."""
        multiplier = channel.multiplier or 0x100
        channel.reload = channel.duration = (value * multiplier) & 0xFF

    def _save(self, channel: Channel) -> None:
        """Sound_SaveChannelState."""
        channel.duration = channel.reload
        if channel.flags & 0x10:
            return
        channel.stop = channel.stop_reload
        channel.volume_cursor = channel.pitch_cursor = channel.envelope_scale = 0
        if channel.modulation & 0x80:
            data = self.data[channel.vibrato : channel.vibrato + 4]
            channel.vibrato_delay, channel.vibrato_period = data[0], data[1]
            channel.vibrato_step, channel.vibrato_steps = _signed(data[2]), data[3] >> 1
            channel.vibrato_offset = 0

    def _event(self, channel: Channel) -> int:
        """Read commands up to the next event byte."""
        channel.resume = channel.cursor
        while True:
            value = self._byte(channel)
            if value < 0xE0:
                return value
            self._command(channel, value)

    def _after_note(self, channel: Channel) -> None:
        value = self._byte(channel)
        if value < 0x80:
            self._duration(channel, value)
        else:
            channel.cursor -= 1
        self._save(channel)

    # -- pitch

    def _pitch(self, channel: Channel) -> int:
        """Sound_ApplyPitchEffects: the frequency word to write."""
        offset = 0
        number = channel.modulation & 0x7F
        if number:
            envelope = self.pitch_envelopes.get(number)
            if envelope is None:
                raise SequencerError(f"pitch envelope {number} does not exist")
            for _ in range(0x400):
                at = channel.pitch_cursor
                channel.pitch_cursor = (channel.pitch_cursor + 1) & 0xFF
                value = envelope[at] if at < len(envelope) else 0
                if value == 0x80:
                    channel.pitch_cursor = 0
                elif value == 0x81:
                    channel.pitch_cursor = (channel.pitch_cursor - 2) & 0xFF
                elif value == 0x82:
                    channel.pitch_cursor = envelope[at + 1]
                elif value == 0x84:
                    channel.envelope_scale = (channel.envelope_scale + envelope[at + 1]) & 0xFF
                    channel.pitch_cursor = (channel.pitch_cursor + 1) & 0xFF
                elif value == 0x83:
                    channel.flags |= 2
                    if channel.psg:
                        self._mute(channel)
                    else:
                        self._key_off(channel)
                    return 0x83
                else:
                    offset = (_signed(value) * _signed(channel.envelope_scale)) & 0xFFFF
                    break
            else:
                raise SequencerError(f"pitch envelope {number} never yields a value")
        offset += channel.detune + channel.frequency
        if channel.modulation & 0x80:
            offset += channel.vibrato_offset
        return offset & 0xFFFF

    def _vibrato(self, channel: Channel) -> None:
        """Sound_ProcessVibrato."""
        if not channel.modulation & 0x80:
            return
        if channel.vibrato_delay:
            channel.vibrato_delay -= 1
            return
        channel.vibrato_period = (channel.vibrato_period - 1) & 0xFF
        if channel.vibrato_period:
            return
        data = self.data[channel.vibrato : channel.vibrato + 4]
        channel.vibrato_period = data[1]
        if not channel.vibrato_steps:
            channel.vibrato_steps = data[3]
            channel.vibrato_step = -channel.vibrato_step
            return
        channel.vibrato_steps -= 1
        channel.vibrato_offset = _word(channel.vibrato_offset + channel.vibrato_step)

    def _fm_frequency(self, channel: Channel) -> None:
        if channel.flags & 6:
            return
        value = self._pitch(channel)
        if self.fm3_mode and channel.type == 2:
            offsets = self.sfx_fm3 if self.group else self.bgm_fm3
            for operator in range(4):
                word = (value + offsets[operator]) & 0xFFFF
                self._write(channel, 0, FM3_REGISTERS[2 * operator], word >> 8)
                self._write(channel, 0, FM3_REGISTERS[2 * operator + 1], word)
            return
        self._fm(channel, 0xA4, value >> 8)
        self._fm(channel, 0xA0, value)

    def _psg_frequency(self, channel: Channel) -> None:
        if channel.flags & 6:
            return
        value = self._pitch(channel)
        latch = 0xC0 if channel.type == 0xE0 else channel.type
        self._psg(channel, latch | (value & 0x0F))
        self._psg(channel, (value >> 4) & 0x3F)

    # -- timers and animation

    def _note_timer(self, channel: Channel) -> bool:
        """Sound_HandleNoteTimer: True when the note stopped (and the frame ends)."""
        if not channel.stop:
            return False
        channel.stop -= 1
        if channel.stop:
            return False
        channel.flags |= 2
        if channel.psg:
            self._mute(channel)
        else:
            self._key_off(channel)
        return True

    def _pan_step(self, channel: Channel, restart: bool) -> None:
        if restart:
            channel.pan_counter = channel.pan_period
            channel.pan_cursor = 0
        if channel.pan_counter == channel.pan_period:
            if _signed(channel.pan_length - channel.pan_cursor) < 0:
                if channel.pan_mode == 2:
                    return
                channel.pan_cursor = 0
            channel.pan_counter = 0
            channel.pan_cursor = (channel.pan_cursor + 1) & 0xFF
        at = self.pan_starts[channel.pan_animation - 1] + channel.pan_cursor - 1
        value = self.pan_memory[at] | (channel.pan & 0x37)
        self._pan(channel, value)
        channel.pan_counter = (channel.pan_counter + 1) & 0xFF

    # The pan mode indexes two jump tables of four branches, the per-note
    # table straight before the per-tick routine and the per-tick table
    # straight before the restart: mode 4 runs past the end of both, on a
    # note into the per-tick routine and from there (as on a tick) into the
    # restart, so its animation starts over every frame.

    def _pan_on_note(self, channel: Channel) -> None:
        if channel.flags & 2 or channel.pan_mode not in (1, 2, 3, 4):
            return
        self._pan_step(channel, restart=channel.pan_mode != 1)

    def _pan_on_tick(self, channel: Channel) -> None:
        if channel.flags & 2 or channel.pan_mode not in (2, 3, 4):
            return
        self._pan_step(channel, restart=channel.pan_mode == 4)

    # -- PSG volume

    def _psg_volume(self, channel: Channel, value: int) -> None:
        """Sound_ApplyPSGVolume."""
        if channel.flags & 6:
            return
        if channel.flags & 0x10 and channel.stop_reload and not channel.stop:
            return
        self._psg(channel, ((value | channel.type) + 0x10) & 0xFF)

    def _volume_envelope(self, channel: Channel) -> None:
        """Sound_ApplyPSGVolumeEnvelope."""
        level = channel.volume
        if channel.instrument:
            envelope = self.volume_envelopes.get(channel.instrument)
            if envelope is None:
                raise SequencerError(f"PSG volume envelope {channel.instrument} does not exist")
            for _ in range(0x400):
                at = channel.volume_cursor
                channel.volume_cursor = (channel.volume_cursor + 1) & 0xFF
                value = envelope[at] if at < len(envelope) else 0x83
                if value == 0x83:
                    channel.volume_cursor = (channel.volume_cursor - 2) & 0xFF
                    channel.flags |= 2
                    self._mute(channel)
                    return
                if value == 0x81:
                    channel.volume_cursor = (channel.volume_cursor - 2) & 0xFF
                    return
                if value == 0x82:
                    channel.volume_cursor = envelope[at + 1]
                    continue
                if value == 0x80:
                    channel.volume_cursor = 0
                    continue
                level = (level + value) & 0xFFFF
                if level & 0xFF >= 0x10:
                    level = 0x0F
                break
            else:
                raise SequencerError(f"PSG volume envelope {channel.instrument} never yields a value")
        self._psg_volume(channel, level & 0xFF)

    # -- channels

    def _fm_channel(self, channel: Channel) -> None:
        """Sound_ProcessChannel."""
        channel.duration = (channel.duration - 1) & 0xFF
        if channel.duration:
            if self._note_timer(channel):
                return
            self._pan_on_tick(channel)
            self._vibrato(channel)
            if channel.modulation:
                self._fm_frequency(channel)
            return
        channel.flags &= ~0x10
        channel.flags &= ~2
        value = self._event(channel)
        self._key_off(channel)
        if value >= 0x80:
            if value == 0x80:
                channel.flags |= 2
                channel.frequency = 0
                self._save(channel)
            else:
                note = (value - 0x80 + channel.transpose) & 0x7F
                block, index = divmod(note, 12)
                channel.frequency = self.frequencies[index] | ((block & 7) << 11)
            self._after_note(channel)
        else:
            self._duration(channel, value)
            self._save(channel)
        if channel.frequency:
            self._fm_frequency(channel)
        else:
            channel.flags |= 2
        self._pan_on_note(channel)
        self._key_on(channel)

    def _psg_channel(self, channel: Channel) -> None:
        """Sound_ProcessPSGChannel."""
        channel.duration = (channel.duration - 1) & 0xFF
        if channel.duration:
            if self._note_timer(channel):
                return
            if channel.instrument:
                self._volume_envelope(channel)
            self._vibrato(channel)
            if channel.modulation:
                self._psg_frequency(channel)
            return
        channel.flags &= ~0x10
        channel.flags &= ~2
        value = self._event(channel)
        if value >= 0x80:
            if value == 0x80:
                channel.flags |= 2
                channel.frequency = 0xFFFF
                self._save(channel)
                self._mute(channel)
            else:
                index = (value - 0x81 + channel.transpose) & 0x7F
                # Past the table the driver reads the pointers after it;
                # without the ROM a period of 0 (inaudible) stands in.
                channel.frequency = self.periods[index] if index < len(self.periods) else 0
                self._save(channel)
            self._after_note(channel)
        else:
            self._duration(channel, value)
            self._save(channel)
        if channel.frequency & 0x8000:
            channel.flags |= 2
        else:
            self._psg_frequency(channel)
        self._volume_envelope(channel)

    def _dac_channel(self, channel: Channel) -> None:
        """Sound_ProcessPCMSequence."""
        channel.duration = (channel.duration - 1) & 0xFF
        if channel.duration:
            return
        self.dac_event = True
        try:
            value = self._event(channel)
            if value >= 0x80:
                channel.sample = value
                following = self._byte(channel)
                if following < 0x80:
                    self._duration(channel, following)
                else:
                    channel.cursor -= 1
                    channel.duration = channel.reload
            else:
                self._duration(channel, value)
        finally:
            self.dac_event = False
        if channel.sample > 0x80:
            self._start_sample(channel)

    def _start_sample(self, channel: Channel) -> None:
        found = self.dac.get(channel.sample)
        if found is None:
            raise SequencerError(f"DAC sample ${channel.sample:02X} does not exist")
        levels, delay = found
        self.dac_levels = levels
        self.dac_position = 0
        self.dac_step = self.sample_rate / dac_rate(delay)
        self.dac_time = float(self.now)
        self.dac_channel = channel
        self._write(channel, 1, 0xB6, channel.pan)
        self._write(channel, 0, 0x2B, 0x80)

    def _play_dac(self, until: int) -> None:
        """The Z80's levels up to output sample `until`."""
        while self.dac_levels is not None and self.dac_time < until:
            channel = self.dac_channel
            saved, self.now = self.now, int(self.dac_time)
            if self.dac_position < len(self.dac_levels):
                self._write(channel, 0, 0x2A, self.dac_levels[self.dac_position])
                self.dac_position += 1
                self.dac_time += self.dac_step
            else:
                self._write(channel, 1, 0xB6, channel.pan)
                self._write(channel, 0, 0x2B, 0)
                self.dac_levels = None
            self.now = saved

    # -- playing

    def _load(self, label: str) -> None:
        item = self.payloads.get(label)
        if item is None:
            raise SequencerError(f"no sound payload {label}")
        self.data = bytes.fromhex(item["data"])
        self.instruments = item["instruments"]
        self.channels: list[Channel] = []
        self.kind = item["kind"]
        self.special = self.kind != "bgm" and any(request >= 0xF9 for request in item.get("requests", []))
        self.group = 0 if self.kind == "bgm" else (0x40 if self.special else 0x80)
        multiplier = item.get("tick_multiplier", 1)
        for index, header in enumerate(item["channels"]):
            channel = Channel(index=index, type=header["type"], multiplier=multiplier, cursor=header["sequence"])
            channel.transpose = header["initial"] >> 8
            channel.volume = header["initial"] & 0xFF
            if self.kind == "bgm":
                channel.modulation = header.get("pitch_envelope", 0)
                channel.instrument = header.get("volume_envelope", 0)
            else:
                channel.flags = header.get("status", 0x80)
            # The loaders give only the FM records their pan.
            channel.pan = 0 if channel.psg else 0xC0
            self.channels.append(channel)
        if self.kind != "bgm":
            # The driver loads a sound effect's channels into its fixed
            # records and runs them in the records' order.
            self.channels.sort(key=lambda channel: SFX_SLOTS.get(channel.type, len(SFX_SLOTS)))
        self.tempo = self.tempo_counter = item.get("tempo", 0) if self.kind == "bgm" else 0

    def render(
        self,
        label: str,
        *,
        seconds: float = 30.0,
        enabled: set[int] | None = None,
        observe: Callable[[int, list[Channel]], None] | None = None,
    ) -> tuple[list[ChipWrite], int]:
        """The chip writes of `label` played for `seconds`, and the samples
        that lasts. `enabled`: the channel indices heard (None: all);
        `observe` is called with the channels after each frame. Frame 0 is
        the frame after the load (the load's own frame returns before the
        channels run)."""
        self.writes: list[ChipWrite] = []
        self.enabled = enabled
        self.now = 0.0
        self.fm3_mode = False
        self.bgm_fm3 = [0, 0, 0, 0]
        self.sfx_fm3 = [0, 0, 0, 0]
        self.dac_event = False
        self.dac_levels: list[int] | None = None
        self._load(label)
        # Sound_StopAllPlayback and the BGM load: FM3 normal, keys off, PSG silent.
        self._write(None, 0, 0x27, 0)
        self._write(None, 0, 0x2B, 0)
        for number in (0, 1, 2, 4, 5, 6):
            self._write(None, 0, 0x28, number)
        for value in (0x9F, 0xBF, 0xDF, 0xFF):
            self.writes.append(ChipWrite(0, "sn76489", value))
        frames = int(seconds * self.frame_rate)
        total = int(seconds * self.sample_rate)
        for frame in range(frames):
            start = frame * self.sample_rate // self.frame_rate
            self._play_dac(start)
            # A frame whose writes ran past the next one's start delays it.
            self.now = max(float(start), self.now)
            if self.tempo:
                self.tempo_counter = (self.tempo_counter - 1) & 0xFF
                if not self.tempo_counter:
                    self.tempo_counter = self.tempo
                    for channel in self.channels:
                        if channel.flags & 0x80:
                            channel.duration = (channel.duration + 1) & 0xFF
            active = False
            for channel in self.channels:
                if not channel.flags & 0x80:
                    continue
                active = True
                try:
                    if self.kind == "bgm" and channel.index == 0 and channel.type == 6:
                        self._dac_channel(channel)
                    elif channel.psg:
                        self._psg_channel(channel)
                    else:
                        self._fm_channel(channel)
                except Stopped as stopped:
                    # A stop leaves the saved position where the frame
                    # started reading.
                    channel.cursor = channel.resume
                    if isinstance(stopped, StoppedFrame):
                        break
            if observe is not None:
                observe(frame, self.channels)
            if not active and self.dac_levels is None:
                total = min(total, start + self.sample_rate // 2)
                break
        self._play_dac(total)
        return [write for write in self.writes if write.sample < total], total
