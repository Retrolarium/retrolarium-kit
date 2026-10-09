"""A game repository's descriptor: the .rlj at the root of a disassembly.

The descriptor (schema "retrolarium.game") names the game and its kit
profile, the revisions the repository builds (each with the ROM file it
expects at the root) and the repository's asset split. The studio's views
are the kit's, one set per profile (studio/views/<profile>.toml), so the
repositories do not each carry a copy. The descriptor is tracked in the
repository; nothing it leads to is:

- the ROM copies at the root (the repository ignores them already);
- hack/<revision>/: the documents the studio edits, created from the ROM the
  first time and laid over it on build (ignored: they hold game data);
- .retrolarium/<revision>/: the project generated for the studio and every
  build, trace and cache (ignored).

Flicky builds through its repository's own content pipeline instead: its
revision gets a snapshot of the repository in .retrolarium/<revision>/
flicky-source, and the edited documents are that snapshot's workspace.

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
import tempfile
import tomllib
from typing import Any, Mapping
from importlib import resources

from retrolarium_kit.core.authoring import AuthoringError


SCHEMA = "retrolarium.game"
SCHEMA_VERSION = 1
WORKSPACE_DIRECTORY = ".retrolarium"
HACK_DIRECTORY = "hack"
PLATFORMS = frozenset({"nes", "snes", "megadrive"})
FLICKY_PROFILE = "flicky-ue.v1"
FLICKY_REVISION = "ue"


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
    source_mode: str = "rom"

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


def profile_views(profile_id: str) -> Mapping[str, Any]:
    """The studio views of a profile: its [workspace] with its tabs."""
    view_file = resources.files("retrolarium_kit.studio") / "views" / f"{profile_id}.toml"
    try:
        workspace = tomllib.loads(view_file.read_text(encoding="utf-8")).get("workspace")
    except FileNotFoundError:
        raise GameRepositoryError(f"the kit has no studio views for the {profile_id} profile") from None
    if not isinstance(workspace, Mapping) or not isinstance(workspace.get("tabs"), list):
        raise GameRepositoryError(f"the {profile_id} views need [workspace] with [[workspace.tabs]]")
    return workspace


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
    if profile_id == FLICKY_PROFILE:
        from retrolarium_kit.games.flicky.content import CANONICAL_SHA256

        shas = {FLICKY_REVISION: CANONICAL_SHA256}
    else:
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
    if "workspace" in raw:
        raise GameRepositoryError(
            f"{path.name}: the studio views come from the kit now; remove [workspace] from the descriptor"
        )
    workspace = profile_views(profile_id)
    for tab in workspace["tabs"]:
        only = tab.get("revisions") if isinstance(tab, Mapping) else None
        if only is not None and (not isinstance(only, list) or not only or not set(only) <= set(shas)):
            raise GameRepositoryError(f"{profile_id} view {tab.get('id')!r}: revisions must list revisions of the profile")
    return GameDescriptor(path, path.parent, _string(game.get("id"), "game.id"),
                          _string(game.get("name"), "game.name"), platform, profile_id,
                          tuple(revisions), split, workspace,
                          "flicky-content" if profile_id == FLICKY_PROFILE else "rom")


def _profile_by_id(profile_id: str):
    """The packaged game profile a descriptor names ("pacman.v1")."""
    from retrolarium_kit.core.game_profiles import packaged_game_profiles

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
    from retrolarium_kit.core.game_profiles import packaged_game_profiles

    from retrolarium_kit.games.flicky.content import CANONICAL_SHA256

    digest = _sha256(path)
    registry = packaged_game_profiles()
    found = []
    if digest == CANONICAL_SHA256:
        found.append({"game": "flicky", "profile": "flicky", "revision": FLICKY_REVISION,
                      "name": "Flicky (USA, Europe)"})
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


def _tabs(descriptor: GameDescriptor, revision: Revision) -> list[Mapping[str, Any]]:
    """The views of one revision: a tab with `revisions` shows only in those."""
    return [tab for tab in descriptor.workspace["tabs"]
            if revision.id in tab.get("revisions", [revision.id])]


def _documents(descriptor: GameDescriptor, revision: Revision) -> list[tuple[str, str | None]]:
    """The documents the views edit, with the profile resource each holds."""
    seen: dict[str, str | None] = {}
    for tab in _tabs(descriptor, revision):
        document = tab.get("document")
        if isinstance(document, str):
            seen.setdefault(_relative(document, "workspace.tabs.document"), tab.get("resource"))
    return list(seen.items())


def _edits(descriptor: GameDescriptor, revision: Revision) -> Path:
    """Where a revision's edited documents live."""
    if descriptor.source_mode == "flicky-content":
        from retrolarium_kit.games.flicky.content import SOURCE_DIRECTORY

        return descriptor.root / WORKSPACE_DIRECTORY / revision.id / SOURCE_DIRECTORY
    return descriptor.root / HACK_DIRECTORY / revision.id


