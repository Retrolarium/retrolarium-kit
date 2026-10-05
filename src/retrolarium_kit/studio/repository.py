"""A game repository's descriptor: the .rlj at the root of a disassembly.

The descriptor (schema "retrolarium.game") tells the studio everything about
the game: the kit profile, the revisions the repository builds (each with
the ROM file it expects at the root), the repository's asset split and the
studio's views. It is tracked in the repository; nothing it leads to is:

- the ROM copies at the root (the repository ignores them already);
- hack/<revision>/: the documents the studio edits, created from the ROM the
  first time and laid over it on build (ignored: they hold game data);
- .retrolarium/<revision>/: the project generated for the studio and every
  build, trace and cache (ignored).

Opening a revision ("prepare") checks its ROM, runs the repository's split
with the kit's own Python when its outputs are missing (no make needed),
creates hack/<revision> and writes the generated project the studio opens.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tomllib
from typing import Any, Mapping

from .authoring import AuthoringError


SCHEMA = "retrolarium.game"
SCHEMA_VERSION = 1
WORKSPACE_DIRECTORY = ".retrolarium"
HACK_DIRECTORY = "hack"
PLATFORMS = frozenset({"nes", "snes", "megadrive"})


class GameRepositoryError(AuthoringError):
    """A game descriptor or one of its revisions cannot be used."""


@dataclass(frozen=True)
class Revision:
    id: str
    label: str
    rom: str
    sha256: str


@dataclass(frozen=True)
class GameDescriptor:
    path: Path
    root: Path
    id: str
    name: str
    platform: str
    profile: str
    revisions: tuple[Revision, ...]
    split: Mapping[str, Any] | None
    workspace: Mapping[str, Any]

    def revision(self, revision_id: str) -> Revision:
        for revision in self.revisions:
            if revision.id == revision_id:
                return revision
        raise GameRepositoryError(f"{self.name} has no revision {revision_id!r}")


def _string(value: object, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise GameRepositoryError(f"{context} must be a non-empty string")
    return value


def _relative(value: object, context: str) -> str:
    text = _string(value, context)
    path = PurePosixPath(text)
    if "\\" in text or path.is_absolute() or ".." in path.parts or ":" in text.split("/")[0]:
        raise GameRepositoryError(f"{context} must be a relative path inside the repository")
    return text


def _command(value: object, context: str) -> list[str]:
    if not isinstance(value, list) or not value or any(not isinstance(item, str) for item in value):
        raise GameRepositoryError(f"{context} must be a list of strings: a script and its arguments")
    _relative(value[0], f"{context}[0]")
    return list(value)


def is_descriptor(path: Path) -> bool:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8")).get("schema") == SCHEMA
    except (OSError, UnicodeError, tomllib.TOMLDecodeError):
        return False


def load_descriptor(path: Path) -> GameDescriptor:
    path = path.resolve()
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise GameRepositoryError(f"cannot read {path.name}: {exc}") from exc
    if raw.get("schema") != SCHEMA or raw.get("schema_version") != SCHEMA_VERSION:
        raise GameRepositoryError(f"{path.name} is not a {SCHEMA} v{SCHEMA_VERSION} descriptor")
    game = raw.get("game")
    if not isinstance(game, Mapping):
        raise GameRepositoryError("the descriptor needs a [game] table")
    platform = _string(game.get("platform"), "game.platform")
    if platform not in PLATFORMS:
        raise GameRepositoryError(f"game.platform must be one of {sorted(PLATFORMS)}")
    profile_id = _string(game.get("profile"), "game.profile")
    profile = _profile_by_id(profile_id)
    shas = {revision.revision_id: revision.sha256 for revision in profile.revisions}
    revisions = []
    for index, item in enumerate(raw.get("revisions") or []):
        if not isinstance(item, Mapping) or set(item) != {"id", "label", "rom"}:
            raise GameRepositoryError(f"revisions[{index}] needs id, label and rom")
        revision_id = _string(item["id"], f"revisions[{index}].id")
        if revision_id not in shas:
            raise GameRepositoryError(f"revisions[{index}].id {revision_id!r} is not a {profile_id} revision")
        revisions.append(Revision(revision_id, _string(item["label"], f"revisions[{index}].label"),
                                  _relative(item["rom"], f"revisions[{index}].rom"), shas[revision_id]))
    if not revisions:
        raise GameRepositoryError("the descriptor lists no revisions")
    split = raw.get("split")
    if split is not None:
        if not isinstance(split, Mapping) or not {"revision", "command"} <= set(split) \
                or set(split) - {"revision", "command", "check"}:
            raise GameRepositoryError("split needs revision and command, and may have check")
        if _string(split["revision"], "split.revision") not in {r.id for r in revisions}:
            raise GameRepositoryError("split.revision must be one of the revisions")
        _command(split["command"], "split.command")
        if "check" in split:
            _command(split["check"], "split.check")
    workspace = raw.get("workspace")
    if not isinstance(workspace, Mapping) or not isinstance(workspace.get("tabs"), list):
        raise GameRepositoryError("the descriptor needs [workspace] with [[workspace.tabs]]")
    return GameDescriptor(path, path.parent, _string(game.get("id"), "game.id"),
                          _string(game.get("name"), "game.name"), platform, profile_id,
                          tuple(revisions), split, workspace)


def _profile_by_id(profile_id: str):
    """The packaged game profile a descriptor names ("pacman.v1")."""
    from .game_profiles import packaged_game_profiles

    registry = packaged_game_profiles()
    for identifier in registry.identifiers():
        profile = registry.get(identifier)
        if f"{profile.game_id}.v{profile.profile_version}" == profile_id or identifier == profile_id:
            return profile
    raise GameRepositoryError(f"the kit has no game profile {profile_id!r}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def identify_rom(path: Path) -> list[dict[str, str]]:
    """Every packaged profile revision whose image this file is."""
    from .game_profiles import packaged_game_profiles

    digest = _sha256(path)
    registry = packaged_game_profiles()
    found = []
    for identifier in registry.identifiers():
        profile = registry.get(identifier)
        for revision in profile.revisions:
            if revision.sha256 == digest:
                found.append({"game": profile.game_id, "profile": identifier,
                              "revision": revision.revision_id, "name": revision.name})
    return found


def _rom_status(descriptor: GameDescriptor, revision: Revision) -> str:
    rom = descriptor.root / revision.rom
    if not rom.is_file():
        return "missing"
    return "ok" if _sha256(rom) == revision.sha256 else "mismatch"


def _project_path(descriptor: GameDescriptor, revision: Revision) -> Path:
    return descriptor.root / WORKSPACE_DIRECTORY / revision.id / f"{descriptor.id}-{revision.id}.rlj"


def _documents(descriptor: GameDescriptor) -> list[tuple[str, str | None]]:
    """The documents the views edit, with the profile resource each holds."""
    seen: dict[str, str | None] = {}
    for tab in descriptor.workspace["tabs"]:
        document = tab.get("document")
        if isinstance(document, str):
            seen.setdefault(_relative(document, "workspace.tabs.document"), tab.get("resource"))
    return list(seen.items())


def _edited(descriptor: GameDescriptor, revision: Revision) -> bool:
    """Whether hack/<revision> differs from the documents first taken from
    the ROM (kept in .retrolarium/<revision>/original)."""
    hack = descriptor.root / HACK_DIRECTORY / revision.id
    original = descriptor.root / WORKSPACE_DIRECTORY / revision.id / "original"
    for document, _resource in _documents(descriptor):
        edited, pristine = hack / document, original / document
        if not edited.is_file():
            continue
        if not pristine.is_file():
            return True
        if edited.suffix == ".json":
            # The studio saves JSON in its own layout: compare the values.
            try:
                if json.loads(edited.read_text(encoding="utf-8")) != json.loads(pristine.read_text(encoding="utf-8")):
                    return True
                continue
            except (OSError, ValueError):
                return True
        if edited.read_bytes() != pristine.read_bytes():
            return True
    return False


def _run(descriptor: GameDescriptor, command: list[str], rom: Path) -> subprocess.CompletedProcess[str]:
    arguments = [part.replace("{rom}", str(rom)) for part in command]
    return subprocess.run([sys.executable, str(descriptor.root / arguments[0]), *arguments[1:]],
                          cwd=descriptor.root, capture_output=True, text=True)


def _split_status(descriptor: GameDescriptor, *, run: bool) -> dict[str, object]:
    """Whether the repository's split outputs come from the right ROM, and
    the split's own result when `run` asks to make them."""
    split = descriptor.split
    if split is None:
        return {"status": "none"}
    reference = descriptor.revision(split["revision"])
    rom = descriptor.root / reference.rom
    if _rom_status(descriptor, reference) != "ok":
        return {"status": "unavailable", "needs": reference.label, "rom": reference.rom}
    if "check" in split and _run(descriptor, split["check"], rom).returncode == 0:
        return {"status": "ok"}
    if not run:
        return {"status": "needed"}
    result = _run(descriptor, split["command"], rom)
    if result.returncode != 0:
        return {"status": "failed", "output": (result.stdout + result.stderr)[-2000:]}
    return {"status": "made"}


