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
    PayloadSpec,
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


def _capabilities(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise AuthoringError(f"{name} must be a string array")
    return tuple(value)


def _resources(value: object, name: str) -> tuple[ResourceSpec, ...]:
    if not isinstance(value, list) or not value:
        raise AuthoringError(f"{name} must be a non-empty array")
    specs = []
    for index, raw in enumerate(value):
        item_name = f"{name}[{index}]"
        item = _mapping(raw, item_name)
        parameters = _mapping(item.get("parameters", {}), f"{item_name}.parameters")
        specs.append(
            ResourceSpec(
                resource_id=_string(item.get("id"), f"{item_name}.id"),
                kind=_string(item.get("kind"), f"{item_name}.kind"),
                codec=_string(item.get("codec"), f"{item_name}.codec"),
                capacity_policy=_string(
                    item.get("capacity_policy"), f"{item_name}.capacity_policy"
                ),
                parameters=parameters,
                payload=(
                    _string(item.get("payload"), f"{item_name}.payload")
                    if "payload" in item
                    else None
                ),
            )
        )
    return tuple(specs)


def _payloads(value: object, name: str) -> tuple[PayloadSpec, ...]:
    if not isinstance(value, list):
        raise AuthoringError(f"{name} must be an array")
    payloads = []
    for index, raw in enumerate(value):
        item_name = f"{name}[{index}]"
        item = _mapping(raw, item_name)
        record_ids = item.get("record_ids", [])
        if not isinstance(record_ids, list):
            raise AuthoringError(f"{item_name}.record_ids must be an array")
        payloads.append(
            PayloadSpec(
                payload_id=_string(item.get("id"), f"{item_name}.id"),
                storage=_string(item.get("storage"), f"{item_name}.storage"),
                size=item.get("size"),
                sha1=_string(item.get("sha1"), f"{item_name}.sha1"),
                file_offset=item.get("file_offset"),
                record_ids=tuple(record_ids),
                load_address=item.get("load_address"),
            )
        )
    return tuple(payloads)


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
    capabilities = _capabilities(capabilities_value, "game profile capabilities")
    resource_specs = _resources(resources_value, "resources")

    revisions = []
    for index, raw in enumerate(revisions_value):
        item = _mapping(raw, f"revisions[{index}]")
        revision_capabilities = (
            _capabilities(
                item.get("capabilities"), f"revisions[{index}].capabilities"
            )
            if "capabilities" in item
            else None
        )
        revision_resources = (
            _resources(item.get("resources"), f"revisions[{index}].resources")
            if "resources" in item
            else None
        )
        revision_payloads = _payloads(
            item.get("payloads", []), f"revisions[{index}].payloads"
        )
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
                capabilities=revision_capabilities,
                resources=revision_resources,
                payloads=revision_payloads,
            )
        )

    return GameProfile(
        game_id=_string(value.get("id"), "game profile id"),
        profile_version=value.get("profile_version"),
        name=_string(value.get("name"), "game profile name"),
        platform=_string(value.get("platform"), "game profile platform"),
        capabilities=capabilities,
        revisions=tuple(revisions),
        resources=resource_specs,
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
