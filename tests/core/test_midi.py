from __future__ import annotations

from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from retrolarium_kit.core.midi import (
    MidiError,
    MidiNote,
    encode_midi,
    encode_midi_channels,
    parse_midi,
    parse_midi_channel,
    tick_seconds,
)


def variable(value: int) -> bytes:
    encoded = [value & 0x7F]
    value >>= 7
    while value:
        encoded.append(0x80 | (value & 0x7F))
        value >>= 7
    return bytes(reversed(encoded))


def midi_file(*tracks: bytes, division: int = 96, format_type: int = 1) -> bytes:
    header = (
        b"MThd"
        + (6).to_bytes(4, "big")
        + format_type.to_bytes(2, "big")
        + len(tracks).to_bytes(2, "big")
        + division.to_bytes(2, "big")
    )
    return header + b"".join(b"MTrk" + len(track).to_bytes(4, "big") + track for track in tracks)


class MidiTests(unittest.TestCase):
    def test_encode_round_trip_preserves_notes_and_tempo(self) -> None:
        notes = (MidiNote(60, 0, 240), MidiNote(64, 360, 840))
        encoded = encode_midi(notes, division=960, tempo=500_000)
        parsed = parse_midi(encoded, 0, 0)
        self.assertEqual(notes, parsed.notes)
        self.assertEqual(((0, 500_000),), parsed.tempos)

    def test_parses_selected_channel_running_status_and_global_tempo(self) -> None:
        tempo = b"\x00\xff\x51\x03\x03\xd0\x90\x00\xff\x2f\x00"
        notes = (
            b"\x00\xc0\x05\x00\xb0\x07\x64"
            b"\x00\x90\x45\x64"
            + variable(96)
            + b"\x45\x00"
            + b"\x00\x46\x40"
            + variable(48)
            + b"\x46\x00"
            + b"\x00\xff\x2f\x00"
        )
        parsed = parse_midi(midi_file(tempo, notes), 1, 0)
        self.assertEqual(
            [(item.note, item.start_tick, item.end_tick) for item in parsed.notes], [(69, 0, 96), (70, 96, 144)]
        )
        self.assertEqual(parsed.tempos, ((0, 250_000),))
        self.assertEqual(parsed.discarded_events, (("controller", 1), ("instrument", 1), ("velocity", 2)))
        self.assertAlmostEqual(tick_seconds(144, parsed.division, parsed.tempos), 0.375)

    def test_encode_multiple_channels_preserves_independent_notes(self) -> None:
        channels = {
            0: (MidiNote(60, 0, 240), MidiNote(64, 240, 480)),
            2: (MidiNote(36, 0, 480),),
            9: (MidiNote(42, 120, 180),),
        }
        encoded = encode_midi_channels(channels, division=960, tempo=500_000, end_tick=600)
        for channel, notes in channels.items():
            parsed = parse_midi(encoded, 0, channel)
            self.assertEqual(notes, parsed.notes)
            self.assertEqual(600, parsed.end_tick)
        self.assertTrue(encoded.endswith(b"\x78\xff\x2f\x00"))

    def test_combines_one_channel_across_format_one_tracks(self) -> None:
        first = b"\x00\x90\x3c\x64" + variable(48) + b"\x80\x3c\x00\x00\xff\x2f\x00"
        second = variable(48) + b"\x90\x40\x64" + variable(48) + b"\x80\x40\x00\x00\xff\x2f\x00"
        parsed = parse_midi_channel(midi_file(first, second), 0)
        self.assertEqual((MidiNote(60, 0, 48), MidiNote(64, 48, 96)), parsed.notes)

    def test_selected_track_ignores_polyphony_in_other_tracks(self) -> None:
        monophonic = b"\x00\x90\x3c\x40\x30\x80\x3c\x00\x00\xff\x2f\x00"
        polyphonic = b"\x00\x90\x40\x40\x00\x90\x43\x40\x30\x80\x40\x00\x00\x80\x43\x00\x00\xff\x2f\x00"
        parsed = parse_midi(midi_file(monophonic, polyphonic), 0, 0)
        self.assertEqual((MidiNote(60, 0, 48),), parsed.notes)

    def test_a_legato_note_on_before_the_note_off_on_the_same_tick(self) -> None:
        legato = (
            b"\x00\x90\x3c\x40"
            + variable(48)
            + b"\x90\x40\x40\x00\x80\x3c\x00"
            + variable(48)
            + b"\x80\x40\x00\x00\xff\x2f\x00"
        )
        parsed = parse_midi(midi_file(legato), 0, 0)
        self.assertEqual((MidiNote(60, 0, 48), MidiNote(64, 48, 96)), parsed.notes)

    def test_chunks_other_than_tracks_are_skipped(self) -> None:
        track = b"\x00\x90\x3c\x40\x30\x80\x3c\x00\x00\xff\x2f\x00"
        data = midi_file(track)
        header_end = 8 + 6
        vendor = b"XFIH" + (3).to_bytes(4, "big") + b"abc"
        with_vendor = data[:header_end] + vendor + data[header_end:] + vendor
        self.assertEqual((MidiNote(60, 0, 48),), parse_midi(with_vendor, 0, 0).notes)

    def test_rejects_polyphony_smpte_and_trailing_data(self) -> None:
        polyphonic = b"\x00\x90\x3c\x40\x00\x90\x40\x40\x60\x80\x3c\x00\x00\x80\x40\x00\x00\xff\x2f\x00"
        with self.assertRaisesRegex(MidiError, "monophonic"):
            parse_midi(midi_file(polyphonic), 0, 0)
        with self.assertRaisesRegex(MidiError, "SMPTE"):
            parse_midi(midi_file(b"\x00\xff\x2f\x00", division=0xE728), 0, 0)
        with self.assertRaisesRegex(MidiError, "trailing"):
            parse_midi(midi_file(b"\x00\xff\x2f\x00") + b"x", 0, 0)


if __name__ == "__main__":
    unittest.main()
