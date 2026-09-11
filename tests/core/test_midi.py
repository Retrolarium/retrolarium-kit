from __future__ import annotations

from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from retrolarium_kit.midi import MidiError, parse_midi, tick_seconds


def variable(value: int) -> bytes:
    encoded = [value & 0x7F]
    value >>= 7
    while value:
        encoded.append(0x80 | (value & 0x7F))
        value >>= 7
    return bytes(reversed(encoded))


def midi_file(*tracks: bytes, division: int = 96, format_type: int = 1) -> bytes:
    header = (
        b"MThd" + (6).to_bytes(4, "big") + format_type.to_bytes(2, "big")
        + len(tracks).to_bytes(2, "big") + division.to_bytes(2, "big")
    )
    return header + b"".join(
        b"MTrk" + len(track).to_bytes(4, "big") + track for track in tracks
    )


class MidiTests(unittest.TestCase):
    def test_parses_selected_channel_running_status_and_global_tempo(self) -> None:
        tempo = b"\x00\xff\x51\x03\x03\xd0\x90\x00\xff\x2f\x00"
        notes = (
            b"\x00\xc0\x05\x00\xb0\x07\x64"
            b"\x00\x90\x45\x64" + variable(96) + b"\x45\x00"
            + b"\x00\x46\x40" + variable(48) + b"\x46\x00"
            + b"\x00\xff\x2f\x00"
        )
        parsed = parse_midi(midi_file(tempo, notes), 1, 0)
        self.assertEqual([(item.note, item.start_tick, item.end_tick)
                          for item in parsed.notes], [(69, 0, 96), (70, 96, 144)])
        self.assertEqual(parsed.tempos, ((0, 250_000),))
        self.assertEqual(parsed.discarded_events,
                         (("controller", 1), ("instrument", 1), ("velocity", 2)))
        self.assertAlmostEqual(tick_seconds(144, parsed.division, parsed.tempos), 0.375)

    def test_rejects_polyphony_smpte_and_trailing_data(self) -> None:
        polyphonic = (
            b"\x00\x90\x3c\x40\x00\x90\x40\x40"
            b"\x60\x80\x3c\x00\x00\x80\x40\x00\x00\xff\x2f\x00"
        )
        with self.assertRaisesRegex(MidiError, "monophonic"):
            parse_midi(midi_file(polyphonic), 0, 0)
        with self.assertRaisesRegex(MidiError, "SMPTE"):
            parse_midi(midi_file(b"\x00\xff\x2f\x00", division=0xE728), 0, 0)
        with self.assertRaisesRegex(MidiError, "trailing"):
            parse_midi(midi_file(b"\x00\xff\x2f\x00") + b"x", 0, 0)


if __name__ == "__main__":
    unittest.main()
