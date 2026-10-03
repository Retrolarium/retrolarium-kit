"""Trace and render Castle Excellent music track sets to WAV.

Ported from the source reconstruction's Sound Studio model
(scripts/authoring/sound_studio_model.py over
scripts/validation/audio/audio_stream_engine.py): each frame the engine
counts down the three channels' notes and, when one runs out, reads its MML
stream to the next note or rest. The tempo command is shared by all three
channels. The streams play from the music document as the codec packs them,
behind the playback prefix (semitone, length and APU period tables).
"""

from __future__ import annotations

from dataclasses import dataclass
import io
import json
import math
from pathlib import Path
import struct
from typing import Any, Mapping
import wave

from .authoring import AuthoringError
from .castle_mml import CODEC


NTSC_FRAME_RATE = 60.0988
CPU_FREQUENCY = 1_789_773.0
SAMPLE_RATE = 44_100
PREVIEW_GAIN = 1.25
CHANNELS = (("pulse1", 0), ("pulse2", 4), ("triangle", 8))
NOTE_OFFSETS = {ord(c): i for c, i in zip("abcdefg", (9, 11, 0, 2, 4, 5, 7))}
DEFAULT_LENGTH_INDICES = {ord("@"): 0, ord("8"): 1, ord("4"): 2, ord("2"): 3, ord("1"): 4}
SEMITONE_TABLE = 0          # offsets into the playback prefix
LENGTH_TABLE = 7
PERIOD_TABLE = 0x3F


class CastleAudioError(AuthoringError):
    """The music document cannot be traced or rendered."""


@dataclass
class _Stream:
    pointer: int
    ticks: int = 1
    octave: int = 0
    length_index: int = 0


@dataclass(frozen=True)
class LaneEvent:
    start: int
    duration: int
    frequency: float


def music_resource(value: Mapping[str, Any]) -> Mapping[str, Any]:
    """The music resource of a bundle, or the resource itself."""
    if value.get("codec") == CODEC:
        return value
    for resource in value.get("resources", []) if isinstance(value.get("resources"), list) else []:
        if isinstance(resource, Mapping) and resource.get("codec") == CODEC:
            return resource
    raise CastleAudioError("the document holds no castle.mml-music.v1 resource")


class _Memory:
    """The playback prefix and the packed MML arena, by CPU address."""

    def __init__(self, music: Mapping[str, Any]) -> None:
        self.prefix_cpu = int(music["playback_prefix_cpu_address"])
        self.mml_cpu = int(music["mml_cpu_address"])
        self.prefix = bytes(music["playback_prefix"])
        arena = bytearray()
        starts: dict[str, int] = {}
        streams = music["streams"]
        for stream in streams:
            starts[stream["id"]] = self.mml_cpu + len(arena)
            arena += stream["text"].encode("ascii") + b"\0\0\0"
        for index, stream in enumerate(streams):
            end = starts[stream["id"]] - self.mml_cpu + len(stream["text"])
            restart = starts.get(stream["restart"], 0) if stream["restart"] else 0
            arena[end + 1:end + 3] = restart.to_bytes(2, "little")
        self.arena = bytes(arena)
        self.starts = starts

    def read(self, address: int) -> int:
        if self.prefix_cpu <= address < self.prefix_cpu + len(self.prefix):
            return self.prefix[address - self.prefix_cpu]
        if self.mml_cpu <= address < self.mml_cpu + len(self.arena):
            return self.arena[address - self.mml_cpu]
        raise CastleAudioError(f"the music reads outside its data at ${address:04X}")

    def table(self, offset: int) -> int:
        return self.prefix[offset & 0xFF] if offset < len(self.prefix) else self.read(self.prefix_cpu + offset)