def _edited(descriptor: GameDescriptor, revision: Revision) -> bool:
    """Whether the edited documents differ from the ones first taken from
    the ROM (kept in .retrolarium/<revision>/original)."""
    hack = _edits(descriptor, revision)
    original = descriptor.root / WORKSPACE_DIRECTORY / revision.id / "original"
    for document, _resource in _documents(descriptor, revision):
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
    """The generated project: the descriptor's views over hack/<revision>
    (Flicky: over its snapshot's workspace)."""
    up = "../.."
    flicky = descriptor.source_mode == "flicky-content"
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
        f"source_mode = {_toml(descriptor.source_mode)}",
        "",
        "[source]",
        *(['manifest = "flicky-source/config/authoring/content_studios.json"'] if flicky else
          [f"rom = {_toml(up + '/' + revision.rom)}"]),
        f"sha256 = {_toml(revision.sha256)}",
        *([] if flicky else [f"repository = {_toml(up)}"]),
        "",
        "[workspace]",
    ]
    workspace = descriptor.workspace
    for key in ("layout", "startup_tab"):
        if key in workspace:
            lines.append(f"{key} = {_toml(workspace[key])}")
    for tab in _tabs(descriptor, revision):
        lines += ["", "[[workspace.tabs]]"]
        for key, value in tab.items():
            if key in ("resource", "revisions"):
                continue
            if key == "document":
                document = _relative(value, "workspace.tabs.document")
                value = f"flicky-source/{document}" if flicky else f"{up}/{HACK_DIRECTORY}/{revision.id}/{document}"
            lines.append(f"{key} = {_toml(value)}")
    lines += ["", "[operations]", 'validate = "project.validate"', 'build = "project.build"',
              'verify = "project.verify"', 'launch = "project.launch"', ""]
    return "\n".join(lines)


def prepare_revision(descriptor: GameDescriptor, revision_id: str, *, rom: Path | None = None) -> dict[str, object]:
    """Make one revision ready to open and return its generated project."""
    from retrolarium_kit.games.catalog import extract_game

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
    hack = _edits(descriptor, revision)
    original = descriptor.root / WORKSPACE_DIRECTORY / revision.id / "original"
    created = []
    if descriptor.source_mode == "flicky-content":
        created = _prepare_flicky(descriptor, revision, target, original)
    else:
        for document, resource in _documents(descriptor, revision):
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


def reset_revision(descriptor: GameDescriptor, revision_id: str) -> dict[str, object]:
    """Put back the documents a revision had when they were first taken
    from its ROM, discarding its edits."""
    revision = descriptor.revision(revision_id)
    original = descriptor.root / WORKSPACE_DIRECTORY / revision.id / "original"
    edits = _edits(descriptor, revision)
    restored = []
    for document, _resource in _documents(descriptor, revision):
        pristine, path = original / document, edits / document
        if not pristine.is_file():
            raise GameRepositoryError(f"{descriptor.name} [{revision.label}] has no original of {document}; "
                                      "open the version once first")
        if path.is_file() and path.read_bytes() == pristine.read_bytes():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(pristine, path)
        restored.append(document)
    return {"status": "reset", "revision": revision.id, "label": revision.label, "restored_documents": restored}


# What a repository's own files are when it is copied for a check: Git's
# list in a checkout, else everything but what the studio and builds make.
_UNTRACKED = frozenset({".git", WORKSPACE_DIRECTORY, HACK_DIRECTORY, "build", "__pycache__"})


