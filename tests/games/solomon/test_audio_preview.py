from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
import wave


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from retrolarium_kit.authoring import AuthoringError
from retrolarium_kit.solomon_audio_preview import render_solomon_wav, trace_effect


def document() -> dict:
    """One program on virtual channel 1: two notes, a rest, then stop."""
    return {
        "schema": "retrolarium.solomon-audio",
        "schema_version": 1,
        "game": "solomons-key",
        "revision": "usa",
        "periods": [1710, 1614, 1523, 1438, 1357, 1281, 1209, 1141, 1077, 1016, 959, 905],
        "durations": [4, 8],
        "envelopes": [{"index": 0, "steps": [{"duration": 255, "volume": 12}]}],
        "effects": [{"number": 1, "channels": [{"selector": 1, "stream": "melody"}]}],
        "commands": [
            {"entry": "melody", "kind": "set_sequence", "value": 0},
            {"kind": "set_control", "value": 0},
            {"kind": "duration", "value": 0},
            {"kind": "note", "value": 0x10},
            {"kind": "note", "value": 0x14},
            {"kind": "duration", "value": 1},
            {"kind": "note", "value": 0x0C},
            {"kind": "stop"},
        ],
    }


class SolomonAudioPreviewTests(unittest.TestCase):
    def test_trace_steps_notes_and_stops(self) -> None:
        trace = trace_effect(document(), 1, 600)
        self.assertTrue(trace.stopped)
        self.assertEqual(trace.note_events, 3)
        # Two four-frame notes and an eight-frame rest before the stop.
        self.assertEqual(len(trace.frames), 1 + 4 + 4 + 8)
        pulse2 = [frame[0] for frame in trace.frames]
        self.assertEqual(pulse2[1].period, 1710 >> 1)
        self.assertEqual(pulse2[5].period, 1357 >> 1)
        self.assertEqual(pulse2[10].volume, 0)

    def test_render_writes_a_wav_and_refuses_bad_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "audio.json"
            source.write_text(json.dumps(document()), encoding="utf-8")
            output = Path(directory) / "out.wav"
            report = render_solomon_wav(source, output, effect=1, seconds=1.0)
            self.assertEqual(report["timing"], "ntsc")
            self.assertTrue(report["stopped"])
            with wave.open(str(output), "rb") as wav:
                self.assertEqual(wav.getframerate(), 44_100)
                self.assertGreater(wav.getnframes(), 0)
            with self.assertRaisesRegex(AuthoringError, "outside 1..26"):
                render_solomon_wav(source, output, effect=2)
            with self.assertRaisesRegex(AuthoringError, "unknown APU voices"):
                render_solomon_wav(source, output, effect=1, muted=("drums",))


if __name__ == "__main__":
    unittest.main()
