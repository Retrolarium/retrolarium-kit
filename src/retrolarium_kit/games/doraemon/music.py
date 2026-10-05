"""Doraemon music: the four bank-local drivers' track headers and every
stream byte their channels reach, as editable event lists.

The document keeps the source reconstruction's format (doraemon-music-
streams): per driver its headers, segments of reachable bytes as event
texts ("event $2B", "duration $8E", "CallStream $CB $F0"), and metrics.
A build keeps every header, segment address and size and each event's
width; only the bytes change.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Any, Mapping

from .authoring import (
    Address, AddressRange, AuthoringError, BuildPlan, Change, ChangeSet,
    GameProfile, Provenance, RevisionSpec,
)
from . import doraemon_audio_streams as streams
from . import doraemon_sound_synth as synth
from .doraemon_track_catalog import track_identity
from .ines import parse_ines


CODEC = "doraemon.music-streams.v1"
SCHEMA = "retrolarium.doraemon-music"
VERSION = 1
BANK_SIZE = 0x8000


class DoraemonMusicError(AuthoringError):
    """A music document does not fit the drivers' fixed streams."""


@dataclass(frozen=True)
class DoraemonMusicDocument:
    game_id: str
    revision_id: str
    source_sha256: str
    drivers: tuple[dict[str, Any], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA, "schema_version": VERSION,
            "game": self.game_id, "revision": self.revision_id,
            "source_sha256": self.source_sha256,
            "drivers": [dict(driver) for driver in self.drivers],
        }


def _manifest(profile: GameProfile, revision: RevisionSpec) -> dict[str, Any]:
    resources = [item for item in profile.resources_for(revision) if item.codec == CODEC]
    if len(resources) != 1:
        raise DoraemonMusicError("Doraemon profile needs one music resource")
    return {"schema_version": 1, **resources[0].parameters}


def _prg(source: bytes, revision: RevisionSpec, *, verify: bool = True) -> bytes:
    if verify and hashlib.sha256(source).hexdigest() != revision.sha256:
        raise DoraemonMusicError("music source identity mismatch")
    image = parse_ines(source)
    if len(image.prg) != 4 * BANK_SIZE:
        raise DoraemonMusicError("Doraemon music needs four 32 KiB PRG banks")
    return image.prg


def _plain(value: Any) -> Any:
    """JSON-shaped copies (the profile's parameters are read-only views)."""
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def decode_music(source: bytes, profile: GameProfile, revision: RevisionSpec,
                 *, _verify_identity: bool = True) -> DoraemonMusicDocument:
    prg = _prg(source, revision, verify=_verify_identity)
    try:
        decoded = streams.decode_authoring(prg, _plain(_manifest(profile, revision)))
        if streams.encode_authoring(decoded) != streams.original_payload(prg, decoded):
            raise DoraemonMusicError("music streams do not re-encode to their bytes")
    except (KeyError, TypeError, ValueError) as exc:
        raise DoraemonMusicError(str(exc)) from exc
    return DoraemonMusicDocument(profile.game_id, revision.revision_id, revision.sha256, tuple(decoded["drivers"]))


def parse_music_document(value: object, profile: GameProfile, revision: RevisionSpec) -> DoraemonMusicDocument:
    _manifest(profile, revision)
    if not isinstance(value, Mapping) or set(value) != {
        "schema", "schema_version", "game", "revision", "source_sha256", "drivers",
    }:
        raise DoraemonMusicError("music document fields differ")
    for key, expected in (
        ("schema", SCHEMA), ("schema_version", VERSION), ("game", profile.game_id),
        ("revision", revision.revision_id), ("source_sha256", revision.sha256),
    ):
        if value[key] != expected:
            raise DoraemonMusicError(f"music {key} differs from profile")
    drivers = value["drivers"]
    try:
        streams.encode_authoring({"schema_version": 1, "format": "doraemon-music-streams", "drivers": drivers})
    except (KeyError, TypeError, ValueError) as exc:
        raise DoraemonMusicError(str(exc)) from exc
    return DoraemonMusicDocument(profile.game_id, revision.revision_id, revision.sha256, tuple(drivers))


