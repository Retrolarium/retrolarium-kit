"""Pac-Man NES sound streams, MIDI lowering and deterministic preview."""

from __future__ import annotations

from dataclasses import dataclass
import io
import math
import struct
import wave
from typing import Mapping

from .authoring import AuthoringError
from .midi import MidiError, parse_midi, tick_seconds


CODEC = "pacman.sound-streams.v1"
NTSC_FRAME_RATE = 60.0988
CPU_FREQUENCY = 1_789_773.0
BASE_MIDI_NOTE = 45
BASE_PERIODS = (
    0x3F9, 0x3C0, 0x38A, 0x357, 0x327, 0x2FA,
    0x2CF, 0x2A7, 0x281, 0x25D, 0x23B, 0x21B,
)
DUTY_CYCLES = (0.125, 0.25, 0.5, 0.75)


class PacmanSoundError(AuthoringError):
    """A Pac-Man sound profile, stream or conversion is invalid."""


@dataclass(frozen=True)
class SoundLayout:
    pointer_file_offset: int
    arena_file_offset: int
    arena_cpu_address: int
    capacity: int
    storage_order: tuple[int, ...]
    labels: tuple[str, ...]
    trailing_sizes: tuple[int, ...]


def _integer(
    value: object, name: str, *, minimum: int = 0, maximum: int = 0xFFFFFFFF
) -> int:
    if (not isinstance(value, int) or isinstance(value, bool)
            or not minimum <= value <= maximum):
        raise PacmanSoundError(f"{name} must be an integer in {minimum}..{maximum}")
    return value


def _bytes(value: object, name: str, *, count: int | None = None) -> bytes:
    if not isinstance(value, list) or (count is not None and len(value) != count):
        suffix = "a byte array" if count is None else f"exactly {count} bytes"
        raise PacmanSoundError(f"{name} must contain {suffix}")
    return bytes(_integer(item, f"{name}[{index}]", maximum=0xFF)
                 for index, item in enumerate(value))


def parse_layout(parameters: Mapping[str, object]) -> SoundLayout:
    required = {
        "pointer_file_offset", "arena_file_offset", "arena_cpu_address",
        "capacity", "storage_order", "labels", "trailing_sizes",
    }
    if set(parameters) != required:
        raise PacmanSoundError("Pac-Man sound layout parameters have the wrong shape")
    pointer_offset = _integer(parameters.get("pointer_file_offset"), "pointer_file_offset")
    arena_offset = _integer(parameters.get("arena_file_offset"), "arena_file_offset")
    arena_address = _integer(
        parameters.get("arena_cpu_address"), "arena_cpu_address", maximum=0xFFFF
    )
    capacity = _integer(parameters.get("capacity"), "capacity", minimum=1)
    order_value = parameters.get("storage_order")
    labels_value = parameters.get("labels")
    trailing_value = parameters.get("trailing_sizes")
    if not isinstance(order_value, list) or len(order_value) != 16:
        raise PacmanSoundError("storage_order must contain 16 slots")
    order = tuple(_integer(item, "storage_order", maximum=15) for item in order_value)
    if set(order) != set(range(16)):
        raise PacmanSoundError("storage_order must be a permutation of slots 0..15")
    if (not isinstance(labels_value, list) or len(labels_value) != 16
            or any(not isinstance(item, str) or not item for item in labels_value)):
        raise PacmanSoundError("labels must contain 16 non-empty strings")
    if not isinstance(trailing_value, list) or len(trailing_value) != 16:
        raise PacmanSoundError("trailing_sizes must contain 16 sizes")
    trailing_sizes = tuple(
        _integer(item, f"trailing_sizes[{index}]")
        for index, item in enumerate(trailing_value)
    )
    if arena_address + capacity > 0x10000:
        raise PacmanSoundError("sound arena exceeds the CPU address space")
    return SoundLayout(
        pointer_offset, arena_offset, arena_address, capacity,
        order, tuple(labels_value), trailing_sizes,
    )


def sound_byte_to_midi(value: int) -> int:
    value = _integer(value, "sound note", maximum=0xBF)
    return BASE_MIDI_NOTE + (value >> 4) + 12 * (value & 0x0F)