def _next_event(memory: _Memory, stream: _Stream, tempo: int, x: int) -> tuple[str | None, int, int]:
    """Read the stream to its next note or rest: (event, period index, tempo)."""
    pointer = stream.pointer
    visited: set[int] = set()
    while True:
        command = memory.read(pointer)
        if command == 0:
            restart = memory.read(pointer + 1) | memory.read(pointer + 2) << 8
            if restart == 0:
                stream.pointer = 0
                return None, 0, tempo
            if restart in visited:
                raise CastleAudioError("a music stream restarts without an event")
            visited.add(restart)
            pointer = restart
            continue
        pointer += 1
        if command == ord("o"):
            stream.octave = ((memory.read(pointer) - 0x32) * 12) & 0xFF
            pointer += 1
            continue
        if command == ord("t"):
            tempo = ((memory.read(pointer) - ord("0")) * 8) & 0xFF
            pointer += 1
            continue
        if command == ord("l"):
            stream.length_index = DEFAULT_LENGTH_INDICES.get(memory.read(pointer), 4)
            pointer += 1
            continue
        if command == ord("v"):
            pointer += 1
            continue
        event = "rest"
        period = 0
        if command in NOTE_OFFSETS:
            event = "note"
            semitone = memory.table(SEMITONE_TABLE + command - ord("a"))
            period = (((semitone + stream.octave) & 0xFF) + 1) << 1 & 0xFF
            accidental = memory.read(pointer)
            if accidental == ord("+"):
                period = (period + 2) & 0xFF
                pointer += 1
            elif accidental == ord("-"):
                period = (period - 2) & 0xFF
                pointer += 1
            if x == 8:
                period = (period + 0x18) & 0xFF
        elif command != ord("r"):
            raise CastleAudioError(f"unsupported stream command ${command:02X}")
        length_index = stream.length_index
        duration = memory.read(pointer)
        if duration == ord("@") or ord("0") <= duration < ord(":"):
            length_index = DEFAULT_LENGTH_INDICES.get(duration, (tempo + stream.length_index) & 0xFF)
            pointer += 1
            if duration not in DEFAULT_LENGTH_INDICES:
                ticks = memory.table(LENGTH_TABLE + length_index)
            else:
                ticks = memory.table(LENGTH_TABLE + ((tempo + length_index) & 0xFF))
        else:
            ticks = memory.table(LENGTH_TABLE + ((tempo + length_index) & 0xFF))
        if memory.read(pointer) == ord("."):
            ticks = (ticks + (ticks >> 1)) & 0xFF
            pointer += 1
        stream.pointer = pointer
        stream.ticks = ticks
        return event, period, tempo


def track_lanes(music: Mapping[str, Any], track: int, frames: int) -> dict[str, list[LaneEvent]]:
    """The notes and rests each channel of a track set plays in `frames`."""
    memory = _Memory(music)
    tracks = music["track_sets"]
    if not 0 <= track < len(tracks):
        raise CastleAudioError(f"track set {track} does not exist")
    record = tracks[track]
    streams = [_Stream(memory.starts[name]) if name else _Stream(0) for name in record["streams"]]
    lanes: dict[str, list[LaneEvent]] = {name: [] for name, _x in CHANNELS}
    tempo = 0
    for frame in range(frames):
        for (name, x), stream in zip(CHANNELS, streams):
            if stream.pointer == 0:
                continue
            stream.ticks = (stream.ticks - 1) & 0xFF
            if stream.ticks != 0:
                continue
            event, period, tempo = _next_event(memory, stream, tempo, x)
            if event is None:
                continue
            frequency = 0.0
            if event == "note":
                value = memory.table(PERIOD_TABLE + period) | memory.table(PERIOD_TABLE + period + 1) << 8
                frequency = CPU_FREQUENCY / ((32 if x == 8 else 16) * (value + 1))
            lanes[name].append(LaneEvent(frame, min(max(1, stream.ticks), frames - frame), frequency))
        if not any(stream.pointer for stream in streams):
            break
    return lanes


def _pulse(frequency: float, elapsed: float, parameter: int) -> float:
    if frequency <= 0:
        return 0.0
    duty = (0.125, 0.25, 0.5, 0.75)[(parameter >> 6) & 3]
    return float(parameter & 0x0F) if (elapsed * frequency) % 1.0 < duty else 0.0


def _triangle(frequency: float, elapsed: float) -> float:
    if frequency <= 0:
        return 0.0
    return abs(((elapsed * frequency) % 1.0) * 4.0 - 2.0) * 7.5


