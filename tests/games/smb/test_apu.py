from __future__ import annotations

import copy
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest

from retrolarium_kit.studio.audio import (
    export_smb_document_midi,
    import_smb_midi_into_document,
    preview_smb_midi_import,
    render_smb_document_wav,
)
from retrolarium_kit.core.authoring import GameProfile, ResourceSpec, RevisionSpec
from retrolarium_kit.cli import main as cli_main
from helpers import game_profile
from retrolarium_kit.platform.nes.resources import (
    build_resources,
    decode_resources,
    inspection_details,
)
from retrolarium_kit.core.midi import parse_midi
from retrolarium_kit.games.smb.apu import (
    SmbApuError,
    apply_midi_channel_commands,
    decode_channel_byte,
    decode_music,
    encode_channel_command,
    encode_music,
    lower_midi_channel_commands,
    music_byte_readers,
    music_header_timeline,
    music_channel_spans,
    music_selector_context,
    parse_layout,
    render_music_header_pcm,
    render_music_header_wav,
)


def layout_parameters() -> dict[str, object]:
    return {
        "cpu_address": 0x9000,
        "selector_names": ["event", "area"],
        "ground_schedule_count": 2,
        "headers": [
            {"name": "alpha", "size": 5},
            {"name": "beta", "size": 6},
            {"name": "silence", "size": 4},
        ],
        "shared_stream_size": 16,
        "note_period_count": 2,
        "note_length_count": 4,
        "envelopes": [
            {"name": "area", "size": 2},
            {"name": "water", "size": 3},
        ],
    }


def synthetic_payload() -> bytes:
    parameters = layout_parameters()
    layout = parse_layout(parameters)
    arena_address = layout.cpu_address + layout.shared_stream_offset
    return bytes([
        4, 9,
        15, 4,
        0x18, arena_address & 0xFF, arena_address >> 8, 3, 2,
        0x20, (arena_address + 4) & 0xFF, (arena_address + 4) >> 8, 4, 2, 7,
        0x08, (arena_address + 12) & 0xFF, (arena_address + 12) >> 8, 0,
        *range(16),
        0x01, 0x23, 0x04, 0x56,
        1, 2, 3, 4,
        0x90, 0x91,
        0x80, 0x81, 0x82,
    ])


def midi_file(events: bytes, division: int = 96) -> bytes:
    track = events + b"\x00\xff\x2f\x00"
    return (
        b"MThd" + (6).to_bytes(4, "big")
        + (0).to_bytes(2, "big") + (1).to_bytes(2, "big")
        + division.to_bytes(2, "big")
        + b"MTrk" + len(track).to_bytes(4, "big") + track
    )


def midi_lowering_fixture() -> tuple[dict[str, object], dict[str, object]]:
    selector_names = [f"event_{index}" for index in range(8)]
    selector_names.append("area_ground")
    parameters = {
        "cpu_address": 0x9000,
        "frame_rate": 60.0,
        "cpu_frequency": 1_788_160.0,
        "selector_names": selector_names,
        "ground_schedule_count": 1,
        "headers": [{"name": "song", "size": 4}],
        "shared_stream_size": 5,
        "note_period_count": 3,
        "note_length_count": 16,
        "envelopes": [{"name": "area", "size": 1}],
    }
    document = {
        "selectors": [
            {"name": name, "header": "song"} for name in selector_names
        ],
        "ground_schedule": ["song"],
        "headers": [{
            "name": "song", "length_table_offset": 0,
            "stream_offset": 0, "triangle_offset": 3,
        }],
        "shared_stream_arena": [0x80, 0x02, 0x00, 0x80, 0x00],
        "note_periods": [0, 253, 239],
        "note_lengths": [30] * 16,
        "envelopes": [{"name": "area", "values": [0x9F]}],
    }
    return parameters, document


