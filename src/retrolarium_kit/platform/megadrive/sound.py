"""Mega Drive sound chips, offline: the register writes a sound driver makes,
as VGM 1.50, and the studio's YMFM renderer that turns a VGM into a WAV.

A game's sequencer follows its driver frame by frame and records each write
to the YM2612 (port 0 or 1, register, value) or the SN76489 (a byte) at the
output sample it happens on; the writes become a VGM, and the renderer
(RETROLARIUM_YMFM_RENDERER, built with the studio) plays it.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import struct
import subprocess


YM2612_CLOCK = 7_670_454
SN76489_CLOCK = 3_579_545
Z80_CLOCK = 3_579_545
DEFAULT_SAMPLE_RATE = 44_100
VGM_HEADER_SIZE = 0x100
RENDERER_VARIABLE = "RETROLARIUM_YMFM_RENDERER"

# The registers an SMPS-style 25-byte FM voice fills after $B0, in its
# operator order (1, 3, 2, 4): detune/multiple, attack, decay, sustain rate,
# release, and the total levels last.
VOICE_REGISTERS = (0x30, 0x38, 0x34, 0x3C, 0x50, 0x58, 0x54, 0x5C, 0x60, 0x68, 0x64, 0x6C, 0x70, 0x78, 0x74, 0x7C)
VOICE_REGISTERS += (0x80, 0x88, 0x84, 0x8C)
LEVEL_REGISTERS = (0x40, 0x48, 0x44, 0x4C)
# Which operators are carriers for each algorithm, one bit per operator in
# the voice's level order.
CARRIER_MASKS = (0x08, 0x08, 0x08, 0x08, 0x0A, 0x0E, 0x0E, 0x0F)


@dataclass(frozen=True)
class ChipWrite:
    sample: int
    chip: str  # "ym2612" or "sn76489"
    value: int
    port: int = 0
    address: int = 0
    channel: int | None = None


def _wait(output: bytearray, samples: int) -> None:
    while samples:
        amount = min(samples, 0xFFFF)
        if amount <= 16:
            output.append(0x70 + amount - 1)
        else:
            output.extend((0x61, amount & 0xFF, amount >> 8))
        samples -= amount


def encode_vgm(writes: list[ChipWrite], total_samples: int, rate: int = 60) -> bytes:
    """The writes, in sample order, as a VGM 1.50 file `total_samples` long."""
    header = bytearray(VGM_HEADER_SIZE)
    header[0:4] = b"Vgm "
    struct.pack_into("<I", header, 0x08, 0x00000150)
    struct.pack_into("<I", header, 0x0C, SN76489_CLOCK)
    struct.pack_into("<I", header, 0x18, total_samples)
    struct.pack_into("<I", header, 0x24, rate)
    struct.pack_into("<I", header, 0x2C, YM2612_CLOCK)
    struct.pack_into("<I", header, 0x34, VGM_HEADER_SIZE - 0x34)

    commands = bytearray()
    cursor = 0
    for write in sorted(writes, key=lambda item: item.sample):
        if write.sample < cursor:
            raise ValueError("chip writes are not ordered by sample")
        _wait(commands, write.sample - cursor)
        cursor = write.sample
        if write.chip == "ym2612":
            commands.extend((0x53 if write.port else 0x52, write.address, write.value))
        elif write.chip == "sn76489":
            commands.extend((0x50, write.value))
        else:
            raise ValueError(f"unsupported VGM chip: {write.chip}")
    _wait(commands, max(0, total_samples - cursor))
    commands.append(0x66)
    output = header + commands
    struct.pack_into("<I", output, 0x04, len(output) - 4)
    return bytes(output)


def write_vgm(path: Path, writes: list[ChipWrite], total_samples: int, rate: int = 60) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encode_vgm(writes, total_samples, rate))
    return path


def renderer(path: Path | None = None) -> Path | None:
    """The YMFM renderer: `path`, else the one the environment names."""
    if path is None and os.environ.get(RENDERER_VARIABLE):
        path = Path(os.environ[RENDERER_VARIABLE])
    return path


def render_wav(vgm: Path, wav: Path, program: Path | None, cwd: Path | None = None) -> Path:
    """Play a VGM into a WAV with the YMFM renderer."""
    if program is None or not program.is_file():
        raise FileNotFoundError(f"the YMFM renderer is missing: {program}")
    wav.parent.mkdir(parents=True, exist_ok=True)
    # The renderer reports on stdout; the kit's own output is JSON.
    subprocess.run(
        [str(program), str(vgm), str(wav)], cwd=cwd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    return wav