def midi_to_sound_byte(note: int) -> int:
    note = _integer(note, "MIDI note", maximum=127)
    delta = note - BASE_MIDI_NOTE
    if not 0 <= delta <= 11 + 12 * 15:
        raise PacmanSoundError("MIDI note is outside the Pac-Man sequencer range")
    octave, semitone = divmod(delta, 12)
    return semitone << 4 | octave


def midi_note_name(note: int) -> str:
    note = _integer(note, "MIDI note", maximum=127)
    names = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
    return f"{names[note % 12]}{note // 12 - 1}"


def decode_stream(payload: bytes) -> dict[str, object]:
    if len(payload) < 5:
        raise PacmanSoundError("sound stream is shorter than its prologue and stop")
    cursor = 4
    commands = []
    stopped = False
    while cursor < len(payload):
        value = payload[cursor]
        cursor += 1
        if value < 0xC0:
            if cursor >= len(payload):
                raise PacmanSoundError("sound note lacks a duration")
            commands.append({"kind": "note", "value": value,
                             "duration": payload[cursor]})
            cursor += 1
        elif value < 0xF0:
            if cursor >= len(payload):
                raise PacmanSoundError("sound duration marker lacks a duration")
            commands.append({"kind": "duration", "marker": value,
                             "duration": payload[cursor]})
            cursor += 1
        elif 0xF1 <= value <= 0xF6:
            if cursor >= len(payload):
                raise PacmanSoundError("sound control opcode lacks an operand")
            commands.append({"kind": "control", "opcode": value,
                             "operand": payload[cursor]})
            cursor += 1
        else:
            commands.append({"kind": "control", "opcode": value})
            stopped = True
            break
    if not stopped:
        raise PacmanSoundError("sound stream has no stop opcode")
    return {
        "prologue": list(payload[:4]),
        "commands": commands,
        "trailing_bytes": list(payload[cursor:]),
    }


def encode_stream(stream: Mapping[str, object], name: str = "stream") -> bytes:
    if set(stream) != {"prologue", "commands", "trailing_bytes"}:
        raise PacmanSoundError(f"{name} has the wrong shape")
    output = bytearray(_bytes(stream.get("prologue"), f"{name}.prologue", count=4))
    commands = stream.get("commands")
    if not isinstance(commands, list) or not commands:
        raise PacmanSoundError(f"{name}.commands must be a non-empty array")
    stopped = False
    for index, command in enumerate(commands):
        context = f"{name}.commands[{index}]"
        if not isinstance(command, Mapping):
            raise PacmanSoundError(f"{context} must be an object")
        kind = command.get("kind")
        if kind == "note":
            if set(command) != {"kind", "value", "duration"}:
                raise PacmanSoundError(f"{context} has the wrong note shape")
            output.extend((_integer(command.get("value"), context, maximum=0xBF),
                           _integer(command.get("duration"), context, minimum=1, maximum=0xFF)))
        elif kind == "duration":
            if set(command) != {"kind", "marker", "duration"}:
                raise PacmanSoundError(f"{context} has the wrong duration shape")
            output.extend((_integer(command.get("marker"), context, minimum=0xC0, maximum=0xEF),
                           _integer(command.get("duration"), context, minimum=1, maximum=0xFF)))
        elif kind == "control":
            opcode = _integer(command.get("opcode"), context, minimum=0xF0, maximum=0xFF)
            expected = {"kind", "opcode", "operand"} if 0xF1 <= opcode <= 0xF6 \
                else {"kind", "opcode"}
            if set(command) != expected:
                raise PacmanSoundError(f"{context} has the wrong control shape")
            output.append(opcode)
            if 0xF1 <= opcode <= 0xF6:
                output.append(_integer(command.get("operand"), context, maximum=0xFF))
            else:
                if index != len(commands) - 1:
                    raise PacmanSoundError(f"{name} has commands after its stop opcode")
                stopped = True
        else:
            raise PacmanSoundError(f"{context}.kind is unsupported")
    if not stopped:
        raise PacmanSoundError(f"{name} has no stop opcode")
    output.extend(_bytes(stream.get("trailing_bytes"), f"{name}.trailing_bytes"))
    return bytes(output)