def _layout(driver: Mapping[str, Any]) -> list[tuple[int, int]]:
    """(address, size) of the header table and each segment."""
    spans = [(streams.number(driver["header_address"]), int(driver["metrics"]["header_bytes"]))]
    spans += [(streams.number(segment["address"]), int(segment["size"])) for segment in driver["segments"]]
    return spans


def build_music(source: bytes, document: DoraemonMusicDocument, profile: GameProfile,
                revision: RevisionSpec) -> tuple[bytes, dict[str, object]]:
    image = parse_ines(source)
    original = decode_music(source, profile, revision)
    checked = parse_music_document(document.as_dict(), profile, revision)
    base = len(image.header) + len(image.trainer)
    changes = []
    for before, after in zip(original.drivers, checked.drivers):
        if _layout(before) != _layout(after) or before["headers"] != after["headers"]:
            raise DoraemonMusicError(f"{before['name']}: headers and stream segments keep their places")
        bank = int(after["bank"])
        payload = streams.encode_driver(after)
        cursor = 0
        for address, size in _layout(after):
            start = base + bank * BANK_SIZE + address - 0x8000
            raw = payload[cursor:cursor + size]
            cursor += size
            if source[start:start + size] != raw:
                changes.append(Change(AddressRange(Address("file", start), size), source[start:start + size], raw,
                                      Provenance(revision.sha256, "music", f"bank{bank}-{address:04x}")))
    output = BuildPlan(revision.sha256, ChangeSet(tuple(changes))).apply(source) if changes else source
    return output, {
        "schema": "retrolarium.doraemon-music-build", "schema_version": 1,
        "game": profile.game_id, "revision": revision.revision_id,
        "source_sha256": revision.sha256,
        "output_sha256": hashlib.sha256(output).hexdigest(),
        "size": len(output),
        "changed_bytes": sum(a != b for a, b in zip(source, output)),
    }


def _streams_document(document: Mapping[str, Any]) -> dict[str, Any]:
    return {"schema_version": 1, "format": "doraemon-music-streams", "drivers": document["drivers"]}


def track_titles(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    result = []
    for d, driver in enumerate(document["drivers"]):
        for h, header in enumerate(driver["headers"]):
            identity = track_identity(str(driver["name"]), int(header["track_id"]))
            result.append({"driver": d, "track": h, "title": identity.title})
    return result


def trace_track(document: Mapping[str, Any], prg: bytes, driver: int, track: int, frames: int) -> dict[str, Any]:
    """Per channel the timed events the driver plays, with their stream
    address, for a piano roll."""
    try:
        decoded = synth.decode_track(_streams_document(document), prg, driver, track, max_frames=frames)
    except (KeyError, TypeError, ValueError) as exc:
        raise DoraemonMusicError(str(exc)) from exc
    import math
    lanes = []
    for lane in decoded.lanes:
        events = []
        for event in lane.events:
            pitch = -1 if event.frequency <= 0 else round(69 + 12 * math.log2(event.frequency / 440.0))
            events.append({"start": event.start_frame, "length": event.duration_frames, "pitch": pitch,
                           "address": event.source_address, "volume": event.volume})
        lanes.append({"name": lane.name, "events": events})
    return {"schema": "retrolarium.doraemon-music-trace", "schema_version": 1,
            "driver": driver, "track": track, "frames": frames, "lanes": lanes}


def render_track(document: Mapping[str, Any], prg: bytes, driver: int, track: int, frames: int, output: Path,
                 channels: set[str] | None = None) -> dict[str, Any]:
    try:
        synth.write_preview(_streams_document(document), prg, driver, track, output,
                            enabled_channels=channels, max_frames=frames)
    except (KeyError, TypeError, ValueError) as exc:
        raise DoraemonMusicError(str(exc)) from exc
    return {"schema": "retrolarium.audio-preview", "schema_version": 1, "engine": CODEC,
            "driver": driver, "track": track, "frames": frames, "output": str(output.resolve())}