def _repository_files(root: Path) -> list[str]:
    if (root / ".git").exists():
        result = subprocess.run(["git", "ls-files", "-z", "--cached"], cwd=root,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode:
            raise GameRepositoryError(f"cannot list the files of {root}: {result.stderr.decode(errors='replace')}")
        return [os.fsdecode(raw) for raw in result.stdout.split(b"\0") if raw]
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*")
                  if path.is_file() and not _UNTRACKED & set(path.relative_to(root).parts))


def check_repository(descriptor: GameDescriptor, revision_ids: list[str] | None = None, *,
                     flicky_level_writer: Path | None = None) -> dict[str, object]:
    """Prove that the kit opens every revision of a repository and gives its
    ROM back unchanged: in a throwaway copy of the repository, each revision
    is prepared from its ROM, validated, built with no edits and compared
    byte for byte with that ROM. The user's hack/ and .retrolarium/ are
    neither read nor written."""
    from retrolarium_kit.studio.project import build_studio_project, validate_studio_project

    chosen = [descriptor.revision(revision_id) for revision_id in revision_ids] \
        if revision_ids else list(descriptor.revisions)
    results = []
    with tempfile.TemporaryDirectory(prefix="rltool-check-") as directory:
        copy = Path(directory) / descriptor.root.name
        for name in _repository_files(descriptor.root):
            source = descriptor.root / name
            if source.is_file():
                (copy / name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, copy / name)
        for revision in descriptor.revisions:
            rom = descriptor.root / revision.rom
            if rom.is_file():
                shutil.copyfile(rom, copy / revision.rom)
        shadow = load_descriptor(copy / descriptor.path.name)
        for revision in chosen:
            entry: dict[str, object] = {"revision": revision.id, "label": revision.label}
            results.append(entry)
            try:
                ready = prepare_revision(shadow, revision.id)
                if ready["status"] != "ready":
                    entry.update(status="failed", reason=ready.get("message", ready["status"]))
                    continue
                project = Path(str(ready["project"]))
                validate_studio_project(project)
                report = build_studio_project(
                    project, flicky_level_writer=flicky_level_writer
                    if shadow.source_mode == "flicky-content" else None)
            except ValueError as exc:  # every kit refusal is a ValueError
                entry.update(status="failed", reason=str(exc))
                continue
            built = (project.parent / report["artifact"]["path"]).read_bytes()
            original = (copy / revision.rom).read_bytes()
            if built != original:
                entry.update(status="failed", reason="a build with no edits differs from the ROM")
                continue
            entry.update(status="ok", documents=len(_documents(shadow, revision)), size=len(built))
    failed = [entry["revision"] for entry in results if entry["status"] != "ok"]
    return {"schema": "retrolarium.game-check", "game": {"id": descriptor.id, "name": descriptor.name},
            "profile": descriptor.profile, "status": "failed" if failed else "ok", "revisions": results}


def _prepare_flicky(descriptor: GameDescriptor, revision: Revision, rom: Path, original: Path) -> list[str]:
    """Import the repository into the revision's snapshot once, keeping a
    pristine copy of its workspace documents."""
    from retrolarium_kit.games.flicky.content import import_flicky_source

    workspace = descriptor.root / WORKSPACE_DIRECTORY / revision.id
    snapshot = _edits(descriptor, revision)
    created = []
    if not snapshot.is_dir():
        import_flicky_source(descriptor.root, rom, workspace)
        created = [f"{WORKSPACE_DIRECTORY}/{revision.id}/{snapshot.name}/{document}"
                   for document, _resource in _documents(descriptor, revision)]
    for document, _resource in _documents(descriptor, revision):
        pristine = original / document
        if not pristine.is_file():
            pristine.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(snapshot / document, pristine)
    return created


def repository_catalog() -> Mapping[str, Any]:
    """The game repositories the kit knows: where each game's disassembly
    is published and the descriptor at its root."""
    from importlib import resources

    path = resources.files("retrolarium_kit.core").joinpath("profiles", "repositories.json")
    return json.loads(path.read_text(encoding="utf-8"))


