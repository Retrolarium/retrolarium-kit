"""The Studio project file (.rlj): its schema, loading, validation, migration,
builds and verification.

A project is a hand-editable TOML entrypoint: it names the generated
disassembly it builds on, the editor tabs the Studio shows, the documents each
tab edits and the operations the Studio may run. create.py makes and upgrades
projects, playtest.py and demo.py plan the MAME runs the Studio starts.
"""

from __future__ import annotations

from collections.abc import Mapping
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import tempfile
import tomllib
from typing import Any

from retrolarium_core.contracts import ContractError, load_disassembly_manifest
from retrolarium_kit.games.catalog import (
    build_game,
    build_game_documents,
    validate_game_document_for_revision,
)
from retrolarium_kit.games.flicky.content import (
    CANONICAL_SHA256 as FLICKY_SHA256,
    WORKSPACE_FILES as FLICKY_WORKSPACE_FILES,
    build_flicky_source_snapshot,
    validate_flicky_source_snapshot,
    FlickyContentError,
)
from retrolarium_core.fds import FdsError, parse_fds_side
from retrolarium_core.ines import InesError, load_ines, parse_ines_prefix
from retrolarium_core.megadrive import load_megadrive
from retrolarium_kit.core.project import build_project, sha256_file, verify_project, write_document
from retrolarium_kit.core.game_profiles import repository_profile
from retrolarium_core.snes import load_snes


class StudioProjectError(ValueError):
    """Raised when a ``.rlj`` project violates the public contract."""


SCHEMA = "retrolarium.project"
SCHEMA_VERSION = 1
INSPECTION_SCHEMA = "retrolarium.project-inspection"
PROJECT_SUFFIX = ".rlj"

# "rom": a project generated from a game repository's descriptor for one
# revision; its base is the verified copy of that revision's ROM.
SOURCE_MODES = frozenset({"binary-profile", "source-project", "flicky-content", "rom"})
LAYOUTS = frozenset({"auto", "single-row", "two-row"})
GROUPS = frozenset({"content", "assets", "tools"})
PLATFORMS = frozenset({"nes", "snes", "megadrive"})

EDITOR_KINDS = frozenset(
    {
        "animation.mapping",
        "audio.sequencer",
        "audio.synthesis",
        "castle-excellent.rooms",
        "castle-excellent.room-cells",
        "castle-excellent.metasprites",
        "castle-excellent.mml-music",
        "data.fixed-byte-records",
        "doraemon.levels",
        "doraemon.world2-metatiles",
        "doraemon.world2-screens",
        "doraemon.world2-routes",
        "flicky.levels", "flicky.graphics", "flicky.semantics",
        "flicky.animations", "flicky.sound",
        "code.analysis",
        "graphics.tile-set",
        "level.procedural",
        "level.tile-grid",
        "metatile.composer",
        "palette.set",
        "screen.tilemap",
        "solomon.rooms",
        "solomon.mirrors",
        "solomon.special",
        "solomon.room-art",
        "solomon.sprite-frames",
        "solomon.animations",
        "solomon.audio",
        "solomon.screens",
        "solomon.presentation",
        "solomon.text",
        "smb.demo",
        "castle-excellent.demo",
        "castle-excellent.screens",
        "pacman.demo",
        "doraemon.text",
        "doraemon.music",
        "doraemon.demo",
        "text.fixed",
        "world.routing",
    }
)

CODECS = frozenset(
    {
        "castle-excellent.object-stream@1",
        "doraemon.hierarchy.v1",
        "doraemon.world2-metatiles.v1",
        "doraemon.metasprites.v1",
        "doraemon.world2-screens.v1",
        "doraemon.world2-stage.v1",
        "castle-excellent.prefix-rooms@1",
        "fds.synthesis.v1",
        "fds.music.v1",
        "flicky.levels.v1", "flicky.graphics.v1", "flicky.semantics.v1",
        "flicky.animations.v1", "flicky.sound.v1",
        "nes.chr-2bpp.v1",
        "castle-excellent.room-cell-visuals.v1",
        "castle-excellent.metasprite-descriptors.v1",
        "castle-excellent.sub-command-text.v1",
        "castle-excellent.sfx-parameters.v1",
        "nes.fixed-text-fields.v1",
        "nes.metatile-2x2.v1",
        "nes.nametable.v1",
        "nes.palette-table.v1",
        "nes.ppu-palette-packets.v1",
        "nes.ppu-screen-packets.v1",
        "pacman.maze-rle.v1",
        "pacman.maze-rle@1",
        "pacman.actor-mapping.v1",
        "pacman.palette-table.v1",
        "pacman.screen-collection.v1",
        "pacman.sound-streams.v1",
        "pacman.sound@1",
        "castle-excellent.mml-music.v1",
        "retrolarium.megadrive.disassembly@1",
        "retrolarium.nes.disassembly@1",
        "retrolarium.snes.disassembly@1",
        "smb.apu@1",
        "smb.apu-envelope.v1",
        "smb.apu-music.v1",
        "smb.area@1",
        "smb.world.v1",
        "smb.levels.v1",
        "smb.player-frames.v1",
        "solomon.rooms@3",
        "solomon.mirrors@3",
        "solomon.special@3",
        "solomon.room-art@1",
        "solomon.sprite-frames@1",
        "solomon.animations@1",
        "solomon.audio@1",
        "solomon.screens@1",
        "solomon.presentation@1",
        "solomon.text@1",
        "smb.demo@1",
        "castle-excellent.demo@1",
        "castle-excellent.screens@1",
        "pacman.demo@1",
        "doraemon.text@1",
        "doraemon.music@1",
        "doraemon.demo@1",
    }
)

