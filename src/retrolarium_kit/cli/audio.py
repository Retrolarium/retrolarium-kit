"""audio: import, export, preview and render typed game audio.

Each command imports what it runs, so the CLI starts without loading every
codec."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from retrolarium_kit.cli.common import print_json


def command_audio_import_midi(args: argparse.Namespace) -> None:
    from retrolarium_kit.studio.audio import import_midi_into_document

    print_json(
        import_midi_into_document(
            args.document,
            args.midi,
            args.output,
            resource_id=args.resource,
            slot=args.slot,
            track=args.track,
            channel=args.channel,
            profile_path=args.profile,
        )
    )


def command_audio_preview_midi(args: argparse.Namespace) -> None:
    from retrolarium_kit.studio.audio import preview_midi_import

    print_json(preview_midi_import(
        args.document,
        args.midi,
        resource_id=args.resource,
        slot=args.slot,
        track=args.track,
        channel=args.channel,
        profile_path=args.profile,
    ))


def command_audio_render_wav(args: argparse.Namespace) -> None:
    from retrolarium_kit.studio.audio import render_document_wav

    print_json(
        render_document_wav(
            args.document,
            args.output,
            resource_id=args.resource,
            slot=args.slot,
            sample_rate=args.sample_rate,
            profile_path=args.profile,
            mix_slots=tuple(args.mix_slot),
        )
    )


def command_audio_export_midi(args: argparse.Namespace) -> None:
    from retrolarium_kit.studio.audio import export_document_midi

    print_json(export_document_midi(
        args.document,
        args.output,
        resource_id=args.resource,
        slot=args.slot,
        profile_path=args.profile,
    ))


def command_audio_render_smb_wav(args: argparse.Namespace) -> None:
    from retrolarium_kit.studio.audio import render_smb_document_wav

    print_json(
        render_smb_document_wav(
            args.document,
            args.output,
            resource_id=args.resource,
            header=args.header,
            selector=args.selector,
            sample_rate=args.sample_rate,
            muted_channels=tuple(args.mute),
            length_table_adder=args.length_table_adder,
            profile_path=args.profile,
        )
    )


def command_audio_render_solomon_wav(args: argparse.Namespace) -> None:
    from retrolarium_kit.games.solomon.audio_preview import render_solomon_wav

    print_json(render_solomon_wav(
        args.document, args.output, effect=args.effect, seconds=args.seconds,
        muted=tuple(args.mute),
    ))


def _doraemon_music_inputs(args: argparse.Namespace) -> tuple[dict, bytes]:
    from retrolarium_kit.studio.project import load_studio_project

    from retrolarium_core.ines import parse_ines
    from retrolarium_kit.studio.create import _project_source
    from retrolarium_kit.studio.project import load_studio_project

    document = json.loads(args.document.read_text(encoding="utf-8"))
    source = _project_source(load_studio_project(args.project)).read_bytes()
    return document, parse_ines(source).prg


def command_audio_render_doraemon_wav(args: argparse.Namespace) -> None:
    from retrolarium_kit.games.doraemon.music import render_track

    document, prg = _doraemon_music_inputs(args)
    print_json(render_track(document, prg, args.driver, args.track, args.frames, args.output,
                            set(args.channel) if args.channel else None))


def command_audio_trace_doraemon(args: argparse.Namespace) -> None:
    from retrolarium_kit.games.doraemon.music import trace_track

    document, prg = _doraemon_music_inputs(args)
    trace = trace_track(document, prg, args.driver, args.track, args.frames)
    args.output.write_text(json.dumps(trace, separators=(",", ":")), encoding="utf-8")
    print_json({"status": "written", "output": str(args.output.resolve())})


def command_audio_render_sfx(args: argparse.Namespace) -> None:
    from retrolarium_kit.studio.project import load_studio_project

    from retrolarium_kit.core.game_profiles import packaged_game_profiles
    from retrolarium_core.ines import parse_ines
    from retrolarium_kit.platform.nes.resources import build_resources, parse_resource_document
    from retrolarium_kit.games.smb.sfx_tables import source_tables
    from retrolarium_kit.platform.nes.sound_effects import CHANNEL_NAMES, render_effect
    from retrolarium_kit.studio.create import _project_source
    from retrolarium_kit.studio.project import load_studio_project

    source = _project_source(load_studio_project(args.project)).read_bytes()
    tables = source_tables(source)
    if args.document is not None:
        # The project's unsaved state: effect tables edited in the studio.
        value = json.loads(args.document.read_text(encoding="utf-8"))
        profile = packaged_game_profiles().get(value.get("game"))
        revision = next(r for r in profile.revisions if r.revision_id == value.get("revision"))
        source, _report = build_resources(source, parse_resource_document(value, profile, revision), profile, revision)
    channels = args.channel or list(CHANNEL_NAMES)
    unknown = set(channels) - set(CHANNEL_NAMES)
    if unknown:
        raise SystemExit(f"unknown channels: {', '.join(sorted(unknown))}")
    muted = {i for i, name in enumerate(CHANNEL_NAMES) if name not in channels}
    print_json(render_effect(args.effect, parse_ines(source).prg, wav=args.wav, trace=args.trace, muted=muted,
                             tables=tables))


def command_audio_render_castle_excellent_wav(args: argparse.Namespace) -> None:
    from retrolarium_kit.games.castle_excellent.audio_preview import render_castle_excellent_wav

    print_json(render_castle_excellent_wav(
        args.document, args.output, track=args.track, seconds=args.seconds,
        muted=tuple(args.mute),
    ))


def command_audio_export_smb_midi(args: argparse.Namespace) -> None:
    from retrolarium_kit.studio.audio import export_smb_document_midi

    print_json(export_smb_document_midi(
        args.document,
        args.output,
        resource_id=args.resource,
        header=args.header,
        selector=args.selector,
        channel=args.channel,
        length_table_adder=args.length_table_adder,
        profile_path=args.profile,
    ))


def _smb_midi_options(args: argparse.Namespace) -> dict[str, object]:
    return {
        "resource_id": args.resource,
        "selector": args.selector,
        "target_channel": args.target_channel,
        "track": args.track,
        "midi_channel": args.midi_channel,
        "max_pitch_error_cents": args.max_pitch_error_cents,
        "max_duration_error_frames": args.max_duration_error_frames,
        "profile_path": args.profile,
    }


def command_audio_preview_smb_midi(args: argparse.Namespace) -> None:
    from retrolarium_kit.studio.audio import preview_smb_midi_import

    print_json(preview_smb_midi_import(
        args.document, args.midi, **_smb_midi_options(args)
    ))


def command_audio_import_smb_midi(args: argparse.Namespace) -> None:
    from retrolarium_kit.studio.audio import import_smb_midi_into_document

    print_json(import_smb_midi_into_document(
        args.document, args.midi, args.output, **_smb_midi_options(args)
    ))


def add_parsers(commands: argparse._SubParsersAction) -> None:
    audio = commands.add_parser(
        "audio", help="convert and preview typed game audio resources"
    )
    audio_commands = audio.add_subparsers(dest="audio_command", required=True)
    audio_import = audio_commands.add_parser(
        "import-midi", help="replace one sound slot from a Standard MIDI file"
    )
    audio_import.add_argument("document", type=Path)
    audio_import.add_argument("--midi", type=Path, required=True)
    audio_import.add_argument("--output", type=Path, required=True)
    audio_import.add_argument("--resource", required=True)
    audio_import.add_argument("--slot", type=int, required=True)
    audio_import.add_argument("--track", type=int, default=0)
    audio_import.add_argument("--channel", type=int, default=0)
    audio_import.add_argument("--profile", type=Path)
    audio_import.set_defaults(handler=command_audio_import_midi)
    audio_export = audio_commands.add_parser(
        "export-midi", help="export one sound slot as a Standard MIDI file"
    )
    audio_export.add_argument("document", type=Path)
    audio_export.add_argument("--output", type=Path, required=True)
    audio_export.add_argument("--resource", required=True)
    audio_export.add_argument("--slot", type=int, required=True)
    audio_export.add_argument("--profile", type=Path)
    audio_export.set_defaults(handler=command_audio_export_midi)
    audio_preview = audio_commands.add_parser(
        "preview-midi", help="validate and report a MIDI conversion without writing"
    )
    audio_preview.add_argument("document", type=Path)
    audio_preview.add_argument("--midi", type=Path, required=True)
    audio_preview.add_argument("--resource", required=True)
    audio_preview.add_argument("--slot", type=int, required=True)
    audio_preview.add_argument("--track", type=int, default=0)
    audio_preview.add_argument("--channel", type=int, default=0)
    audio_preview.add_argument("--profile", type=Path)
    audio_preview.set_defaults(handler=command_audio_preview_midi)
    audio_render = audio_commands.add_parser(
        "render-wav", help="render one sound slot to deterministic WAV"
    )
    audio_render.add_argument("document", type=Path)
    audio_render.add_argument("--output", type=Path, required=True)
    audio_render.add_argument("--resource", required=True)
    audio_render.add_argument("--slot", type=int, required=True)
    audio_render.add_argument("--mix-slot", type=int, action="append", default=[],
                              help="another slot playing along (repeatable)")
    audio_render.add_argument("--sample-rate", type=int, default=44_100)
    audio_render.add_argument("--profile", type=Path)
    audio_render.set_defaults(handler=command_audio_render_wav)
    sfx_render = audio_commands.add_parser(
        "render-sfx", help="play one NES sound effect through the game's own driver to WAV and notes"
    )
    sfx_render.add_argument("project", type=Path)
    sfx_render.add_argument("--effect", required=True,
                            help="smb:square1:N, smb:square2:N, smb:noise:N, castle-excellent:N or doraemon:DRIVER:N")
    sfx_render.add_argument("--document", type=Path, help="NES resource bundle to play instead of the source")
    sfx_render.add_argument("--wav", type=Path)
    sfx_render.add_argument("--trace", type=Path)
    sfx_render.add_argument("--channel", action="append",
                            help="Pulse 1, Pulse 2, Triangle or Noise (repeatable; default all)")
    sfx_render.set_defaults(handler=command_audio_render_sfx)
    doraemon_render = audio_commands.add_parser(
        "render-doraemon-wav", help="render one Doraemon music track to WAV"
    )
    doraemon_trace = audio_commands.add_parser(
        "trace-doraemon", help="write one Doraemon music track's timed events as JSON"
    )
    for sub in (doraemon_render, doraemon_trace):
        sub.add_argument("project", type=Path)
        sub.add_argument("--document", type=Path, required=True)
        sub.add_argument("--driver", type=int, required=True, help="0-3: World 1, 2, 3, shell")
        sub.add_argument("--track", type=int, required=True, help="0-based header")
        sub.add_argument("--frames", type=int, default=1202)
        sub.add_argument("--output", type=Path, required=True)
    doraemon_render.add_argument("--channel", action="append",
                                 help="Pulse 1, Pulse 2, Triangle or Noise (repeatable)")
    doraemon_render.set_defaults(handler=command_audio_render_doraemon_wav)
    doraemon_trace.set_defaults(handler=command_audio_trace_doraemon)
    audio_smb_render = audio_commands.add_parser(
        "render-smb-wav", help="render one SMB music header to deterministic WAV"
    )
    audio_smb_render.add_argument("document", type=Path)
    audio_smb_render.add_argument("--output", type=Path, required=True)
    audio_smb_render.add_argument("--resource", required=True)
    audio_smb_render.add_argument("--header", required=True)
    audio_smb_render.add_argument("--selector")
    audio_smb_render.add_argument("--sample-rate", type=int, default=44_100)
    audio_smb_render.add_argument(
        "--mute", action="append", default=[],
        choices=("square2", "square1", "triangle", "noise"),
    )
    audio_smb_render.add_argument("--length-table-adder", type=int)
    audio_smb_render.add_argument("--profile", type=Path)
    audio_smb_render.set_defaults(handler=command_audio_render_smb_wav)
    audio_solomon_render = audio_commands.add_parser(
        "render-solomon-wav",
        help="render one Solomon's Key audio program (1-26) to WAV",
    )
    audio_solomon_render.add_argument("document", type=Path)
    audio_solomon_render.add_argument("--output", type=Path, required=True)
    audio_solomon_render.add_argument("--effect", type=int, required=True)
    audio_solomon_render.add_argument("--seconds", type=float, default=30.0)
    audio_solomon_render.add_argument(
        "--mute", action="append", default=[],
        choices=("pulse1", "pulse2", "triangle", "noise"),
    )
    audio_solomon_render.set_defaults(handler=command_audio_render_solomon_wav)
    audio_castle_excellent_render = audio_commands.add_parser(
        "render-castle-excellent-wav",
        help="render one Castle Excellent music track set (0-7) to WAV",
    )
    audio_castle_excellent_render.add_argument("document", type=Path,
                                     help="the music resource, or a bundle holding it")
    audio_castle_excellent_render.add_argument("--output", type=Path, required=True)
    audio_castle_excellent_render.add_argument("--track", type=int, required=True)
    audio_castle_excellent_render.add_argument("--seconds", type=float, default=20.0)
    audio_castle_excellent_render.add_argument(
        "--mute", action="append", default=[], choices=("pulse1", "pulse2", "triangle"),
    )
    audio_castle_excellent_render.set_defaults(handler=command_audio_render_castle_excellent_wav)
    audio_smb_export = audio_commands.add_parser(
        "export-smb-midi", help="export one SMB tonal channel as Standard MIDI"
    )
    audio_smb_export.add_argument("document", type=Path)
    audio_smb_export.add_argument("--output", type=Path, required=True)
    audio_smb_export.add_argument("--resource", required=True)
    audio_smb_export.add_argument("--header", required=True)
    audio_smb_export.add_argument("--selector")
    audio_smb_export.add_argument(
        "--channel", required=True,
        choices=("all", "square2", "square1", "triangle", "noise"),
    )
    audio_smb_export.add_argument("--length-table-adder", type=int)
    audio_smb_export.add_argument("--profile", type=Path)
    audio_smb_export.set_defaults(handler=command_audio_export_smb_midi)
    for name, help_text, handler in (
        (
            "preview-smb-midi",
            "validate a guarded SMB MIDI channel edit without writing",
            command_audio_preview_smb_midi,
        ),
        (
            "import-smb-midi",
            "apply a guarded SMB MIDI channel edit to a document",
            command_audio_import_smb_midi,
        ),
    ):
        smb_midi = audio_commands.add_parser(name, help=help_text)
        smb_midi.add_argument("document", type=Path)
        smb_midi.add_argument("--midi", type=Path, required=True)
        if name == "import-smb-midi":
            smb_midi.add_argument("--output", type=Path, required=True)
        smb_midi.add_argument("--resource", required=True)
        smb_midi.add_argument("--selector", required=True)
        smb_midi.add_argument(
            "--target-channel", required=True,
            choices=("all", "square2", "square1", "triangle", "noise"),
        )
        smb_midi.add_argument("--track", type=int, default=-1)
        smb_midi.add_argument("--midi-channel", type=int, default=0)
        smb_midi.add_argument("--max-pitch-error-cents", type=float, default=50.0)
        smb_midi.add_argument(
            "--max-duration-error-frames", type=float, default=1.0
        )
        smb_midi.add_argument("--profile", type=Path)
        smb_midi.set_defaults(handler=handler)
