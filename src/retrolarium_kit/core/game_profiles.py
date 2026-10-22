"""Declarative game/revision/resource profiles, from the games' repositories.

A game's profile says where its resources lie in each revision's image, so it
is knowledge about the game: the game's repository keeps it in
config/game_profile.json, and the kit packages none. The kit learns a
profile when it opens the repository (its descriptor, or a studio project
made from it), and the profiles this process opened are the ones the
commands that name a game by its id can use.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from retrolarium_kit.core.authoring import (
    AuthoringError,
    GameProfile,
    PayloadSpec,
    Registry,
    ResourceOverrideSpec,
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
                capacity_policy=_string(item.get("capacity_policy"), f"{item_name}.capacity_policy"),
                parameters=parameters,
                payload=(_string(item.get("payload"), f"{item_name}.payload") if "payload" in item else None),
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


def _resource_overrides(value: object, name: str) -> tuple[ResourceOverrideSpec, ...]:
    if not isinstance(value, list):
        raise AuthoringError(f"{name} must be an array")
    overrides = []
    for index, raw in enumerate(value):
        item_name = f"{name}[{index}]"
        item = _mapping(raw, item_name)
        overrides.append(
            ResourceOverrideSpec(
                resource_id=_string(item.get("id"), f"{item_name}.id"),
                parameters=_mapping(item.get("parameters", {}), f"{item_name}.parameters"),
                payload=(_string(item.get("payload"), f"{item_name}.payload") if "payload" in item else None),
            )
        )
    return tuple(overrides)


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
            _capabilities(item.get("capabilities"), f"revisions[{index}].capabilities")
            if "capabilities" in item
            else None
        )
        revision_resources = (
            _resources(item.get("resources"), f"revisions[{index}].resources") if "resources" in item else None
        )
        revision_resource_overrides = _resource_overrides(
            item.get("resource_overrides", []),
            f"revisions[{index}].resource_overrides",
        )
        revision_payloads = _payloads(item.get("payloads", []), f"revisions[{index}].payloads")
        revisions.append(
            RevisionSpec(
                revision_id=_string(item.get("id"), f"revisions[{index}].id"),
                name=_string(item.get("name"), f"revisions[{index}].name"),
                size=item.get("size"),
                sha1=_string(item.get("sha1"), f"revisions[{index}].sha1"),
                sha256=_string(item.get("sha256"), f"revisions[{index}].sha256"),
                container=_string(item.get("container"), f"revisions[{index}].container"),
                mapper=item.get("mapper"),
                capabilities=revision_capabilities,
                resources=revision_resources,
                resource_overrides=revision_resource_overrides,
                payloads=revision_payloads,
                playtest=(_mapping(item["playtest"], f"revisions[{index}].playtest") if "playtest" in item else None),
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


PROFILE_PATH = Path("config/game_profile.json")

# The profiles of the repositories this process opened, by game id.
_OPENED: dict[str, GameProfile] = {}


def repository_profile(root: Path) -> GameProfile | None:
    """The profile a game repository keeps, now usable by its game id;
    None when the repository keeps none."""
    path = root / PROFILE_PATH
    if not path.is_file():
        return None
    profile = load_game_profile(path)
    _OPENED[profile.game_id] = profile
    return profile


def discover_profiles(*places: Path) -> None:
    """Makes usable the profile of the game repository each place (a file or
    a folder) lies in, at any depth: a document in hack/, a generated project
    in .retrolarium/, the repository's ROM. A place outside any repository
    adds nothing."""
    seen: set[Path] = set()
    for place in places:
        try:
            folder = Path(place).resolve()
        except (OSError, RuntimeError, ValueError):
            continue
        if not folder.is_dir():
            folder = folder.parent
        for candidate in (folder, *folder.parents):
            if candidate in seen:
                break
            seen.add(candidate)
            if (candidate / PROFILE_PATH).is_file():
                repository_profile(candidate)
                break


def game_profiles() -> Registry[GameProfile]:
    """The profiles of the game repositories this process opened, or found
    around the files it was given (discover_profiles)."""
    registry: Registry[GameProfile] = Registry("game profile")
    for game_id in sorted(_OPENED):
        registry.register(game_id, _OPENED[game_id])
    return registry


def recognize_revision(image: bytes, profiles: Iterable[GameProfile] | None = None) -> tuple[GameProfile, RevisionSpec]:
    if profiles is None:
        registry = game_profiles()
        candidates = tuple(registry.get(identifier) for identifier in registry.identifiers())
    else:
        candidates = tuple(profiles)
    sha256 = hashlib.sha256(image).hexdigest()
    for profile in candidates:
        for revision in profile.revisions:
            if revision.size == len(image) and revision.sha256 == sha256:
                if hashlib.sha1(image).hexdigest() != revision.sha1:
                    raise AuthoringError(f"revision {revision.revision_id} SHA-1 contradicts SHA-256")
                return profile, revision
    raise AuthoringError(f"unsupported game revision: size={len(image)} sha256={sha256}")