def decode_sound_bundle(
    source: bytes, parameters: Mapping[str, object]
) -> dict[str, object]:
    layout = parse_layout(parameters)
    pointer_data = source[
        layout.pointer_file_offset : layout.pointer_file_offset + 32
    ]
    arena = source[layout.arena_file_offset : layout.arena_file_offset + layout.capacity]
    if len(pointer_data) != 32 or len(arena) != layout.capacity:
        raise PacmanSoundError("sound pointer table or arena exceeds the source image")
    pointers = tuple(int.from_bytes(pointer_data[index:index + 2], "little")
                     for index in range(0, 32, 2))
    ordered = tuple(pointers[slot] for slot in layout.storage_order)
    if ordered[0] != layout.arena_cpu_address or any(
            right <= left for left, right in zip(ordered, ordered[1:])):
        raise PacmanSoundError("sound pointers contradict the configured storage order")
    if ordered[-1] >= layout.arena_cpu_address + layout.capacity:
        raise PacmanSoundError("sound pointer lies outside the configured arena")
    ends = (*ordered[1:], layout.arena_cpu_address + layout.capacity)
    slots: list[dict[str, object] | None] = [None] * 16
    for slot, start, end in zip(layout.storage_order, ordered, ends):
        relative = start - layout.arena_cpu_address
        segment = arena[relative : relative + end - start]
        decoded = decode_stream(segment)
        trailing = decoded["trailing_bytes"]
        expected_trailing = layout.trailing_sizes[slot]
        if slot == layout.storage_order[-1]:
            if len(trailing) < expected_trailing or any(
                    value != 0xFF for value in trailing[expected_trailing:]):
                raise PacmanSoundError(
                    "last sound stream has invalid trailing bytes or arena padding"
                )
            decoded["trailing_bytes"] = trailing[:expected_trailing]
        elif len(trailing) != expected_trailing:
            raise PacmanSoundError(
                f"sound slot {slot:02X} has {len(trailing)} trailing bytes, "
                f"expected {expected_trailing}"
            )
        slots[slot] = {
            "slot": slot,
            "label": layout.labels[slot],
            "stream": decoded,
        }
    return {
        "capacity": layout.capacity,
        "slots": [item for item in slots if item is not None],
    }


def encode_sound_bundle(
    document: Mapping[str, object], parameters: Mapping[str, object]
) -> tuple[tuple[int, bytes], ...]:
    layout = parse_layout(parameters)
    if document.get("capacity") != layout.capacity:
        raise PacmanSoundError("sound capacity differs from the profile")
    slots = document.get("slots")
    if not isinstance(slots, list) or len(slots) != 16:
        raise PacmanSoundError("sound resource must contain 16 ordered slots")
    encoded = []
    for slot, item in enumerate(slots):
        if not isinstance(item, Mapping) or set(item) != {"slot", "label", "stream"}:
            raise PacmanSoundError(f"sound slot {slot:02X} has the wrong shape")
        if (item.get("slot"), item.get("label")) != (slot, layout.labels[slot]):
            raise PacmanSoundError(f"sound slot {slot:02X} metadata differs from the profile")
        stream = item.get("stream")
        if not isinstance(stream, Mapping):
            raise PacmanSoundError(f"sound slot {slot:02X}.stream must be an object")
        payload = encode_stream(stream, f"sound slot {slot:02X}")
        if len(stream.get("trailing_bytes", [])) != layout.trailing_sizes[slot]:
            raise PacmanSoundError(
                f"sound slot {slot:02X} trailing size differs from the profile"
            )
        encoded.append(payload)
    used = sum(len(encoded[slot]) for slot in layout.storage_order)
    if used > layout.capacity:
        raise PacmanSoundError(
            f"sound streams use {used} bytes but the arena holds {layout.capacity}"
        )
    pointers = [0] * 16
    arena = bytearray()
    cursor = layout.arena_cpu_address
    for slot in layout.storage_order:
        pointers[slot] = cursor
        arena.extend(encoded[slot])
        cursor += len(encoded[slot])
    arena.extend(b"\xFF" * (layout.capacity - len(arena)))
    pointer_data = b"".join(pointer.to_bytes(2, "little") for pointer in pointers)
    return (
        (layout.pointer_file_offset, pointer_data),
        (layout.arena_file_offset, bytes(arena)),
    )


