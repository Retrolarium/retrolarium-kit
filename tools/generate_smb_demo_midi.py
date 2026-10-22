#!/usr/bin/env python3
"""Generate Retrolarium's original CC0 four-voice SMB demo loop."""

from __future__ import annotations

import argparse
from pathlib import Path

from retrolarium_kit.core.midi import MidiNote, encode_midi_channels


DIVISION = 960
TEMPO = 500_000
FRAME_RATE = 60.0988
STEP_FRAMES = 18


def midi_tick(frame: int) -> int:
    return round(frame * DIVISION * 1_000_000 / (FRAME_RATE * TEMPO))


def voice(pitches: tuple[int, ...]) -> tuple[MidiNote, ...]:
    return tuple(
        MidiNote(
            pitch,
            midi_tick(index * STEP_FRAMES),
            midi_tick((index + 1) * STEP_FRAMES),
        )
        for index, pitch in enumerate(pitches)
    )


def payload() -> bytes:
    square2 = voice(
        (
            72,
            76,
            79,
            84,
            83,
            79,
            76,
            74,
            69,
            72,
            76,
            81,
            79,
            76,
            74,
            67,
        )
    )
    square1 = voice(
        (
            60,
            67,
            72,
            67,
            59,
            67,
            71,
            67,
            59,
            64,
            69,
            64,
            55,
            62,
            67,
            62,
        )
    )
    triangle = voice(
        (
            48,
            48,
            43,
            43,
            45,
            45,
            40,
            40,
            41,
            41,
            48,
            48,
            43,
            50,
            43,
            43,
        )
    )
    noise = voice((42, 42, 38, 42) * 4)
    end_tick = midi_tick(16 * STEP_FRAMES)
    return encode_midi_channels(
        {0: square2, 1: square1, 2: triangle, 9: noise},
        division=DIVISION,
        tempo=TEMPO,
        end_tick=end_tick,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    arguments = parser.parse_args()
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_bytes(payload())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
