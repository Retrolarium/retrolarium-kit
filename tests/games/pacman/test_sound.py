from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from retrolarium_kit.pacman_sound import (
    PacmanSoundError,
    decode_sound_bundle,
    encode_sound_bundle,
    import_midi_commands,
    midi_to_sound_byte,
    render_stream_wav,
    sound_byte_to_midi,
    sound_summary,
)
from retrolarium_kit.authoring import GameProfile, ResourceSpec, RevisionSpec
from retrolarium_kit.audio_operations import (
    export_document_midi,
    import_midi_into_document,
    preview_midi_import,
    render_document_wav,
)
from retrolarium_kit.midi import parse_midi
from retrolarium_kit.nes_resources import (
    build_resources,
    decode_resources,
    inspection_details,
    parse_resource_document,
)


ORDER = [2, 6, 10, 11, 12, 9, 7, 13, 14, 8, 3, 0, 1, 4, 5, 15]


def parameters(capacity: int = 256) -> dict[str, object]:
    return {
        "pointer_file_offset": 16,
        "arena_file_offset": 48,
        "arena_cpu_address": 0xF000,
        "capacity": capacity,
        "storage_order": ORDER,
        "labels": [f"Sound {index:02X}" for index in range(16)],
        "trailing_sizes": [0] * 15 + [2],
    }


def stream(slot: int) -> dict[str, object]:
    return {
        "prologue": [slot, 0x8F, 0, 0],
        "commands": [
            {"kind": "note", "value": slot, "duration": slot + 1},
            {"kind": "control", "opcode": 0xF0},
        ],
        "trailing_bytes": [0x12, 0x34] if slot == 15 else [],
    }


def document(capacity: int = 256) -> dict[str, object]:
    return {
        "capacity": capacity,
        "slots": [
            {"slot": slot, "label": f"Sound {slot:02X}", "stream": stream(slot)}
            for slot in range(16)
        ],
    }


def image_for_document(value: dict[str, object], config: dict[str, object]) -> bytes:
    result = bytearray(48 + int(config["capacity"]))
    for offset, payload in encode_sound_bundle(value, config):
        result[offset:offset + len(payload)] = payload
    return bytes(result)


def midi_file(events: bytes, division: int = 96) -> bytes:
    track = events + b"\x00\xff\x2f\x00"
    return (
        b"MThd\x00\x00\x00\x06\x00\x00\x00\x01"
        + division.to_bytes(2, "big") + b"MTrk" + len(track).to_bytes(4, "big") + track
    )