def sound_summary(document: Mapping[str, object], parameters: Mapping[str, object]) -> dict[str, int]:
    layout = parse_layout(parameters)
    encode_sound_bundle(document, parameters)
    slots = document["slots"]
    used = sum(len(encode_stream(item["stream"])) for item in slots)
    return {
        "slots": 16,
        "notes": sum(
            command.get("kind") == "note"
            for item in slots for command in item["stream"]["commands"]
        ),
        "used_bytes": used,
        "capacity": layout.capacity,
        "free_bytes": layout.capacity - used,
    }


def import_midi_commands(
    data: bytes, track: int, channel: int
) -> tuple[list[dict[str, object]], dict[str, object]]:
    try:
        parsed = parse_midi(data, track, channel)
    except MidiError as exc:
        raise PacmanSoundError(str(exc)) from exc
    if not parsed.notes:
        raise PacmanSoundError("selected MIDI track contains no notes")
    commands: list[dict[str, object]] = []
    previous_end = 0
    split_notes = 0
    source_seconds = 0.0
    output_frames = 0
    quantization_errors = []
    for note in parsed.notes:
        if note.start_tick != previous_end:
            raise PacmanSoundError(
                "MIDI rests and leading silence are not representable by the Pac-Man engine"
            )
        seconds = (
            tick_seconds(note.end_tick, parsed.division, parsed.tempos)
            - tick_seconds(note.start_tick, parsed.division, parsed.tempos)
        )
        exact_frames = seconds * NTSC_FRAME_RATE
        frames = max(1, round(exact_frames))
        source_seconds += seconds
        output_frames += frames
        quantization_errors.append(abs(frames / NTSC_FRAME_RATE - seconds) * 1000.0)
        remaining = frames
        parts = 0
        while remaining:
            duration = min(remaining, 255)
            commands.append({
                "kind": "note",
                "value": midi_to_sound_byte(note.note),
                "duration": duration,
            })
            remaining -= duration
            parts += 1
        split_notes += max(0, parts - 1)
        previous_end = note.end_tick
    commands.append({"kind": "control", "opcode": 0xF0})
    discarded_reasons = {
        "velocity": "the engine has no per-note velocity",
        "instrument": "the target slot keeps its original APU prologue",
        "controller": "MIDI controllers have no Pac-Man bytecode equivalent",
        "polyphonic_aftertouch": "aftertouch has no Pac-Man bytecode equivalent",
        "channel_aftertouch": "aftertouch has no Pac-Man bytecode equivalent",
        "pitch_bend": "continuous pitch bend has no Pac-Man bytecode equivalent",
    }
    return commands, {
        "schema": "retrolarium.midi-import-report",
        "schema_version": 1,
        "engine": CODEC,
        "track": track,
        "channel": channel,
        "source_notes": len(parsed.notes),
        "tempo_events": len(parsed.tempos),
        "output_note_commands": len(commands) - 1,
        "split_notes": split_notes,
        "output_frames": output_frames,
        "quantization": {
            "frame_rate": NTSC_FRAME_RATE,
            "method": "nearest NTSC frame; minimum one frame",
            "source_seconds": round(source_seconds, 6),
            "output_seconds": round(output_frames / NTSC_FRAME_RATE, 6),
            "total_error_ms": round(abs(output_frames / NTSC_FRAME_RATE
                                        - source_seconds) * 1000.0, 3),
            "maximum_note_error_ms": round(max(quantization_errors), 3),
        },
        "preserved": ["monophonic note order", "pitch", "tempo-derived duration"],
        "discarded": [
            {"feature": feature, "events": count, "reason": discarded_reasons[feature]}
            for feature, count in parsed.discarded_events
        ],
        "rejected_instead_of_discarded": [
            "polyphony", "rests or leading silence", "out-of-range pitch",
            "SMPTE timing", "unclosed notes",
        ],
    }


