"""Profile-driven game authoring operations used by the CLI and Qt bridge."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import Any

from .authoring import AuthoringError, GameProfile, Registry, RevisionSpec
from .castle import (
    CastleLevelDocument,
    build_levels,
    decode_levels,
    parse_level_document,
)
from .game_profiles import (
    load_game_profile,
    packaged_game_profiles,
    recognize_revision,
)


def _profiles(custom_profile: Path | None) -> tuple[GameProfile, ...]:
    if custom_profile is not None:
        return (load_game_profile(custom_profile),)
    registry = packaged_game_profiles()
    return tuple(registry.get(identifier) for identifier in registry.identifiers())


class _AuthoringAdapter:
    def decode(
        self, payload: bytes, profile: GameProfile, revision: RevisionSpec
    ) -> CastleLevelDocument:
        return decode_levels(payload, profile, revision)

    def parse(
        self, document: object, profile: GameProfile, revision: RevisionSpec
    ) -> CastleLevelDocument:
        return parse_level_document(document, profile, revision)

    def build(
        self,
        payload: bytes,
        document: CastleLevelDocument,
        profile: GameProfile,
        revision: RevisionSpec,
    ) -> tuple[bytes, dict[str, object]]:
        return build_levels(payload, document, profile, revision)


def authoring_adapters() -> Registry[_AuthoringAdapter]:
    registry: Registry[_AuthoringAdapter] = Registry("authoring adapter")
    registry.register("castle.prefix-rooms.v1", _AuthoringAdapter())
    return registry


def _adapter(profile: GameProfile) -> _AuthoringAdapter:
    return authoring_adapters().get(profile.resource("rooms").codec)


def _read_image(
    path: Path, custom_profile: Path | None
) -> tuple[bytes, GameProfile, RevisionSpec]:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise AuthoringError(f"cannot read game image {path}: {exc}") from exc
    profile, revision = recognize_revision(payload, _profiles(custom_profile))
    return payload, profile, revision


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AuthoringError(f"cannot read authoring document {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise AuthoringError("authoring document must be a JSON object")
    return value


def _atomic_write(path: Path, payload: bytes) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_write_json(path: Path, value: object) -> None:
    _atomic_write(
        path,
        (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )


def profile_catalog() -> dict[str, object]:
    registry = packaged_game_profiles()
    profiles = []
    for identifier in registry.identifiers():
        profile = registry.get(identifier)
        profiles.append(
            {
                "id": profile.game_id,
                "name": profile.name,
                "platform": profile.platform,
                "profile_version": profile.profile_version,
                "capabilities": list(profile.capabilities),
                "revisions": [revision.revision_id for revision in profile.revisions],
                "resources": [resource.resource_id for resource in profile.resources],
            }
        )
    return {
        "schema": "retrolarium.game-profile-catalog",
        "schema_version": 1,
        "profiles": profiles,
    }


def inspect_game(path: Path, *, custom_profile: Path | None = None) -> dict[str, object]:
    payload, profile, revision = _read_image(path, custom_profile)
    levels = _adapter(profile).decode(payload, profile, revision)
    details = {
        "rooms": len(levels.rooms),
        "objects": sum(len(room) for room in levels.objects),
        "width": levels.width,
        "height": levels.height,
    }
    return {
        "schema": "retrolarium.game-inspection",
        "schema_version": 1,
        "path": str(path.resolve()),
        "game": profile.game_id,
        "name": profile.name,
        "profile_version": profile.profile_version,
        "platform": profile.platform,
        "revision": revision.revision_id,
        "sha256": revision.sha256,
        "capabilities": list(profile.capabilities),
        "resources": [
            {
                "id": resource.resource_id,
                "kind": resource.kind,
                "codec": resource.codec,
                "capacity_policy": resource.capacity_policy,
            }
            for resource in profile.resources
        ],
        "details": details,
    }


def extract_game(
    path: Path, output: Path, *, custom_profile: Path | None = None
) -> dict[str, object]:
    payload, profile, revision = _read_image(path, custom_profile)
    levels = _adapter(profile).decode(payload, profile, revision)
    document = levels.as_dict()
    _atomic_write_json(output, document)
    return {
        "schema": "retrolarium.game-extract-result",
        "schema_version": 1,
        "game": profile.game_id,
        "revision": revision.revision_id,
        "resource": "levels",
        "output": str(output.resolve()),
        "rooms": len(levels.rooms),
    }


def validate_game(
    path: Path, document_path: Path, *, custom_profile: Path | None = None
) -> dict[str, object]:
    payload, profile, revision = _read_image(path, custom_profile)
    adapter = _adapter(profile)
    document = adapter.parse(_load_json(document_path), profile, revision)
    _output, report = adapter.build(payload, document, profile, revision)
    return {
        "schema": "retrolarium.game-validation-result",
        "schema_version": 1,
        "status": "valid",
        "game": profile.game_id,
        "revision": revision.revision_id,
        "document": str(document_path.resolve()),
        "build": report,
    }


def build_game(
    path: Path,
    document_path: Path,
    output: Path,
    *,
    custom_profile: Path | None = None,
    report_path: Path | None = None,
) -> dict[str, object]:
    if path.resolve() == output.resolve():
        raise AuthoringError("game build output must not overwrite the source image")
    payload, profile, revision = _read_image(path, custom_profile)
    adapter = _adapter(profile)
    document = adapter.parse(_load_json(document_path), profile, revision)
    rebuilt, report = adapter.build(payload, document, profile, revision)
    _atomic_write(output, rebuilt)
    result = {
        **report,
        "output": str(output.resolve()),
        "document": str(document_path.resolve()),
    }
    if report_path is not None:
        _atomic_write_json(report_path, result)
        result["report"] = str(report_path.resolve())
    return result
