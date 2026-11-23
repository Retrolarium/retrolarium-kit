"""The bundled, pinned game catalog shared by trust checks and downloads."""

from importlib import resources
import json
import re
from typing import Any, Mapping

from retrolarium_kit.core.authoring import AuthoringError


class CatalogError(AuthoringError):
    pass


def validate_catalog(document: object) -> Mapping[str, Any]:
    if not isinstance(document, Mapping) or document.get("schema") != "retrolarium.game-repositories":
        raise CatalogError("the repository catalog is not a retrolarium.game-repositories document")
    if not isinstance(document.get("organization"), str) or not isinstance(document.get("games"), Mapping):
        raise CatalogError("the repository catalog needs organization and games")
    for game_id, entry in document["games"].items():
        if not isinstance(entry, Mapping):
            raise CatalogError(f"the catalog entry for {game_id} is not an object")
        for key in ("repository", "descriptor", "ref"):
            if not isinstance(entry.get(key), str) or not entry[key]:
                raise CatalogError(f"the catalog entry for {game_id} has no {key}")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", entry["repository"]):
            raise CatalogError(f"the catalog entry for {game_id}: repository must be letters, digits, '-' and '_'")
        if not isinstance(entry.get("commit"), str) or not re.fullmatch(r"[0-9a-f]{40}", entry["commit"]):
            raise CatalogError(f"the catalog entry for {game_id} is not pinned to a commit (40 hex digits)")
        if not isinstance(entry.get("content_sha256"), str) or not re.fullmatch(
            r"[0-9a-f]{64}", entry["content_sha256"]
        ):
            raise CatalogError(f"the catalog entry for {game_id} has no content_sha256 (64 hex digits)")
        if not isinstance(entry.get("revisions"), list):
            raise CatalogError(f"the catalog entry for {game_id} lists no revisions")
    return document


def repository_catalog() -> Mapping[str, Any]:
    path = resources.files("retrolarium_kit.core").joinpath("profiles", "repositories.json")
    return validate_catalog(json.loads(path.read_text(encoding="utf-8")))


def repository_url(catalog: Mapping[str, Any], entry: Mapping[str, Any]) -> str:
    return f"{str(catalog['organization']).rstrip('/')}/{entry['repository']}/tree/{entry['commit']}"