def note_frequency(value: int) -> float:
    value = _integer(value, "sound note", maximum=0xBF)
    period = BASE_PERIODS[value >> 4] >> (value & 0x0F)
    return CPU_FREQUENCY / (16.0 * (period + 1))


# The APU's length-counter loads ($4003 bits 3-7); its frame sequencer
# clocks envelopes at ~240 Hz and length counters at ~120 Hz (as the game
# sounds in MAME).
LENGTH_TABLE = (
    10, 254, 20, 2, 40, 4, 80, 6, 160, 8, 60, 10, 14, 12, 26, 14,
    12, 16, 24, 18, 48, 20, 96, 22, 192, 24, 72, 26, 16, 28, 32, 30,
)
ENVELOPE_RATE = 240.0
LENGTH_RATE = 120.0


def render_stream_pcm(stream: Mapping[str, object], sample_rate: int = 44_100) -> bytes:
    """A square channel as the engine drives it: the prologue's $4000 byte
    (duty, constant volume or envelope, halt) and $4003 byte (length load);
    each note rewrites the quad, restarting the envelope and length."""

    sample_rate = _integer(sample_rate, "sample_rate", minimum=1, maximum=384_000)
    encode_stream(stream)
    prologue = stream["prologue"]
    register0 = int(prologue[1])
    control = int(prologue[3])
    frequency = 0.0
    phase = 0.0
    envelope = 0
    divider = 0
    length = 0
    envelope_clock = 0.0
    length_clock = 0.0
    samples = bytearray()
    for command in stream["commands"]:
        kind = command["kind"]
        if kind == "control":
            opcode = int(command["opcode"])
            if opcode == 0xF6:
                register0 = int(command["operand"])
            if opcode == 0xF0 or opcode >= 0xF7:
                break
            continue
        if kind == "note":
            frequency = note_frequency(int(command["value"]))
            envelope = 15
            divider = register0 & 0x0F
            length = LENGTH_TABLE[control >> 3]
        duration = int(command["duration"])
        count = round(duration * sample_rate / NTSC_FRAME_RATE)
        for _ in range(count):
            halt = bool(register0 & 0x20)
            level = register0 & 0x0F if register0 & 0x10 else envelope
            sounding = frequency and (halt or length > 0)
            volume = level / 15.0 if sounding else 0.0
            duty = DUTY_CYCLES[(register0 >> 6) & 3]
            amplitude = volume if phase < duty else -volume
            samples.extend(struct.pack("<h", round(amplitude * 12_000)))
            if frequency:
                phase = math.fmod(phase + frequency / sample_rate, 1.0)
            envelope_clock += ENVELOPE_RATE / sample_rate
            while envelope_clock >= 1.0:
                envelope_clock -= 1.0
                if divider == 0:
                    divider = register0 & 0x0F
                    if envelope > 0:
                        envelope -= 1
                    elif halt:
                        envelope = 15
                else:
                    divider -= 1
            length_clock += LENGTH_RATE / sample_rate
            while length_clock >= 1.0:
                length_clock -= 1.0
                if not halt and length > 0:
                    length -= 1
    return bytes(samples)


def mix_pcm(parts: list[bytes]) -> bytes:
    """Sum 16-bit mono PCM streams (the APU's square channels play together)."""

    length = max((len(part) for part in parts), default=0) // 2
    total = [0] * length
    for part in parts:
        for index, (sample,) in enumerate(struct.iter_unpack("<h", part)):
            total[index] += sample
    return b"".join(struct.pack("<h", max(-32_768, min(32_767, sample))) for sample in total)


def render_stream_wav(stream: Mapping[str, object], sample_rate: int = 44_100,
                      *, also: tuple[Mapping[str, object], ...] = ()) -> bytes:
    pcm = render_stream_pcm(stream, sample_rate)
    if also:
        pcm = mix_pcm([pcm, *(render_stream_pcm(other, sample_rate) for other in also)])
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return output.getvalue()