CAPABILITIES = frozenset(
    {
        "changed-markers",
        "build",
        "compose",
        "edit",
        "eyedropper",
        "midi-import",
        "navigate",
        "paint",
        "piano-roll",
        "pixel-paint",
        "playtest",
        "preview",
        "restore",
        "run",
        "semantic-overlays",
        "undo",
        "wav-preview",
    }
)

OPERATIONS = frozenset(
    {"project.build", "project.launch", "project.validate", "project.verify"}
)

_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_.-]*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_AUTHORING_SCHEMAS = frozenset(
    {"retrolarium.castle-excellent-levels", "retrolarium.nes-resource-bundle",
     "retrolarium.solomon-rooms", "retrolarium.solomon-room-art",
     "retrolarium.solomon-sprite-frames", "retrolarium.solomon-animations",
     "retrolarium.solomon-audio",
     "retrolarium.solomon-presentation", "retrolarium.solomon-text",
     "retrolarium.smb-demo", "retrolarium.castle-excellent-demo", "retrolarium.castle-excellent-screens",
     "retrolarium.pacman-demo", "retrolarium.doraemon-text", "retrolarium.doraemon-music",
     "retrolarium.doraemon-demo"}
)


def _error(context: str, message: str) -> StudioProjectError:
    return StudioProjectError(f"{context}: {message}")


