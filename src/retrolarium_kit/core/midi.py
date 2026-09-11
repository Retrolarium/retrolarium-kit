"""Dependency-free Standard MIDI parsing for engine-aware importers."""

from __future__ import annotations

from dataclasses import dataclass

from .authoring import AuthoringError


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
    data: bytes, channel: int
) -> tuple[list[MidiNote], list[tuple[int, int]], dict[str, int]]:
    cursor = 0
    tick = 0
    running_status: int | None = None
    active: dict[int, int] = {}
    notes: list[MidiNote] = []
    tempos: list[tuple[int, int]] = []
    discarded: dict[str, int] = {}
    while cursor < len(data):
        delta, cursor = _varlen(data, cursor)
        tick += delta
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
                raise MidiError("MIDI import requires a monophonic channel")
            active[note] = tick
        elif note in active:
            start = active.pop(note)
            if tick <= start:
                raise MidiError("MIDI notes must have positive duration")
            notes.append(MidiNote(note, start, tick))
    if active:
        raise MidiError("MIDI note remains active at end of track")
    if notes:
        discarded["velocity"] = len(notes)
    return notes, tempos, discarded


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
    tempos: list[tuple[int, int]] = []
    for index in range(track_count):
        if cursor + 8 > len(data) or data[cursor : cursor + 4] != b"MTrk":
            raise MidiError("missing MIDI track chunk")
        length = int.from_bytes(data[cursor + 4 : cursor + 8], "big")
        payload = data[cursor + 8 : cursor + 8 + length]
        if len(payload) != length:
            raise MidiError("truncated MIDI track")
        notes, track_tempos, discarded = _track_events(payload, channel)
        tempos.extend(track_tempos)
        if index == track_index:
            selected = notes
            selected_discarded = discarded
        cursor += 8 + length
    if cursor != len(data):
        raise MidiError("MIDI file has trailing data")
    return MidiTrack(
        division, tuple(selected or ()), tuple(sorted(tempos)),
        tuple(sorted(selected_discarded.items())),
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