class SmbApuTests(unittest.TestCase):
    def test_file_operations_preview_and_apply_guarded_midi(self) -> None:
        parameters, music = midi_lowering_fixture()
        parameters["selector_names"] = ["event"]
        music["selectors"] = [{"name": "event", "header": "song"}]
        digest = "2" * 64
        bundle = {
            "schema": "retrolarium.nes-resource-bundle",
            "schema_version": 1,
            "game": "smb-midi-probe",
            "revision": "probe",
            "source_sha256": digest,
            "resources": [{
                "id": "music", "kind": "audio", "codec": "smb.apu-music.v1",
                **music,
            }],
        }
        profile = {
            "schema": "retrolarium.game-profile",
            "schema_version": 1,
            "id": "smb-midi-probe",
            "profile_version": 1,
            "name": "SMB MIDI probe",
            "platform": "nes",
            "capabilities": ["audio"],
            "revisions": [{
                "id": "probe", "name": "Probe", "size": 1,
                "sha1": "2" * 40, "sha256": digest,
                "container": "ines", "mapper": 0,
            }],
            "resources": [{
                "id": "music", "kind": "audio", "codec": "smb.apu-music.v1",
                "capacity_policy": "fixed", "parameters": parameters,
            }],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            document_path = root / "game.json"
            profile_path = root / "profile.json"
            midi_path = root / "channel.mid"
            output_path = root / "edited.json"
            exported_midi_path = root / "exported.mid"
            exported_song_path = root / "exported-song.mid"
            roundtrip_path = root / "roundtrip.json"
            document_path.write_text(json.dumps(bundle), encoding="utf-8")
            profile_path.write_text(json.dumps(profile), encoding="utf-8")
            midi_path.write_bytes(
                midi_file(b"\x00\x90\x46\x64\x60\x80\x46\x00")
            )

            preview = preview_smb_midi_import(
                document_path,
                midi_path,
                resource_id="music",
                selector="event",
                target_channel="square2",
                profile_path=profile_path,
            )
            self.assertEqual("preview", preview["operation"])
            self.assertEqual([1], preview["changed_offsets"])
            self.assertFalse(output_path.exists())

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                result = cli_main([
                    "audio", "import-smb-midi", str(document_path),
                    "--midi", str(midi_path), "--output", str(output_path),
                    "--resource", "music", "--selector", "event",
                    "--target-channel", "square2",
                    "--profile", str(profile_path),
                ])
            self.assertEqual(0, result)
            report = json.loads(stdout.getvalue())
            self.assertEqual("apply", report["operation"])
            self.assertEqual([1], report["changed_offsets"])
            self.assertEqual(
                [0x80, 0x02, 0x00],
                bundle["resources"][0]["shared_stream_arena"][:3],
            )
            edited = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(
                [0x80, 0x04, 0x00],
                edited["resources"][0]["shared_stream_arena"][:3],
            )
            exported = export_smb_document_midi(
                output_path,
                exported_midi_path,
                resource_id="music",
                header="song",
                selector="event",
                channel="square2",
                profile_path=profile_path,
            )
            parsed_midi = parse_midi(exported_midi_path.read_bytes(), 0, 0)
            self.assertEqual(1, exported["notes"])
            self.assertEqual(70, parsed_midi.notes[0].note)
            exported_song = export_smb_document_midi(
                output_path,
                exported_song_path,
                resource_id="music",
                header="song",
                selector="event",
                channel="all",
                profile_path=profile_path,
            )
            self.assertEqual(["square2", "triangle"], exported_song["channels"])
            self.assertEqual(
                parsed_midi.notes,
                parse_midi(exported_song_path.read_bytes(), 0, 0).notes,
            )
            preview_song = preview_smb_midi_import(
                output_path,
                exported_song_path,
                resource_id="music",
                selector="event",
                target_channel="all",
                profile_path=profile_path,
            )
            self.assertEqual(["square2"], preview_song["channels"])
            self.assertEqual(0, preview_song["changed_bytes"])
            imported_song = import_smb_midi_into_document(
                output_path,
                exported_song_path,
                roundtrip_path,
                resource_id="music",
                selector="event",
                target_channel="all",
                profile_path=profile_path,
            )
            self.assertEqual("apply", imported_song["operation"])
            self.assertEqual(
                json.loads(output_path.read_text(encoding="utf-8")),
                json.loads(roundtrip_path.read_text(encoding="utf-8")),
            )

    def test_midi_apply_changes_only_exclusive_consumed_prefix(self) -> None:
        parameters, document = midi_lowering_fixture()
        parameters = copy.deepcopy(parameters)
        document = copy.deepcopy(document)
        parameters["selector_names"] = ["event"]
        document["selectors"] = [{"name": "event", "header": "song"}]
        midi = midi_file(b"\x00\x90\x46\x64\x60\x80\x46\x00")
        edited, report = apply_midi_channel_commands(
            midi, document, parameters, "event", "square2"
        )
        self.assertEqual([0x80, 0x02, 0x00], document["shared_stream_arena"][:3])
        self.assertEqual([0x80, 0x04, 0x00], edited["shared_stream_arena"][:3])
        self.assertEqual([1], report["changed_offsets"])
        self.assertEqual(1, report["changed_bytes"])
        self.assertEqual(3, report["available_bytes"])
        self.assertEqual(
            ["selector:event", "ground_schedule:0"],
            report["affected_logical_owners"],
        )

    def test_midi_apply_rejects_shared_capacity_and_duration_changes(self) -> None:
        parameters, document = midi_lowering_fixture()
        midi = midi_file(b"\x00\x90\x46\x64\x60\x80\x46\x00")
        with self.assertRaisesRegex(SmbApuError, "is shared with"):
            apply_midi_channel_commands(
                midi, document, parameters, "area_ground", "square2"
            )
        shared_edit, shared_report = apply_midi_channel_commands(
            midi, document, parameters, "area_ground", "square2",
            allow_same_channel_sharing=True,
        )
        self.assertEqual(0x04, shared_edit["shared_stream_arena"][1])
        self.assertTrue(shared_report["shared_channel_edit"])

        exclusive_parameters = copy.deepcopy(parameters)
        exclusive_document = copy.deepcopy(document)
        exclusive_parameters["selector_names"] = ["event"]
        exclusive_document["selectors"] = [
            {"name": "event", "header": "song"}
        ]
        exclusive_document["note_lengths"][1] = 15
        short = midi_file(b"\x00\x90\x46\x64\x30\x80\x46\x00")
        with self.assertRaisesRegex(SmbApuError, "must equal"):
            apply_midi_channel_commands(
                short, exclusive_document, exclusive_parameters,
                "event", "square2",
            )

        two_notes = midi_file(
            b"\x00\x90\x46\x64\x30\x80\x46\x00"
            b"\x00\x90\x45\x64\x30\x80\x45\x00"
        )
        with self.assertRaisesRegex(SmbApuError, "consumed prefix"):
            apply_midi_channel_commands(
                two_notes, exclusive_document, exclusive_parameters,
                "event", "square2",
            )

    def test_byte_readers_capture_aliases_and_cross_channel_sharing(self) -> None:
        parameters, document = midi_lowering_fixture()
        parameters = copy.deepcopy(parameters)
        document = copy.deepcopy(document)
        parameters["headers"] = [
            {"name": "song", "size": 4},
            {"name": "alias", "size": 4},
        ]
        document["headers"].append({
            "name": "alias", "length_table_offset": 0,
            "stream_offset": 0, "triangle_offset": 0,
        })
        document["selectors"][0]["header"] = "alias"
        readers = {
            item["offset"]: item["readers"]
            for item in music_byte_readers(document, parameters)
        }
        identities = {
            (item["header"], item["channel"], item["length_table_adder"])
            for item in readers[0]
        }
        self.assertEqual(
            {
                ("alias", "square2", 0),
                ("alias", "triangle", 0),
                ("song", "square2", 0),
                ("song", "square2", 8),
            },
            identities,
        )
        song = next(
            item for item in readers[0]
            if (item["header"], item["channel"],
                item["length_table_adder"]) == ("song", "square2", 0)
        )
        self.assertIn("selector:area_ground", song["logical_owners"])
        self.assertIn("ground_schedule:0", song["logical_owners"])
        self.assertEqual(
            [("song", "triangle", 0), ("song", "triangle", 8)],
            [(item["header"], item["channel"], item["length_table_adder"])
             for item in readers[3]],
        )

    def test_midi_lowering_uses_engine_periods_and_lengths(self) -> None:
        parameters, document = midi_lowering_fixture()
        midi = midi_file(b"\x00\x90\x45\x64\x60\x80\x45\x00")
        commands, report = lower_midi_channel_commands(
            midi,
            document,
            parameters,
            "area_ground",
            "square2",
            max_pitch_error_cents=0,
            max_duration_error_frames=0,
        )
        self.assertEqual([0x80, 0x02, 0x00], commands)
        self.assertEqual("song", report["header"])
        self.assertEqual("square2", report["target_channel"])
        self.assertEqual(1, report["source_notes"])
        self.assertEqual(30, report["output_frames"])
        self.assertEqual(0.0, report["maximum_pitch_error_cents"])
        self.assertEqual(0.0, report["maximum_duration_error_frames"])
        self.assertEqual(
            [{"feature": "velocity", "events": 1}], report["discarded"]
        )

    def test_midi_lowering_encodes_and_compresses_noise_pattern(self) -> None:
        parameters, document = midi_lowering_fixture()
        parameters = copy.deepcopy(parameters)
        document = copy.deepcopy(document)
        parameters["selector_names"] = ["event"]
        parameters["headers"] = [{"name": "song", "size": 6}]
        document["selectors"] = [{"name": "event", "header": "song"}]
        document["headers"][0]["square1_offset"] = 0
        document["headers"][0]["noise_offset"] = 4
        document["note_lengths"] = [5] * 16
        percussion = midi_file(
            b"\x00\x99\x2a\x64\x10\x89\x2a\x00"
            b"\x00\x99\x2a\x64\x10\x89\x2a\x00"
        )
        commands, report = lower_midi_channel_commands(
            percussion, document, parameters, "event", "noise",
            midi_channel=9, max_duration_error_frames=0.1,
        )
        self.assertEqual([0x10, 0x00], commands)
        self.assertEqual(10, report["output_frames"])
        self.assertEqual(2, report["source_notes"])

        long_hit = midi_file(
            b"\x00\x99\x26\x64\x20\x89\x26\x00"
        )
        commands, report = lower_midi_channel_commands(
            long_hit, document, parameters, "event", "noise",
            midi_channel=9, max_duration_error_frames=0.1,
        )
        self.assertEqual([0x20, 0x00], commands)
        self.assertEqual(10, report["output_frames"])

    def test_midi_lowering_rejects_unrepresentable_input(self) -> None:
        parameters, document = midi_lowering_fixture()
        a4 = midi_file(b"\x00\x90\x45\x64\x60\x80\x45\x00")
        with self.assertRaisesRegex(SmbApuError, "has no noise channel"):
            lower_midi_channel_commands(
                a4, document, parameters, "area_ground", "noise"
            )
        with self.assertRaisesRegex(SmbApuError, "has no square1 channel"):
            lower_midi_channel_commands(
                a4, document, parameters, "area_ground", "square1"
            )

        distant_pitch = midi_file(
            b"\x00\x90\x47\x64\x60\x80\x47\x00"
        )
        with self.assertRaisesRegex(SmbApuError, "nearest SMB period"):
            lower_midi_channel_commands(
                distant_pitch, document, parameters, "area_ground", "square2"
            )

        short_document = copy.deepcopy(document)
        short_document["note_lengths"] = [10] * 16
        with self.assertRaisesRegex(SmbApuError, "nearest SMB length"):
            lower_midi_channel_commands(
                a4, short_document, parameters, "area_ground", "square2"
            )

        rest = midi_file(
            b"\x00\x90\x45\x64\x60\x80\x45\x00"
            b"\x60\x90\x45\x64\x60\x80\x45\x00"
        )
        with self.assertRaisesRegex(SmbApuError, "without rests"):
            lower_midi_channel_commands(
                rest, document, parameters, "area_ground", "square2"
            )

    def test_header_timeline_synchronizes_tonal_and_looping_channels(self) -> None:
        parameters = {
            "cpu_address": 0x9000,
            "selector_names": ["song"],
            "ground_schedule_count": 1,
            "headers": [{"name": "song", "size": 6}],
            "shared_stream_size": 14,
            "note_period_count": 4,
            "note_length_count": 8,
            "envelopes": [{"name": "area", "size": 1}],
        }
        document = {
            "selectors": [{"name": "song", "header": "song"}],
            "ground_schedule": ["song"],
            "headers": [{
                "name": "song", "length_table_offset": 0,
                "stream_offset": 0, "triangle_offset": 6,
                "square1_offset": 4, "noise_offset": 11,
            }],
            "shared_stream_arena": [
                0x82, 0x04, 0x06, 0x00,
                0x84, 0x84,
                0x80, 0x04, 0x00, 0x06, 0x00,
                0x20, 0x10, 0x00,
            ],
            "note_periods": [0, 0x123, 0x234, 0x345],
            "note_lengths": [2, 3, 4, 5, 6, 7, 8, 9],
            "envelopes": [{"name": "area", "values": [0x90]}],
        }
        timeline = music_header_timeline(document, parameters, "song")
        self.assertEqual(8, timeline["duration"])
        channels = {item["channel"]: item for item in timeline["channels"]}
        self.assertEqual({"square2", "square1", "triangle", "noise"},
                         set(channels))
        self.assertEqual(
            [(0, 4, 2), (4, 4, 3)],
            [(event["tick"], event["duration"], event["period_index"])
             for event in channels["square2"]["events"]
             if event["kind"] == "note"],
        )
        self.assertEqual(
            ["note", "rest", "note", "rest"],
            [event["kind"] for event in channels["triangle"]["events"]
             if event["kind"] != "length"],
        )
        self.assertEqual(
            [0, 2, 4, 6],
            [event["tick"] for event in channels["noise"]["events"]
             if event["kind"] == "beat"],
        )
        self.assertEqual(1, sum(
            event["kind"] == "loop"
            for event in channels["noise"]["events"]
        ))
        noise_beats = [
            event for event in channels["noise"]["events"]
            if event["kind"] == "beat"
        ]
        self.assertEqual([12, 3, 12, 3], [
            event["period_index"] for event in noise_beats
        ])
        self.assertEqual([2, 2, 2, 2], [
            event["length_counter"] for event in noise_beats
        ])

    def test_header_timeline_rejects_unknown_and_unbounded_streams(self) -> None:
        parameters = layout_parameters()
        document = decode_music(synthetic_payload(), parameters)
        with self.assertRaisesRegex(SmbApuError, "unknown SMB music header"):
            music_header_timeline(document, parameters, "missing")
        parameters["note_length_count"] = 40
        document["note_lengths"] = [1] * 40
        document["shared_stream_arena"][0] = 2
        with self.assertRaisesRegex(SmbApuError, "precedes its length command"):
            music_header_timeline(document, parameters, "alpha")

    def test_header_preview_is_deterministic_mixed_pcm_wav(self) -> None:
        parameters = {
            "cpu_address": 0x9000,
            "selector_names": ["song"],
            "ground_schedule_count": 1,
            "headers": [{"name": "song", "size": 6}],
            "shared_stream_size": 14,
            "note_period_count": 4,
            "note_length_count": 8,
            "envelopes": [{"name": "area", "size": 1}],
        }
        document = {
            "selectors": [{"name": "song", "header": "song"}],
            "ground_schedule": ["song"],
            "headers": [{
                "name": "song", "length_table_offset": 0,
                "stream_offset": 0, "triangle_offset": 6,
                "square1_offset": 4, "noise_offset": 11,
            }],
            "shared_stream_arena": [
                0x82, 0x04, 0x06, 0x00, 0x84, 0x84,
                0x80, 0x04, 0x00, 0x06, 0x00, 0x20, 0x10, 0x00,
            ],
            "note_periods": [0x180, 0x123, 0x234, 0x345],
            "note_lengths": [2, 3, 4, 5, 6, 7, 8, 9],
            "envelopes": [{"name": "area", "values": [0x90]}],
        }
        first = render_music_header_pcm(
            document, parameters, "song", sample_rate=8_000
        )
        second = render_music_header_pcm(
            document, parameters, "song", sample_rate=8_000
        )
        muted = render_music_header_pcm(
            document, parameters, "song", sample_rate=8_000,
            muted_channels=("noise",),
        )
        self.assertEqual(first, second)
        self.assertNotEqual(first, muted)
        self.assertEqual(round(8 * 8_000 / 60.0988) * 2, len(first))
        noise_only = render_music_header_pcm(
            document, parameters, "song", sample_rate=8_000,
            muted_channels=("square2", "square1", "triangle"),
        )
        slower_noise = render_music_header_pcm(
            document, parameters, "song", sample_rate=8_000,
            cpu_frequency=1_000_000.0,
            muted_channels=("square2", "square1", "triangle"),
        )
        self.assertNotEqual(noise_only, slower_noise)
        regional_parameters = copy.deepcopy(parameters)
        regional_parameters.update({
            "frame_rate": 50.0,
            "cpu_frequency": 1_000_000.0,
        })
        regional = render_music_header_pcm(
            document, regional_parameters, "song", sample_rate=8_000
        )
        self.assertEqual(round(8 * 8_000 / 50.0) * 2, len(regional))
        self.assertNotEqual(first, regional)
        wav = render_music_header_wav(
            document, parameters, "song", sample_rate=8_000
        )
        self.assertEqual(b"RIFF", wav[:4])
        self.assertEqual(b"WAVE", wav[8:12])
        self.assertEqual(len(first) + 44, len(wav))

    def test_header_preview_rejects_invalid_audio_options(self) -> None:
        document = decode_music(synthetic_payload(), layout_parameters())
        with self.assertRaisesRegex(SmbApuError, "frame_rate"):
            render_music_header_pcm(
                document, layout_parameters(), "silence", frame_rate=0
            )
        with self.assertRaisesRegex(SmbApuError, "muted_channels"):
            render_music_header_pcm(
                document, layout_parameters(), "silence",
                muted_channels=("dmc",),
            )

    def test_header_preview_applies_selector_volume_envelope(self) -> None:
        selector_names = [f"event_{index}" for index in range(8)]
        selector_names.append("area_ground")
        parameters = {
            "cpu_address": 0x9000,
            "selector_names": selector_names,
            "ground_schedule_count": 1,
            "headers": [{"name": "song", "size": 4}],
            "shared_stream_size": 5,
            "note_period_count": 2,
            "note_length_count": 1,
            "envelopes": [{"name": "area", "size": 3}],
        }
        document = {
            "selectors": [
                {"name": name, "header": "song"} for name in selector_names
            ],
            "ground_schedule": ["song"],
            "headers": [{
                "name": "song", "length_table_offset": 0,
                "stream_offset": 0, "triangle_offset": 3,
            }],
            "shared_stream_arena": [0x80, 0x02, 0x00, 0x80, 0x00],
            "note_periods": [0, 0x123],
            "note_lengths": [3],
            "envelopes": [{
                "name": "area", "values": [0x90, 0x94, 0x9F],
            }],
        }
        pcm = render_music_header_pcm(
            document, parameters, "song", selector_name="area_ground",
            sample_rate=8_000, frame_rate=1_000.0,
            cpu_frequency=1_000_000.0,
        )
        samples = struct.unpack(f"<{len(pcm) // 2}h", pcm)
        frame_levels = [
            sum(abs(value) for value in samples[start:start + 8])
            for start in range(0, 24, 8)
        ]
        self.assertGreater(frame_levels[0], frame_levels[1])
        self.assertGreater(frame_levels[1], frame_levels[2])
        self.assertEqual(0, frame_levels[2])
        with self.assertRaisesRegex(SmbApuError, "does not select header"):
            render_music_header_pcm(
                document, parameters, "missing", selector_name="area_ground"
            )

    def test_packaged_profile_drives_regional_preview_timing(self) -> None:
        profile = game_profile("smb1")

        def music_parameters(revision_id: str) -> dict[str, object]:
            resource = next(
                item for item in profile.resources_for(revision_id)
                if item.resource_id == "music"
            )
            return dict(resource.parameters)

        for revision_id in (
            "ju", "pc10", "vs_smb", "fds_smb", "ann_fds", "smb2_jp_fds"
        ):
            with self.subTest(revision=revision_id):
                parameters = music_parameters(revision_id)
                self.assertEqual(60.0988, parameters["frame_rate"])
                self.assertEqual(1_789_773.0, parameters["cpu_frequency"])
                expected_mask = 0x81 if revision_id == "vs_smb" else 0x91
                self.assertEqual(
                    expected_mask, parameters["hardware_envelope_event_mask"]
                )
        pal = music_parameters("pal")
        self.assertEqual(50.00698, pal["frame_rate"])
        self.assertEqual(1_662_607.0, pal["cpu_frequency"])
        self.assertEqual(0x91, pal["hardware_envelope_event_mask"])
        ann = music_parameters("ann_fds")
        smb2 = music_parameters("smb2_jp_fds")
        self.assertEqual((0x794A, 0xD94A, 0x680, 1296), (
            ann["file_offset"], ann["cpu_address"],
            parse_layout(ann).encoded_size, ann["shared_stream_size"],
        ))
        self.assertEqual((0x7949, 0xD949, 0x681, 1297), (
            smb2["file_offset"], smb2["cpu_address"],
            parse_layout(smb2).encoded_size, smb2["shared_stream_size"],
        ))
        self.assertNotIn("victory", [item["name"] for item in ann["headers"]])
        self.assertNotIn("victory", [item["name"] for item in smb2["headers"]])

    def test_selector_context_matches_music_engine_masks(self) -> None:
        parameters = {
            "cpu_address": 0x9000,
            "selector_names": [f"selector_{index}" for index in range(16)],
            "ground_schedule_count": 1,
            "headers": [{"name": "song", "size": 4}],
            "shared_stream_size": 1,
            "note_period_count": 1,
            "note_length_count": 1,
            "envelopes": [
                {"name": "castle_clear", "size": 4},
                {"name": "area", "size": 8},
                {"name": "water_event", "size": 40},
            ],
        }
        document = {
            "selectors": [
                {"name": name, "header": "song"}
                for name in parameters["selector_names"]
            ],
            "ground_schedule": ["song"],
            "headers": [{
                "name": "song", "length_table_offset": 0,
                "stream_offset": 0, "triangle_offset": 0,
            }],
            "shared_stream_arena": [0],
            "note_periods": [0],
            "note_lengths": [1],
            "envelopes": [
                {"name": "castle_clear", "values": [0] * 4},
                {"name": "area", "values": [0] * 8},
                {"name": "water_event", "values": [0] * 40},
            ],
        }
        expected = {
            0: ("event", 0x01, "hardware", 0),
            3: ("event", 0x08, "castle_clear", 0),
            5: ("event", 0x20, "water_event", 0),
            6: ("event", 0x40, "water_event", 8),
            8: ("area", 0x01, "area", 0),
            9: ("area", 0x02, "water_event", 0),
            15: ("area", 0x80, "water_event", 0),
        }
        for index, values in expected.items():
            with self.subTest(selector=index):
                context = music_selector_context(
                    document, parameters, f"selector_{index}"
                )
                self.assertEqual(values, (
                    context["kind"], context["mask"], context["envelope"],
                    context["length_table_adder"],
                ))
                self.assertEqual("song", context["header"])
        vs_parameters = {
            **parameters, "hardware_envelope_event_mask": 0x81,
        }
        self.assertEqual(
            "water_event",
            music_selector_context(
                document, vs_parameters, "selector_4"
            )["envelope"],
        )
        with self.assertRaisesRegex(SmbApuError, "unknown SMB music selector"):
            music_selector_context(document, parameters, "missing")

    def test_file_operation_renders_validated_smb_header_atomically(self) -> None:
        parameters = {
            "cpu_address": 0x9000,
            "frame_rate": 50.0,
            "cpu_frequency": 1_000_000.0,
            "selector_names": ["song"],
            "ground_schedule_count": 1,
            "headers": [{"name": "song", "size": 4}],
            "shared_stream_size": 5,
            "note_period_count": 2,
            "note_length_count": 8,
            "envelopes": [{"name": "area", "size": 1}],
        }
        music = {
            "selectors": [{"name": "song", "header": "song"}],
            "ground_schedule": ["song"],
            "headers": [{
                "name": "song", "length_table_offset": 0,
                "stream_offset": 0, "triangle_offset": 3,
            }],
            "shared_stream_arena": [0x80, 0x02, 0x00, 0x80, 0x00],
            "note_periods": [0, 0x123],
            "note_lengths": [2, 3, 4, 5, 6, 7, 8, 9],
            "envelopes": [{"name": "area", "values": [0x90]}],
        }
        digest = "1" * 64
        bundle = {
            "schema": "retrolarium.nes-resource-bundle",
            "schema_version": 1,
            "game": "smb-sound-probe",
            "revision": "probe",
            "source_sha256": digest,
            "resources": [{
                "id": "music", "kind": "audio", "codec": "smb.apu-music.v1",
                **music,
            }],
        }
        profile = {
            "schema": "retrolarium.game-profile",
            "schema_version": 1,
            "id": "smb-sound-probe",
            "profile_version": 1,
            "name": "SMB sound probe",
            "platform": "nes",
            "capabilities": ["audio"],
            "revisions": [{
                "id": "probe", "name": "Probe", "size": 1,
                "sha1": "1" * 40, "sha256": digest,
                "container": "ines", "mapper": 0,
            }],
            "resources": [{
                "id": "music", "kind": "audio", "codec": "smb.apu-music.v1",
                "capacity_policy": "fixed", "parameters": parameters,
            }],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            document_path = root / "game.json"
            profile_path = root / "profile.json"
            wav_path = root / "preview.wav"
            document_path.write_text(json.dumps(bundle), encoding="utf-8")
            profile_path.write_text(json.dumps(profile), encoding="utf-8")
            report = render_smb_document_wav(
                document_path,
                wav_path,
                resource_id="music",
                header="song",
                selector="song",
                sample_rate=8_000,
                muted_channels=("noise",),
                profile_path=profile_path,
            )
            self.assertEqual("smb.apu-music.v1", report["engine"])
            self.assertEqual(2, report["duration_ticks"])
            self.assertEqual("song", report["selector"])
            self.assertEqual("hardware", report["envelope"])
            self.assertEqual(0, report["length_table_adder"])
            self.assertEqual(50.0, report["frame_rate"])
            self.assertEqual(["noise"], report["muted_channels"])
            self.assertEqual(b"RIFF", wav_path.read_bytes()[:4])
            self.assertEqual(wav_path.stat().st_size, report["size"])

            cli_wav_path = root / "cli-preview.wav"
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                result = cli_main([
                    "audio", "render-smb-wav", str(document_path),
                    "--output", str(cli_wav_path),
                    "--resource", "music", "--header", "song",
                    "--selector", "song",
                    "--sample-rate", "8000", "--mute", "noise",
                    "--profile", str(profile_path),
                ])
            self.assertEqual(0, result)
            cli_report = json.loads(stdout.getvalue())
            self.assertEqual("song", cli_report["header"])
            self.assertEqual("song", cli_report["selector"])
            self.assertEqual("hardware", cli_report["envelope"])
            self.assertEqual(["noise"], cli_report["muted_channels"])
            self.assertEqual(b"RIFF", cli_wav_path.read_bytes()[:4])

            rejected_path = root / "rejected.wav"
            with self.assertRaisesRegex(SmbApuError, "unknown SMB music header"):
                render_smb_document_wav(
                    document_path,
                    rejected_path,
                    resource_id="music",
                    header="missing",
                    profile_path=profile_path,
                )
            self.assertFalse(rejected_path.exists())

    def test_channel_byte_grammar_preserves_engine_fields(self) -> None:
        self.assertEqual({"kind": "end", "raw": 0},
                         decode_channel_byte(0, "square2"))
        self.assertEqual({"kind": "rest", "raw": 0},
                         decode_channel_byte(0, "triangle"))
        self.assertEqual(
            {"kind": "length", "raw": 0x86, "length_index": 6},
            decode_channel_byte(0x86, "square2"),
        )
        self.assertEqual(
            {"kind": "note", "raw": 0x34, "period_index": 26},
            decode_channel_byte(0x34, "triangle"),
        )
        self.assertEqual({"kind": "control", "raw": 0},
                         decode_channel_byte(0, "square1"))
        self.assertEqual(
            {"kind": "note", "raw": 0xD5, "period_index": 10,
             "length_index": 7},
            decode_channel_byte(0xD5, "square1"),
        )
        self.assertEqual({"kind": "loop", "raw": 0},
                         decode_channel_byte(0, "noise"))
        self.assertEqual(
            {"kind": "beat", "raw": 0xE1, "beat": "strong",
             "length_index": 7},
            decode_channel_byte(0xE1, "noise"),
        )

    def test_channel_byte_grammar_rejects_unknown_inputs(self) -> None:
        with self.assertRaisesRegex(SmbApuError, "channel byte"):
            decode_channel_byte(0x100, "square2")
        with self.assertRaisesRegex(SmbApuError, "unknown SMB music channel"):
            decode_channel_byte(0, "dmc")

    def test_channel_commands_lower_to_canonical_bytes(self) -> None:
        cases = (
            ("square2", "end", {}, 0),
            ("triangle", "rest", {}, 0),
            ("square2", "length", {"length_index": 6}, 0x86),
            ("triangle", "note", {"period_index": 26}, 0x34),
            ("square1", "control", {}, 0),
            ("square1", "note", {"period_index": 10, "length_index": 7}, 0xD5),
            ("noise", "loop", {}, 0),
            ("noise", "beat", {"beat": "strong", "length_index": 7}, 0xE1),
        )
        for channel, kind, arguments, expected in cases:
            with self.subTest(channel=channel, kind=kind):
                encoded = encode_channel_command(channel, kind, **arguments)
                self.assertEqual(expected, encoded)
                self.assertEqual(kind, decode_channel_byte(encoded, channel)["kind"])

    def test_channel_commands_reject_ambiguous_or_foreign_forms(self) -> None:
        with self.assertRaisesRegex(SmbApuError, "reserved zero-byte"):
            encode_channel_command("square2", "note", period_index=0)
        with self.assertRaisesRegex(SmbApuError, "control marker"):
            encode_channel_command(
                "square1", "note", period_index=0, length_index=0
            )
        with self.assertRaisesRegex(SmbApuError, "loop marker"):
            encode_channel_command("noise", "beat", beat="rest", length_index=0)
        with self.assertRaisesRegex(SmbApuError, "not valid"):
            encode_channel_command("noise", "note", period_index=1)
        with self.assertRaisesRegex(SmbApuError, "does not accept"):
            encode_channel_command("triangle", "rest", period_index=1)

    def test_round_trip_models_shared_stream_ownership(self) -> None:
        payload = synthetic_payload()
        document = decode_music(payload, layout_parameters())
        self.assertEqual("alpha", document["selectors"][0]["header"])
        self.assertEqual(["silence", "alpha"], document["ground_schedule"])
        self.assertEqual(4, document["headers"][1]["stream_offset"])
        self.assertEqual(7, document["headers"][1]["noise_offset"])
        self.assertEqual([0x0123, 0x0456], document["note_periods"])
        self.assertEqual(payload, encode_music(document, layout_parameters()))

    def test_channel_spans_partition_aliases_without_copying_arena(self) -> None:
        document = decode_music(synthetic_payload(), layout_parameters())
        spans = music_channel_spans(document, layout_parameters())
        self.assertEqual(
            [(0, 2), (2, 1), (3, 1), (4, 2), (6, 2), (8, 3),
             (11, 1), (12, 4)],
            [(span["offset"], span["size"]) for span in spans],
        )
        self.assertEqual(
            [
                {"header": "silence", "channel": "square2"},
                {"header": "silence", "channel": "triangle"},
            ],
            spans[-1]["owners"],
        )
        self.assertEqual(
            document["shared_stream_arena"],
            list(range(16)),
        )

    def test_known_arena_edit_changes_one_shared_byte(self) -> None:
        original = synthetic_payload()
        document = decode_music(original, layout_parameters())
        edited = copy.deepcopy(document)
        edited["shared_stream_arena"][5] = 0xEA
        payload = encode_music(edited, layout_parameters())
        changed = [
            index for index, values in enumerate(zip(original, payload))
            if values[0] != values[1]
        ]
        self.assertEqual(
            [parse_layout(layout_parameters()).shared_stream_offset + 5], changed
        )

    def test_selector_edit_retargets_one_header_offset(self) -> None:
        original = synthetic_payload()
        document = decode_music(original, layout_parameters())
        document["selectors"][0]["header"] = "silence"
        payload = encode_music(document, layout_parameters())
        self.assertEqual([15], list(payload[:1]))
        self.assertEqual(original[1:], payload[1:])

    def test_rejects_reference_to_middle_of_header(self) -> None:
        payload = bytearray(synthetic_payload())
        payload[0] = 5
        with self.assertRaisesRegex(SmbApuError, "unknown header offset"):
            decode_music(bytes(payload), layout_parameters())

    def test_rejects_channel_outside_shared_arena(self) -> None:
        document = decode_music(synthetic_payload(), layout_parameters())
        document["headers"][1]["noise_offset"] = 12
        with self.assertRaisesRegex(SmbApuError, "leaves the shared stream arena"):
            encode_music(document, layout_parameters())

    def test_rejects_layout_beyond_cpu_address_space(self) -> None:
        parameters = layout_parameters()
        parameters["cpu_address"] = 0xFFF0
        with self.assertRaisesRegex(SmbApuError, "CPU address space"):
            parse_layout(parameters)

    def test_leading_envelopes_precede_the_header_offset_base(self) -> None:
        base_parameters = layout_parameters()
        document = decode_music(synthetic_payload(), base_parameters)
        parameters = copy.deepcopy(base_parameters)
        parameters["envelopes"] = [
            {"name": "area", "size": 2, "location": "leading"},
            {"name": "water", "size": 3, "location": "trailing"},
        ]
        payload = encode_music(document, parameters)
        layout = parse_layout(parameters)
        self.assertEqual(bytes((0x90, 0x91)), payload[:2])
        self.assertEqual(bytes((4, 9)), payload[layout.selector_offset:][:2])
        self.assertEqual(document, decode_music(payload, parameters))

    def test_nes_bundle_round_trip_and_known_edit(self) -> None:
        header = b"NES\x1a" + bytes((1, 1, 0, 0)) + bytes(8)
        body = bytearray(0x6000)
        payload = synthetic_payload()
        file_offset = 32
        body[file_offset - 16 : file_offset - 16 + len(payload)] = payload
        image = header + bytes(body)
        revision = RevisionSpec(
            "probe", "SMB APU probe", len(image),
            hashlib.sha1(image).hexdigest(), hashlib.sha256(image).hexdigest(),
            "ines", 0,
        )
        parameters = {
            **layout_parameters(),
            "file_offset": file_offset,
            "size": len(payload),
        }
        profile = GameProfile(
            "smb-apu-probe", 1, "SMB APU probe", "nes", ("audio",),
            (revision,),
            (
                ResourceSpec(
                    "bundle", "resource-bundle", "nes.resource-bundle.v1",
                    "fixed",
                ),
                ResourceSpec(
                    "music", "audio", "smb.apu-music.v1", "fixed", parameters
                ),
            ),
        )
        document = decode_resources(image, profile, revision)
        self.assertEqual(3, inspection_details(document)["smb_music_headers"])
        self.assertEqual(16, inspection_details(document)["smb_music_arena_bytes"])
        exact, report = build_resources(image, document, profile, revision)
        self.assertEqual(image, exact)
        self.assertEqual(0, report["changed_bytes"])

        document.resources[0]["shared_stream_arena"][5] = 0xEA
        edited, report = build_resources(image, document, profile, revision)
        changed = [
            index for index, values in enumerate(zip(image, edited))
            if values[0] != values[1]
        ]
        self.assertEqual(
            [file_offset + parse_layout(parameters).shared_stream_offset + 5],
            changed,
        )
        self.assertEqual(1, report["changed_bytes"])


if __name__ == "__main__":
    unittest.main()