def revision_statuses(descriptor: GameDescriptor) -> dict[str, object]:
    split = _split_status(descriptor, run=False)
    revisions = []
    for revision in descriptor.revisions:
        project = _project_path(descriptor, revision)
        revisions.append({
            "id": revision.id,
            "label": revision.label,
            "rom": revision.rom,
            "rom_status": _rom_status(descriptor, revision),
            "edited": _edited(descriptor, revision),
            "project": str(project) if project.is_file() else None,
        })
    return {"schema": "retrolarium.game-revisions", "game": {"id": descriptor.id, "name": descriptor.name},
            "descriptor": str(descriptor.path), "split": split, "revisions": revisions}


def _toml(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ", ".join(_toml(item) for item in value) + "]"
    raise GameRepositoryError(f"cannot write {type(value).__name__} to TOML")


def _project_text(descriptor: GameDescriptor, revision: Revision) -> str:
    """The generated project: the descriptor's views over hack/<revision>."""
    up = "../.."
    lines = [
        "# Generated by the Retrolarium Kit from " + descriptor.path.name + "; do not edit.",
        'schema = "retrolarium.project"',
        "schema_version = 1",
        "",
        "[project]",
        f"id = {_toml(descriptor.id + '-' + revision.id)}",
        f"name = {_toml(descriptor.name + ' [' + revision.label + ']')}",
        f"platform = {_toml(descriptor.platform)}",
        f"profile = {_toml(descriptor.profile)}",
        'source_mode = "rom"',
        "",
        "[source]",
        f"rom = {_toml(up + '/' + revision.rom)}",
        f"sha256 = {_toml(revision.sha256)}",
        f"repository = {_toml(up)}",
        "",
        "[workspace]",
    ]
    workspace = descriptor.workspace
    for key in ("layout", "startup_tab"):
        if key in workspace:
            lines.append(f"{key} = {_toml(workspace[key])}")
    for tab in workspace["tabs"]:
        lines += ["", "[[workspace.tabs]]"]
        for key, value in tab.items():
            if key == "resource":
                continue
            if key == "document":
                value = f"{up}/{HACK_DIRECTORY}/{revision.id}/{_relative(value, 'workspace.tabs.document')}"
            lines.append(f"{key} = {_toml(value)}")
    lines += ["", "[operations]", 'validate = "project.validate"', 'build = "project.build"',
              'verify = "project.verify"', 'launch = "project.launch"', ""]
    return "\n".join(lines)


def prepare_revision(descriptor: GameDescriptor, revision_id: str, *, rom: Path | None = None) -> dict[str, object]:
    """Make one revision ready to open and return its generated project."""
    from .games import extract_game

    revision = descriptor.revision(revision_id)
    target = descriptor.root / revision.rom
    if rom is not None:
        if _sha256(rom) != revision.sha256:
            matches = identify_rom(rom)
            return {"status": "rom-mismatch", "revision": revision.id, "identified": matches,
                    "message": (f"This file is {matches[0]['name']}, not {descriptor.name} [{revision.label}]."
                                if matches else "This file is not a ROM the kit knows (or it was changed).")}
        if rom.resolve() != target.resolve():
            shutil.copyfile(rom, target)
    status = _rom_status(descriptor, revision)
    if status != "ok":
        return {"status": "needs-rom" if status == "missing" else "rom-mismatch", "revision": revision.id,
                "label": revision.label, "rom": revision.rom,
                "message": (f"{descriptor.name} [{revision.label}] needs its ROM ({revision.rom})."
                            if status == "missing" else f"{revision.rom} is not {descriptor.name} [{revision.label}].")}
    split = _split_status(descriptor, run=True)
    hack = descriptor.root / HACK_DIRECTORY / revision.id
    original = descriptor.root / WORKSPACE_DIRECTORY / revision.id / "original"
    created = []
    for document, resource in _documents(descriptor):
        pristine = original / document
        if not pristine.is_file():
            pristine.parent.mkdir(parents=True, exist_ok=True)
            extract_game(target, pristine, resource=resource)
        path = hack / document
        if path.is_file():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(pristine, path)
        created.append(f"{HACK_DIRECTORY}/{revision.id}/{document}")
    project = _project_path(descriptor, revision)
    project.parent.mkdir(parents=True, exist_ok=True)
    text = _project_text(descriptor, revision)
    if not project.is_file() or project.read_text(encoding="utf-8") != text:
        project.write_text(text, encoding="utf-8", newline="\n")
    return {"status": "ready", "revision": revision.id, "label": revision.label, "project": str(project),
            "split": split, "created_documents": created}


def ips_patch(original: bytes, edited: bytes) -> bytes:
    """An IPS patch from `original` to `edited` (same size or larger; IPS
    addresses 16 MiB)."""
    if len(edited) < len(original):
        raise GameRepositoryError("IPS cannot shrink a ROM")
    if len(edited) > 0xFFFFFF:
        raise GameRepositoryError("IPS addresses at most 16 MiB")
    out = bytearray(b"PATCH")
    index = 0
    size = len(edited)
    while index < size:
        if index < len(original) and original[index] == edited[index]:
            index += 1
            continue
        start = index
        while index < size and (index >= len(original) or original[index] != edited[index]) \
                and index - start < 0xFFFF:
            index += 1
        # Offset 0x454F46 spells "EOF": start one byte earlier there.
        if start == 0x454F46:
            start -= 1
        chunk = edited[start:index]
        out += start.to_bytes(3, "big") + len(chunk).to_bytes(2, "big") + chunk
    out += b"EOF"
    return bytes(out)
