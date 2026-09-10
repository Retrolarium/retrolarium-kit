"""Load declarative game/revision/resource profiles from package data."""

from __future__ import annotations

import hashlib
from importlib import resources
import json
from pathlib import Path
from typing import Any, Iterable

from .authoring import (
    AuthoringError,
    GameProfile,
    Registry,
    ResourceSpec,
    RevisionSpec,
)


PROFILE_SCHEMA = "retrolarium.game-profile"
PROFILE_SCHEMA_VERSION = 1


def _mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AuthoringError(f"{name} must be an object")
    return value


def _string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise AuthoringError(f"{name} must be a non-empty string")
    return value


def parse_game_profile(document: object) -> GameProfile:
    value = _mapping(document, "game profile")
    if value.get("schema") != PROFILE_SCHEMA:
        raise AuthoringError("unsupported game-profile schema")
    if value.get("schema_version") != PROFILE_SCHEMA_VERSION:
        raise AuthoringError("unsupported game-profile schema version")
    revisions_value = value.get("revisions")
    resources_value = value.get("resources")
    capabilities_value = value.get("capabilities")
    if not isinstance(revisions_value, list) or not revisions_value:
        raise AuthoringError("game profile revisions must be a non-empty array")
    if not isinstance(resources_value, list) or not resources_value:
        raise AuthoringError("game profile resources must be a non-empty array")
    if not isinstance(capabilities_value, list) or any(
        not isinstance(item, str) for item in capabilities_value
    ):
        raise AuthoringError("game profile capabilities must be a string array")

    revisions = []
    for index, raw in enumerate(revisions_value):
        item = _mapping(raw, f"revisions[{index}]")
        revisions.append(
            RevisionSpec(
                revision_id=_string(item.get("id"), f"revisions[{index}].id"),
                name=_string(item.get("name"), f"revisions[{index}].name"),
                size=item.get("size"),
                sha1=_string(item.get("sha1"), f"revisions[{index}].sha1"),
                sha256=_string(
                    item.get("sha256"), f"revisions[{index}].sha256"
                ),
                container=_string(
                    item.get("container"), f"revisions[{index}].container"
                ),
                mapper=item.get("mapper"),
            )
        )

    resource_specs = []
    for index, raw in enumerate(resources_value):
        item = _mapping(raw, f"resources[{index}]")
        parameters = _mapping(
            item.get("parameters", {}), f"resources[{index}].parameters"
        )
        resource_specs.append(
            ResourceSpec(
                resource_id=_string(item.get("id"), f"resources[{index}].id"),
                kind=_string(item.get("kind"), f"resources[{index}].kind"),
                codec=_string(item.get("codec"), f"resources[{index}].codec"),
                capacity_policy=_string(
                    item.get("capacity_policy"),
                    f"resources[{index}].capacity_policy",
                ),
                parameters=parameters,
            )
        )

    return GameProfile(
        game_id=_string(value.get("id"), "game profile id"),
        profile_version=value.get("profile_version"),
        name=_string(value.get("name"), "game profile name"),
        platform=_string(value.get("platform"), "game profile platform"),
        capabilities=tuple(capabilities_value),
        revisions=tuple(revisions),
        resources=tuple(resource_specs),
    )


def load_game_profile(path: Path) -> GameProfile:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AuthoringError(f"cannot read game profile {path}: {exc}") from exc
    return parse_game_profile(document)


def packaged_game_profiles() -> Registry[GameProfile]:
    registry: Registry[GameProfile] = Registry("game profile")
    profile_root = resources.files("retrolarium_kit").joinpath("profiles")
    for item in sorted(profile_root.iterdir(), key=lambda value: value.name):
        if not item.name.startswith("game_") or not item.name.endswith(".json"):
            continue
        try:
            document = json.loads(item.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AuthoringError(
                f"cannot read packaged game profile {item.name}: {exc}"
            ) from exc
        profile = parse_game_profile(document)
        registry.register(profile.game_id, profile)
    if not registry.identifiers():
        raise AuthoringError("package contains no game profiles")
    return registry


def recognize_revision(
    image: bytes, profiles: Iterable[GameProfile] | None = None
) -> tuple[GameProfile, RevisionSpec]:
    if profiles is None:
        registry = packaged_game_profiles()
        candidates = tuple(registry.get(identifier) for identifier in registry.identifiers())
    else:
        candidates = tuple(profiles)
    sha256 = hashlib.sha256(image).hexdigest()
    for profile in candidates:
        for revision in profile.revisions:
            if revision.size == len(image) and revision.sha256 == sha256:
                if hashlib.sha1(image).hexdigest() != revision.sha1:
                    raise AuthoringError(
                        f"revision {revision.revision_id} SHA-1 contradicts SHA-256"
                    )
                return profile, revision
    raise AuthoringError(
        f"unsupported game revision: size={len(image)} sha256={sha256}"
    )
