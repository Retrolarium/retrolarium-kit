from __future__ import annotations

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from retrolarium_kit.games.castle_excellent.audio_preview import (
    CastleExcellentAudioError,
    music_resource,
    render_wav,
    track_lanes,
)


def music(streams: list[dict[str, object]], pointers: list[str | None]) -> dict[str, object]:
    prefix = bytearray(209)
    prefix[0:7] = bytes((9, 11, 0, 2, 4, 5, 7))  # semitones of a-g
    prefix[7:12] = bytes((6, 12, 24, 48, 96))  # lengths @ 8 4 2 1 at tempo 0
    for index in range(0, 146, 2):  # APU periods
        prefix[63 + index : 65 + index] = (0x200 - index).to_bytes(2, "little")
    return {
        "codec": "castle-excellent.mml-music.v1",
        "playback_prefix_cpu_address": 0xBB16,
        "mml_cpu_address": 0xBBE7,
        "playback_prefix": list(prefix),
        "streams": streams,
        "track_sets": [{"index": 0, "name": "Probe", "streams": pointers, "channel_parameters": [0xBF, 0xBB]}],
    }


class CastleExcellentAudioPreviewTests(unittest.TestCase):
    def test_lanes_follow_lengths_and_restarts(self) -> None:
        value = music(
            [
                {"id": "stream_00", "text": "l4co5c8r2", "restart": "stream_00"},
                {"id": "stream_01", "text": "l1e", "restart": None},
            ],
            ["stream_00", None, "stream_01"],
        )
        lanes = track_lanes(value, 0, 120)
        self.assertEqual(
            [(0, 24), (24, 12), (36, 48), (84, 24), (108, 12)], [(e.start, e.duration) for e in lanes["pulse1"]]
        )
        self.assertEqual(0.0, lanes["pulse1"][2].frequency)
        # The octave command raises the period-table index by twelve notes.
        self.assertNotEqual(lanes["pulse1"][0].frequency, lanes["pulse1"][1].frequency)
        self.assertEqual([(0, 96)], [(e.start, e.duration) for e in lanes["triangle"]])
        self.assertEqual([], lanes["pulse2"])

    def test_render_and_resource_lookup(self) -> None:
        value = music([{"id": "stream_00", "text": "l8cdefg", "restart": None}], ["stream_00", None, None])
        wav = render_wav(value, 0, seconds=1.0)
        self.assertEqual(b"RIFF", wav[:4])
        self.assertIs(value, music_resource({"resources": [{"codec": "other"}, value]}))
        with self.assertRaises(CastleExcellentAudioError):
            music_resource({"resources": []})
        with self.assertRaises(CastleExcellentAudioError):
            track_lanes(value, 3, 10)


if __name__ == "__main__":
    unittest.main()
