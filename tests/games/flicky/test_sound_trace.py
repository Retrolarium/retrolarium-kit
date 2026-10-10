"""The kit's Flicky sequencer against the real Z80 driver.

Flicky's repository records what the game's Z80 driver writes to the YM2612
in Gens (`make trace-sound`, into build/sound_trace/gens.csv). Title song $85
plays in frames 320-457 of its longplay; after aligning on the song's first
32 writes, every audible write must equal the sequencer's, in order, and land
within 2.1 frames of it. Registers $24-$27 are the driver's timer traffic.
"""
from __future__ import annotations

import csv
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from helpers import game_repository
from retrolarium_kit.games.flicky.workspace.sequencer import SoundSequencer


REPOSITORY = game_repository("flicky_src")
TRACE = REPOSITORY / "build/sound_trace/gens.csv"
SOURCE = REPOSITORY / "src/sound/z80/data.asm"
TIMER_REGISTERS = frozenset((0x24, 0x25, 0x26, 0x27))
FIRST_FRAME, LAST_FRAME = 320, 457
MINIMUM_WRITES = 2600
SIGNATURE = 32
FRAME_RATE = 60.0
TOLERANCE_FRAMES = 2.1


def gens_writes() -> list[tuple[int, tuple[int, int, int]]]:
    """(frame, (port, register, value)) for every audible YM2612 write."""
    result = []
    with TRACE.open(newline="", encoding="ascii") as source:
        rows = csv.DictReader(source)
        assert set(rows.fieldnames or ()) == {"sequence", "frame", "chip", "port", "address", "value"}
        for row in rows:
            frame, address = int(row["frame"]), int(row["address"], 16)
            if FIRST_FRAME <= frame <= LAST_FRAME and row["chip"] == "YM2612" and address not in TIMER_REGISTERS:
                result.append((frame, (int(row["port"]), address, int(row["value"], 16))))
    return result


@unittest.skipUnless(TRACE.is_file() and SOURCE.is_file(),
                     "Flicky's repository has no Gens sound trace (make trace-sound)")
class FlickySoundTraceTests(unittest.TestCase):
    def test_title_song_matches_the_z80_driver_write_for_write(self) -> None:
        rendered = SoundSequencer.from_source(SOURCE, SOURCE).render("zMusic85Header", seconds=4.0)
        expected = [(write.sample, (write.port, write.address, write.value)) for write in rendered.writes
                    if write.chip == "ym2612" and write.address not in TIMER_REGISTERS]
        observed = gens_writes()
        signature = [write for _sample, write in expected[:SIGNATURE]]
        starts = [index for index in range(len(observed) - SIGNATURE + 1)
                  if [write for _frame, write in observed[index:index + SIGNATURE]] == signature]
        self.assertEqual(1, len(starts), "the song must start exactly once in the trace")
        actual = observed[starts[0]:]
        self.assertGreaterEqual(len(actual), MINIMUM_WRITES)
        self.assertGreaterEqual(len(expected), len(actual))
        for index, ((_frame, left), (_sample, right)) in enumerate(zip(actual, expected)):
            self.assertEqual(left, right, f"write {index}")
        first_frame, first_sample = actual[0][0], expected[0][0]
        drift = max(abs((frame - first_frame) - (sample - first_sample) * FRAME_RATE / rendered.sample_rate)
                    for (frame, _left), (sample, _right) in zip(actual, expected))
        self.assertLessEqual(drift, TOLERANCE_FRAMES)


if __name__ == "__main__":
    unittest.main()
