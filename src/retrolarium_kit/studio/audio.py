"""File-oriented audio operations shared by the CLI and Retrolarium GUI."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping

from retrolarium_kit.core.authoring import AuthoringError, GameProfile, ResourceSpec, RevisionSpec
from retrolarium_kit.core.game_profiles import load_game_profile, game_profiles
from retrolarium_kit.games.pacman.sound import (
    CODEC,
    NTSC_FRAME_RATE as PACMAN_NTSC_FRAME_RATE,
    encode_sound_bundle,
    import_midi_commands,
    note_periods,
    render_stream_wav,
    sound_byte_to_midi,
    sound_summary,
)
from retrolarium_kit.core.midi import MidiNote, encode_midi, encode_midi_channels
from retrolarium_kit.games.smb.apu import (
    CODEC as SMB_APU_CODEC,
    NTSC_CPU_FREQUENCY,
    NTSC_FRAME_RATE,
    apply_midi_channel_commands,
    apply_midi_song_commands,
    encode_music as encode_smb_music,
    music_header_timeline,
    music_selector_context,
    render_music_header_wav,
)


def _load_document(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AuthoringError(f"cannot read audio document {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise AuthoringError("audio document must be a JSON object")
    if (value.get("schema"), value.get("schema_version")) != ("retrolarium.nes-resource-bundle", 1):
        raise AuthoringError("audio document is not a NES resource bundle v1")
    return value


def _profile(document: Mapping[str, object], profile_path: Path | None) -> tuple[GameProfile, RevisionSpec]:
    game = document.get("game")
    if not isinstance(game, str) or not game:
        raise AuthoringError("audio document game must be a non-empty string")
    profile = load_game_profile(profile_path) if profile_path is not None else game_profiles().get(game)
    if profile.game_id != game:
        raise AuthoringError("audio document game differs from the selected profile")
    digest = document.get("source_sha256")
    revision = document.get("revision")
    matches = [item for item in profile.revisions if item.sha256 == digest and item.revision_id == revision]
    if len(matches) != 1:
        raise AuthoringError("audio document revision differs from the selected profile")
    return profile, matches[0]


def _sound_resource(
    document: Mapping[str, object],
    profile: GameProfile,
    revision: RevisionSpec,
    resource_id: str,
) -> tuple[dict[str, Any], ResourceSpec]:
    resources = document.get("resources")
    if not isinstance(resources, list):
        raise AuthoringError("audio document resources must be an array")
    matches = [item for item in resources if isinstance(item, dict) and item.get("id") == resource_id]
    specs = [item for item in profile.resources_for(revision) if item.resource_id == resource_id]
    if len(matches) != 1 or len(specs) != 1:
        raise AuthoringError(f"audio resource {resource_id!r} is not uniquely defined")
    resource = matches[0]
    spec = specs[0]
    if resource.get("codec") != CODEC or spec.codec != CODEC:
        raise AuthoringError(f"resource {resource_id!r} does not use {CODEC}")
    encode_sound_bundle(resource, spec.parameters)
    return resource, spec


def _smb_music_resource(
    document: Mapping[str, object],
    profile: GameProfile,
    revision: RevisionSpec,
    resource_id: str,
) -> tuple[dict[str, object], ResourceSpec]:
    resources = document.get("resources")
    if not isinstance(resources, list):
        raise AuthoringError("audio document resources must be an array")
    matches = [item for item in resources if isinstance(item, dict) and item.get("id") == resource_id]
    specs = [item for item in profile.resources_for(revision) if item.resource_id == resource_id]
    if len(matches) != 1 or len(specs) != 1:
        raise AuthoringError(f"audio resource {resource_id!r} is not uniquely defined")
    resource = matches[0]
    spec = specs[0]
    if resource.get("codec") != SMB_APU_CODEC or spec.codec != SMB_APU_CODEC:
        raise AuthoringError(f"resource {resource_id!r} does not use {SMB_APU_CODEC}")
    music = {key: value for key, value in resource.items() if key not in {"id", "kind", "codec"}}
    encode_smb_music(music, spec.parameters)
    return music, spec


def _slot(resource: Mapping[str, object], slot: int) -> dict[str, Any]:
    if not isinstance(slot, int) or isinstance(slot, bool) or not 0 <= slot < 16:
        raise AuthoringError("sound slot must be in 0..15")
    slots = resource.get("slots")
    if not isinstance(slots, list) or len(slots) != 16:
        raise AuthoringError("sound resource must contain 16 slots")
    item = slots[slot]
    if not isinstance(item, dict) or item.get("slot") != slot:
        raise AuthoringError(f"sound slot {slot} has invalid metadata")
    return item


def _atomic_write(path: Path, payload: bytes) -> None:
    destination = path.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(dir=destination.parent, prefix=f".{destination.name}.", suffix=".tmp")
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


MIDI_DIVISION = 960
MIDI_TEMPO = 500_000


def _midi_tick(frame: int, frame_rate: float) -> int:
    return round(frame * MIDI_DIVISION * 1_000_000 / (frame_rate * MIDI_TEMPO))


def export_document_midi(
    document_path: Path,
    output_path: Path,
    *,
    resource_id: str,
    slot: int,
    profile_path: Path | None = None,
) -> dict[str, object]:
    """Export one Pac-Man sound slot as a format-0 Standard MIDI file."""
    document = _load_document(document_path)
    profile, revision = _profile(document, profile_path)
    resource, _spec = _sound_resource(document, profile, revision, resource_id)
    item = _slot(resource, slot)
    stream = item.get("stream")
    if not isinstance(stream, Mapping):
        raise AuthoringError(f"sound slot {slot} stream must be an object")
    commands = stream.get("commands")
    if not isinstance(commands, list):
        raise AuthoringError(f"sound slot {slot} commands must be an array")
    frame = 0
    notes: list[MidiNote] = []
    for command in commands:
        if not isinstance(command, Mapping):
            raise AuthoringError(f"sound slot {slot} contains an invalid command")
        duration = command.get("duration", -1)
        if command.get("kind") == "note":
            if not isinstance(duration, int) or duration < 1:
                raise AuthoringError("Pac-Man note duration is invalid")
            start = _midi_tick(frame, PACMAN_NTSC_FRAME_RATE)
            end = _midi_tick(frame + duration, PACMAN_NTSC_FRAME_RATE)
            notes.append(
                MidiNote(
                    sound_byte_to_midi(int(command.get("value", -1))),
                    start,
                    max(start + 1, end),
                )
            )
        if isinstance(duration, int) and duration > 0:
            frame += duration
    payload = encode_midi(tuple(notes), division=MIDI_DIVISION, tempo=MIDI_TEMPO)
    _atomic_write(output_path, payload)
    return {
        "schema": "retrolarium.midi-export",
        "schema_version": 1,
        "engine": CODEC,
        "resource": resource_id,
        "slot": slot,
        "notes": len(notes),
        "duration_frames": frame,
        "output": str(output_path.resolve()),
        "size": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def import_midi_into_document(
    document_path: Path,
    midi_path: Path,
    output_path: Path,
    *,
    resource_id: str,
    slot: int,
    track: int = 0,
    channel: int = 0,
    profile_path: Path | None = None,
) -> dict[str, object]:
    """Replace one slot's commands with a validated, atomic MIDI conversion."""

    edited, report = _prepare_midi_import(
        document_path,
        midi_path,
        resource_id=resource_id,
        slot=slot,
        track=track,
        channel=channel,
        profile_path=profile_path,
    )
    _atomic_write(
        output_path,
        (json.dumps(edited, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    return {
        **report,
        "operation": "apply",
        "output": str(output_path.resolve()),
    }


def _prepare_midi_import(
    document_path: Path,
    midi_path: Path,
    *,
    resource_id: str,
    slot: int,
    track: int,
    channel: int,
    profile_path: Path | None,
) -> tuple[dict[str, Any], dict[str, object]]:
    document = _load_document(document_path)
    profile, revision = _profile(document, profile_path)
    resource, spec = _sound_resource(document, profile, revision, resource_id)
    before = sound_summary(resource, spec.parameters)
    try:
        midi_data = midi_path.read_bytes()
    except OSError as exc:
        raise AuthoringError(f"cannot read MIDI file {midi_path}: {exc}") from exc
    commands, conversion = import_midi_commands(midi_data, track, channel)
    edited = copy.deepcopy(document)
    edited_resource, edited_spec = _sound_resource(edited, profile, revision, resource_id)
    edited_slot = _slot(edited_resource, slot)
    stream = edited_slot.get("stream")
    if not isinstance(stream, dict):
        raise AuthoringError(f"sound slot {slot} stream must be an object")
    stream["commands"] = commands
    after = sound_summary(edited_resource, edited_spec.parameters)
    return edited, {
        **conversion,
        "resource": resource_id,
        "slot": slot,
        "before": before,
        "after": after,
    }


def preview_midi_import(
    document_path: Path,
    midi_path: Path,
    *,
    resource_id: str,
    slot: int,
    track: int = 0,
    channel: int = 0,
    profile_path: Path | None = None,
) -> dict[str, object]:
    """Validate and describe a MIDI conversion without writing a document."""

    _edited, report = _prepare_midi_import(
        document_path,
        midi_path,
        resource_id=resource_id,
        slot=slot,
        track=track,
        channel=channel,
        profile_path=profile_path,
    )
    return {**report, "operation": "preview"}


def render_document_wav(
    document_path: Path,
    output_path: Path,
    *,
    resource_id: str,
    slot: int,
    sample_rate: int = 44_100,
    profile_path: Path | None = None,
    mix_slots: tuple[int, ...] = (),
) -> dict[str, object]:
    """Render one validated sound slot into deterministic mono PCM WAV.

    ``mix_slots`` play along with it on the other square channel (the
    player-ready and intermission tunes take two slots)."""

    document = _load_document(document_path)
    profile, revision = _profile(document, profile_path)
    resource, _spec = _sound_resource(document, profile, revision, resource_id)

    def slot_stream(number: int) -> Mapping[str, object]:
        stream = _slot(resource, number).get("stream")
        if not isinstance(stream, Mapping):
            raise AuthoringError(f"sound slot {number} stream must be an object")
        return stream

    item = _slot(resource, slot)
    stream = slot_stream(slot)
    tables = next(
        (
            item
            for item in document.get("resources", [])
            if isinstance(item, dict) and item.get("id") == "render-tables"
        ),
        None,
    )
    if tables is None:
        raise AuthoringError("the project lacks the game's sound tables; upgrade it with this kit")
    payload = render_stream_wav(
        stream,
        sample_rate,
        periods=note_periods(tables["tables"]["note_period_base_pairs"]),
        also=tuple(slot_stream(number) for number in mix_slots),
    )
    _atomic_write(output_path, payload)
    return {
        "schema": "retrolarium.audio-preview",
        "schema_version": 1,
        "engine": CODEC,
        "resource": resource_id,
        "slot": slot,
        "mixed_slots": list(mix_slots),
        "label": item.get("label"),
        "sample_rate": sample_rate,
        "output": str(output_path.resolve()),
        "size": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def render_smb_document_wav(
    document_path: Path,
    output_path: Path,
    *,
    resource_id: str,
    header: str,
    selector: str | None = None,
    sample_rate: int = 44_100,
    muted_channels: tuple[str, ...] = (),
    length_table_adder: int | None = None,
    profile_path: Path | None = None,
) -> dict[str, object]:
    """Render one validated SMB music header to an atomic WAV output."""

    document = _load_document(document_path)
    profile, revision = _profile(document, profile_path)
    resource, spec = _smb_music_resource(document, profile, revision, resource_id)
    selector_context = music_selector_context(resource, spec.parameters, selector) if selector is not None else None
    if selector_context is not None and selector_context["header"] != header:
        raise AuthoringError(f"SMB music selector {selector!r} does not select header {header!r}")
    resolved_length_adder = (
        int(selector_context["length_table_adder"])
        if length_table_adder is None and selector_context is not None
        else 0
        if length_table_adder is None
        else length_table_adder
    )
    timeline = music_header_timeline(
        resource,
        spec.parameters,
        header,
        length_table_adder=resolved_length_adder,
    )
    payload = render_music_header_wav(
        resource,
        spec.parameters,
        header,
        selector_name=selector,
        sample_rate=sample_rate,
        muted_channels=muted_channels,
        length_table_adder=length_table_adder,
    )
    _atomic_write(output_path, payload)
    return {
        "schema": "retrolarium.audio-preview",
        "schema_version": 1,
        "engine": SMB_APU_CODEC,
        "resource": resource_id,
        "header": header,
        "selector": selector,
        "envelope": (selector_context["envelope"] if selector_context is not None else None),
        "length_table_adder": resolved_length_adder,
        "duration_ticks": timeline["duration"],
        "muted_channels": list(muted_channels),
        "sample_rate": sample_rate,
        "frame_rate": spec.parameters.get("frame_rate", NTSC_FRAME_RATE),
        "cpu_frequency": spec.parameters.get("cpu_frequency", NTSC_CPU_FREQUENCY),
        "output": str(output_path.resolve()),
        "size": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def encode_smb_music_midi(
    resource: dict[str, Any],
    parameters: Mapping[str, object],
    header: str,
    channel: str,
    *,
    selector: str | None = None,
    length_table_adder: int | None = None,
) -> tuple[bytes, list[str], int, int]:
    """The resource-level conversion used by both CLI export and parity tests."""
    if channel not in {"all", "square2", "square1", "triangle", "noise"}:
        raise AuthoringError("unknown SMB MIDI export channel")
    selector_context = music_selector_context(resource, parameters, selector) if selector is not None else None
    if selector_context is not None and selector_context["header"] != header:
        raise AuthoringError(f"SMB music selector {selector!r} does not select header {header!r}")
    resolved_adder = (
        int(selector_context["length_table_adder"])
        if length_table_adder is None and selector_context is not None
        else 0
        if length_table_adder is None
        else length_table_adder
    )
    timeline = music_header_timeline(
        resource,
        parameters,
        header,
        length_table_adder=resolved_adder,
    )
    frame_rate = float(parameters.get("frame_rate", NTSC_FRAME_RATE))
    cpu_frequency = float(parameters.get("cpu_frequency", NTSC_CPU_FREQUENCY))
    channel_numbers = {"square2": 0, "square1": 1, "triangle": 2, "noise": 9}
    selected = tuple(channel_numbers) if channel == "all" else (channel,)
    exported: dict[int, tuple[MidiNote, ...]] = {}
    exported_names: list[str] = []
    for channel_name in selected:
        raw_channel = next(
            (item for item in timeline["channels"] if item["channel"] == channel_name),
            None,
        )
        if raw_channel is None:
            if channel == "all":
                continue
            raise AuthoringError(f"SMB header {header!r} has no {channel_name} channel")
        notes: list[MidiNote] = []
        if channel_name == "noise":
            percussion = {"short": 42, "strong": 38, "long": 49}
            for event in raw_channel["events"]:
                pitch = percussion.get(str(event.get("beat")))
                if event.get("kind") != "beat" or pitch is None:
                    continue
                start_frame = int(event["tick"])
                end_frame = start_frame + int(event["duration"])
                start = _midi_tick(start_frame, frame_rate)
                end = _midi_tick(end_frame, frame_rate)
                notes.append(MidiNote(pitch, start, max(start + 1, end)))
        else:
            divider = 32.0 if channel_name == "triangle" else 16.0
            for event in raw_channel["events"]:
                if event.get("kind") != "note":
                    continue
                period = int(event["period"])
                if period < 8 or (period & 0xFF) == 0:
                    continue
                frequency = cpu_frequency / (divider * ((period & 0x7FF) + 1))
                pitch = max(0, min(127, round(69.0 + 12.0 * math.log2(frequency / 440.0))))
                start_frame = int(event["tick"])
                end_frame = start_frame + int(event["duration"])
                start = _midi_tick(start_frame, frame_rate)
                end = _midi_tick(end_frame, frame_rate)
                notes.append(MidiNote(pitch, start, max(start + 1, end)))
        exported[channel_numbers[channel_name]] = tuple(notes)
        exported_names.append(channel_name)
    end_tick = _midi_tick(int(timeline["duration"]), frame_rate)
    payload = encode_midi_channels(
        exported,
        division=MIDI_DIVISION,
        tempo=MIDI_TEMPO,
        end_tick=end_tick,
    )
    return (
        payload,
        exported_names,
        sum(len(notes) for notes in exported.values()),
        int(timeline["duration"]),
    )


def export_smb_document_midi(
    document_path: Path,
    output_path: Path,
    *,
    resource_id: str,
    header: str,
    channel: str,
    selector: str | None = None,
    length_table_adder: int | None = None,
    profile_path: Path | None = None,
) -> dict[str, object]:
    """Export one SMB channel or the complete four-channel song as MIDI."""
    if channel not in {"all", "square2", "square1", "triangle", "noise"}:
        raise AuthoringError("unknown SMB MIDI export channel")
    document = _load_document(document_path)
    profile, revision = _profile(document, profile_path)
    resource, spec = _smb_music_resource(document, profile, revision, resource_id)
    payload, exported_names, note_count, duration_ticks = encode_smb_music_midi(
        resource,
        spec.parameters,
        header,
        channel,
        selector=selector,
        length_table_adder=length_table_adder,
    )
    _atomic_write(output_path, payload)
    return {
        "schema": "retrolarium.midi-export",
        "schema_version": 1,
        "engine": SMB_APU_CODEC,
        "resource": resource_id,
        "header": header,
        "selector": selector,
        "channel": channel,
        "channels": exported_names,
        "notes": note_count,
        "duration_ticks": duration_ticks,
        "output": str(output_path.resolve()),
        "size": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _prepare_smb_midi_import(
    document_path: Path,
    midi_path: Path,
    *,
    resource_id: str,
    selector: str,
    target_channel: str,
    track: int,
    midi_channel: int,
    max_pitch_error_cents: float,
    max_duration_error_frames: float,
    profile_path: Path | None,
) -> tuple[dict[str, Any], dict[str, object]]:
    document = _load_document(document_path)
    profile, revision = _profile(document, profile_path)
    music, spec = _smb_music_resource(document, profile, revision, resource_id)
    try:
        midi_data = midi_path.read_bytes()
    except OSError as exc:
        raise AuthoringError(f"cannot read MIDI file {midi_path}: {exc}") from exc
    if target_channel == "all":
        edited_music, report = apply_midi_song_commands(
            midi_data,
            music,
            spec.parameters,
            selector,
            track=track,
            max_pitch_error_cents=max_pitch_error_cents,
            max_duration_error_frames=max_duration_error_frames,
        )
    else:
        edited_music, report = apply_midi_channel_commands(
            midi_data,
            music,
            spec.parameters,
            selector,
            target_channel,
            track=track,
            midi_channel=midi_channel,
            max_pitch_error_cents=max_pitch_error_cents,
            max_duration_error_frames=max_duration_error_frames,
        )
    edited = copy.deepcopy(document)
    resources = edited["resources"]
    assert isinstance(resources, list)
    edited_resource = next(item for item in resources if isinstance(item, dict) and item.get("id") == resource_id)
    edited_resource["shared_stream_arena"] = edited_music["shared_stream_arena"]
    _smb_music_resource(edited, profile, revision, resource_id)
    return edited, {**report, "resource": resource_id}


def preview_smb_midi_import(
    document_path: Path,
    midi_path: Path,
    *,
    resource_id: str,
    selector: str,
    target_channel: str,
    track: int = -1,
    midi_channel: int = 0,
    max_pitch_error_cents: float = 50.0,
    max_duration_error_frames: float = 1.0,
    profile_path: Path | None = None,
) -> dict[str, object]:
    """Validate a guarded SMB MIDI edit without writing the document."""

    _edited, report = _prepare_smb_midi_import(
        document_path,
        midi_path,
        resource_id=resource_id,
        selector=selector,
        target_channel=target_channel,
        track=track,
        midi_channel=midi_channel,
        max_pitch_error_cents=max_pitch_error_cents,
        max_duration_error_frames=max_duration_error_frames,
        profile_path=profile_path,
    )
    return {**report, "operation": "preview"}


def import_smb_midi_into_document(
    document_path: Path,
    midi_path: Path,
    output_path: Path,
    *,
    resource_id: str,
    selector: str,
    target_channel: str,
    track: int = -1,
    midi_channel: int = 0,
    max_pitch_error_cents: float = 50.0,
    max_duration_error_frames: float = 1.0,
    profile_path: Path | None = None,
) -> dict[str, object]:
    """Apply one guarded SMB MIDI channel edit to an atomic JSON output."""

    edited, report = _prepare_smb_midi_import(
        document_path,
        midi_path,
        resource_id=resource_id,
        selector=selector,
        target_channel=target_channel,
        track=track,
        midi_channel=midi_channel,
        max_pitch_error_cents=max_pitch_error_cents,
        max_duration_error_frames=max_duration_error_frames,
        profile_path=profile_path,
    )
    _atomic_write(
        output_path,
        (json.dumps(edited, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    return {
        **report,
        "operation": "apply",
        "output": str(output_path.resolve()),
    }
