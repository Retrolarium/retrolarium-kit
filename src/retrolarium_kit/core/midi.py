"""Dependency-free Standard MIDI parsing for engine-aware importers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from retrolarium_kit.core.authoring import AuthoringError


class MidiError(AuthoringError):
    """A Standard MIDI file cannot be represented by the requested importer."""


@dataclass(frozen=True)
class MidiNote:
    note: int
    start_tick: int
    end_tick: int


@dataclass(frozen=True)
class MidiTrack:
    division: int
    notes: tuple[MidiNote, ...]
    tempos: tuple[tuple[int, int], ...]
    discarded_events: tuple[tuple[str, int], ...]
    end_tick: int = 0


def _varlen(data: bytes, cursor: int) -> tuple[int, int]:
    value = 0
    for _ in range(4):
        if cursor >= len(data):
            raise MidiError("truncated MIDI variable-length value")
        byte = data[cursor]
        cursor += 1
        value = value << 7 | (byte & 0x7F)
        if byte < 0x80:
            return value, cursor
    raise MidiError("MIDI variable-length value exceeds four bytes")


def _track_events(
    data: bytes,
    channel: int,
    collect_channel: bool = True,
) -> tuple[list[MidiNote], list[tuple[int, int]], dict[str, int], int]:
    cursor = 0
    tick = 0
    running_status: int | None = None
    active: dict[int, int] = {}
    # A note that starts on the tick the sounding one ends, written before
    # that one's note-off (legato, as some programs order them).
    waiting: tuple[int, int] | None = None
    notes: list[MidiNote] = []
    tempos: list[tuple[int, int]] = []
    discarded: dict[str, int] = {}
    while cursor < len(data):
        delta, cursor = _varlen(data, cursor)
        tick += delta
        if waiting is not None and tick > waiting[1]:
            raise MidiError("MIDI import requires a monophonic channel")
        if cursor >= len(data):
            raise MidiError("truncated MIDI event")
        status = data[cursor]
        if status < 0x80:
            if running_status is None:
                raise MidiError("MIDI running status has no channel event")
            status = running_status
        else:
            cursor += 1
            if status < 0xF0:
                running_status = status
            else:
                running_status = None
        if status == 0xFF:
            if cursor >= len(data):
                raise MidiError("truncated MIDI meta event")
            kind = data[cursor]
            cursor += 1
            length, cursor = _varlen(data, cursor)
            payload = data[cursor : cursor + length]
            if len(payload) != length:
                raise MidiError("truncated MIDI meta payload")
            cursor += length
            if kind == 0x51:
                if length != 3:
                    raise MidiError("MIDI tempo event must contain three bytes")
                tempo = int.from_bytes(payload, "big")
                if tempo == 0:
                    raise MidiError("MIDI tempo must be positive")
                tempos.append((tick, tempo))
            if kind == 0x2F:
                break
            continue
        if status in (0xF0, 0xF7):
            length, cursor = _varlen(data, cursor)
            if cursor + length > len(data):
                raise MidiError("truncated MIDI system-exclusive event")
            cursor += length
            continue
        event = status & 0xF0
        event_channel = status & 0x0F
        if event < 0x80 or event > 0xE0:
            raise MidiError(f"unsupported MIDI status ${status:02X}")
        length = 1 if event in (0xC0, 0xD0) else 2
        payload = data[cursor : cursor + length]
        if len(payload) != length:
            raise MidiError("truncated MIDI channel event")
        cursor += length
        if event_channel != channel:
            continue
        if not collect_channel:
            continue
        if event not in (0x80, 0x90):
            feature = {
                0xA0: "polyphonic_aftertouch",
                0xB0: "controller",
                0xC0: "instrument",
                0xD0: "channel_aftertouch",
                0xE0: "pitch_bend",
            }[event]
            discarded[feature] = discarded.get(feature, 0) + 1
            continue
        note = payload[0]
        is_on = event == 0x90 and payload[1] != 0
        if is_on:
            if active:
                if waiting is not None:
                    raise MidiError("MIDI import requires a monophonic channel")
                waiting = (note, tick)
                continue
            active[note] = tick
        elif note in active:
            start = active.pop(note)
            if tick <= start:
                raise MidiError("MIDI notes must have positive duration")
            notes.append(MidiNote(note, start, tick))
            if waiting is not None and not active:
                active[waiting[0]] = waiting[1]
                waiting = None
    if active or waiting is not None:
        raise MidiError("MIDI note remains active at end of track")
    if notes:
        discarded["velocity"] = len(notes)
    return notes, tempos, discarded, tick


def parse_midi(data: bytes, track_index: int, channel: int) -> MidiTrack:
    if not 0 <= channel < 16:
        raise MidiError("MIDI channel must be in 0..15")
    if len(data) < 14 or data[:4] != b"MThd":
        raise MidiError("not a Standard MIDI file")
    header_size = int.from_bytes(data[4:8], "big")
    if header_size < 6 or 8 + header_size > len(data):
        raise MidiError("invalid MIDI header length")
    track_count = int.from_bytes(data[10:12], "big")
    division = int.from_bytes(data[12:14], "big")
    if not track_count or not division:
        raise MidiError("MIDI track count and division must be positive")
    if division & 0x8000:
        raise MidiError("SMPTE MIDI timing is not supported")
    if not 0 <= track_index < track_count:
        raise MidiError("MIDI track index is out of range")
    cursor = 8 + header_size
    selected: list[MidiNote] | None = None
    selected_discarded: dict[str, int] = {}
    selected_end_tick = 0
    tempos: list[tuple[int, int]] = []
    index = 0
    while index < track_count:
        if cursor + 8 > len(data):
            raise MidiError("missing MIDI track chunk")
        length = int.from_bytes(data[cursor + 4 : cursor + 8], "big")
        if data[cursor : cursor + 4] != b"MTrk":
            # Another kind of chunk: readers skip what they do not know.
            if cursor + 8 + length > len(data):
                raise MidiError("truncated MIDI chunk")
            cursor += 8 + length
            continue
        payload = data[cursor + 8 : cursor + 8 + length]
        if len(payload) != length:
            raise MidiError("truncated MIDI track")
        notes, track_tempos, discarded, end_tick = _track_events(payload, channel, index == track_index)
        tempos.extend(track_tempos)
        if index == track_index:
            selected = notes
            selected_discarded = discarded
            selected_end_tick = end_tick
        cursor += 8 + length
        index += 1
    while cursor + 8 <= len(data) and data[cursor : cursor + 4] != b"MTrk":
        cursor += 8 + int.from_bytes(data[cursor + 4 : cursor + 8], "big")
    if cursor != len(data):
        raise MidiError("MIDI file has trailing data")
    return MidiTrack(
        division,
        tuple(selected or ()),
        tuple(sorted(tempos)),
        tuple(sorted(selected_discarded.items())),
        selected_end_tick,
    )


def parse_midi_channel(data: bytes, channel: int) -> MidiTrack:
    """Read one MIDI channel across all format-0 or format-1 tracks."""
    if len(data) < 14 or data[:4] != b"MThd":
        raise MidiError("not a Standard MIDI file")
    track_count = int.from_bytes(data[10:12], "big")
    combined: list[MidiNote] = []
    discarded: dict[str, int] = {}
    reference: MidiTrack | None = None
    end_tick = 0
    for track_index in range(track_count):
        parsed = parse_midi(data, track_index, channel)
        if reference is None:
            reference = parsed
        combined.extend(parsed.notes)
        end_tick = max(end_tick, parsed.end_tick)
        for feature, count in parsed.discarded_events:
            discarded[feature] = discarded.get(feature, 0) + count
    assert reference is not None
    ordered = tuple(sorted(combined, key=lambda note: (note.start_tick, note.end_tick, note.note)))
    previous_end = 0
    for note in ordered:
        if note.start_tick < previous_end:
            raise MidiError("MIDI import requires a monophonic channel across all tracks")
        previous_end = note.end_tick
    return MidiTrack(
        reference.division,
        ordered,
        reference.tempos,
        tuple(sorted(discarded.items())),
        end_tick,
    )


def tick_seconds(tick: int, division: int, tempos: tuple[tuple[int, int], ...]) -> float:
    elapsed = 0.0
    previous_tick = 0
    tempo = 500_000
    for change_tick, new_tempo in tempos:
        if change_tick >= tick:
            break
        if change_tick < previous_tick:
            raise MidiError("MIDI tempo events are not ordered")
        elapsed += (change_tick - previous_tick) * tempo / (division * 1_000_000)
        previous_tick = change_tick
        tempo = new_tempo
    return elapsed + (tick - previous_tick) * tempo / (division * 1_000_000)


def _encode_varlen(value: int) -> bytes:
    if not 0 <= value <= 0x0FFFFFFF:
        raise MidiError("MIDI delta time is outside the variable-length range")
    encoded = bytearray((value & 0x7F,))
    value >>= 7
    while value:
        encoded.insert(0, 0x80 | (value & 0x7F))
        value >>= 7
    return bytes(encoded)


def encode_midi(notes: tuple[MidiNote, ...], *, division: int = 960, tempo: int = 500_000) -> bytes:
    """Encode one monophonic channel as a format-0 Standard MIDI file."""
    return encode_midi_channels({0: notes}, division=division, tempo=tempo)


def encode_midi_channels(
    channels: Mapping[int, tuple[MidiNote, ...]],
    *,
    division: int = 960,
    tempo: int = 500_000,
    end_tick: int | None = None,
) -> bytes:
    """Encode independent monophonic channels in one format-0 MIDI track."""
    if not 1 <= division < 0x8000:
        raise MidiError("MIDI division must be in 1..32767")
    if not 1 <= tempo <= 0xFFFFFF:
        raise MidiError("MIDI tempo must be in 1..16777215 microseconds")
    events: list[tuple[int, int, int, int]] = []
    latest_tick = 0
    for channel, notes in sorted(channels.items()):
        if not 0 <= channel < 16:
            raise MidiError("MIDI channel must be in 0..15")
        ordered = tuple(sorted(notes, key=lambda note: (note.start_tick, note.end_tick)))
        previous_end = 0
        for note in ordered:
            if not 0 <= note.note <= 127:
                raise MidiError("MIDI note is outside 0..127")
            if note.start_tick < previous_end:
                raise MidiError("MIDI export requires a monophonic note sequence per channel")
            if note.end_tick <= note.start_tick:
                raise MidiError("MIDI notes must have positive duration")
            events.append((note.start_tick, 1, channel, note.note))
            events.append((note.end_tick, 0, channel, note.note))
            previous_end = note.end_tick
            latest_tick = max(latest_tick, note.end_tick)
    if end_tick is not None:
        if end_tick < latest_tick:
            raise MidiError("MIDI end tick precedes a note")
        latest_tick = end_tick

    track = bytearray(b"\x00\xff\x51\x03" + tempo.to_bytes(3, "big"))
    cursor = 0
    for tick, event_kind, channel, note in sorted(events):
        track.extend(_encode_varlen(tick - cursor))
        if event_kind == 0:
            track.extend((0x80 | channel, note, 0))
        else:
            track.extend((0x90 | channel, note, 100))
        cursor = tick
    track.extend(_encode_varlen(latest_tick - cursor))
    track.extend(b"\xff\x2f\x00")
    return (
        b"MThd"
        + (6).to_bytes(4, "big")
        + (0).to_bytes(2, "big")
        + (1).to_bytes(2, "big")
        + division.to_bytes(2, "big")
        + b"MTrk"
        + len(track).to_bytes(4, "big")
        + bytes(track)
    )
