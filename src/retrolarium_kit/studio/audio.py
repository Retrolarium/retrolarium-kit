"""File-oriented audio operations shared by the CLI and Retrolarium GUI."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping

from .authoring import AuthoringError, GameProfile, ResourceSpec, RevisionSpec
from .game_profiles import load_game_profile, packaged_game_profiles
from .pacman_sound import (
    CODEC,
    encode_sound_bundle,
    import_midi_commands,
    render_stream_wav,
    sound_summary,
)


def _load_document(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AuthoringError(f"cannot read audio document {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise AuthoringError("audio document must be a JSON object")
    if (value.get("schema"), value.get("schema_version")) != (
        "retrolarium.nes-resource-bundle", 1
    ):
        raise AuthoringError("audio document is not a NES resource bundle v1")
    return value


def _profile(
    document: Mapping[str, object], profile_path: Path | None
) -> tuple[GameProfile, RevisionSpec]:
    game = document.get("game")
    if not isinstance(game, str) or not game:
        raise AuthoringError("audio document game must be a non-empty string")
    profile = (
        load_game_profile(profile_path)
        if profile_path is not None
        else packaged_game_profiles().get(game)
    )
    if profile.game_id != game:
        raise AuthoringError("audio document game differs from the selected profile")
    digest = document.get("source_sha256")
    revision = document.get("revision")
    matches = [
        item for item in profile.revisions
        if item.sha256 == digest and item.revision_id == revision
    ]
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
    matches = [item for item in resources
               if isinstance(item, dict) and item.get("id") == resource_id]
    specs = [
        item for item in profile.resources_for(revision)
        if item.resource_id == resource_id
    ]
    if len(matches) != 1 or len(specs) != 1:
        raise AuthoringError(f"audio resource {resource_id!r} is not uniquely defined")
    resource = matches[0]
    spec = specs[0]
    if resource.get("codec") != CODEC or spec.codec != CODEC:
        raise AuthoringError(f"resource {resource_id!r} does not use {CODEC}")
    encode_sound_bundle(resource, spec.parameters)
    return resource, spec


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
    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent, prefix=f".{destination.name}.", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


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
        document_path, midi_path, resource_id=resource_id, slot=slot,
        track=track, channel=channel, profile_path=profile_path,
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
    edited_resource, edited_spec = _sound_resource(
        edited, profile, revision, resource_id
    )
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
        document_path, midi_path, resource_id=resource_id, slot=slot,
        track=track, channel=channel, profile_path=profile_path,
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
) -> dict[str, object]:
    """Render one validated sound slot into deterministic mono PCM WAV."""

    document = _load_document(document_path)
    profile, revision = _profile(document, profile_path)
    resource, _spec = _sound_resource(document, profile, revision, resource_id)
    item = _slot(resource, slot)
    stream = item.get("stream")
    if not isinstance(stream, Mapping):
        raise AuthoringError(f"sound slot {slot} stream must be an object")
    payload = render_stream_wav(stream, sample_rate)
    _atomic_write(output_path, payload)
    return {
        "schema": "retrolarium.audio-preview",
        "schema_version": 1,
        "engine": CODEC,
        "resource": resource_id,
        "slot": slot,
        "label": item.get("label"),
        "sample_rate": sample_rate,
        "output": str(output_path.resolve()),
        "size": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }
