"""Opt-in Solomon GUI APU trace/WAV comparison against neighbor source."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from retrolarium_kit.games.catalog import extract_game, inspect_game


def check(
    rom: Path, revision: str, editor: Path, oracle: object,
    effects: tuple[int, ...], mute_pulse1: bool,
) -> None:
    inspection = inspect_game(rom)
    if (inspection["game"], inspection["revision"]) != ("solomons-key", revision):
        raise AssertionError(f"{revision}: unexpected source ROM")
    with tempfile.TemporaryDirectory(
        prefix=f"retrolarium-solomon-audio-preview-{revision}-"
    ) as temporary:
        root = Path(temporary)
        document_path = root / "audio.json"
        extract_game(rom, document_path, resource="audio")
        document = json.loads(document_path.read_text(encoding="utf-8"))
        timing = "ntsc" if revision == "usa" else "pal"
        for effect in effects:
            trace_path = root / f"effect-{effect:02d}-trace.json"
            wav_path = root / f"effect-{effect:02d}.wav"
            subprocess.run(
                [str(editor), str(document_path), str(trace_path),
                 str(wav_path), str(effect), "180",
                 "--solomon-audio-preview-mute" if mute_pulse1
                 else "--solomon-audio-preview"],
                check=True, timeout=90,
            )
            actual = json.loads(trace_path.read_text(encoding="utf-8"))
            expected = oracle.trace_effect(document, effect, 180)
            frames = [
                [
                    [(-1 if voice.source is None else voice.source),
                     voice.period, voice.volume, voice.duty,
                     voice.noise_mode, voice.period_reload]
                    for voice in frame
                ]
                for frame in expected.frames
            ]
            if (actual["frames"] != frames
                    or actual["note_events"] != expected.note_events
                    or actual["stopped"] != expected.stopped):
                raise AssertionError(
                    f"{revision}: effect {effect} APU trace differs"
                )
            reference_pcm = oracle.render_trace(
                expected, timing,
                enabled_voices={1, 2, 3} if mute_pulse1 else None,
            )
            with wave.open(str(wav_path), "rb") as wav:
                if (wav.getnchannels(), wav.getsampwidth(),
                        wav.getframerate()) != (1, 2, 44100):
                    raise AssertionError(
                        f"{revision}: effect {effect} WAV format differs"
                    )
                actual_pcm = wav.readframes(len(reference_pcm) // 2)
            if len(actual_pcm) != len(reference_pcm):
                raise AssertionError(
                    f"{revision}: effect {effect} WAV sample count differs"
                )
            actual_samples = struct.unpack(
                "<" + "h" * (len(actual_pcm) // 2), actual_pcm
            )
            reference_samples = struct.unpack(
                "<" + "h" * (len(reference_pcm) // 2), reference_pcm
            )
            difference = max(
                abs(left - right)
                for left, right in zip(actual_samples, reference_samples)
            )
            if difference > 1:
                raise AssertionError(
                    f"{revision}: effect {effect} WAV differs by {difference}"
                )
            print(
                f"Solomon {revision} effect {effect:02d}: "
                f"{len(frames)} APU frames, WAV max delta {difference}"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--neighbor", type=Path, required=True)
    parser.add_argument("--editor", type=Path, required=True)
    parser.add_argument("--usa", type=Path, required=True)
    parser.add_argument("--europe", type=Path, required=True)
    parser.add_argument("--effects", default="1,8,17,26")
    parser.add_argument("--mute-pulse1", action="store_true")
    args = parser.parse_args()
    sys.path.insert(0, str(args.neighbor.resolve(strict=True)))
    from scripts.authoring import audio_preview

    effects = tuple(int(item) for item in args.effects.split(","))
    editor = args.editor.resolve(strict=True)
    check(args.usa.resolve(strict=True), "usa", editor, audio_preview,
          effects, args.mute_pulse1)
    check(args.europe.resolve(strict=True), "europe", editor, audio_preview,
          effects, args.mute_pulse1)


if __name__ == "__main__":
    main()