def _archive_url(catalog: Mapping[str, Any], entry: Mapping[str, Any], source: str | None) -> str:
    template = source or os.environ.get("RETROLARIUM_GAMES_SOURCE") \
        or "{organization}/{repository}/archive/refs/heads/{branch}.zip"
    return template.format(organization=catalog["organization"], repository=entry["repository"],
                           branch=catalog["branch"])


def fetch_repository(game_id: str, games: Path, *, source: str | None = None) -> Path:
    """Download a game's repository as an archive (no Git needed) into
    games/<repository> and return its descriptor. `source` (or
    $RETROLARIUM_GAMES_SOURCE) is a URL or file template with {organization},
    {repository} and {branch}."""
    import urllib.request
    import zipfile

    catalog = repository_catalog()
    entry = catalog["games"].get(game_id)
    if entry is None:
        raise GameRepositoryError(f"the kit knows no repository for {game_id!r}")
    target = games / entry["repository"]
    descriptor = target / entry["descriptor"]
    if descriptor.is_file():
        return descriptor
    if target.exists():
        raise GameRepositoryError(f"{target} exists but has no {entry['descriptor']}")
    url = _archive_url(catalog, entry, source)
    games.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".download-", dir=games))
    try:
        archive = staging / "repository.zip"
        if Path(url).is_file():
            shutil.copyfile(url, archive)
        else:
            try:
                with urllib.request.urlopen(url, timeout=60) as response, archive.open("wb") as out:
                    shutil.copyfileobj(response, out)
            except OSError as exc:
                raise GameRepositoryError(f"cannot download {url}: {exc}") from exc
        unpacked = staging / "unpacked"
        try:
            with zipfile.ZipFile(archive) as bundle:
                for name in bundle.namelist():
                    parts = PurePosixPath(name).parts
                    if name.startswith("/") or ".." in parts or (parts and ":" in parts[0]):
                        raise GameRepositoryError(f"the archive holds an unsafe path: {name}")
                bundle.extractall(unpacked)
        except zipfile.BadZipFile as exc:
            raise GameRepositoryError(f"{url} is not a zip archive: {exc}") from exc
        # GitHub archives hold one folder, <repository>-<branch>.
        entries = list(unpacked.iterdir())
        root = entries[0] if len(entries) == 1 and entries[0].is_dir() else unpacked
        if not (root / entry["descriptor"]).is_file():
            raise GameRepositoryError(f"the archive has no {entry['descriptor']} at its top")
        root.rename(target)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return descriptor


def open_rom(rom: Path, games: Path, *, known: list[Path] = (), source: str | None = None) -> dict[str, object]:
    """Everything "Open ROM" needs: identify the ROM, find its game's
    repository (one already opened, one downloaded before, or a fresh
    download into `games`) and prepare the revision the ROM is."""
    rom = rom.resolve()
    digest = _sha256(rom)
    matches = identify_rom(rom)
    if not matches:
        return {"status": "unknown", "rom": str(rom)}
    match = matches[0]
    for path in known:
        if not path.is_file() or not is_descriptor(path):
            continue
        try:
            descriptor = load_descriptor(path)
        except GameRepositoryError:
            continue
        revision = next((item for item in descriptor.revisions if item.sha256 == digest), None)
        if revision is not None:
            return {**prepare_revision(descriptor, revision.id, rom=rom), "descriptor": str(descriptor.path),
                    "name": match["name"], "downloaded": False}
    if match["game"] not in repository_catalog()["games"]:
        return {"status": "unsupported", "game": match["game"], "name": match["name"]}
    entry = repository_catalog()["games"][match["game"]]
    downloaded = not (games / entry["repository"] / entry["descriptor"]).is_file()
    descriptor = load_descriptor(fetch_repository(match["game"], games, source=source))
    revision = next((item for item in descriptor.revisions if item.sha256 == digest), None)
    if revision is None:
        return {"status": "unsupported", "game": match["game"], "name": match["name"],
                "descriptor": str(descriptor.path)}
    return {**prepare_revision(descriptor, revision.id, rom=rom), "descriptor": str(descriptor.path),
            "name": match["name"], "downloaded": downloaded}


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