def _mix(pulse1: float, pulse2: float, triangle: float) -> float:
    pulse_sum = pulse1 + pulse2
    pulse = 0.0 if pulse_sum == 0 else 95.88 / ((8128.0 / pulse_sum) + 100.0)
    tnd_input = triangle / 8227.0
    tnd = 0.0 if tnd_input == 0 else 159.79 / ((1.0 / tnd_input) + 100.0)
    return pulse + tnd


class _OutputFilter:
    """The NES coupling and reconstruction filters, approximated."""

    def __init__(self, rate: int) -> None:
        def high(cutoff: float) -> float:
            constant = 1.0 / (2.0 * math.pi * cutoff)
            return constant / (constant + 1.0 / rate)
        constant = 1.0 / (2.0 * math.pi * 14_000.0)
        self.hp90 = high(90.0)
        self.hp440 = high(440.0)
        self.lp = (1.0 / rate) / (constant + 1.0 / rate)
        self.state = [0.0] * 5
        self.primed = False

    def process(self, value: float) -> float:
        s = self.state
        if not self.primed:
            s[0] = value
            self.primed = True
        hp90 = self.hp90 * (s[1] + value - s[0])
        s[0], s[1] = value, hp90
        hp440 = self.hp440 * (s[3] + hp90 - s[2])
        s[2], s[3] = hp90, hp440
        s[4] += self.lp * (hp440 - s[4])
        return s[4]


def render_wav(
    music: Mapping[str, Any], track: int, *, seconds: float = 20.0, muted: frozenset[str] = frozenset(),
) -> bytes:
    frames = max(1, round(seconds * NTSC_FRAME_RATE))
    lanes = track_lanes(music, track, frames)
    total = max((events[-1].start + events[-1].duration for events in lanes.values() if events), default=1)
    parameters = list(music["track_sets"][track]["channel_parameters"]) + [0]
    samples = max(1, round(total * SAMPLE_RATE / NTSC_FRAME_RATE))
    cursors = [0, 0, 0]
    output = _OutputFilter(SAMPLE_RATE)
    pcm = bytearray()
    for sample in range(samples):
        frame = sample * NTSC_FRAME_RATE / SAMPLE_RATE
        values = [0.0, 0.0, 0.0]
        for index, (name, _x) in enumerate(CHANNELS):
            events = lanes[name]
            if name in muted or not events:
                continue
            while cursors[index] + 1 < len(events) and \
                    frame >= events[cursors[index]].start + events[cursors[index]].duration:
                cursors[index] += 1
            event = events[cursors[index]]
            if not event.start <= frame < event.start + event.duration:
                continue
            # The engine gates a note three frames before it ends.
            if event.duration > 3 and frame >= event.start + event.duration - 3:
                continue
            elapsed = (frame - event.start) / NTSC_FRAME_RATE
            values[index] = _triangle(event.frequency, elapsed) if index == 2 \
                else _pulse(event.frequency, elapsed, parameters[index])
        value = max(-1.0, min(1.0, output.process(_mix(*values)) * PREVIEW_GAIN))
        pcm += struct.pack("<h", round(value * 32767))
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(SAMPLE_RATE)
        stream.writeframes(bytes(pcm))
    return buffer.getvalue()


def render_castle_wav(
    document: Path, output: Path, *, track: int, seconds: float = 20.0, muted: tuple[str, ...] = (),
) -> dict[str, object]:
    try:
        value = json.loads(document.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CastleAudioError(f"cannot read {document}: {exc}") from exc
    music = music_resource(value)
    unknown = set(muted) - {name for name, _x in CHANNELS}
    if unknown:
        raise CastleAudioError(f"unknown channels: {', '.join(sorted(unknown))}")
    if not 0 < seconds <= 600:
        raise CastleAudioError("seconds must be within 0-600")
    payload = render_wav(music, track, seconds=seconds, muted=frozenset(muted))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(payload)
    return {"schema": "retrolarium.castle-wav", "output": str(output), "track": track,
            "bytes": len(payload), "seconds": seconds}