def _mapping(value: object, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _error(context, "must be a table")
    return value


def _check_keys(
    value: Mapping[str, Any], context: str, allowed: set[str], required: set[str]
) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise _error(context, f"unknown field {unknown[0]!r}")
    missing = sorted(required - set(value))
    if missing:
        raise _error(context, f"missing field {missing[0]!r}")


def _string(value: object, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _error(context, "must be a non-empty string")
    return value


def _identifier(value: object, context: str) -> str:
    result = _string(value, context)
    if _IDENTIFIER.fullmatch(result) is None:
        raise _error(context, "must match [a-z][a-z0-9_.-]*")
    return result


def _choice(value: object, context: str, choices: frozenset[str]) -> str:
    result = _string(value, context)
    if result not in choices:
        raise _error(context, f"unsupported value {result!r}")
    return result


def _string_list(value: object, context: str) -> list[str]:
    if not isinstance(value, list):
        raise _error(context, "must be an array")
    result: list[str] = []
    for index, item in enumerate(value):
        result.append(_string(item, f"{context}[{index}]"))
    if len(set(result)) != len(result):
        raise _error(context, "must not contain duplicates")
    return result


def _project_path(
    root: Path, value: object, context: str, *, must_exist: bool = True,
    boundary: Path | None = None,
) -> tuple[str, Path]:
    """A portable path relative to `root`. With a `boundary` (a generated
    project's repository) it may climb out of `root` but not out of the
    boundary."""
    source = _string(value, context)
    if "\\" in source:
        raise _error(context, "must use portable forward slashes")
    posix = PurePosixPath(source)
    windows = PureWindowsPath(source)
    if posix.is_absolute() or windows.is_absolute() or windows.drive:
        raise _error(context, "absolute paths are forbidden")
    parts = list(posix.parts)
    if boundary is not None:
        while parts and parts[0] == "..":
            parts.pop(0)
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise _error(context, "must be a normalized relative path without '..'")
    resolved_root = root.resolve()
    resolved = resolved_root.joinpath(*posix.parts).resolve()
    limit = boundary.resolve() if boundary is not None else resolved_root
    if not resolved.is_relative_to(limit):
        raise _error(context, "path escapes the project directory")
    if must_exist and not resolved.is_file():
        raise _error(context, f"file does not exist: {source}")
    return posix.as_posix(), resolved


def _portable(path: Path, root: Path) -> str:
    """A path relative to `root`, climbing out with '..' for a generated
    project's documents in its repository."""
    return Path(os.path.relpath(path.resolve(), root.resolve())).as_posix()


def _read_toml(path: Path) -> Mapping[str, Any]:
    if path.suffix.lower() != PROJECT_SUFFIX:
        raise StudioProjectError(
            f"{path}: project file must use the {PROJECT_SUFFIX} extension"
        )
    try:
        payload = path.read_text(encoding="utf-8")
        return _mapping(tomllib.loads(payload), str(path))
    except tomllib.TOMLDecodeError as exc:
        line = getattr(exc, "lineno", None)
        column = getattr(exc, "colno", None)
        message = getattr(exc, "msg", None)
        rendered = str(exc)
        if line is None or column is None:
            location = re.search(r"\(at line (\d+), column (\d+)\)$", rendered)
            if location is not None:
                line, column = map(int, location.groups())
                rendered = rendered[: location.start()].rstrip()
            else:
                lines = payload.splitlines() or [""]
                line = len(lines)
                column = len(lines[-1]) + 1
                rendered = rendered.removesuffix(" (at end of document)")
        raise StudioProjectError(
            f"{path}:{line}:{column}: {message or rendered}"
        ) from exc
    except (OSError, UnicodeError) as exc:
        raise StudioProjectError(f"cannot read Studio project {path}: {exc}") from exc


def _replace_schema_version(payload: str, version: int) -> str:
    pattern = re.compile(
        r"^(?P<prefix>[ \t]*schema_version[ \t]*=[ \t]*)\d+"
        r"(?P<suffix>[ \t]*(?:#.*)?(?:\r?\n|$))",
        re.MULTILINE,
    )
    replacement = rf"\g<prefix>{version}\g<suffix>"
    migrated, count = pattern.subn(replacement, payload, count=1)
    if count == 1:
        return migrated

    schema = re.compile(
        r'^(?P<line>[ \t]*schema[ \t]*=[ \t]*["\']retrolarium\.project["\']'
        r"[ \t]*(?:#.*)?)(?P<newline>\r?\n)",
        re.MULTILINE,
    )

    def insert(match: re.Match[str]) -> str:
        newline = match.group("newline")
        return (
            match.group("line") + newline
            + f"schema_version = {version}" + newline
        )

    migrated, count = schema.subn(insert, payload, count=1)
    if count != 1:
        raise StudioProjectError("project: cannot locate the schema declaration")
    return migrated


def migrate_studio_project(
    path: Path,
    *,
    output: Path | None = None,
    target_version: int = SCHEMA_VERSION,
) -> dict[str, Any]:
    """Upgrade a project in place while preserving unrelated TOML text."""

    source_path = path.resolve()
    destination = (output or source_path).resolve()
    if source_path.suffix.lower() != PROJECT_SUFFIX \
            or destination.suffix.lower() != PROJECT_SUFFIX:
        raise StudioProjectError(
            f"Studio migrations require the {PROJECT_SUFFIX} extension"
        )
    if destination.parent != source_path.parent:
        raise StudioProjectError(
            "migration output must stay beside the source so relative paths remain valid"
        )
    if target_version != SCHEMA_VERSION:
        raise StudioProjectError(
            f"unsupported Studio migration target version {target_version}"
        )
    try:
        payload = source_path.read_bytes().decode("utf-8")
        parsed = _mapping(tomllib.loads(payload), str(source_path))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise StudioProjectError(
            f"cannot read Studio project for migration {source_path}: {exc}"
        ) from exc
    if parsed.get("schema") != SCHEMA:
        raise _error("project.schema", f"expected {SCHEMA!r}")
    raw_version = parsed.get("schema_version", 0)
    if not isinstance(raw_version, int) or isinstance(raw_version, bool):
        raise _error("project.schema_version", "must be an integer")
    if raw_version < 0 or raw_version > target_version:
        raise _error(
            "project.schema_version",
            f"cannot migrate version {raw_version} to {target_version}",
        )

    migrated = payload
    version = raw_version
    while version < target_version:
        if version == 0:
            migrated = _replace_schema_version(migrated, 1)
            version = 1
        else:
            raise StudioProjectError(
                f"no Studio migration is registered for version {version}"
            )
    if destination != source_path or migrated != payload:
        _atomic_write(destination, migrated)
    inspection = load_studio_project(destination)
    return {
        "schema": "retrolarium.project-migration",
        "schema_version": 1,
        "status": "migrated" if migrated != payload else "current",
        "from_version": raw_version,
        "to_version": version,
        "project_file": str(destination),
        "inspection": inspection,
    }


def _validate_dependency_graph(tabs: list[dict[str, Any]]) -> None:
    by_id = {tab["id"]: tab for tab in tabs}
    for tab in tabs:
        for dependency in tab["depends_on"]:
            if dependency not in by_id:
                raise _error(
                    f"workspace.tabs.{tab['id']}.depends_on",
                    f"unknown tab {dependency!r}",
                )
            if tab["enabled"] and not by_id[dependency]["enabled"]:
                raise _error(
                    f"workspace.tabs.{tab['id']}.depends_on",
                    f"enabled tab depends on disabled tab {dependency!r}",
                )

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(tab_id: str) -> None:
        if tab_id in visiting:
            raise _error("workspace.tabs", f"dependency cycle contains {tab_id!r}")
        if tab_id in visited:
            return
        visiting.add(tab_id)
        for dependency in by_id[tab_id]["depends_on"]:
            visit(dependency)
        visiting.remove(tab_id)
        visited.add(tab_id)

    for tab_id in by_id:
        visit(tab_id)


def rom_as_is(path: Path) -> tuple[Path, Path] | None:
    """The original and the edited ROM of a project the Studio made by opening
    a ROM as it is (profile ``rom.v1``: no documents, no build), or None for
    any other project. Both live in the project's folder."""

    project_file = path.resolve()
    root = project_file.parent
    source = _read_toml(project_file)
    project = source.get("project")
    if not isinstance(project, Mapping) or project.get("profile") != "rom.v1":
        return None
    table = source.get("source")
    if not isinstance(table, Mapping):
        raise _error("source", "must be a table")
    files = []
    for key in ("original", "rom"):
        relative = PurePosixPath(_string(table.get(key), f"source.{key}"))
        resolved = root.joinpath(*relative.parts).resolve()
        if relative.is_absolute() or ":" in str(relative) or not resolved.is_relative_to(root):
            raise _error(f"source.{key}", "must name a file in the project's folder")
        files.append(resolved)
    return files[0], files[1]


def load_studio_project(path: Path) -> dict[str, Any]:
    """Validate and return the stable normalized projection consumed by the GUI."""

    project_file = path.resolve()
    root = project_file.parent
    source = _read_toml(project_file)
    _check_keys(
        source,
        "project",
        {"schema", "schema_version", "project", "source", "workspace", "operations"},
        {"schema", "schema_version", "project", "source", "workspace"},
    )
    if source["schema"] != SCHEMA or source["schema_version"] != SCHEMA_VERSION:
        raise _error("project", f"expected {SCHEMA!r} version {SCHEMA_VERSION}")

    metadata = _mapping(source["project"], "project")
    _check_keys(
        metadata,
        "project",
        {"id", "name", "platform", "profile", "source_mode"},
        {"id", "name", "platform", "profile", "source_mode"},
    )
    normalized_metadata = {
        "id": _identifier(metadata["id"], "project.id"),
        "name": _string(metadata["name"], "project.name"),
        "platform": _choice(metadata["platform"], "project.platform", PLATFORMS),
        "profile": _identifier(metadata["profile"], "project.profile"),
        "source_mode": _choice(
            metadata["source_mode"], "project.source_mode", SOURCE_MODES
        ),
    }

    source_table = _mapping(source["source"], "source")
    flicky_content = normalized_metadata["source_mode"] == "flicky-content"
    rom_mode = normalized_metadata["source_mode"] == "rom"
    boundary: Path | None = None
    if rom_mode:
        source_key = "rom"
        _check_keys(source_table, "source", {"rom", "sha256", "repository"},
                    {"rom", "sha256", "repository"})
        repository = PurePosixPath(_string(source_table["repository"], "source.repository"))
        if not repository.parts or any(part != ".." for part in repository.parts):
            raise _error("source.repository", "must name the repository root as '../..'")
        boundary = root.joinpath(*repository.parts).resolve()
        # The game's profile, which the commands on this project use.
        repository_profile(boundary)
    else:
        source_key = "manifest" if flicky_content else "disassembly"
        _check_keys(source_table, "source", {source_key, "sha256"},
                    {source_key, "sha256"})
    source_relative, source_path = _project_path(
        root, source_table[source_key], f"source.{source_key}", boundary=boundary
    )
    source_sha256 = _string(source_table["sha256"], "source.sha256")
    if _SHA256.fullmatch(source_sha256) is None:
        raise _error("source.sha256", "must be 64 lowercase hexadecimal characters")
    if rom_mode:
        if sha256_file(source_path) != source_sha256:
            raise _error("source.rom", "the ROM differs from this revision")
        manifest_schema = "retrolarium.rom"
    elif flicky_content:
        if normalized_metadata["platform"] != "megadrive" \
                or normalized_metadata["profile"] != "flicky-ue.v1":
            raise _error("project", "Flicky content requires megadrive/flicky-ue.v1")
        if source_relative != "flicky-source/retrolarium-flicky-snapshot.json":
            raise _error("source.manifest", "must name the Flicky source snapshot record")
        if source_sha256 != FLICKY_SHA256:
            raise _error("source.sha256", "does not match canonical Flicky (UE)")
        try:
            manifest = json.loads(source_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise _error("source.manifest", f"cannot read the snapshot record: {exc}") from exc
        if (not isinstance(manifest, Mapping)
                or manifest.get("schema") != "retrolarium.flicky-source-snapshot"
                or manifest.get("schema_version") != 1 or manifest.get("canonical_sha256") != FLICKY_SHA256):
            raise _error("source.manifest", "is not a canonical Flicky source snapshot record")
        manifest_schema = "retrolarium.flicky-source-snapshot"
    else:
        try:
            manifest = load_disassembly_manifest(source_path)
        except ContractError as exc:
            raise _error("source.disassembly", str(exc)) from exc
        manifest_schema = _string(manifest.get("schema"), "source manifest schema")
        expected_schema = f"retrolarium.{normalized_metadata['platform']}.disassembly"
        if manifest_schema != expected_schema:
            raise _error(
                "source.disassembly",
                f"platform requires {expected_schema!r}, got {manifest_schema!r}",
            )
        manifest_input = _mapping(manifest.get("input"), "source manifest input")
        if manifest_input.get("sha256") != source_sha256:
            raise _error("source.sha256", "does not match the disassembly manifest input")

    workspace = _mapping(source["workspace"], "workspace")
    _check_keys(
        workspace,
        "workspace",
        {"layout", "startup_tab", "tabs"},
        {"layout", "tabs"},
    )
    layout = _choice(workspace["layout"], "workspace.layout", LAYOUTS)
    raw_tabs = workspace["tabs"]
    if not isinstance(raw_tabs, list) or not raw_tabs:
        raise _error("workspace.tabs", "must contain at least one tab")

    tabs: list[dict[str, Any]] = []
    tab_ids: set[str] = set()
    for index, raw_tab in enumerate(raw_tabs):
        context = f"workspace.tabs[{index}]"
        tab = _mapping(raw_tab, context)
        _check_keys(
            tab,
            context,
            {
                "id", "title", "kind", "group", "document", "codec",
                "capabilities", "depends_on", "enabled",
            },
            {"id", "title", "kind", "group", "codec"},
        )
        tab_id = _identifier(tab["id"], f"{context}.id")
        if tab_id in tab_ids:
            raise _error(f"{context}.id", f"duplicate tab id {tab_id!r}")
        tab_ids.add(tab_id)
        kind = _choice(tab["kind"], f"{context}.kind", EDITOR_KINDS)
        codec = _choice(tab["codec"], f"{context}.codec", CODECS)
        enabled = tab.get("enabled", True)
        if not isinstance(enabled, bool):
            raise _error(f"{context}.enabled", "must be a boolean")
        capabilities = _string_list(
            tab.get("capabilities", []), f"{context}.capabilities"
        )
        unknown_capabilities = sorted(set(capabilities) - CAPABILITIES)
        if unknown_capabilities:
            raise _error(
                f"{context}.capabilities",
                f"unsupported value {unknown_capabilities[0]!r}",
            )
        depends_on = _string_list(tab.get("depends_on", []), f"{context}.depends_on")
        document: str | None = None
        document_path: str | None = None
        if "document" in tab:
            document, resolved_document = _project_path(
                root, tab["document"], f"{context}.document", boundary=boundary
            )
            document_path = str(resolved_document)
        elif kind != "code.analysis":
            raise _error(f"{context}.document", "is required for this editor kind")
        tabs.append(
            {
                "id": tab_id,
                "title": _string(tab["title"], f"{context}.title"),
                "kind": kind,
                "group": _choice(tab["group"], f"{context}.group", GROUPS),
                "document": document,
                "document_path": document_path,
                "codec": codec,
                "capabilities": capabilities,
                "depends_on": depends_on,
                "enabled": enabled,
            }
        )

    _validate_dependency_graph(tabs)
    if flicky_content:
        expected_tabs = {
            "flicky.levels": "level_layouts",
            "flicky.graphics": "graphics_assets",
            "flicky.semantics": "graphics_semantics",
            "flicky.animations": "graphics_sequences",
            "flicky.sound": "z80_sound_banks",
        }
        kinds = {tab["kind"] for tab in tabs}
        if len(tabs) not in {len(expected_tabs), len(expected_tabs) + 1} \
                or kinds != set(expected_tabs):
            raise _error("workspace.tabs", "Flicky requires five content documents and optional Text view")
        if len(tabs) == len(expected_tabs) + 1 and sum(
            tab["kind"] == "flicky.semantics" for tab in tabs
        ) != 2:
            raise _error("workspace.tabs", "Flicky permits only one extra Text view")
        if len(tabs) == len(expected_tabs) + 1 and not any(
            tab["id"] == "text" and tab["kind"] == "flicky.semantics"
            for tab in tabs
        ):
            raise _error("workspace.tabs", "Flicky Text view must use the text tab id")
        for tab in tabs:
            expected_document = "flicky-source/" + FLICKY_WORKSPACE_FILES[
                expected_tabs[tab["kind"]]
            ]
            if tab["document"] != expected_document \
                    or tab["codec"] != tab["kind"] + ".v1" or not tab["enabled"]:
                raise _error("workspace.tabs", "Flicky tab must use its enabled source workspace document and codec")
    enabled_ids = [tab["id"] for tab in tabs if tab["enabled"]]
    if not enabled_ids:
        raise _error("workspace.tabs", "must contain at least one enabled tab")
    startup_tab = workspace.get("startup_tab", enabled_ids[0])
    startup_tab = _identifier(startup_tab, "workspace.startup_tab")
    if startup_tab not in enabled_ids:
        raise _error("workspace.startup_tab", "must name an enabled tab")

    operations_table = _mapping(source.get("operations", {}), "operations")
    _check_keys(
        operations_table,
        "operations",
        {"validate", "build", "verify", "launch"},
        set(),
    )
    operations: dict[str, str] = {}
    for name, value in operations_table.items():
        operation = _choice(value, f"operations.{name}", OPERATIONS)
        expected = f"project.{name}"
        if operation != expected:
            raise _error(
                f"operations.{name}", f"must use registered operation {expected!r}"
            )
        operations[name] = operation

    return {
        "schema": INSPECTION_SCHEMA,
        "schema_version": 1,
        "project_file": str(project_file),
        "project_root": str(root),
        "project": normalized_metadata,
        "source": {
            source_key: source_relative,
            source_key + "_path": str(source_path),
            "sha256": source_sha256,
            "schema": manifest_schema,
        },
        "workspace": {
            "layout": layout,
            "startup_tab": startup_tab,
            "tabs": tabs,
        },
        "operations": operations,
    }


def validate_studio_project(path: Path) -> dict[str, Any]:
    inspection = load_studio_project(path)
    _require_declared_operation(inspection, "validate")
    documents = _validate_editor_documents(inspection)
    return {
        "schema": "retrolarium.project-validation",
        "schema_version": 1,
        "status": "valid",
        "project_file": inspection["project_file"],
        "project_id": inspection["project"]["id"],
        "enabled_tabs": [
            tab["id"] for tab in inspection["workspace"]["tabs"] if tab["enabled"]
        ],
        "documents": documents,
    }


def _require_declared_operation(
    inspection: Mapping[str, Any], name: str
) -> None:
    operations = _mapping(inspection.get("operations", {}), "inspection.operations")
    if operations.get(name) != f"project.{name}":
        raise StudioProjectError(
            f"Studio project does not declare the {name!r} operation"
        )


def _validate_editor_documents(
    inspection: Mapping[str, Any]
) -> list[dict[str, object]]:
    if inspection["project"]["source_mode"] == "flicky-content":
        try:
            hashes = validate_flicky_source_snapshot(Path(inspection["project_root"]))
        except FlickyContentError as exc:
            raise StudioProjectError(str(exc)) from exc
        root = Path(inspection["project_root"])
        by_path = {"flicky-source/" + relative: digest
                   for key, relative in FLICKY_WORKSPACE_FILES.items()
                   for digest in [hashes[key]]}
        return [{"status": "valid", "document": str(document),
                 "sha256": by_path[_portable(document, root)]}
                for document in _unique_editor_documents(inspection)]
    documents = []
    for document_path in _unique_editor_documents(inspection):
        schema = _document_schema(document_path)
        if schema in _AUTHORING_SCHEMAS:
            documents.append(
                validate_game_document_for_revision(
                    document_path,
                    profile_reference=inspection["project"]["profile"],
                    source_sha256=inspection["source"]["sha256"],
                )
            )
        else:
            documents.append(
                {
                    "status": "contract-only",
                    "document": str(document_path),
                    "schema": schema,
                }
            )
    return documents


def _unique_editor_documents(inspection: Mapping[str, Any]) -> list[Path]:
    result: list[Path] = []
    seen: set[Path] = set()
    workspace = _mapping(inspection["workspace"], "inspection.workspace")
    for item in workspace["tabs"]:
        tab = _mapping(item, "inspection.workspace.tabs")
        value = tab.get("document_path")
        if not tab.get("enabled") or value is None:
            continue
        resolved = Path(_string(value, "inspection tab document_path")).resolve()
        if resolved not in seen:
            seen.add(resolved)
            result.append(resolved)
    return result


def _document_schema(path: Path) -> str:
    if path.suffix.lower() != ".json":
        return "unregistered"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StudioProjectError(f"cannot read Studio document {path}: {exc}") from exc
    if not isinstance(value, Mapping):
        raise StudioProjectError(f"Studio document {path} must be an object")
    schema = value.get("schema")
    return schema if isinstance(schema, str) and schema else "unregistered"


def _studio_output(root: Path, project_id: str, platform: str, extension: str | None = None) -> Path:
    extension = extension or {"nes": ".nes", "megadrive": ".bin", "snes": ".sfc"}[platform]
    return root / "build" / f"{project_id}-edited{extension}"


def _validate_container(path: Path, platform: str, *, rom_mode: bool = False) -> None:
    if platform == "nes" and rom_mode:
        # A revision's own image may be a Disk System side or an iNES image
        # with more after it (PlayChoice-10's instruction ROM).
        data = path.read_bytes()
        try:
            if path.suffix.lower() == ".fds":
                parse_fds_side(data)
            else:
                parse_ines_prefix(data, path)
        except (FdsError, InesError) as exc:
            raise StudioProjectError(f"{path.name}: {exc}") from exc
    elif platform == "nes":
        load_ines(path)
    elif platform == "megadrive":
        load_megadrive(path)
    elif platform == "snes":
        load_snes(path)
    else:
        raise StudioProjectError(f"unsupported Studio platform: {platform}")


def build_studio_project(
    path: Path,
    *,
    container: str | None = None,
    toolchain_cache: Path | None = None,
    flicky_level_writer: Path | None = None,
) -> dict[str, Any]:
    """Build the exact source project, then transactionally apply typed resources."""

    inspection = load_studio_project(path)
    _require_declared_operation(inspection, "build")
    _validate_editor_documents(inspection)
    if inspection["project"]["source_mode"] == "flicky-content":
        return _build_flicky_studio_project(inspection, flicky_level_writer)
    if flicky_level_writer is not None:
        raise StudioProjectError("--flicky-level-writer requires a Flicky project")
    if inspection["project"]["source_mode"] == "rom":
        # The revision's own ROM: what its repository assembles, byte for byte.
        base_artifact = Path(inspection["source"]["rom_path"])
        base_build = {"artifact": {"path": inspection["source"]["rom"],
                                   "size": base_artifact.stat().st_size,
                                   "sha256": inspection["source"]["sha256"]}}
    else:
        manifest_path = Path(inspection["source"]["disassembly_path"])
        base_build = build_project(
            manifest_path, container=container, toolchain_cache=toolchain_cache
        )
        base_verification = verify_project(manifest_path)
        if base_verification["status"] != "exact":
            raise StudioProjectError(
                "Studio source project does not rebuild the immutable source exactly"
            )
        base_artifact = (
            manifest_path.parent / base_build["artifact"]["path"]
        ).resolve()
    root = Path(inspection["project_root"])
    rom_mode = inspection["project"]["source_mode"] == "rom"
    output = _studio_output(
        root, inspection["project"]["id"], inspection["project"]["platform"],
        base_artifact.suffix.lower() if rom_mode else None,
    ).resolve()
    if not output.is_relative_to(root.resolve()):
        raise StudioProjectError("Studio build output escapes the project directory")

    editor_documents = _unique_editor_documents(inspection)
    authoring_documents = [
        document for document in editor_documents
        if _document_schema(document) in _AUTHORING_SCHEMAS
    ]
    unhandled = [
        document for document in editor_documents if document not in authoring_documents
    ]
    if unhandled:
        raise StudioProjectError(
            "Studio build has no registered writer for "
            + ", ".join(str(document) for document in unhandled)
        )
    authoring: dict[str, object] | None = None
    if len(authoring_documents) == 1:
        authoring = build_game(base_artifact, authoring_documents[0], output)
    elif authoring_documents:
        authoring = build_game_documents(base_artifact, authoring_documents, output)
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                # A built image: the next build makes it again, so no fsync.
                stream.write(base_artifact.read_bytes())
            os.replace(temporary, output)
        finally:
            temporary.unlink(missing_ok=True)
    expansion = _expand_solomon_build(base_artifact, output, authoring_documents)
    _validate_container(output, inspection["project"]["platform"], rom_mode=rom_mode)

    report = {
        "schema": "retrolarium.studio-build-result",
        "schema_version": 1,
        "status": "built",
        "project": {
            "path": Path(inspection["project_file"]).name,
            "sha256": sha256_file(Path(inspection["project_file"])),
        },
        "source_sha256": inspection["source"]["sha256"],
        "base": base_build["artifact"],
        "documents": [
            {
                "path": _portable(document, root),
                "sha256": sha256_file(document),
            }
            for document in editor_documents
        ],
        "artifact": {
            "path": output.relative_to(root).as_posix(),
            "size": output.stat().st_size,
            "sha256": sha256_file(output),
        },
        "authoring": authoring,
    }
    if expansion is not None:
        report["expansion"] = expansion
    write_document(root / "build" / "studio-build-result.json", report)
    return report


def _expand_solomon_build(
    base_artifact: Path, output: Path, documents: list[Path],
) -> dict[str, Any] | None:
    """Rewrite a Solomon's Key build in the expanded mapper-66 layout when its
    rooms document asks for it (solomon_expansion)."""
    from retrolarium_kit.games.catalog import _read_image
    from retrolarium_kit.games.solomon.rooms import expand_room_build, parse_room_document

    for document_path in documents:
        try:
            value = json.loads(document_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise StudioProjectError(f"cannot read {document_path}: {exc}") from exc
        if not isinstance(value, Mapping) or value.get("schema") != "retrolarium.solomon-rooms" \
                or value.get("expanded_rom") is not True:
            continue
        _payload, profile, revision = _read_image(base_artifact, None)
        document = parse_room_document(value, profile, revision)
        expanded, report = expand_room_build(output.read_bytes(), document, revision)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                # A built image: the next build makes it again, so no fsync.
                stream.write(expanded)
            os.replace(temporary, output)
        finally:
            temporary.unlink(missing_ok=True)
        return report
    return None


def _build_flicky_studio_project(
    inspection: Mapping[str, Any], native_writer: Path | None = None,
) -> dict[str, Any]:
    root = Path(inspection["project_root"])
    try:
        built = build_flicky_source_snapshot(root, native_writer=native_writer)
    except FlickyContentError as exc:
        raise StudioProjectError(str(exc)) from exc
    output = Path(built["artifact"])
    editor_documents = _unique_editor_documents(inspection)
    report = {
        "schema": "retrolarium.studio-build-result",
        "schema_version": 1,
        "status": "built",
        "project": {"path": Path(inspection["project_file"]).name,
                    "sha256": sha256_file(Path(inspection["project_file"]))},
        "source_sha256": FLICKY_SHA256,
        "base": {"mode": "flicky-content", "sha256": FLICKY_SHA256},
        "documents": [
            {"path": _portable(document, root),
             "sha256": sha256_file(document)}
            for document in editor_documents
        ],
        "artifact": {"path": output.relative_to(root).as_posix(),
                     "size": output.stat().st_size,
                     "sha256": sha256_file(output)},
        "authoring": {"mode": "flicky-content", "documents": built["documents"]},
    }
    write_document(root / "build" / "studio-build-result.json", report)
    return report


def verify_studio_project(path: Path) -> dict[str, Any]:
    """Prove that the edited artifact matches the current project and documents."""

    inspection = load_studio_project(path)
    _require_declared_operation(inspection, "verify")
    root = Path(inspection["project_root"])
    report_path = root / "build" / "studio-build-result.json"
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StudioProjectError(f"cannot read Studio build result: {exc}") from exc
    if not isinstance(report, Mapping) or report.get("schema") != "retrolarium.studio-build-result" \
            or report.get("schema_version") != 1:
        raise StudioProjectError("Studio build result has an unsupported schema")

    differences: list[str] = []
    project_record = _mapping(report.get("project"), "build result project")
    if project_record.get("sha256") != sha256_file(Path(inspection["project_file"])):
        differences.append("project")
    if report.get("source_sha256") != inspection["source"]["sha256"]:
        differences.append("source")

    expected_documents = {
        _portable(document, root): sha256_file(document)
        for document in _unique_editor_documents(inspection)
    }
    records = report.get("documents")
    built_documents: dict[str, str] = {}
    if isinstance(records, list):
        for record in records:
            if isinstance(record, Mapping) and isinstance(record.get("path"), str) \
                    and isinstance(record.get("sha256"), str):
                built_documents[record["path"]] = record["sha256"]
    if built_documents != expected_documents:
        differences.append("documents")

    artifact_record = _mapping(report.get("artifact"), "build result artifact")
    artifact_relative, artifact = _project_path(
        root, artifact_record.get("path"), "build result artifact.path"
    )
    if artifact_record.get("size") != artifact.stat().st_size \
            or artifact_record.get("sha256") != sha256_file(artifact):
        differences.append("artifact")
    if inspection["project"]["source_mode"] == "flicky-content":
        if artifact.stat().st_size != 131072:
            differences.append("artifact-size")
        elif load_megadrive(artifact).checksum_status != "valid":
            differences.append("artifact-checksum")
        try:
            validate_flicky_source_snapshot(root)
        except FlickyContentError:
            differences.append("source-snapshot")
    else:
        if inspection["project"]["source_mode"] == "rom":
            expected_size = Path(inspection["source"]["rom_path"]).stat().st_size
        else:
            manifest = load_disassembly_manifest(
                Path(inspection["source"]["disassembly_path"]), verify_rebuilt=False
            )
            expected_size = manifest["input"]["size"]
        # An expanded Solomon build adds a second 32 KiB PRG bank.
        if isinstance(report.get("expansion"), Mapping):
            expected_size += 0x8000
        if artifact.stat().st_size != expected_size:
            differences.append("artifact-size")
    _validate_container(artifact, inspection["project"]["platform"],
                        rom_mode=inspection["project"]["source_mode"] == "rom")

    result = {
        "schema": "retrolarium.studio-verification-result",
        "schema_version": 1,
        "status": "current" if not differences else "mismatch",
        "project_file": inspection["project_file"],
        "artifact": {
            "path": artifact_relative,
            "absolute_path": str(artifact),
            "size": artifact.stat().st_size,
            "sha256": sha256_file(artifact),
        },
        "differences": differences,
    }
    write_document(root / "build" / "studio-verification-result.json", result)
    return result


SOLOMON_REVISIONS = frozenset({"usa", "europe", "japan"})


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _atomic_write(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