class PacmanSoundTests(unittest.TestCase):
    def test_bundle_round_trip_repack_and_padding(self) -> None:
        config = parameters()
        original_document = document()
        image = image_for_document(original_document, config)
        decoded = decode_sound_bundle(image, config)
        self.assertEqual(decoded, original_document)
        self.assertEqual(image_for_document(decoded, config), image)

        edited = copy.deepcopy(decoded)
        edited["slots"][2]["stream"]["commands"].insert(
            1, {"kind": "note", "value": 0x10, "duration": 4}
        )
        spans = encode_sound_bundle(edited, config)
        pointers = [int.from_bytes(spans[0][1][index:index + 2], "little")
                    for index in range(0, 32, 2)]
        self.assertEqual(pointers[2], 0xF000)
        self.assertEqual(pointers[6], 0xF009)
        self.assertTrue(spans[1][1].endswith(b"\xff"))
        rebuilt = bytearray(image)
        for offset, payload in spans:
            rebuilt[offset:offset + len(payload)] = payload
        self.assertEqual(decode_sound_bundle(bytes(rebuilt), config), edited)

    def test_rejects_capacity_metadata_and_non_ff_padding_errors(self) -> None:
        too_small = parameters(100)
        with self.assertRaisesRegex(PacmanSoundError, "arena holds"):
            encode_sound_bundle(document(100), too_small)
        wrong = document()
        wrong["slots"][0]["label"] = "wrong"
        with self.assertRaisesRegex(PacmanSoundError, "metadata"):
            encode_sound_bundle(wrong, parameters())
        image = bytearray(image_for_document(document(), parameters()))
        image[-1] = 0
        with self.assertRaisesRegex(PacmanSoundError, "padding"):
            decode_sound_bundle(bytes(image), parameters())

    def test_note_mapping_midi_import_and_capacity_summary(self) -> None:
        for note in (45, 57, 69, 80, 127):
            self.assertEqual(sound_byte_to_midi(midi_to_sound_byte(note)), note)
        events = (
            b"\x00\x90\x2d\x64\x60\x80\x2d\x00"
            b"\x00\x90\x2e\x40\x60\x80\x2e\x00"
        )
        commands, report = import_midi_commands(midi_file(events), 0, 0)
        self.assertEqual([item["duration"] for item in commands[:-1]], [30, 30])
        self.assertEqual(report["source_notes"], 2)
        self.assertEqual(report["output_frames"], 60)
        self.assertEqual(report["quantization"]["method"],
                         "nearest NTSC frame; minimum one frame")
        self.assertEqual(report["discarded"][0]["feature"], "velocity")
        self.assertEqual(report["discarded"][0]["events"], 2)
        summary = sound_summary(document(), parameters())
        self.assertEqual(summary["slots"], 16)
        self.assertEqual(summary["notes"], 16)
        self.assertEqual(summary["used_bytes"], 16 * 7 + 2)
        self.assertEqual(summary["free_bytes"], 256 - 114)

    def test_midi_import_rejects_rests_and_out_of_range_pitch(self) -> None:
        rest = b"\x01\x90\x2d\x40\x60\x80\x2d\x00"
        with self.assertRaisesRegex(PacmanSoundError, "rests"):
            import_midi_commands(midi_file(rest), 0, 0)
        low = b"\x00\x90\x20\x40\x60\x80\x20\x00"
        with self.assertRaisesRegex(PacmanSoundError, "outside"):
            import_midi_commands(midi_file(low), 0, 0)

    def test_preview_is_deterministic_mono_pcm_wav(self) -> None:
        first = render_stream_wav(stream(3), sample_rate=8_000)
        second = render_stream_wav(stream(3), sample_rate=8_000)
        self.assertEqual(first, second)
        self.assertEqual(first[:4], b"RIFF")
        self.assertEqual(first[8:12], b"WAVE")
        self.assertGreater(len(first), 44)

    def test_preview_mixes_slots_playing_together(self) -> None:
        alone = render_stream_wav(stream(3), sample_rate=8_000)
        mixed = render_stream_wav(stream(3), sample_rate=8_000, also=(stream(5),))
        self.assertNotEqual(alone, mixed)
        self.assertEqual(mixed, render_stream_wav(stream(3), sample_rate=8_000, also=(stream(5),)))
        self.assertGreaterEqual(len(mixed), len(alone))

    def test_nes_resource_bundle_rebuilds_pointer_table_and_arena(self) -> None:
        config = parameters()
        raw_image = image_for_document(document(), config)
        source = (
            b"NES\x1a" + bytes((1, 0, 0, 0)) + bytes(8) + raw_image[16:]
        ).ljust(0x4010, b"\0")
        revision = RevisionSpec(
            "probe", "Sound probe", len(source), hashlib.sha1(source).hexdigest(),
            hashlib.sha256(source).hexdigest(), "ines", 0,
        )
        profile = GameProfile(
            "sound-probe", 1, "Sound probe", "nes", ("audio",), (revision,),
            (
                ResourceSpec(
                    "bundle", "resource-bundle", "nes.resource-bundle.v1", "fixed"
                ),
                ResourceSpec(
                    "sounds", "audio", "pacman.sound-streams.v1", "fixed", config
                ),
            ),
        )
        decoded = decode_resources(source, profile, revision)
        self.assertEqual(inspection_details(decoded)["sound_slots"], 16)
        edited = decoded.as_dict()
        edited["resources"][0]["slots"][2]["stream"]["commands"].insert(
            1, {"kind": "note", "value": 0x10, "duration": 4}
        )
        parsed = parse_resource_document(edited, profile, revision)
        rebuilt, report = build_resources(source, parsed, profile, revision)
        self.assertEqual(report["resources"][0]["codec"], "pacman.sound-streams.v1")
        self.assertEqual(len(report["resources"][0]["ranges"]), 2)
        self.assertEqual(
            decode_resources(
                rebuilt,
                profile,
                RevisionSpec(
                    "probe", "Sound probe", len(rebuilt), hashlib.sha1(rebuilt).hexdigest(),
                    hashlib.sha256(rebuilt).hexdigest(), "ines", 0,
                ),
            ).resources[0]["slots"][2]["stream"]["commands"][1]["value"],
            0x10,
        )

    def test_file_operations_import_midi_and_render_wav(self) -> None:
        config = parameters()
        digest = "1" * 64
        bundle = {
            "schema": "retrolarium.nes-resource-bundle",
            "schema_version": 1,
            "game": "sound-probe",
            "revision": "probe",
            "source_sha256": digest,
            "resources": [
                {"id": "sounds", "kind": "audio", "codec": "pacman.sound-streams.v1",
                 **document()}
            ],
        }
        profile = {
            "schema": "retrolarium.game-profile",
            "schema_version": 1,
            "id": "sound-probe",
            "profile_version": 1,
            "name": "Sound probe",
            "platform": "nes",
            "capabilities": ["audio"],
            "revisions": [{
                "id": "probe", "name": "Probe", "size": 1,
                "sha1": "1" * 40, "sha256": digest, "container": "ines", "mapper": 0,
            }],
            "resources": [{
                "id": "sounds", "kind": "audio", "codec": "pacman.sound-streams.v1",
                "capacity_policy": "fixed", "parameters": config,
            }],
        }
        events = b"\x00\x90\x2d\x64\x60\x80\x2d\x00"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            document_path = root / "game.json"
            profile_path = root / "profile.json"
            midi_path = root / "note.mid"
            edited_path = root / "edited.json"
            wav_path = root / "preview.wav"
            exported_midi_path = root / "exported.mid"
            document_path.write_text(json.dumps(bundle), encoding="utf-8")
            profile_path.write_text(json.dumps(profile), encoding="utf-8")
            midi_path.write_bytes(midi_file(events))
            preview_report = preview_midi_import(
                document_path, midi_path, resource_id="sounds", slot=3,
                profile_path=profile_path,
            )
            self.assertEqual(preview_report["operation"], "preview")
            self.assertEqual(preview_report["after"]["notes"], 16)
            self.assertFalse(edited_path.exists())
            report = import_midi_into_document(
                document_path, midi_path, edited_path, resource_id="sounds", slot=3,
                profile_path=profile_path,
            )
            self.assertEqual(report["source_notes"], 1)
            self.assertEqual(report["operation"], "apply")
            edited = json.loads(edited_path.read_text(encoding="utf-8"))
            self.assertEqual(
                edited["resources"][0]["slots"][3]["stream"]["commands"][0],
                {"kind": "note", "value": 0, "duration": 30},
            )
            preview = render_document_wav(
                edited_path, wav_path, resource_id="sounds", slot=3,
                sample_rate=8_000, profile_path=profile_path,
            )
            self.assertEqual(preview["size"], wav_path.stat().st_size)
            self.assertEqual(wav_path.read_bytes()[:4], b"RIFF")
            exported = export_document_midi(
                edited_path, exported_midi_path, resource_id="sounds", slot=3,
                profile_path=profile_path,
            )
            parsed_midi = parse_midi(exported_midi_path.read_bytes(), 0, 0)
            self.assertEqual(1, exported["notes"])
            self.assertEqual(45, parsed_midi.notes[0].note)
            self.assertGreater(parsed_midi.notes[0].end_tick, 0)


if __name__ == "__main__":
    unittest.main()
