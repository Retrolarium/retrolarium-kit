"""Human-editable Retrolarium Studio project contract."""

from __future__ import annotations

from collections.abc import Mapping
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import runpy
import subprocess
import sys
import tempfile
import tomllib
from typing import Any, Sequence

from retrolarium_core.contracts import ContractError, load_disassembly_manifest
from retrolarium_kit.games.catalog import (
    build_game,
    build_game_documents,
    validate_game_document_for_revision,
)
from retrolarium_kit.core.game_profiles import packaged_game_profiles
from retrolarium_kit.games.flicky.content import (
    CANONICAL_SHA256 as FLICKY_SHA256,
    ROM_NAME as FLICKY_ROM_NAME,
    SNAPSHOT_RECORD as FLICKY_SNAPSHOT_RECORD,
    SOURCE_DIRECTORY as FLICKY_SOURCE_DIRECTORY,
    WORKSPACE_FILES as FLICKY_WORKSPACE_FILES,
    build_flicky_source_snapshot,
    validate_flicky_source_snapshot,
    FlickyContentError,
)
from retrolarium_kit.games.flicky.bonus_placement import (
    export_bonus_placements, validate_bonus_placements,
)
from retrolarium_kit.games.flicky.title_layout import (
    export_title_layout, validate_title_layout,
)
from retrolarium_kit.games.flicky.demo import export_attract_demos, validate_attract_demos
from retrolarium_core.fds import FdsError, parse_fds_side
from retrolarium_core.ines import InesError, load_ines, parse_ines_prefix
from retrolarium_core.megadrive import load_megadrive
from retrolarium_kit.core.project import (
    build_project,
    launch_image_plan,
    launch_image_smoke,
    sha256_file,
    verify_project,
    write_document,
)
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
        "castle.rooms",
        "castle.room-cells",
        "castle.metasprites",
        "castle.mml-music",
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
        "castle.demo",
        "castle.screens",
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
        "castle.object-stream@1",
        "doraemon.hierarchy.v1",
        "doraemon.world2-metatiles.v1",
        "doraemon.metasprites.v1",
        "doraemon.world2-screens.v1",
        "doraemon.world2-stage.v1",
        "castle.prefix-rooms@1",
        "fds.synthesis.v1",
        "fds.music.v1",
        "flicky.levels.v1", "flicky.graphics.v1", "flicky.semantics.v1",
        "flicky.animations.v1", "flicky.sound.v1",
        "nes.chr-2bpp.v1",
        "castle.room-cell-visuals.v1",
        "castle.metasprite-descriptors.v1",
        "castle.sub-command-text.v1",
        "castle.sfx-parameters.v1",
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
        "castle.mml-music.v1",
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
        "castle.demo@1",
        "castle.screens@1",
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
    {"retrolarium.castle-levels", "retrolarium.nes-resource-bundle",
     "retrolarium.solomon-rooms", "retrolarium.solomon-room-art",
     "retrolarium.solomon-sprite-frames", "retrolarium.solomon-animations",
     "retrolarium.solomon-audio",
     "retrolarium.solomon-presentation", "retrolarium.solomon-text",
     "retrolarium.smb-demo", "retrolarium.castle-demo", "retrolarium.castle-screens",
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
        if source_relative != "flicky-source/config/authoring/content_studios.json":
            raise _error("source.manifest", "must name the imported Flicky content manifest")
        if source_sha256 != FLICKY_SHA256:
            raise _error("source.sha256", "does not match canonical Flicky (UE)")
        try:
            manifest = json.loads(source_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise _error("source.manifest", f"cannot read content manifest: {exc}") from exc
        if not isinstance(manifest, Mapping) or manifest.get("schema_version") != 1 \
                or manifest.get("profile") != "canonical" \
                or manifest.get("image_size") != 131072 \
                or manifest.get("image_sha1") != "83d8bbf0a9b38c42a0bf492d105cc3abe9644a96":
            raise _error("source.manifest", "is not the canonical Flicky content manifest")
        manifest_schema = "flicky.content-studios"
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
                stream.write(base_artifact.read_bytes())
                stream.flush()
                os.fsync(stream.fileno())
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
        if not isinstance(value, Mapping) or value.get("schema") != "retrolarium.solomon-rooms"                 or value.get("expanded_rom") is not True:
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
                stream.write(expanded)
                stream.flush()
                os.fsync(stream.fileno())
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
# Revisions made for 50 Hz consoles run on MAME's PAL machine.
PAL_REVISIONS = frozenset({("smb1", "pal"), ("solomons-key", "europe")})
# SMB cartridge revisions whose program runs the point playtest script:
# Japan/USA, its PlayChoice-10 copy (the same program) and Europe (the same
# RAM and hooks).
SMB_SCRIPTED_REVISIONS = frozenset({"ju", "pc10", "pal"})
# The point playtest also runs the Disk System versions. Super Mario Bros. 2
# and All Night Nippon are on the later Disk System engine: the timer digits
# sit at $07EC, the running game is OperMode_Task 4 (5 in All Night Nippon,
# which adds a task), and worlds 5-8 (file $20) and the hard / extended
# worlds (file $40) load from the disk while the game runs; the playtest puts
# that file in RAM the way the game's loader does and records it in the file
# list number ($07F7).
SMB_POINT_PLAYTEST_REVISIONS = SMB_SCRIPTED_REVISIONS | {"fds_smb", "smb2_jp_fds", "ann_fds"}
SMB_LATE_FDS_RUNNING_TASKS = {"smb2_jp_fds": 4, "ann_fds": 5}
SMB_LATE_FDS_HARD_WORLDS = frozenset({"world-hard", "world-extended"})


def _fds_program_at(image_path: Path, address: int) -> tuple[bytes, int]:
    """The program file the console loads at boot over ``address`` (the first
    on the disk; overlays loaded later come after it), and the address's
    offset in it."""
    side = image_path.read_bytes()[-65500:]
    try:
        records = parse_fds_side(side)
    except FdsError as exc:
        raise StudioProjectError(f"{image_path.name}: {exc}") from exc
    for record in records:
        if record.file_type == 0 and record.load_address <= address < record.load_address + record.size:
            return side[record.data_offset:record.data_offset + record.size], address - record.load_address
    raise StudioProjectError(f"{image_path.name} has no program file at ${address:04X}")


def _fds_file_blob(image_path: Path, file_id: int) -> bytes:
    """A program file of a built disk image: its load address, then its data."""
    image = image_path.read_bytes()
    try:
        records = parse_fds_side(image[-65500:])
    except FdsError as exc:
        raise StudioProjectError(f"{image_path.name}: {exc}") from exc
    side = image[-65500:]
    for record in records:
        if record.file_id == file_id and record.file_type == 0:
            return record.load_address.to_bytes(2, "little") \
                + side[record.data_offset:record.data_offset + record.size]
    raise StudioProjectError(f"{image_path.name} has no program file ${file_id:02X}")


def _source_revision(inspection: Mapping[str, Any]):
    """The profile and revision a project's source is, if the kit knows it."""
    registry = packaged_game_profiles()
    for identifier in registry.identifiers():
        profile = registry.get(identifier)
        if f"{profile.game_id}.v{profile.profile_version}" != inspection["project"]["profile"]:
            continue
        for revision in profile.revisions:
            if revision.sha256 == inspection["source"].get("sha256"):
                return profile, revision
    return None, None


def _revision_machine(inspection: Mapping[str, Any]) -> str | None:
    profile, revision = _source_revision(inspection)
    if profile is not None and (profile.game_id, revision.revision_id) in PAL_REVISIONS:
        return "nespal"
    return None


def studio_launch_plan(
    path: Path,
    *,
    mame: Path,
    machine: str | None = None,
    attach_window: str | None = None,
) -> dict[str, Any]:
    inspection = load_studio_project(path)
    _require_declared_operation(inspection, "launch")
    verification = verify_studio_project(path)
    if verification["status"] != "current":
        raise StudioProjectError("Studio artifact is stale; build it again before launch")
    plan = launch_image_plan(
        Path(verification["artifact"]["absolute_path"]),
        platform=inspection["project"]["platform"],
        mame=mame,
        machine=machine or _revision_machine(inspection),
        attach_window=attach_window,
    )
    plan["operation"] = "retrolarium.studio.launch"
    plan["project_file"] = inspection["project_file"]
    return plan


def studio_flicky_round_playtest_plan(
    path: Path, *, mame: Path, round: int,
) -> dict[str, Any]:
    """Run one authored Flicky round through the game's regular mode loader."""

    inspection = load_studio_project(path)
    _require_declared_operation(inspection, "launch")
    project = inspection["project"]
    if project["source_mode"] != "flicky-content" \
            or project["platform"] != "megadrive" \
            or project["profile"] != "flicky-ue.v1":
        raise StudioProjectError(
            "Flicky round playtest requires the canonical flicky-ue.v1 source profile"
        )
    matching_tabs = [
        tab for tab in inspection["workspace"]["tabs"]
        if tab["enabled"] and tab["codec"] == "flicky.levels.v1"
        and "playtest" in tab["capabilities"]
    ]
    if len(matching_tabs) != 1:
        raise StudioProjectError(
            "Studio project must expose one enabled playtest-capable Flicky Levels tab"
        )
    round = _bounded_integer(round, "round", 1, 48)
    verification = verify_studio_project(path)
    if verification["status"] != "current":
        raise StudioProjectError(
            "Studio artifact is stale; build it again before round playtest"
        )
    lua = Path(__file__).with_name("runtime") / "flicky_mame_round_playtest.lua"
    if not lua.is_file():
        raise StudioProjectError(f"packaged Flicky round playtest script is missing: {lua}")
    artifact = Path(verification["artifact"]["absolute_path"])
    plan = launch_image_plan(
        artifact, platform="megadrive", mame=mame, machine="genesis",
    )
    plan.update({
        "operation": "retrolarium.studio.flicky-round-playtest",
        "backend": "mame-lua",
        "arguments": [
            *plan["arguments"], "-autoboot_delay", "0",
            "-autoboot_script", str(lua.resolve()),
        ],
        "environment": {"FLICKY_PLAYTEST_ROUND": str(round)},
        "project_file": inspection["project_file"],
        "script": {"path": str(lua.resolve()), "sha256": sha256_file(lua)},
        "point": {"round": round},
    })
    return plan


def studio_flicky_demo_plan(
    path: Path, *, mame: Path, demo: int, record: Path | None = None,
) -> dict[str, Any]:
    """Loop one attract demo (1-4) of the current Flicky build in MAME, or
    record a new one from the player's controller into ``record``."""

    inspection = load_studio_project(path)
    _require_declared_operation(inspection, "launch")
    project = inspection["project"]
    if project["source_mode"] != "flicky-content"             or project["platform"] != "megadrive"             or project["profile"] != "flicky-ue.v1":
        raise StudioProjectError(
            "Flicky demo playtest requires the canonical flicky-ue.v1 source profile"
        )
    demo = _bounded_integer(demo, "demo", 1, 4)
    executable = mame.resolve()
    if not executable.is_file():
        raise StudioProjectError(f"MAME executable does not exist: {executable}")
    verification = verify_studio_project(path)
    if verification["status"] != "current":
        raise StudioProjectError("Studio artifact is stale; build it again before the demo")
    lua = Path(__file__).with_name("runtime") / "flicky_mame_demo.lua"
    if not lua.is_file():
        raise StudioProjectError(f"packaged Flicky demo script is missing: {lua}")
    artifact = Path(verification["artifact"]["absolute_path"])
    plan = launch_image_plan(
        artifact, platform="megadrive", mame=executable, machine="genesis",
    )
    recording = "" if record is None else str(record.resolve())
    plan.update({
        "operation": "retrolarium.studio.flicky-demo",
        "backend": "mame-lua",
        "arguments": [
            *plan["arguments"], "-autoboot_delay", "0",
            "-autoboot_script", str(lua.resolve()),
        ],
        "environment": {
            "FLICKY_DEMO_INDEX": str(demo - 1),
            "FLICKY_DEMO_RECORD": recording,
        },
        "project_file": inspection["project_file"],
        "script": {"path": str(lua.resolve()), "sha256": sha256_file(lua)},
        "demo": {"index": demo, "record": recording or None},
    })
    return plan


def studio_flicky_sound_preview(
    path: Path, *, header: str, seconds: int = 30,
    enabled_channels: tuple[int, ...] | None = None,
    document_override: Path | None = None,
) -> dict[str, Any]:
    """Render saved or transient Flicky Sound data through the pinned source oracle."""

    inspection = load_studio_project(path)
    project = inspection["project"]
    if project["source_mode"] != "flicky-content" \
            or project["platform"] != "megadrive" \
            or project["profile"] != "flicky-ue.v1":
        raise StudioProjectError(
            "Flicky sound preview requires the canonical flicky-ue.v1 source profile"
        )
    matching_tabs = [
        tab for tab in inspection["workspace"]["tabs"]
        if tab["enabled"] and tab["codec"] == "flicky.sound.v1"
        and "preview" in tab["capabilities"]
    ]
    if len(matching_tabs) != 1:
        raise StudioProjectError(
            "Studio project must expose one enabled preview-capable Flicky Sound tab"
        )
    seconds = _bounded_integer(seconds, "seconds", 1, 30)
    root = Path(inspection["project_root"]).resolve()
    try:
        validate_flicky_source_snapshot(root)
    except FlickyContentError as exc:
        raise StudioProjectError(str(exc)) from exc
    source = root / "flicky-source"
    document = Path(matching_tabs[0]["document_path"]).resolve()
    expected_document = source / FLICKY_WORKSPACE_FILES["z80_sound_banks"]
    if document != expected_document.resolve():
        raise StudioProjectError("Flicky Sound tab does not own the source workspace")
    output_dir = root / "build/sound-preview"
    if not output_dir.resolve().is_relative_to(root):
        raise StudioProjectError(
            "Flicky Sound preview output must remain inside the project"
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    document_origin = "saved"
    if document_override is not None:
        candidate = document_override.resolve()
        if candidate.parent != output_dir.resolve() \
                or not candidate.name.startswith(".flicky-unsaved-") \
                or candidate.suffix != ".asm" or not candidate.is_file():
            raise StudioProjectError(
                "transient Flicky Sound document must be an ASM file in "
                "this project's build/sound-preview directory"
            )
        document = candidate
        document_origin = "unsaved"
    model_path = source / "scripts/authoring/sound_studio_model.py"
    try:
        sound_model = runpy.run_path(str(model_path))
        inventory = sound_model["export_document"](document)
    except (OSError, ValueError, KeyError) as exc:
        raise StudioProjectError(f"cannot read Flicky Sound inventory: {exc}") from exc
    headers = {item["id"]: item for item in inventory["headers"]}
    if header not in headers:
        raise StudioProjectError(f"unknown Flicky Sound header: {header!r}")
    track_count = len(headers[header]["tracks"])
    if enabled_channels is not None:
        if not isinstance(enabled_channels, tuple) or not enabled_channels \
                or any(not isinstance(index, int) or isinstance(index, bool)
                       or not 0 <= index < track_count
                       for index in enabled_channels) \
                or len(set(enabled_channels)) != len(enabled_channels):
            raise StudioProjectError(
                f"enabled channels must be unique indexes in 0..{track_count - 1}"
            )
        enabled_channels = tuple(sorted(enabled_channels))
    renderer = source / "bin/windows_i386/ymfm_renderer.exe"
    if not renderer.is_file():
        raise StudioProjectError(
            "pinned Flicky YMFM renderer is unavailable in the source snapshot"
        )
    channel_key = "all" if enabled_channels is None else "-".join(
        str(index) for index in enabled_channels)
    stem = f"{header}-{sha256_file(document)[:12]}-s{seconds}-c{channel_key}"
    vgm = output_dir / f"{stem}.vgm"
    wav = output_dir / f"{stem}.wav"
    with tempfile.TemporaryDirectory(prefix=".flicky-sound-", dir=output_dir) as name:
        temporary = Path(name)
        command = [
            sys.executable, str(Path(__file__).resolve().parents[1] / "games" / "flicky"
                / "sound_preview_runtime.py"), header,
            "--workspace", str(document),
            "--vgm", str(temporary / "preview.vgm"),
            "--wav", str(temporary / "preview.wav"),
            "--renderer", str(renderer),
            "--seconds", str(seconds),
        ]
        if enabled_channels is not None:
            command.extend(("--channels", ",".join(map(str, enabled_channels))))
        try:
            result = subprocess.run(
                command, cwd=source, capture_output=True, text=True, timeout=180,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise StudioProjectError(f"Flicky Sound preview failed: {exc}") from exc
        if result.returncode:
            raise StudioProjectError(
                "Flicky Sound preview failed: " + (result.stderr or result.stdout)[-4000:]
            )
        temporary_vgm = temporary / "preview.vgm"
        temporary_wav = temporary / "preview.wav"
        try:
            if temporary_vgm.read_bytes()[:4] != b"Vgm " \
                    or temporary_wav.read_bytes()[:4] != b"RIFF":
                raise StudioProjectError(
                    "Flicky Sound preview produced invalid audio files"
                )
            os.replace(temporary_vgm, vgm)
            os.replace(temporary_wav, wav)
        except OSError as exc:
            raise StudioProjectError(
                f"Flicky Sound preview cannot publish audio: {exc}"
            ) from exc
    return {
        "schema": "retrolarium.flicky-sound-preview",
        "schema_version": 1,
        "status": "rendered",
        "project_file": inspection["project_file"],
        "document_origin": document_origin,
        "header": header,
        "seconds": seconds,
        "enabled_channels": list(range(track_count)) if enabled_channels is None
            else list(enabled_channels),
        "document_sha256": sha256_file(document),
        "renderer_sha256": sha256_file(renderer),
        "vgm": {"path": str(vgm), "size": vgm.stat().st_size,
                "sha256": sha256_file(vgm)},
        "wav": {"path": str(wav), "size": wav.stat().st_size,
                "sha256": sha256_file(wav)},
    }


def _smb_area_pointer(name: str) -> int:
    area_types = ("water", "ground", "underground", "castle")
    area_type, separator, index_text = name.rpartition("_")
    if not separator or area_type not in area_types:
        raise StudioProjectError(f"unsupported SMB area name: {name!r}")
    try:
        area_index = int(index_text) - 1
    except ValueError as exc:
        raise StudioProjectError(f"unsupported SMB area name: {name!r}") from exc
    if not 0 <= area_index <= 31:
        raise StudioProjectError(f"SMB area index is outside 1..32: {name!r}")
    return (area_types.index(area_type) << 5) | area_index


def _smb_object_pages(area: Mapping[str, Any]) -> set[int]:
    """The pages an area object starts on, as the game's area parser counts
    them: the page bit moves on one page, a row-13 page control (d6 clear)
    jumps to a page, and page controls are not objects themselves."""
    pages: set[int] = set()
    page = 0
    for item in area.get("objects") or []:
        if not isinstance(item, Mapping):
            continue
        if item.get("page_advance"):
            page += 1
        control = item.get("object_control")
        if item.get("row") == 13 and isinstance(control, int) and not control & 0x40:
            page = control & 0x1F
            continue
        pages.add(page)
    return pages


def _bounded_integer(value: object, name: str, minimum: int, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) \
            or not minimum <= value <= maximum:
        raise StudioProjectError(f"{name} must be in range {minimum}..{maximum}")
    return value


def studio_smb_point_playtest_plan(
    path: Path,
    *,
    mame: Path,
    course_bank: str,
    area: str,
    world: int,
    course: int,
    column: int,
    row: int,
    theme: str,
    infinite_time: bool = True,
    invincible: bool = True,
    wrap_pits: bool = True,
    sfx_channel: str = "none",
    sfx_value: int = 0,
) -> dict[str, Any]:
    """Emit the fixed MAME/Lua process contract for an SMB JU editor point."""

    inspection = load_studio_project(path)
    _require_declared_operation(inspection, "launch")
    if inspection["project"]["platform"] != "nes" \
            or inspection["project"]["profile"] != "smb1.v1":
        raise StudioProjectError(
            "SMB point playtest currently supports only the smb1.v1 NES profile"
        )
    matching_tabs = [
        tab for tab in inspection["workspace"]["tabs"]
        if tab["enabled"] and tab["codec"] == "smb.levels.v1"
        and "playtest" in tab["capabilities"]
    ]
    if len(matching_tabs) != 1:
        raise StudioProjectError(
            "Studio project must expose one enabled playtest-capable SMB Levels tab"
        )

    verification = verify_studio_project(path)
    if verification["status"] != "current":
        raise StudioProjectError(
            "Studio artifact is stale; build it again before point playtest"
        )
    executable = mame.resolve()
    if not executable.is_file():
        raise StudioProjectError(f"MAME executable does not exist: {executable}")

    document_path = Path(matching_tabs[0]["document_path"])
    try:
        document = _mapping(
            json.loads(document_path.read_text(encoding="utf-8")),
            "SMB resource document",
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StudioProjectError(f"cannot read SMB resource document: {exc}") from exc
    if document.get("schema") != "retrolarium.nes-resource-bundle" \
            or document.get("game") != "smb1":
        raise StudioProjectError("SMB point playtest requires an smb1 resource document")
    if document.get("revision") not in SMB_POINT_PLAYTEST_REVISIONS:
        raise StudioProjectError(
            "Starting inside a level does not work in this version; play it from power-on."
        )
    resources = document.get("resources")
    if not isinstance(resources, list):
        raise StudioProjectError("SMB resource document resources must be an array")
    level_resources = [
        item for item in resources if isinstance(item, Mapping)
        and item.get("id") == course_bank and item.get("codec") == "smb.levels.v1"
    ]
    if len(level_resources) != 1:
        raise StudioProjectError(f"unknown SMB course bank: {course_bank!r}")
    level_resource = level_resources[0]
    areas = level_resource.get("areas")
    selected_areas = [
        item for item in areas if isinstance(item, Mapping) and item.get("name") == area
    ] if isinstance(areas, list) else []
    if not selected_areas:
        raise StudioProjectError(
            f"SMB area {area!r} does not belong to course bank {course_bank!r}"
        )

    world = _bounded_integer(world, "world", 1, 8)
    course = _bounded_integer(course, "course", 1, 8)
    column = _bounded_integer(column, "column", 0, 511)
    row = _bounded_integer(row, "row", 1, 12)
    if theme not in {"day", "night"}:
        raise StudioProjectError("theme must be 'day' or 'night'")
    if sfx_channel not in {"none", "square1", "square2", "noise"}:
        raise StudioProjectError(
            "sfx_channel must be 'none', 'square1', 'square2', or 'noise'"
        )
    sfx_value = _bounded_integer(sfx_value, "sfx_value", 0, 255)
    if (sfx_channel == "none") != (sfx_value == 0):
        raise StudioProjectError(
            "sfx_channel 'none' requires value 0 and an SFX channel requires a non-zero value"
        )
    for name, value in (
        ("infinite_time", infinite_time),
        ("invincible", invincible),
        ("wrap_pits", wrap_pits),
    ):
        if not isinstance(value, bool):
            raise StudioProjectError(f"{name} must be a boolean")

    declared_world_resource = level_resource.get("world_resource")
    if declared_world_resource is not None and not isinstance(
        declared_world_resource, str
    ):
        raise StudioProjectError(
            f"SMB course bank {course_bank!r} has an invalid world resource"
        )
    world_resources = [
        item
        for item in resources
        if isinstance(item, Mapping)
        and item.get("codec") == "smb.world.v1"
        and (
            declared_world_resource is None
            or item.get("id") == declared_world_resource
        )
    ]
    if len(world_resources) != 1:
        if declared_world_resource is None:
            raise StudioProjectError("SMB point playtest requires one world resource")
        raise StudioProjectError(
            f"SMB course bank {course_bank!r} refers to an unknown world resource: "
            f"{declared_world_resource!r}"
        )
    worlds = world_resources[0].get("worlds")
    if not isinstance(worlds, list) or world > len(worlds):
        raise StudioProjectError(f"world {world} is not present in the SMB document")
    route = _mapping(worlds[world - 1], f"SMB world {world}").get("areas")
    if not isinstance(route, list) or course > len(route):
        raise StudioProjectError(
            f"course {course} is not present in SMB world {world}"
        )

    palette_resources = [
        item for item in resources if isinstance(item, Mapping)
        and item.get("codec") == "nes.ppu-palette-packets.v1"
    ]
    if len(palette_resources) != 1:
        raise StudioProjectError("SMB point playtest requires one area palette resource")
    day_color = _bounded_integer(
        palette_resources[0].get("day_background"), "day_background", 0, 63
    )
    night_color = _bounded_integer(
        palette_resources[0].get("night_background"), "night_background", 0, 63
    )

    pointer = _smb_area_pointer(area)
    selected_route = _mapping(
        route[course - 1], f"SMB world {world} course {course}"
    )
    route_pointer = (
        _bounded_integer(selected_route.get("area_type"), "area_type", 0, 3) << 5
    ) | _bounded_integer(selected_route.get("area_index"), "area_index", 0, 31)
    artifact = Path(verification["artifact"]["absolute_path"]).resolve()
    lua = Path(__file__).with_name("runtime") / "smb1_mame_point_playtest.lua"
    if not lua.is_file():
        raise StudioProjectError(f"packaged SMB point playtest script is missing: {lua}")
    environment = {
        "SMB1_PLAYTEST_AREA": str(pointer),
        "SMB1_PLAYTEST_WORLD": str(world - 1),
        "SMB1_PLAYTEST_LEVEL": str(course - 1),
        "SMB1_PLAYTEST_PAGE": str(column // 16),
        "SMB1_PLAYTEST_X": str((column % 16) * 16),
        "SMB1_PLAYTEST_Y": str((row + 1) * 16),
        "SMB1_PLAYTEST_THEME": theme,
        "SMB1_PLAYTEST_DAY_COLOR": str(day_color),
        "SMB1_PLAYTEST_NIGHT_COLOR": str(night_color),
        "SMB1_PLAYTEST_INFINITE_TIME": "1" if infinite_time else "0",
        "SMB1_PLAYTEST_INVINCIBLE": "1" if invincible else "0",
        "SMB1_PLAYTEST_WRAP_PITS": "1" if wrap_pits else "0",
        "SMB1_PLAYTEST_SFX_CHANNEL": sfx_channel,
        "SMB1_PLAYTEST_SFX_VALUE": str(sfx_value),
    }
    # Entering on a later page, the game's area parser looks for an object
    # that starts on that page and spins forever when there is none (a page
    # control can skip pages); the script then stops it looking.
    if column // 16 and column // 16 not in _smb_object_pages(selected_areas[0]):
        environment["SMB1_PLAYTEST_EMPTY_PAGE"] = "1"
    if document.get("revision") in SMB_LATE_FDS_RUNNING_TASKS:
        environment["SMB1_PLAYTEST_TIMER"] = str(0x07EC)
        environment["SMB1_PLAYTEST_RUNNING_TASK"] = str(SMB_LATE_FDS_RUNNING_TASKS[document["revision"]])
        hard = world_resources[0].get("id") in SMB_LATE_FDS_HARD_WORLDS
        if hard or world >= 5:
            file_id, file_list = (0x40, 3) if hard else (0x20, 1)
            blob = artifact.parent / "playtest-disk-file.bin"
            blob.write_bytes(_fds_file_blob(artifact, file_id))
            environment["SMB1_PLAYTEST_DISK_FILE"] = str(blob)
            environment["SMB1_PLAYTEST_DISK_LIST"] = str(file_list)
            if hard:
                environment["SMB1_PLAYTEST_HARD"] = "1"
    plan = launch_image_plan(
        artifact, platform="nes", mame=executable,
        machine="nespal" if document.get("revision") == "pal" else "nes",
    )
    plan.update({
        "operation": "retrolarium.studio.smb-point-playtest",
        "backend": "mame-lua",
        "arguments": [
            *plan["arguments"],
            "-autoboot_delay", "0",
            "-autoboot_script", str(lua.resolve()),
        ],
        "environment": environment,
        "project_file": inspection["project_file"],
        "script": {"path": str(lua.resolve()), "sha256": sha256_file(lua)},
        "point": {
            "course_bank": course_bank,
            "area": area,
            "area_pointer": pointer,
            "world": world,
            "course": course,
            "route_matches_area": route_pointer == pointer,
            "column": column,
            "row": row,
            "theme": theme,
            "sfx": {"channel": sfx_channel, "value": sfx_value},
        },
    })
    return plan


def studio_castle_point_playtest_plan(
    path: Path, *, mame: Path, room: int, x: int, y: int,
) -> dict[str, Any]:
    """Prepare an isolated Castle ROM that boots through its normal room loader."""

    inspection = load_studio_project(path)
    _require_declared_operation(inspection, "launch")
    if inspection["project"]["platform"] != "nes" \
            or inspection["project"]["profile"] != "castle-excellent.v1":
        raise StudioProjectError(
            "Castle point playtest requires the castle-excellent.v1 NES profile"
        )
    matching_tabs = [
        tab for tab in inspection["workspace"]["tabs"]
        if tab["enabled"] and tab["codec"] == "castle.prefix-rooms@1"
        and "playtest" in tab["capabilities"]
    ]
    if len(matching_tabs) != 1:
        raise StudioProjectError(
            "Studio project must expose one enabled playtest-capable Castle Rooms tab"
        )
    verification = verify_studio_project(path)
    if verification["status"] != "current":
        raise StudioProjectError(
            "Studio artifact is stale; build it again before point playtest"
        )
    executable = mame.resolve()
    if not executable.is_file():
        raise StudioProjectError(f"MAME executable does not exist: {executable}")

    document_path = Path(matching_tabs[0]["document_path"])
    try:
        document = _mapping(
            json.loads(document_path.read_text(encoding="utf-8")),
            "Castle rooms document",
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StudioProjectError(f"cannot read Castle rooms document: {exc}") from exc
    if document.get("schema") != "retrolarium.castle-levels" \
            or document.get("schema_version") != 1 \
            or document.get("game") != "castle-excellent" \
            or document.get("revision") not in {
                "castle-excellent-japan-hsp-05", "castlequest-usa"
            }:
        raise StudioProjectError("Castle rooms document has an unsupported identity")
    rooms = document.get("rooms")
    if not isinstance(rooms, list) or len(rooms) != 100:
        raise StudioProjectError("Castle point playtest requires exactly 100 rooms")
    room = _bounded_integer(room, "room", 1, 100)
    x = _bounded_integer(x, "x", 0, 319)
    y = _bounded_integer(y, "y", 0, 159)

    artifact = Path(verification["artifact"]["absolute_path"]).resolve()
    source = artifact.read_bytes()
    # iNES header + PRG offset $22B1: LDA #$47; STA $37. Both supported
    # revisions share this sequence. Only the immediate operand is changed.
    if len(source) != 65552 or source[:4] != b"NES\x1a" \
            or source[0x22c1] != 0xa9 or source[0x22c3:0x22c5] != b"\x85\x37":
        raise StudioProjectError(
            "Castle build does not match the guarded initial-room layout"
        )
    preview = bytearray(source)
    preview[0x22c2] = room
    project_root = Path(inspection["project_root"]).resolve()
    preview_directory = project_root / "build" / "point-playtest"
    preview_directory.mkdir(parents=True, exist_ok=True)
    preview_path = preview_directory / (
        f"castle-{sha256_file(artifact)[:16]}-room-{room:03d}.nes"
    )
    if not preview_path.is_file() or preview_path.read_bytes() != preview:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".castle-point-", suffix=".tmp", dir=preview_directory
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(preview)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, preview_path)
        finally:
            temporary.unlink(missing_ok=True)

    lua = Path(__file__).with_name("runtime") / "castle_mame_point_playtest.lua"
    if not lua.is_file():
        raise StudioProjectError(f"packaged Castle point playtest script is missing: {lua}")
    plan = launch_image_plan(preview_path, platform="nes", mame=executable)
    plan.update({
        "operation": "retrolarium.studio.castle-point-playtest",
        "backend": "mame-lua",
        "arguments": [
            *plan["arguments"], "-autoboot_delay", "0",
            "-autoboot_script", str(lua.resolve()),
        ],
        "environment": {
            "CASTLE_PLAYTEST_ROOM": str(room),
            "CASTLE_PLAYTEST_X": str(x),
            "CASTLE_PLAYTEST_Y": str(y),
        },
        "project_file": inspection["project_file"],
        "script": {"path": str(lua.resolve()), "sha256": sha256_file(lua)},
        "point": {"room": room, "x": x, "y": y},
        "base_rom": {"path": str(artifact), "sha256": sha256_file(artifact)},
    })
    return plan


def studio_solomon_point_playtest_plan(
    path: Path, *, mame: Path, room: int,
) -> dict[str, Any]:
    """Enter a selected Solomon room in MAME without patching the content ROM."""

    inspection = load_studio_project(path)
    _require_declared_operation(inspection, "launch")
    if inspection["project"]["platform"] != "nes" \
            or inspection["project"]["profile"] != "solomons-key.v1":
        raise StudioProjectError(
            "Solomon point playtest requires the solomons-key.v1 NES profile"
        )
    matching_tabs = [
        tab for tab in inspection["workspace"]["tabs"]
        if tab["enabled"] and tab["codec"] == "solomon.rooms@3"
        and "playtest" in tab["capabilities"]
    ]
    if len(matching_tabs) != 1:
        raise StudioProjectError(
            "Studio project must expose one enabled playtest-capable Solomon Rooms tab"
        )
    document_path = Path(matching_tabs[0]["document_path"])
    try:
        document = _mapping(
            json.loads(document_path.read_text(encoding="utf-8")),
            "Solomon rooms document",
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StudioProjectError(f"cannot read Solomon rooms document: {exc}") from exc
    revision_id = document.get("revision")
    if document.get("schema") != "retrolarium.solomon-rooms" \
            or document.get("schema_version") != 3 \
            or document.get("game") != "solomons-key" \
            or revision_id not in SOLOMON_REVISIONS \
            or not isinstance(document.get("rooms"), list) \
            or len(document["rooms"]) != 53:
        raise StudioProjectError("Solomon rooms document has an unsupported identity")
    room = _bounded_integer(room, "room", 0, 52)
    profile = packaged_game_profiles().get("solomons-key")
    revision = next(
        item for item in profile.revisions if item.revision_id == revision_id
    )
    hooks = revision.playtest
    if hooks is None:
        raise StudioProjectError(
            f"Solomon revision {revision_id} has no point-playtest contract"
        )
    executable = mame.resolve()
    if not executable.is_file():
        raise StudioProjectError(f"MAME executable does not exist: {executable}")
    verification = verify_studio_project(path)
    if verification["status"] != "current":
        raise StudioProjectError(
            "Studio artifact is stale; build it again before point playtest"
        )
    artifact = Path(verification["artifact"]["absolute_path"]).resolve()
    source = artifact.read_bytes()
    hook_offset = 16 + hooks["room_load_address"] - 0x8000
    # An expanded build (solomon_expansion) runs the game from its second
    # 32 KiB PRG bank.
    expanded = len(source) == 16 + 0x10000 + 0x8000 \
        and ((source[6] >> 4) | (source[7] & 0xF0)) == 66
    if expanded:
        hook_offset += 0x8000
    if (len(source) != revision.size and not expanded) or source[:4] != b"NES\x1a" \
            or source[hook_offset:hook_offset + 6] != b"\xa9\xfd\x25\x7c\x85\x7c":
        raise StudioProjectError(
            "Solomon build does not match the guarded room-loader layout"
        )
    lua = Path(__file__).with_name("runtime") / "solomon_mame_point_playtest.lua"
    if not lua.is_file():
        raise StudioProjectError(f"packaged Solomon point-playtest script is missing: {lua}")
    plan = launch_image_plan(
        artifact, platform="nes", mame=executable,
        machine="nespal" if revision_id == "europe" else "nes",
    )
    plan.update({
        "operation": "retrolarium.studio.solomon-point-playtest",
        "backend": "mame-lua",
        "arguments": [
            *plan["arguments"], "-autoboot_delay", "0",
            "-autoboot_script", str(lua.resolve()),
        ],
        "environment": {
            "SOLOMON_PLAYTEST_ROOM": str(room),
            "SOLOMON_PLAYTEST_ROOM_LOAD": str(hooks["room_load_address"]),
            "SOLOMON_PLAYTEST_GAMEPLAY": str(hooks["gameplay_address"]),
            "SOLOMON_PLAYTEST_CURRENT_ROOM": str(hooks["current_room_address"]),
            "SOLOMON_PLAYTEST_START_FRAME": str(hooks["start_frame"]),
            "SOLOMON_PLAYTEST_READY_FRAMES": str(hooks["ready_frames"]),
        },
        "project_file": inspection["project_file"],
        "script": {"path": str(lua.resolve()), "sha256": sha256_file(lua)},
        "point": {"room": room, "revision": revision_id},
    })
    return plan


# Attract-demo hooks per revision: the demo input thread's entry and the
# post-game thread's entry.
SOLOMON_DEMO_HOOKS = {
    "usa": {"run": 0xCB2B, "post_game": 0xCA6E},
    "europe": {"run": 0xCABB, "post_game": 0xC9FE},
    "japan": {"run": 0xCBDC, "post_game": 0xCB6E},
}
SOLOMON_DEMO_RUN_GUARD = bytes.fromhex("a90085228581")

# SMB JU: the title demo's exit to the title screen and the RAM the demo
# script reads and writes (the tables and the engine come from the profile).
SMB_DEMO_HOOKS = {
    "saved_joypad": 0x06FC, "demo_action": 0x0717, "action_timer": 0x0718,
    "oper_mode": 0x0770,
}
# Where each version's title goes back to the start when its demo is over;
# the RAM above is the same in all of them.
SMB_DEMO_RESET_TITLE = {
    "ju": 0x82C9, "pc10": 0x82C9, "pal": 0x82C9,
    "fds_smb": 0x62CF, "ann_fds": 0xC497, "smb2_jp_fds": 0xC4DD,
}


def studio_smb_demo_plan(path: Path, *, mame: Path, record: Path | None = None) -> dict[str, Any]:
    """Loop the SMB title demo of the current build in MAME, or record a new
    one from the player's controller into ``record``."""

    inspection = load_studio_project(path)
    _require_declared_operation(inspection, "launch")
    if inspection["project"]["platform"] != "nes" or inspection["project"]["profile"] != "smb1.v1":
        raise StudioProjectError("SMB demo playtest requires the smb1.v1 NES profile")
    profile = packaged_game_profiles().get("smb1")
    revision = next(
        (item for item in profile.revisions if item.sha256 == inspection["source"]["sha256"]), None
    )
    if revision is None or revision.revision_id not in SMB_DEMO_RESET_TITLE:
        raise StudioProjectError("The demo does not play in MAME in this version.")
    demo = next(item for item in profile.resources_for(revision) if item.codec == "smb.demo-input.v1")
    tables = demo.parameters
    executable = mame.resolve()
    if not executable.is_file():
        raise StudioProjectError(f"MAME executable does not exist: {executable}")
    verification = verify_studio_project(path)
    if verification["status"] != "current":
        raise StudioProjectError("Studio artifact is stale; build it again before the demo")
    artifact = Path(verification["artifact"]["absolute_path"]).resolve()
    if artifact.suffix.lower() == ".fds":
        source, engine = _fds_program_at(artifact, tables["engine_cpu"])
    else:
        source = artifact.read_bytes()
        engine = 16 + tables["engine_cpu"] - 0x8000
    expected = (tables["durations_cpu"] - 1).to_bytes(2, "little")
    if engine < 0 or source[engine:engine + 3] != b"\xae\x17\x07" \
            or source[engine + 14:engine + 16] != expected:
        raise StudioProjectError("SMB build does not match the guarded demo layout")
    lua = Path(__file__).with_name("runtime") / "smb1_mame_demo.lua"
    if not lua.is_file():
        raise StudioProjectError(f"packaged SMB demo script is missing: {lua}")
    plan = launch_image_plan(artifact, platform="nes", mame=executable,
                             machine="nespal" if revision.revision_id == "pal" else "nes")
    recording = "" if record is None else str(record.resolve())
    plan.update({
        "operation": "retrolarium.studio.smb-demo",
        "backend": "mame-lua",
        "arguments": [
            *plan["arguments"], "-autoboot_delay", "0",
            "-autoboot_script", str(lua.resolve()),
        ],
        "environment": {
            "SMB_DEMO_ENGINE": str(tables["engine_cpu"]),
            "SMB_DEMO_RESET_TITLE": str(SMB_DEMO_RESET_TITLE[revision.revision_id]),
            "SMB_DEMO_ACTIONS": str(tables["actions_cpu"]),
            "SMB_DEMO_DURATIONS": str(tables["durations_cpu"]),
            "SMB_DEMO_CAPACITY": str(tables["capacity"]),
            "SMB_DEMO_SAVED_JOYPAD": str(SMB_DEMO_HOOKS["saved_joypad"]),
            "SMB_DEMO_ACTION": str(SMB_DEMO_HOOKS["demo_action"]),
            "SMB_DEMO_ACTION_TIMER": str(SMB_DEMO_HOOKS["action_timer"]),
            "SMB_DEMO_OPER_MODE": str(SMB_DEMO_HOOKS["oper_mode"]),
            "SMB_DEMO_RECORD": recording,
        },
        "project_file": inspection["project_file"],
        "script": {"path": str(lua.resolve()), "sha256": sha256_file(lua)},
        "demo": {"revision": revision.revision_id, "record": recording or None},
    })
    return plan


# Castle Excellent: the RAM flag that plays the demo in place of controller 1.
CASTLE_DEMO_ACTIVE = 0x07C7


def studio_castle_demo_plan(path: Path, *, mame: Path, record: Path | None = None) -> dict[str, Any]:
    """Loop the Castle Excellent title demo of the current build in MAME, or
    record a new one from the player's controller into ``record``."""

    inspection = load_studio_project(path)
    _require_declared_operation(inspection, "launch")
    if inspection["project"]["platform"] != "nes" \
            or inspection["project"]["profile"] != "castle-excellent.v1":
        raise StudioProjectError("Castle demo playtest requires the castle-excellent.v1 NES profile")
    profile = packaged_game_profiles().get("castle-excellent")
    revision = next(
        (item for item in profile.revisions if item.sha256 == inspection["source"]["sha256"]), None
    )
    if revision is None:
        raise StudioProjectError("Castle demo playtest needs a known Castle Excellent source")
    demo = next(item for item in profile.resources_for(revision) if item.codec == "castle.title-demo.v1")
    layout = demo.parameters
    executable = mame.resolve()
    if not executable.is_file():
        raise StudioProjectError(f"MAME executable does not exist: {executable}")
    verification = verify_studio_project(path)
    if verification["status"] != "current":
        raise StudioProjectError("Studio artifact is stale; build it again before the demo")
    artifact = Path(verification["artifact"]["absolute_path"]).resolve()
    source = artifact.read_bytes()
    init = 16 + layout["init_cpu"] - 0x8000
    script = layout["script_cpu"]
    reader = 16 + layout["reader_cpu"] - 0x8000
    if source[init:init + 8] != bytes((0xA9, script & 0xFF, 0x85, 0xFD, 0xA9, script >> 8, 0x85, 0xFE)) \
            or source[reader + 10:reader + 12] != b"\xb1\xfd":
        raise StudioProjectError("Castle build does not match the guarded demo layout")
    lua = Path(__file__).with_name("runtime") / "castle_mame_demo.lua"
    if not lua.is_file():
        raise StudioProjectError(f"packaged Castle demo script is missing: {lua}")
    plan = launch_image_plan(artifact, platform="nes", mame=executable,
                             machine="nespal" if revision.revision_id == "pal" else "nes")
    recording = "" if record is None else str(record.resolve())
    plan.update({
        "operation": "retrolarium.studio.castle-demo",
        "backend": "mame-lua",
        "arguments": [
            *plan["arguments"], "-autoboot_delay", "0",
            "-autoboot_script", str(lua.resolve()),
        ],
        "environment": {
            "CASTLE_DEMO_SCRIPT": str(script),
            "CASTLE_DEMO_READER": str(layout["reader_cpu"]),
            "CASTLE_DEMO_CAPACITY": str(layout["capacity"]),
            "CASTLE_DEMO_ACTIVE": str(CASTLE_DEMO_ACTIVE),
            "CASTLE_DEMO_RECORD": recording,
        },
        "project_file": inspection["project_file"],
        "script": {"path": str(lua.resolve()), "sha256": sha256_file(lua)},
        "demo": {"revision": revision.revision_id, "record": recording or None},
    })
    return plan


def studio_pacman_round_playtest_plan(path: Path, *, mame: Path, round: int) -> dict[str, Any]:
    """Run the current Pac-Man build from power-on into a one-player game
    that starts at ``round`` (1-23, where the game stops counting)."""

    inspection = load_studio_project(path)
    _require_declared_operation(inspection, "launch")
    if inspection["project"]["platform"] != "nes"             or inspection["project"]["profile"] != "pacman.v1":
        raise StudioProjectError("Pac-Man round playtest requires the pacman.v1 NES profile")
    round = _bounded_integer(round, "round", 1, 23)
    executable = mame.resolve()
    if not executable.is_file():
        raise StudioProjectError(f"MAME executable does not exist: {executable}")
    verification = verify_studio_project(path)
    if verification["status"] != "current":
        raise StudioProjectError("Studio artifact is stale; build it again before round playtest")
    lua = Path(__file__).with_name("runtime") / "pacman_mame_round_playtest.lua"
    if not lua.is_file():
        raise StudioProjectError(f"packaged Pac-Man round playtest script is missing: {lua}")
    artifact = Path(verification["artifact"]["absolute_path"]).resolve()
    plan = launch_image_plan(artifact, platform="nes", mame=executable, machine="nes")
    plan.update({
        "operation": "retrolarium.studio.pacman-round-playtest",
        "backend": "mame-lua",
        "arguments": [
            *plan["arguments"], "-autoboot_delay", "0",
            "-autoboot_script", str(lua.resolve()),
        ],
        "environment": {"PACMAN_PLAYTEST_ROUND": str(round)},
        "project_file": inspection["project_file"],
        "script": {"path": str(lua.resolve()), "sha256": sha256_file(lua)},
        "point": {"round": round},
    })
    return plan


# The shell's reads when Start begins a game (bank 3 CPU addresses).
DORAEMON_START_BUTTONS_READ = 0x83EE
DORAEMON_START_CHAPTER_READ = 0x83FD
# The world start-up stores a playtest point answers (CPU addresses in the
# world's bank): World1_InitializeDemoState's camera, player position and
# area palette ($29),
# Bank1_World2Main's stage index, Bank2_World3Main's two room clears and
# World3_LoadRoomFromPassHoop's store of the hoop's room (a respawn
# returns to wherever the shuffled pass hoop lies).
DORAEMON_WORLD1_START_WRITES = (0x831C, 0x8320, 0x8324, 0x8328, 0x8342)
# World 1's underground: World1_TryEnterManhole's reads of the buttons and
# the manhole flag, World1_EnterManhole's store of the manhole's object id
# (the room + 8), then the room profile's player X/Y, camera X and axis
# position stores.
DORAEMON_UNDERGROUND_WRITES = (0xD237, 0xD23D, 0xD268, 0xCDF3, 0xCDF8, 0xCE07, 0xCE16)
DORAEMON_UNDERGROUND_GUARDS = {
    0xD237: (0xA5, 0x21), 0xD23D: (0xA5, 0x81), 0xD268: (0x85, 0x81), 0xCDF3: (0x85, 0x75),
    0xCDF8: (0x85, 0x76), 0xCE07: (0x85, 0x5B), 0xCE16: (0x85, 0x89),
}
# World1_UndergroundRoomCameraProfiles ($D1EF: camera X, camera Y, axis
# limit, axis start) and World1_UndergroundRoomEntryProfiles ($D213: player
# X, Y and the two city returns), four bytes a room.
DORAEMON_UNDERGROUND_CAMERAS = 0xD1EF
DORAEMON_UNDERGROUND_ROOMS = 9
DORAEMON_WORLD2_START_WRITES = (0x88B5,)
DORAEMON_WORLD3_START_WRITES = (0x830C, 0x8326, 0x8557)


def studio_pacman_demo_plan(path: Path, *, mame: Path, record: Path | None = None) -> dict[str, Any]:
    """Loop the Pac-Man attract demo of the current build in MAME, or record
    a new one from the player's controller into ``record``."""

    inspection = load_studio_project(path)
    _require_declared_operation(inspection, "launch")
    if inspection["project"]["platform"] != "nes"             or inspection["project"]["profile"] != "pacman.v1":
        raise StudioProjectError("Pac-Man demo playtest requires the pacman.v1 NES profile")
    profile = packaged_game_profiles().get("pacman")
    revision = next(
        (item for item in profile.revisions if item.sha256 == inspection["source"]["sha256"]), None
    )
    if revision is None:
        raise StudioProjectError("Pac-Man demo playtest needs a known Pac-Man source")
    demo = next(item for item in profile.resources_for(revision) if item.codec == "pacman.attract-demo.v1")
    layout = demo.parameters
    executable = mame.resolve()
    if not executable.is_file():
        raise StudioProjectError(f"MAME executable does not exist: {executable}")
    verification = verify_studio_project(path)
    if verification["status"] != "current":
        raise StudioProjectError("Studio artifact is stale; build it again before the demo")
    artifact = Path(verification["artifact"]["absolute_path"]).resolve()
    source = artifact.read_bytes()
    script = 16 + layout["script_cpu"] - 0xC000
    table = layout["table_cpu"]
    if source[script + 12:script + 15] != bytes((0xB9, table & 0xFF, table >> 8)):
        raise StudioProjectError("Pac-Man build does not match the guarded demo layout")
    lua = Path(__file__).with_name("runtime") / "pacman_mame_demo.lua"
    if not lua.is_file():
        raise StudioProjectError(f"packaged Pac-Man demo script is missing: {lua}")
    plan = launch_image_plan(artifact, platform="nes", mame=executable,
                             machine="nespal" if revision.revision_id == "pal" else "nes")
    recording = "" if record is None else str(record.resolve())
    plan.update({
        "operation": "retrolarium.studio.pacman-demo",
        "backend": "mame-lua",
        "arguments": [
            *plan["arguments"], "-autoboot_delay", "0",
            "-autoboot_script", str(lua.resolve()),
        ],
        "environment": {
            "PACMAN_DEMO_SCRIPT": str(layout["script_cpu"]),
            "PACMAN_DEMO_TABLE": str(table),
            "PACMAN_DEMO_PAIRS": str(layout["pairs"]),
            "PACMAN_DEMO_RECORD": recording,
        },
        "project_file": inspection["project_file"],
        "script": {"path": str(lua.resolve()), "sha256": sha256_file(lua)},
        "demo": {"revision": revision.revision_id, "record": recording or None},
    })
    return plan


def _doraemon_underground_rooms(prg_bank0: bytes) -> list[tuple[int, int, int, int]]:
    """Each room's (camera X, camera Y, axis limit, axis start) in cells."""
    base = DORAEMON_UNDERGROUND_CAMERAS - 0x8000
    return [tuple(prg_bank0[base + room * 4:base + room * 4 + 4])
            for room in range(DORAEMON_UNDERGROUND_ROOMS)]


def _doraemon_underground_point(
    block: tuple[int, int], rooms: list[tuple[int, int, int, int]],
) -> tuple[str, dict[str, Any]]:
    x, y = block
    if not (0 <= x < 64 and 0 <= y < 25):
        raise StudioProjectError("World 1 underground blocks are 0-63 across and 0-24 down")
    # The underground atlas is 64x25 blocks of 32 pixels. A room scrolls
    # across from its axis origin (camera X - axis start) by its limit, a
    # screen of 32x30 cells at its fixed camera Y; duplicate entrances
    # (rooms 5/6 and 7/8) share one region, the first one is used.
    cx, cy = x * 4 + 2, y * 4 + 2
    for room, (camera_x, camera_y, limit, start) in enumerate(rooms):
        origin = camera_x - start
        if origin <= cx < origin + limit + 32 and camera_y <= cy < camera_y + 30:
            break
    else:
        raise StudioProjectError("that underground block is not in a room a manhole leads to")
    coarse = max(0, min(limit, cx - origin - 16))
    left = origin + coarse
    sx = max(0x10, min(0xF0, x * 32 + 16 - left * 8))
    sy = max(0x10, min(0xE0, y * 32 + 16 - camera_y * 8))
    return (f"underground {room} {sx} {sy} {left} {coarse}",
            {"world": 1, "underground": [x, y], "room": room, "camera": [left, camera_y],
             "axis": coarse, "player": [sx, sy]})


def _doraemon_playtest_point(
    world: int, block: tuple[int, int] | None, stage: int | None, room: int | None,
    underground: tuple[int, int] | None = None,
    underground_rooms: list[tuple[int, int, int, int]] | None = None,
) -> tuple[str, tuple[int, ...], dict[str, Any] | None]:
    """The Lua point, the store PCs it answers and the plan's description."""
    if world == 1 and underground is not None:
        if block is not None or underground_rooms is None:
            raise StudioProjectError("pick a city block or an underground block, not both")
        point, described = _doraemon_underground_point(underground, underground_rooms)
        return point, DORAEMON_UNDERGROUND_WRITES, described
    if underground is not None:
        raise StudioProjectError("an underground block is for World 1")
    if world == 1 and block is not None:
        x, y = block
        if not (0 <= x < 64 and 0 <= y < 64):
            raise StudioProjectError("World 1 city blocks are 0-63 on each axis")
        # The city is 64x64 blocks of 32 pixels; the camera counts 8-pixel
        # cells (0-$E0 across, 0-$E2 down) and the player is on the screen.
        px, py = x * 32 + 16, y * 32 + 16
        cx = max(0, min(0xE0, (px - 0x78) // 8))
        cy = max(0, min(0xE2, (py - 0x80) // 8))
        sx = max(8, min(0xF0, px - cx * 8))
        sy = max(16, min(0xE0, py - cy * 8))
        # Above camera row $40 is the mountain, reached from the underground,
        # which then sets the area palette $29 to 1.
        area = 1 if cy < 0x40 else 0
        return (f"camera {cx} {cy} {sx} {sy} {area}", DORAEMON_WORLD1_START_WRITES,
                {"world": 1, "block": [x, y], "camera": [cx, cy], "player": [sx, sy], "area": area})
    if world == 2 and stage is not None:
        if stage not in (0, 1, 2):
            raise StudioProjectError("World 2 stages are 0-2")
        return f"stage {stage}", DORAEMON_WORLD2_START_WRITES, {"world": 2, "stage": stage}
    if world == 3 and room is not None:
        if not 1 <= room <= 63:
            raise StudioProjectError("World 3 rooms are 1-63 (room 0 is the start)")
        return f"room {room}", DORAEMON_WORLD3_START_WRITES, {"world": 3, "room": room}
    if block is not None or stage is not None or room is not None:
        raise StudioProjectError("a city block is for World 1, a stage for World 2, a room for World 3")
    return "", (), None


def studio_doraemon_playtest_plan(
    path: Path, *, mame: Path, world: int, block: tuple[int, int] | None = None,
    stage: int | None = None, room: int | None = None, underground: tuple[int, int] | None = None,
) -> dict[str, Any]:
    """Start the current Doraemon build in one world in MAME, through the
    title's chapter select (A and B held with Start), optionally at a city
    block (World 1), a stage (World 2) or a room (World 3)."""

    if world not in (1, 2, 3):
        raise StudioProjectError("Doraemon playtest world must be 1, 2 or 3")
    inspection = load_studio_project(path)
    _require_declared_operation(inspection, "launch")
    if inspection["project"]["platform"] != "nes" or inspection["project"]["profile"] != "doraemon.v1":
        raise StudioProjectError("Doraemon playtest requires the doraemon.v1 NES profile")
    executable = mame.resolve()
    if not executable.is_file():
        raise StudioProjectError(f"MAME executable does not exist: {executable}")
    verification = verify_studio_project(path)
    if verification["status"] != "current":
        raise StudioProjectError("Studio artifact is stale; build it again before the playtest")
    artifact = Path(verification["artifact"]["absolute_path"]).resolve()
    source = artifact.read_bytes()
    # Bank 3's game start: LDA $21 / AND #$C0 / CMP #$C0, then LDX $3C.
    shell = 16 + 3 * 0x8000 - 0x8000
    if source[shell + DORAEMON_START_BUTTONS_READ:shell + DORAEMON_START_BUTTONS_READ + 6] \
            != bytes((0xA5, 0x21, 0x29, 0xC0, 0xC9, 0xC0)) \
            or source[shell + DORAEMON_START_CHAPTER_READ:shell + DORAEMON_START_CHAPTER_READ + 2] != bytes((0xA6, 0x3C)):
        raise StudioProjectError("Doraemon build does not match the guarded chapter start")
    bank = 16 + (world - 1) * 0x8000 - 0x8000
    point, writes, described = _doraemon_playtest_point(
        world, block, stage, room, underground,
        _doraemon_underground_rooms(source[16:16 + 0x8000]) if underground is not None else None)
    if writes == DORAEMON_UNDERGROUND_WRITES:
        for pc, expected in DORAEMON_UNDERGROUND_GUARDS.items():
            if source[bank + pc:bank + pc + 2] != bytes(expected):
                raise StudioProjectError("Doraemon build does not match the guarded manhole entry")
        writes_checked: tuple[int, ...] = ()
    else:
        writes_checked = writes
    # The stores the point answers: LDA #imm / STA zp in the world's bank.
    for pc in writes_checked:
        target = {0x831C: 0x5B, 0x8320: 0x5C, 0x8324: 0x75, 0x8328: 0x76, 0x8342: 0x29,
                  0x88B5: 0xA9, 0x830C: 0xDF, 0x8326: 0xDF, 0x8557: 0xDF}[pc]
        if source[bank + pc:bank + pc + 2] != bytes((0x85, target)):
            raise StudioProjectError("Doraemon build does not match the guarded world start")
    lua = Path(__file__).with_name("runtime") / "doraemon_mame_playtest.lua"
    if not lua.is_file():
        raise StudioProjectError(f"packaged Doraemon playtest script is missing: {lua}")
    plan = launch_image_plan(artifact, platform="nes", mame=executable, machine="nes")
    plan.update({
        "operation": "retrolarium.studio.doraemon-playtest",
        "backend": "mame-lua",
        "arguments": [
            *plan["arguments"], "-autoboot_delay", "0",
            "-autoboot_script", str(lua.resolve()),
        ],
        "environment": {
            "DORAEMON_PLAYTEST_WORLD": str(world),
            "DORAEMON_PLAYTEST_BUTTONS_READ": str(DORAEMON_START_BUTTONS_READ),
            "DORAEMON_PLAYTEST_CHAPTER_READ": str(DORAEMON_START_CHAPTER_READ),
            "DORAEMON_PLAYTEST_POINT": point,
            "DORAEMON_PLAYTEST_WRITES": ",".join(str(pc) for pc in writes),
        },
        "project_file": inspection["project_file"],
        "script": {"path": str(lua.resolve()), "sha256": sha256_file(lua)},
        "point": described or {"world": world},
    })
    return plan


def studio_doraemon_demo_plan(path: Path, *, mame: Path, record: Path | None = None) -> dict[str, Any]:
    """Loop World 1's attract demo of the current Doraemon build in MAME, or
    record a new one from the player's controller into ``record``."""

    from retrolarium_kit.games.doraemon.demo import CODEC as DEMO_CODEC, capacity

    inspection = load_studio_project(path)
    _require_declared_operation(inspection, "launch")
    if inspection["project"]["platform"] != "nes" or inspection["project"]["profile"] != "doraemon.v1":
        raise StudioProjectError("Doraemon demo playtest requires the doraemon.v1 NES profile")
    profile = packaged_game_profiles().get("doraemon")
    revision = next(
        (item for item in profile.revisions if item.sha256 == inspection["source"]["sha256"]), None
    )
    if revision is None:
        raise StudioProjectError("Doraemon demo playtest needs a known Doraemon source")
    layout = next(item for item in profile.resources_for(revision) if item.codec == DEMO_CODEC).parameters
    executable = mame.resolve()
    if not executable.is_file():
        raise StudioProjectError(f"MAME executable does not exist: {executable}")
    verification = verify_studio_project(path)
    if verification["status"] != "current":
        raise StudioProjectError("Studio artifact is stale; build it again before the demo")
    artifact = Path(verification["artifact"]["absolute_path"]).resolve()
    source = artifact.read_bytes()
    stream = layout["stream_cpu"]
    entry = 16 + layout["bank"] * 0x8000 + layout["entry_cpu"] - 0x8000
    reader = 16 + layout["bank"] * 0x8000 + layout["reader_cpu"] + 46 - 0x8000
    if source[entry:entry + 8] != bytes((0xA9, stream & 0xFF, 0x85, 0xA6, 0xA9, stream >> 8, 0x85, 0xA7)) \
            or source[reader:reader + 2] != bytes((0x85, 0x21)):
        raise StudioProjectError("Doraemon build does not match the guarded demo layout")
    lua = Path(__file__).with_name("runtime") / "doraemon_mame_demo.lua"
    if not lua.is_file():
        raise StudioProjectError(f"packaged Doraemon demo script is missing: {lua}")
    plan = launch_image_plan(artifact, platform="nes", mame=executable,
                             machine="nespal" if revision.revision_id == "pal" else "nes")
    recording = "" if record is None else str(record.resolve())
    plan.update({
        "operation": "retrolarium.studio.doraemon-demo",
        "backend": "mame-lua",
        "arguments": [
            *plan["arguments"], "-autoboot_delay", "0",
            "-autoboot_script", str(lua.resolve()),
        ],
        "environment": {
            "DORAEMON_DEMO_ENTRY": str(layout["entry_cpu"]),
            "DORAEMON_DEMO_READER": str(layout["reader_cpu"]),
            "DORAEMON_DEMO_STEPS": str(capacity(layout)),
            "DORAEMON_DEMO_RECORD": recording,
        },
        "project_file": inspection["project_file"],
        "script": {"path": str(lua.resolve()), "sha256": sha256_file(lua)},
        "demo": {"revision": revision.revision_id, "record": recording or None},
    })
    return plan


def studio_solomon_demo_plan(
    path: Path, *, mame: Path, record: Path | None = None,
) -> dict[str, Any]:
    """Loop the attract demo of the current build in MAME, or record a new one
    from the player's controller into ``record``."""

    inspection = load_studio_project(path)
    _require_declared_operation(inspection, "launch")
    if inspection["project"]["platform"] != "nes" \
            or inspection["project"]["profile"] != "solomons-key.v1":
        raise StudioProjectError("Solomon demo playtest requires the solomons-key.v1 NES profile")
    profile = packaged_game_profiles().get("solomons-key")
    revision = next(
        (item for item in profile.revisions if item.sha256 == inspection["source"]["sha256"]), None
    )
    if revision is None or revision.revision_id not in SOLOMON_DEMO_HOOKS:
        raise StudioProjectError("Solomon demo playtest needs the USA or Europe source")
    hooks = SOLOMON_DEMO_HOOKS[revision.revision_id]
    executable = mame.resolve()
    if not executable.is_file():
        raise StudioProjectError(f"MAME executable does not exist: {executable}")
    verification = verify_studio_project(path)
    if verification["status"] != "current":
        raise StudioProjectError("Studio artifact is stale; build it again before the demo")
    artifact = Path(verification["artifact"]["absolute_path"]).resolve()
    source = artifact.read_bytes()
    offset = 16 + hooks["run"] - 0x8000
    # An expanded build (solomon_expansion) runs the game from its second bank.
    if len(source) == 16 + 0x10000 + 0x8000 and ((source[6] >> 4) | (source[7] & 0xF0)) == 66:
        offset += 0x8000
    if source[offset:offset + len(SOLOMON_DEMO_RUN_GUARD)] != SOLOMON_DEMO_RUN_GUARD             or source[offset + 0x1A] != 0xBD or source[offset + 0x21] != 0xE0:
        raise StudioProjectError("Solomon build does not match the guarded demo layout")
    # The demo's step tables as this build has them (LDA durations,X and
    # CPX #count in the playback loop).
    durations = source[offset + 0x1B] | (source[offset + 0x1C] << 8)
    steps = source[offset + 0x22]
    lua = Path(__file__).with_name("runtime") / "solomon_mame_demo.lua"
    if not lua.is_file():
        raise StudioProjectError(f"packaged Solomon demo script is missing: {lua}")
    plan = launch_image_plan(
        artifact, platform="nes", mame=executable,
        machine="nespal" if revision.revision_id == "europe" else "nes",
    )
    recording = "" if record is None else str(record.resolve())
    plan.update({
        "operation": "retrolarium.studio.solomon-demo",
        "backend": "mame-lua",
        "arguments": [
            *plan["arguments"], "-autoboot_delay", "0",
            "-autoboot_script", str(lua.resolve()),
        ],
        "environment": {
            "SOLOMON_DEMO_RUN": str(hooks["run"]),
            "SOLOMON_DEMO_POST_GAME": str(hooks["post_game"]),
            "SOLOMON_DEMO_DURATIONS": str(durations),
            "SOLOMON_DEMO_STEPS": str(steps),
            "SOLOMON_DEMO_JOYPAD_RAW": str(0x0082),
            "SOLOMON_DEMO_INDEX": str(0x0081),
            "SOLOMON_DEMO_NMI_COUNTER": str(0x0022),
            "SOLOMON_DEMO_RECORD": recording,
        },
        "project_file": inspection["project_file"],
        "script": {"path": str(lua.resolve()), "sha256": sha256_file(lua)},
        "demo": {"revision": revision.revision_id, "record": recording or None},
    })
    return plan


def launch_studio_smoke(
    path: Path,
    *,
    mame: Path,
    output: Path | None = None,
    machine: str | None = None,
    seconds: int = 2,
    timeout: int = 60,
) -> dict[str, Any]:
    inspection = load_studio_project(path)
    _require_declared_operation(inspection, "launch")
    verification = verify_studio_project(path)
    if verification["status"] != "current":
        raise StudioProjectError("Studio artifact is stale; build it again before launch")
    root = Path(inspection["project_root"])
    return launch_image_smoke(
        Path(verification["artifact"]["absolute_path"]),
        platform=inspection["project"]["platform"],
        mame=mame,
        output=(output or root / "build" / "runs"),
        machine=machine,
        seconds=seconds,
        timeout=timeout,
    )


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


def create_flicky_studio_project(project_root: Path) -> dict[str, Any]:
    """Declare all five documents of an already imported Flicky snapshot."""

    root = project_root.resolve()
    project = root / "flicky-ue.rlj"
    if project.exists():
        raise StudioProjectError(f"Flicky Studio project already exists: {project}")
    try:
        validate_flicky_source_snapshot(root)
    except FlickyContentError as exc:
        raise StudioProjectError(str(exc)) from exc
    definitions = (
        ("levels", "Levels", "flicky.levels", "content", "level_layouts"),
        ("graphics", "Graphics", "flicky.graphics", "assets", "graphics_assets"),
        ("semantics", "Palettes", "flicky.semantics", "assets", "graphics_semantics"),
        ("text", "Text", "flicky.semantics", "content", "graphics_semantics"),
        ("animations", "Animations", "flicky.animations", "assets", "graphics_sequences"),
        ("sound", "Sound", "flicky.sound", "content", "z80_sound_banks"),
    )
    lines = [
        'schema = "retrolarium.project"',
        "schema_version = 1", "", "[project]",
        'id = "flicky-ue"', 'name = "Flicky (UE)"',
        'platform = "megadrive"', 'profile = "flicky-ue.v1"',
        'source_mode = "flicky-content"', "", "[source]",
        'manifest = "flicky-source/config/authoring/content_studios.json"',
        f'sha256 = "{FLICKY_SHA256}"', "", "[workspace]",
        'layout = "two-row"', 'startup_tab = "levels"',
    ]
    for identifier, title, kind, group, artifact in definitions:
        lines.extend((
            "", "[[workspace.tabs]]", f'id = "{identifier}"',
            f'title = "{title}"', f'kind = "{kind}"', f'group = "{group}"',
            f'codec = "{kind}.v1"',
            f'document = "flicky-source/{FLICKY_WORKSPACE_FILES[artifact]}"',
            ('capabilities = ["paint", "edit", "preview", "playtest", "navigate", "undo", "restore"]'
             if identifier == "levels" else
             'capabilities = ["paint", "navigate", "undo", "restore"]'
             if identifier == "graphics" else
             'capabilities = ["edit", "preview", "navigate", "undo", "restore"]'
             if identifier == "sound" else
             'capabilities = ["edit", "navigate", "undo", "restore"]'
             if identifier in {"semantics", "text", "animations"} else 'capabilities = []'),
        ))
        if identifier == "levels":
            lines.append('depends_on = ["graphics", "semantics", "animations"]')
        elif identifier == "graphics":
            lines.append('depends_on = ["semantics"]')
    lines.extend(("", "[operations]", 'validate = "project.validate"',
                  'build = "project.build"', 'verify = "project.verify"',
                  'launch = "project.launch"', ""))
    _atomic_write(project, "\n".join(lines))
    return load_studio_project(project)


def _tab_block(tab_id: str, title: str, kind: str, document: str, codec: str) -> str:
    return (
        "[[workspace.tabs]]\n"
        f'id = "{tab_id}"\n'
        f'title = "{title}"\n'
        f'kind = "{kind}"\n'
        'group = "content"\n'
        f'document = "{document}"\n'
        f'codec = "{codec}"\n'
        'capabilities = ["edit", "preview", "restore", "undo"]\n\n'
    )


def _project_source(inspection: Mapping[str, Any]) -> Path:
    """The project's unedited source image: its disassembly's recorded
    build, or a fresh one that must match the source identity."""

    if inspection["project"]["source_mode"] == "rom":
        return Path(inspection["source"]["rom_path"])
    if inspection["project"]["source_mode"] == "flicky-content":
        return Path(inspection["project_root"]) / FLICKY_SOURCE_DIRECTORY / FLICKY_ROM_NAME

    manifest_path = Path(inspection["source"]["disassembly_path"])
    record = manifest_path.parent / "build-result.json"
    try:
        artifact = json.loads(record.read_text(encoding="utf-8"))["artifact"]["path"]
        candidate = (manifest_path.parent / artifact).resolve()
        if sha256_file(candidate) == inspection["source"]["sha256"]:
            return candidate
    except (OSError, KeyError, TypeError, ValueError):
        pass
    built = build_project(manifest_path)
    source = (manifest_path.parent / built["artifact"]["path"]).resolve()
    if sha256_file(source) != inspection["source"]["sha256"]:
        raise StudioProjectError("the project's source does not rebuild exactly")
    return source


def _add_bundle_resources(path: Path, *, profile: str, codec: str) -> list[str]:
    """Give a project's resource bundle the resources newer kits add to its
    profile, decoded from the project's own source; edited resources stay."""

    from retrolarium_kit.games.catalog import extract_game

    project_file = path.resolve()
    inspection = load_studio_project(project_file)
    if inspection["project"]["profile"] != profile:
        raise StudioProjectError(f"this upgrade requires a {profile} project")
    tab = next((item for item in inspection["workspace"]["tabs"] if item.get("codec") == codec), None)
    if tab is None:
        raise StudioProjectError(f"the project has no {codec} tab")
    document_path = Path(tab["document_path"])
    document = json.loads(document_path.read_text(encoding="utf-8"))
    present = {item.get("id") for item in document.get("resources", [])}
    descriptor, temporary_name = tempfile.mkstemp(prefix=".bundle-upgrade-", suffix=".json", dir=document_path.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        extract_game(_project_source(inspection), temporary)
        fresh = json.loads(temporary.read_text(encoding="utf-8"))
    finally:
        temporary.unlink(missing_ok=True)
    added = [item["id"] for item in fresh["resources"] if item["id"] not in present]
    if not added:
        return []
    by_id = {item.get("id"): item for item in document["resources"]}
    document["resources"] = [by_id.get(item["id"], item) for item in fresh["resources"]]
    payload = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(prefix=".bundle-upgrade-", suffix=".json", dir=document_path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
        os.replace(temporary, document_path)
    finally:
        temporary.unlink(missing_ok=True)
    return added


def _add_document_tab(
    path: Path, *, profile: str, resource: str, document_name: str,
    tab_id: str, title: str, kind: str, codec: str,
) -> dict[str, Any]:
    """Extract one more document from a project's own source and give it a
    tab, unless the project has one with that codec already."""

    from retrolarium_kit.games.catalog import extract_game

    project_file = path.resolve()
    inspection = load_studio_project(project_file)
    if inspection["project"]["profile"] != profile:
        raise StudioProjectError(f"this upgrade requires a {profile} project")
    if any(tab.get("codec") == codec for tab in inspection["workspace"]["tabs"]):
        return {"status": "current", "project_file": str(project_file), "added_documents": []}
    root = Path(inspection["project_root"])
    document = root / "documents" / document_name
    if document.exists():
        raise StudioProjectError(f"{document} exists but no tab uses it; move it away first")
    extract_game(_project_source(inspection), document, resource=resource)
    block = _tab_block(tab_id, title, kind, f"documents/{document_name}", codec)
    payload = project_file.read_text(encoding="utf-8")
    marker = "\n[operations]"
    at = payload.find(marker)
    upgraded = payload + "\n" + block if at < 0 else payload[:at + 1] + block + payload[at + 1:]
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".project-upgrade-", suffix=".rlj", dir=project_file.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(upgraded)
        os.replace(temporary, project_file)
    finally:
        temporary.unlink(missing_ok=True)
    try:
        load_studio_project(project_file)
    except StudioProjectError:
        project_file.write_text(payload, encoding="utf-8", newline="")
        document.unlink(missing_ok=True)
        raise
    return {"status": "upgraded", "project_file": str(project_file),
            "added_documents": [f"documents/{document_name}"]}


def upgrade_solomon_studio_project(path: Path) -> dict[str, Any]:
    """Add the screen text (documents/text.json) to a Solomon's Key project."""
    return _add_document_tab(
        path, profile="solomons-key.v1", resource="text", document_name="text.json",
        tab_id="text", title="Text", kind="solomon.text", codec="solomon.text@1",
    )


def upgrade_smb_studio_project(path: Path) -> dict[str, Any]:
    """Add what newer kits edit to a Super Mario Bros. project: the title
    demo (documents/demo.json) and new bundle resources (the sound effect
    tables)."""
    result = _add_document_tab(
        path, profile="smb1.v1", resource="demo", document_name="demo.json",
        tab_id="demo", title="Demo", kind="smb.demo", codec="smb.demo@1",
    )
    added = _add_bundle_resources(path, profile="smb1.v1", codec="smb.world.v1")
    if added:
        result["status"] = "upgraded"
    result["added_resources"] = added
    return result


def upgrade_castle_studio_project(path: Path) -> dict[str, Any]:
    """Add the documents newer kits edit to a Castle Excellent project: the
    title demo (documents/demo.json) and the screens (documents/screens.json)."""
    added: list[str] = []
    for resource, name, tab_id, title, kind, codec in (
        ("title-demo", "demo.json", "demo", "Demo", "castle.demo", "castle.demo@1"),
        ("screens", "screens.json", "screens", "Screens", "castle.screens", "castle.screens@1"),
    ):
        result = _add_document_tab(
            path, profile="castle-excellent.v1", resource=resource, document_name=name,
            tab_id=tab_id, title=title, kind=kind, codec=codec,
        )
        added += result["added_documents"]
    return {"status": "upgraded" if added else "current", "project_file": str(path.resolve()),
            "added_documents": added}


def upgrade_doraemon_studio_project(path: Path) -> dict[str, Any]:
    """Add what newer kits edit to a Doraemon project: the shell text
    (documents/text.json) and new bundle resources (the shell palettes)."""
    documents: list[str] = []
    for resource, name, tab_id, title, kind, codec in (
        ("text", "text.json", "text", "Text", "doraemon.text", "doraemon.text@1"),
        ("music", "music.json", "sound", "Sound", "doraemon.music", "doraemon.music@1"),
        ("demo", "demo.json", "demo", "Demo", "doraemon.demo", "doraemon.demo@1"),
    ):
        documents += _add_document_tab(
            path, profile="doraemon.v1", resource=resource, document_name=name,
            tab_id=tab_id, title=title, kind=kind, codec=codec,
        )["added_documents"]
    added = _add_bundle_resources(path, profile="doraemon.v1", codec="doraemon.hierarchy.v1")
    return {"status": "upgraded" if added or documents else "current",
            "project_file": str(path.resolve()), "added_documents": documents,
            "added_resources": added}


def upgrade_pacman_studio_project(path: Path) -> dict[str, Any]:
    """Add the attract demo (documents/demo.json) to a Pac-Man project."""
    return _add_document_tab(
        path, profile="pacman.v1", resource="attract-demo", document_name="demo.json",
        tab_id="demo", title="Demo", kind="pacman.demo", codec="pacman.demo@1",
    )


def upgrade_flicky_studio_project(
    path: Path, *, output: Path | None = None,
) -> dict[str, Any]:
    """Upgrade capabilities, pinned bonus placements and attract demos without
    reimporting."""

    source_path = path.resolve()
    destination = (output or source_path).resolve()
    if source_path.suffix.lower() != PROJECT_SUFFIX \
            or destination.suffix.lower() != PROJECT_SUFFIX \
            or destination.parent != source_path.parent:
        raise StudioProjectError(
            "Flicky upgrade output must be a .rlj beside the source project"
        )
    inspection = load_studio_project(source_path)
    project = inspection["project"]
    if project["source_mode"] != "flicky-content" \
            or project["platform"] != "megadrive" \
            or project["profile"] != "flicky-ue.v1":
        raise StudioProjectError("Flicky upgrade requires canonical flicky-ue.v1")
    project_root = Path(inspection["project_root"])
    source_root = project_root / FLICKY_SOURCE_DIRECTORY
    snapshot_record = source_root / FLICKY_SNAPSHOT_RECORD
    objects_source = source_root / "src/game/bonus/objects.s"
    skipped_documents = (["levels.bonus_placements"]
                         if not snapshot_record.is_file() and not objects_source.exists()
                         else [])
    bonus_document_path: Path | None = None
    bonus_document_payload: str | None = None
    title_document_path: Path | None = None
    title_document_payload: str | None = None
    if snapshot_record.is_file():
        try:
            validate_flicky_source_snapshot(project_root)
            levels_tab = next(tab for tab in inspection["workspace"]["tabs"]
                              if tab["kind"] == "flicky.levels")
            levels_path = Path(levels_tab["document_path"])
            levels = json.loads(levels_path.read_text(encoding="utf-8"))
            if not isinstance(levels, dict):
                raise ValueError("Flicky Levels document is not an object")
            if "bonus_placements" not in levels or "attract_demos" not in levels:
                if "bonus_placements" not in levels:
                    levels["bonus_placements"] = export_bonus_placements(source_root)
                    validate_bonus_placements(levels, source_root)
                if "attract_demos" not in levels:
                    levels["attract_demos"] = export_attract_demos(source_root)
                    validate_attract_demos(levels, source_root)
                bonus_document_path = levels_path
                bonus_document_payload = json.dumps(levels, indent=2) + "\n"
            # The title sprites' positions join the semantics document.
            semantics_tab = next(tab for tab in inspection["workspace"]["tabs"]
                                 if tab["kind"] == "flicky.semantics")
            semantics_path = Path(semantics_tab["document_path"])
            semantics = json.loads(semantics_path.read_text(encoding="utf-8"))
            if isinstance(semantics, dict) and "title_layout" not in semantics:
                semantics["title_layout"] = export_title_layout(source_root)
                validate_title_layout(semantics, source_root)
                title_document_path = semantics_path
                title_document_payload = json.dumps(semantics, indent=2) + "\n"
        except (FlickyContentError, OSError, UnicodeError, ValueError) as exc:
            raise StudioProjectError(
                f"cannot safely upgrade Flicky bonus placements: {exc}"
            ) from exc
    elif objects_source.exists():
        raise StudioProjectError(
            "cannot upgrade Flicky bonus placements without a pinned source snapshot"
        )
    payload = source_path.read_bytes().decode("utf-8")
    parsed = tomllib.loads(payload)
    raw_tabs = parsed["workspace"]["tabs"]
    markers = list(re.finditer(
        r"(?m)^\[\[workspace\.tabs\]\][ \t]*(?:#.*)?\r?$", payload
    ))
    if len(markers) != len(raw_tabs):
        raise StudioProjectError("cannot identify Flicky tab blocks without rewriting TOML")
    updates: list[tuple[int, int, str]] = []
    requirements = {"flicky.levels.v1": "playtest",
                    "flicky.sound.v1": "preview"}
    for index, tab in enumerate(raw_tabs):
        capability = requirements.get(tab.get("codec"))
        if capability is None or capability in tab.get("capabilities", []):
            continue
        start = markers[index].end()
        end = markers[index + 1].start() if index + 1 < len(markers) \
            else len(payload)
        block = payload[start:end]
        line = re.search(
            r"(?m)^(?P<prefix>[ \t]*capabilities[ \t]*=[ \t]*)"
            r"\[(?P<items>[^\]\r\n]*)\](?P<suffix>[^\r\n]*)(?P<cr>\r?)$", block,
        )
        if line is None or not isinstance(tab.get("capabilities"), list):
            raise StudioProjectError(
                f"cannot safely upgrade capabilities for {tab.get('codec')}"
            )
        values = [*tab["capabilities"], capability]
        replacement = (line.group("prefix") + "["
                       + ", ".join(json.dumps(value) for value in values)
                       + "]" + line.group("suffix") + line.group("cr"))
        updates.append((start + line.start(), start + line.end(), replacement))
    if not any(tab.get("id") == "text" for tab in raw_tabs):
        semantics_index = next(
            index for index, tab in enumerate(raw_tabs)
            if tab.get("kind") == "flicky.semantics"
        )
        semantics = raw_tabs[semantics_index]
        start = markers[semantics_index].end()
        end = markers[semantics_index + 1].start() \
            if semantics_index + 1 < len(markers) else len(payload)
        block = payload[start:end]
        old_title = re.search(r'(?m)^(?P<prefix>[ \t]*title[ \t]*=[ \t]*)"Palettes & Text"', block)
        if old_title:
            title_start = start + old_title.start()
            title_end = start + old_title.end()
            updates.append((title_start, title_end,
                            old_title.group("prefix") + '"Palettes"'))
        text_block = (
            '[[workspace.tabs]]\n'
            'id = "text"\n'
            'title = "Text"\n'
            'kind = "flicky.semantics"\n'
            'group = "content"\n'
            f'document = {json.dumps(semantics["document"])}\n'
            'codec = "flicky.semantics.v1"\n'
            'capabilities = ["edit", "navigate", "undo", "restore"]\n\n'
        )
        if "\r\n" in payload:
            text_block = text_block.replace("\n", "\r\n")
        updates.append((end, end, text_block))
    upgraded = payload
    for start, end, replacement in sorted(updates, key=lambda item: item[0], reverse=True):
        upgraded = upgraded[:start] + replacement + upgraded[end:]
    upgraded_documents = (["levels"] if bonus_document_payload else []) \
        + (["semantics"] if title_document_payload else [])
    if destination == source_path and not updates:
        if not upgraded_documents:
            return {"status": "current", "project_file": str(destination),
                    "inspection": inspection, "upgraded_documents": [],
                    "skipped_documents": skipped_documents}
        if bonus_document_path is not None and bonus_document_payload is not None:
            _atomic_write(bonus_document_path, bonus_document_payload)
        if title_document_path is not None and title_document_payload is not None:
            _atomic_write(title_document_path, title_document_payload)
        return {"status": "upgraded", "project_file": str(destination),
                "inspection": load_studio_project(destination),
                "upgraded_documents": upgraded_documents,
                "skipped_documents": skipped_documents}
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".flicky-upgrade-", suffix=".rlj", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(upgraded)
        load_studio_project(temporary)
        if bonus_document_path is not None and bonus_document_payload is not None:
            _atomic_write(bonus_document_path, bonus_document_payload)
        if title_document_path is not None and title_document_payload is not None:
            _atomic_write(title_document_path, title_document_payload)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return {"status": "upgraded" if updates or upgraded_documents else "current",
            "project_file": str(destination),
            "upgraded_documents": upgraded_documents,
            "skipped_documents": skipped_documents,
            "inspection": load_studio_project(destination)}


def create_studio_project(
    manifest_path: Path,
    output_path: Path,
    *,
    project_id: str,
    name: str,
    profile: str,
    source_mode: str = "source-project",
    resource_document: Path | None = None,
    resource_documents: Sequence[Path] | None = None,
) -> dict[str, Any]:
    """Create a minimal Code & Analysis workspace next to a disassembly."""

    output = output_path.resolve()
    if output.suffix.lower() != PROJECT_SUFFIX:
        raise StudioProjectError(f"output must use the {PROJECT_SUFFIX} extension")
    project_root = output.parent
    try:
        manifest_relative = manifest_path.resolve().relative_to(project_root.resolve())
    except ValueError as exc:
        raise StudioProjectError(
            "disassembly manifest must be inside the Studio project directory"
        ) from exc
    relative_manifest, resolved_manifest = _project_path(
        project_root, manifest_relative.as_posix(), "source.disassembly"
    )
    try:
        manifest = load_disassembly_manifest(resolved_manifest)
    except ContractError as exc:
        raise StudioProjectError(str(exc)) from exc
    schema = _string(manifest.get("schema"), "source manifest schema")
    schema_platforms = {
        "retrolarium.nes.disassembly": "nes",
        "retrolarium.megadrive.disassembly": "megadrive",
        "retrolarium.snes.disassembly": "snes",
    }
    if schema not in schema_platforms:
        raise StudioProjectError(f"unsupported disassembly schema {schema!r}")
    input_record = _mapping(manifest.get("input"), "source manifest input")
    digest = _string(input_record.get("sha256"), "source manifest input.sha256")
    _identifier(project_id, "project.id")
    _string(name, "project.name")
    _identifier(profile, "project.profile")
    _choice(source_mode, "project.source_mode", SOURCE_MODES)
    if source_mode == "flicky-content":
        raise StudioProjectError(
            "Flicky content projects must be created with create_flicky_studio_project"
        )

    codec = f"{schema}@1"
    tabs: list[dict[str, object]] = []
    if resource_document is not None and resource_documents is not None:
        raise StudioProjectError(
            "resource_document and resource_documents are mutually exclusive"
        )
    selected_documents = (
        tuple(resource_documents)
        if resource_documents is not None
        else (() if resource_document is None else (resource_document,))
    )
    if resource_documents is not None and not selected_documents:
        raise StudioProjectError("resource_documents must not be empty")
    selected_schemas: set[str] = set()
    for resource_document in selected_documents:
        try:
            document_relative = resource_document.resolve().relative_to(
                project_root.resolve()
            )
        except ValueError as exc:
            raise StudioProjectError(
                "resource document must be inside the Studio project directory"
            ) from exc
        portable_document, document_path = _project_path(
            project_root, document_relative.as_posix(), "resource_document"
        )
        try:
            resource_value = json.loads(document_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise StudioProjectError(f"cannot read resource document: {exc}") from exc
        resource_root = _mapping(resource_value, "resource document")
        document_schema = resource_root.get("schema")
        if document_schema in {
            "retrolarium.solomon-sprite-frames",
            "retrolarium.solomon-animations",
        } and selected_schemas.intersection({
            "retrolarium.solomon-sprite-frames",
            "retrolarium.solomon-animations",
        }):
            raise StudioProjectError(
                "Solomon sprite frames and animations cannot own the same ROM bytes"
            )
        if isinstance(document_schema, str):
            selected_schemas.add(document_schema)
        expected_version = 3 if document_schema == "retrolarium.solomon-rooms" else 1
        if (document_schema not in _AUTHORING_SCHEMAS
                or resource_root.get("schema_version") != expected_version
                or resource_root.get("source_sha256") != digest):
            raise StudioProjectError(
                "resource document schema or source identity does not match the disassembly"
            )
        if document_schema == "retrolarium.castle-levels":
            rooms = resource_root.get("rooms")
            if not isinstance(rooms, list) or not rooms:
                raise StudioProjectError(
                    "Castle resource document rooms must be a non-empty array"
                )
            resources = [{
                "id": "rooms",
                "kind": "castle",
                "codec": "castle.prefix-rooms@1",
            }]
        elif document_schema == "retrolarium.solomon-rooms":
            if profile != "solomons-key.v1" \
                    or resource_root.get("game") != "solomons-key" \
                    or resource_root.get("revision") not in SOLOMON_REVISIONS \
                    or not isinstance(resource_root.get("rooms"), list) \
                    or len(resource_root["rooms"]) != 53:
                raise StudioProjectError(
                    "Solomon rooms require the USA/Europe solomons-key.v1 profile"
                )
            resources = [{
                "id": "rooms", "kind": "solomon", "codec": "solomon.rooms@3",
            }, {
                "id": "mirrors", "kind": "solomon-mirrors",
                "codec": "solomon.mirrors@3",
            }, {
                "id": "special", "kind": "solomon-special",
                "codec": "solomon.special@3",
            }]
        elif document_schema == "retrolarium.solomon-room-art":
            if profile != "solomons-key.v1" \
                    or resource_root.get("game") != "solomons-key" \
                    or resource_root.get("revision") not in SOLOMON_REVISIONS \
                    or not isinstance(resource_root.get("room_group_colors"), list) \
                    or len(resource_root["room_group_colors"]) != 14 \
                    or not isinstance(resource_root.get("room_map_patterns"), list) \
                    or len(resource_root["room_map_patterns"]) != 58:
                raise StudioProjectError(
                    "Solomon room art requires 14 colors and 58 RoomMap records"
                )
            resources = [{
                "id": "room-art", "kind": "solomon-art",
                "codec": "solomon.room-art@1",
            }]
        elif document_schema == "retrolarium.solomon-sprite-frames":
            if profile != "solomons-key.v1" \
                    or resource_root.get("game") != "solomons-key" \
                    or resource_root.get("revision") not in SOLOMON_REVISIONS \
                    or not isinstance(resource_root.get("frames"), list) \
                    or len(resource_root["frames"]) != 28:
                raise StudioProjectError(
                    "Solomon sprite frames require 28 shared USA/Europe records"
                )
            resources = [{
                "id": "sprite-frames", "kind": "solomon-sprites",
                "codec": "solomon.sprite-frames@1",
            }]
        elif document_schema == "retrolarium.solomon-animations":
            if profile != "solomons-key.v1" \
                    or resource_root.get("game") != "solomons-key" \
                    or resource_root.get("revision") not in SOLOMON_REVISIONS \
                    or not isinstance(resource_root.get("group_pointers"), list) \
                    or len(resource_root["group_pointers"]) != 33 \
                    or not isinstance(resource_root.get("descriptors"), list) \
                    or len(resource_root["descriptors"]) != 340 \
                    or not isinstance(resource_root.get("selectors"), list) \
                    or len(resource_root["selectors"]) != 4 \
                    or not isinstance(resource_root.get("frames"), list) \
                    or len(resource_root["frames"]) != 275:
                raise StudioProjectError(
                    "Solomon animations require the full USA/Europe object tables"
                )
            resources = [{
                "id": "animations", "kind": "solomon-animations",
                "codec": "solomon.animations@1",
            }]
        elif document_schema == "retrolarium.solomon-audio":
            if (
                profile != "solomons-key.v1"
                or resource_root.get("game") != "solomons-key"
                or resource_root.get("revision") not in SOLOMON_REVISIONS
                or not isinstance(resource_root.get("effects"), list)
                or len(resource_root["effects"]) != 26
                or not isinstance(resource_root.get("envelopes"), list)
                or len(resource_root["envelopes"]) != 8
            ):
                raise StudioProjectError(
                    "Solomon audio requires the USA/Europe solomons-key.v1 profile"
                )
            resources = [{
                "id": "audio", "kind": "solomon-audio",
                "codec": "solomon.audio@1",
            }]
        elif document_schema == "retrolarium.solomon-presentation":
            if (
                profile != "solomons-key.v1"
                or resource_root.get("game") != "solomons-key"
                or resource_root.get("revision") not in SOLOMON_REVISIONS
                or not isinstance(resource_root.get("streams"), list)
                or len(resource_root["streams"]) != 2
                or not isinstance(resource_root.get("demo_steps"), list)
                or not 1 <= len(resource_root["demo_steps"]) <= 93
            ):
                raise StudioProjectError(
                    "Solomon presentation requires USA/Europe title streams "
                    "and 1-93 demo input steps"
                )
            resources = [{
                "id": "screens", "kind": "solomon-title",
                "codec": "solomon.screens@1",
            }, {
                "id": "demo", "kind": "solomon-demo",
                "codec": "solomon.presentation@1",
            }]
        elif document_schema == "retrolarium.castle-screens":
            if profile != "castle-excellent.v1" or resource_root.get("game") != "castle-excellent" \
                    or not isinstance(resource_root.get("screens"), list):
                raise StudioProjectError("Castle screens require the castle-excellent.v1 profile")
            resources = [{"id": "screens", "kind": "castle-screens", "codec": "castle.screens@1"}]
        elif document_schema == "retrolarium.castle-demo":
            if profile != "castle-excellent.v1" or resource_root.get("game") != "castle-excellent" \
                    or not isinstance(resource_root.get("steps"), list):
                raise StudioProjectError("Castle demo requires the castle-excellent.v1 profile")
            resources = [{"id": "title-demo", "kind": "castle-demo", "codec": "castle.demo@1"}]
        elif document_schema == "retrolarium.doraemon-demo":
            if profile != "doraemon.v1" or resource_root.get("game") != "doraemon" \
                    or not isinstance(resource_root.get("steps"), list):
                raise StudioProjectError("Doraemon demo requires the doraemon.v1 profile")
            resources = [{"id": "demo", "kind": "doraemon-demo", "codec": "doraemon.demo@1"}]
        elif document_schema == "retrolarium.doraemon-music":
            if profile != "doraemon.v1" or resource_root.get("game") != "doraemon" \
                    or not isinstance(resource_root.get("drivers"), list):
                raise StudioProjectError("Doraemon music requires the doraemon.v1 profile")
            resources = [{"id": "music", "kind": "doraemon-music", "codec": "doraemon.music@1"}]
        elif document_schema == "retrolarium.doraemon-text":
            if profile != "doraemon.v1" or resource_root.get("game") != "doraemon"                     or not isinstance(resource_root.get("ending_credit_rows"), list):
                raise StudioProjectError("Doraemon text requires the doraemon.v1 profile")
            resources = [{"id": "text", "kind": "doraemon-text", "codec": "doraemon.text@1"}]
        elif document_schema == "retrolarium.pacman-demo":
            if profile != "pacman.v1" or resource_root.get("game") != "pacman"                     or not isinstance(resource_root.get("steps"), list):
                raise StudioProjectError("Pac-Man demo requires the pacman.v1 profile")
            resources = [{"id": "attract-demo", "kind": "pacman-demo", "codec": "pacman.demo@1"}]
        elif document_schema == "retrolarium.smb-demo":
            if profile != "smb1.v1" or resource_root.get("game") != "smb1" \
                    or not isinstance(resource_root.get("steps"), list):
                raise StudioProjectError("SMB demo requires the smb1.v1 profile")
            resources = [{"id": "demo", "kind": "smb-demo", "codec": "smb.demo@1"}]
        elif document_schema == "retrolarium.solomon-text":
            if (
                profile != "solomons-key.v1"
                or resource_root.get("game") != "solomons-key"
                or resource_root.get("revision") not in SOLOMON_REVISIONS
                or not isinstance(resource_root.get("static_programs"), list)
                or len(resource_root["static_programs"]) != 18
                or not isinstance(resource_root.get("ending_messages"), list)
                or len(resource_root["ending_messages"]) != 10
            ):
                raise StudioProjectError(
                    "Solomon text requires the USA/Europe static PPU programs "
                    "and ten ending messages"
                )
            resources = [{
                "id": "text", "kind": "solomon-text",
                "codec": "solomon.text@1",
            }]
        else:
            resources = resource_root.get("resources")
            if not isinstance(resources, list) or not resources:
                raise StudioProjectError(
                    "resource document resources must be a non-empty array"
                )
        kinds: dict[str, set[str]] = {}
        has_packet_screen_text = False
        for index, item in enumerate(resources):
            resource = _mapping(item, f"resource document resources[{index}]")
            kind = _string(resource.get("kind"), f"resource document resources[{index}].kind")
            resource_codec = _string(
                resource.get("codec"), f"resource document resources[{index}].codec"
            )
            kinds.setdefault(kind, set()).add(resource_codec)
            if (
                kind == "screen"
                and resource_codec == "nes.ppu-screen-packets.v1"
                and isinstance(resource.get("text_fields"), list)
                and bool(resource["text_fields"])
            ):
                has_packet_screen_text = True
        if kinds.get("screen") == {"pacman.screen-collection.v1"}:
            kinds["text"] = {"pacman.screen-collection.v1"}
        elif has_packet_screen_text:
            kinds.setdefault("text", set()).add("nes.ppu-screen-packets.v1")
        templates = (
            ("solomon", "rooms", "Rooms & Objects", "solomon.rooms", "content",
             "solomon.rooms@3", ["edit", "preview", "restore", "undo"]),
            ("solomon-mirrors", "mirrors", "Spawn Mirrors", "solomon.mirrors",
             "content", "solomon.mirrors@3",
             ["edit", "restore", "undo"]),
            ("solomon-special", "special", "Special Rooms", "solomon.special",
             "content", "solomon.special@3",
             ["edit", "preview", "restore", "undo"]),
            ("solomon-art", "room-art", "Room Art", "solomon.room-art",
             "assets", "solomon.room-art@1",
             ["edit", "preview", "restore", "undo"]),
            ("solomon-sprites", "sprite-frames", "Sprite Frames",
             "solomon.sprite-frames", "assets", "solomon.sprite-frames@1",
             ["edit", "preview", "restore", "undo"]),
            ("solomon-animations", "animations", "Animations & Mappings",
             "solomon.animations", "assets", "solomon.animations@1",
             ["edit", "preview", "restore", "undo"]),
            ("solomon-audio", "audio", "Music & Sounds", "solomon.audio",
             "content", "solomon.audio@1",
             ["edit", "piano-roll", "preview", "wav-preview", "restore", "undo"]),
            ("solomon-title", "screens", "Screens", "solomon.screens",
             "content", "solomon.screens@1",
             ["edit", "preview", "restore", "undo"]),
            ("solomon-demo", "demo", "Presentation", "solomon.presentation",
             "content", "solomon.presentation@1",
             ["edit", "preview", "restore", "undo"]),
            ("smb-demo", "demo", "Demo", "smb.demo",
             "content", "smb.demo@1",
             ["edit", "preview", "restore", "undo"]),
            ("castle-demo", "demo", "Demo", "castle.demo",
             "content", "castle.demo@1",
             ["edit", "preview", "restore", "undo"]),
            ("castle-screens", "screens", "Screens", "castle.screens",
             "content", "castle.screens@1",
             ["edit", "preview", "restore", "undo"]),
            ("doraemon-demo", "demo", "Demo", "doraemon.demo",
             "content", "doraemon.demo@1",
             ["edit", "preview", "restore", "undo"]),
            ("doraemon-music", "sound", "Sound", "doraemon.music",
             "content", "doraemon.music@1",
             ["edit", "piano-roll", "wav-preview", "restore", "undo"]),
            ("doraemon-text", "text", "Text", "doraemon.text",
             "content", "doraemon.text@1",
             ["edit", "preview", "restore", "undo"]),
            ("pacman-demo", "demo", "Demo", "pacman.demo",
             "content", "pacman.demo@1",
             ["edit", "preview", "restore", "undo"]),
            ("solomon-text", "text", "Text", "solomon.text",
             "content", "solomon.text@1",
             ["edit", "preview", "restore", "undo"]),
            ("castle", "rooms", "Rooms & Objects", "castle.rooms", "content",
             "castle.prefix-rooms@1", ["edit", "preview", "restore", "undo"]),
            ("world", "world", "World", "world.routing", "content",
             "smb.world.v1", ["edit", "restore", "undo"]),
            ("level", "maze", "Maze", "level.tile-grid", "content",
             "pacman.maze-rle.v1", ["paint", "eyedropper", "semantic-overlays", "undo"]),
            ("screen-stream", "world2-screens", "World 2 Screens",
             "doraemon.world2-screens", "content", "doraemon.world2-screens.v1",
             ["paint", "eyedropper", "preview", "restore", "undo"]),
            ("stage-sequence", "world2-routes", "World 2 Routes",
             "doraemon.world2-routes", "content", "doraemon.world2-stage.v1",
             ["paint", "eyedropper", "preview", "restore", "undo"]),
            ("screen", "screens", "Screens", "screen.tilemap", "content",
             "nes.nametable.v1", ["paint", "preview", "undo"]),
            ("audio", "music-sounds", "Sound", "audio.sequencer", "content",
             "pacman.sound-streams.v1",
             ["edit", "piano-roll", "midi-import", "wav-preview", "restore", "undo"]),
            ("audio-parameter", "sfx-parameters", "SFX Parameters",
             "data.fixed-byte-records", "content", "castle.sfx-parameters.v1",
             ["edit", "restore", "undo"]),
            ("text", "text", "Text", "text.fixed", "content",
             "nes.fixed-text-fields.v1", ["edit", "restore", "undo"]),
            ("graphics", "graphics", "Graphics", "graphics.tile-set", "assets",
             "nes.chr-2bpp.v1", ["pixel-paint", "restore", "undo"]),
            ("metatile", "metatiles", "Metatiles", "metatile.composer", "assets",
             "nes.metatile-2x2.v1", ["compose", "preview", "restore", "undo"]),
            ("palette", "palettes", "Palettes", "palette.set", "assets",
             "nes.palette-table.v1", ["paint", "preview", "undo"]),
            ("animation", "animations", "Animations & Mappings", "animation.mapping", "assets",
             "pacman.actor-mapping.v1", ["paint", "preview", "restore", "undo"]),
        )
        for kind, tab_id, title, editor_kind, group, expected_codec, capabilities in templates:
            if kind not in kinds:
                continue
            selected_codec = expected_codec
            additional_tab = None
            if kind == "castle" and profile == "castle-excellent.v1" \
                    and resource_root.get("game") == "castle-excellent" \
                    and resource_root.get("revision") in {
                        "castle-excellent-japan-hsp-05", "castlequest-usa"
                    }:
                capabilities = ["edit", "preview", "playtest", "restore", "undo"]
            elif kind == "solomon" and profile == "solomons-key.v1" \
                    and resource_root.get("game") == "solomons-key" \
                    and resource_root.get("revision") in SOLOMON_REVISIONS:
                capabilities = ["edit", "preview", "playtest", "restore", "undo"]
            elif kind == "level" and kinds[kind] == {"smb.levels.v1"}:
                tab_id = "levels"
                title = "Levels"
                editor_kind = "level.procedural"
                selected_codec = "smb.levels.v1"
                capabilities = [
                    "edit", "preview", "restore", "semantic-overlays", "undo"
                ]
                if (
                    resource_root.get("game"), resource_root.get("revision")
                ) == ("smb1", "ju"):
                    capabilities.insert(1, "playtest")
            elif kind == "level" and kinds[kind] == {"doraemon.hierarchy.v1"}:
                if (profile != "doraemon.v1"
                        or resource_root.get("game") != "doraemon"
                        or resource_root.get("revision") not in {"original", "rev_a"}
                        or {item.get("world") for item in resources
                            if isinstance(item, dict) and item.get("kind") == "level"}
                        != {"world1", "world3"}):
                    raise StudioProjectError(
                        "Doraemon levels require both World 1 and World 3 hierarchies"
                    )
                tab_id = "levels"
                title = "Levels"
                editor_kind = "doraemon.levels"
                selected_codec = "doraemon.hierarchy.v1"
                capabilities = ["paint", "eyedropper", "preview", "restore", "undo"]
            elif kind == "audio" and "smb.apu-music.v1" in kinds[kind] \
                    and kinds[kind].issubset({
                        "smb.apu-music.v1", "smb.apu-envelope.v1", "smb.sfx-tables.v1",
                        "fds.synthesis.v1", "fds.music.v1",
                    }):
                selected_codec = "smb.apu-music.v1"
                capabilities = [
                    "edit", "midi-import", "restore", "undo", "wav-preview"
                ]
                fds_codec = (
                    "fds.music.v1"
                    if "fds.music.v1" in kinds[kind]
                    else "fds.synthesis.v1"
                )
                if fds_codec in kinds[kind]:
                    additional_tab = {
                        "id": "synthesis",
                        "title": "FDS Synthesis",
                        "kind": "audio.synthesis",
                        "group": "content",
                        "document": portable_document,
                        "codec": fds_codec,
                        "capabilities": ["edit", "restore", "undo"],
                    }
            elif kind == "audio" and kinds[kind] == {"castle.mml-music.v1"}:
                title = "Music & Sounds"
                editor_kind = "castle.mml-music"
                selected_codec = "castle.mml-music.v1"
                capabilities = [
                    "edit", "piano-roll", "wav-preview", "restore", "undo"
                ]
            elif kind == "audio" and (
                    "fds.synthesis.v1" in kinds[kind]
                    or "fds.music.v1" in kinds[kind]) \
                    and kinds[kind].issubset({
                        "fds.synthesis.v1", "fds.music.v1",
                        "smb.apu-envelope.v1", "smb.sfx-tables.v1"
                    }):
                tab_id = "synthesis"
                title = "FDS Synthesis"
                editor_kind = "audio.synthesis"
                selected_codec = (
                    "fds.music.v1"
                    if "fds.music.v1" in kinds[kind]
                    else "fds.synthesis.v1"
                )
                capabilities = ["edit", "restore", "undo"]
            elif kind in ("screen", "text") \
                    and kinds[kind] == {"pacman.screen-collection.v1"}:
                selected_codec = "pacman.screen-collection.v1"
            elif kind == "text" and kinds[kind].issubset({
                "nes.fixed-text-fields.v1", "nes.ppu-screen-packets.v1"
            }):
                selected_codec = (
                    "nes.fixed-text-fields.v1"
                    if "nes.fixed-text-fields.v1" in kinds[kind]
                    else "nes.ppu-screen-packets.v1"
                )
            elif kind == "text" and kinds[kind] == {
                    "castle.sub-command-text.v1"}:
                tab_id = "screens-text"
                title = "Screens & Text"
                editor_kind = "data.fixed-byte-records"
                selected_codec = "castle.sub-command-text.v1"
                capabilities = ["edit", "restore", "undo"]
            elif kind == "screen" and kinds[kind].issubset({
                "nes.nametable.v1", "nes.ppu-screen-packets.v1"
            }):
                selected_codec = (
                    "nes.nametable.v1"
                    if "nes.nametable.v1" in kinds[kind]
                    else "nes.ppu-screen-packets.v1"
                )
            elif kind == "palette" and kinds[kind] == {"pacman.palette-table.v1"}:
                selected_codec = "pacman.palette-table.v1"
            elif kind == "palette" and kinds[kind].issubset({
                "nes.palette-table.v1", "nes.ppu-palette-packets.v1"
            }):
                selected_codec = (
                    "nes.palette-table.v1"
                    if "nes.palette-table.v1" in kinds[kind]
                    else "nes.ppu-palette-packets.v1"
                )
            elif kind == "animation" and kinds[kind] == {"smb.player-frames.v1"}:
                tab_id = "player-frames"
                title = "Animations"
                selected_codec = "smb.player-frames.v1"
                capabilities = ["edit", "preview", "restore", "undo"]
            elif kind == "animation" and kinds[kind] == {
                    "castle.metasprite-descriptors.v1"}:
                title = "Animations & Mappings"
                editor_kind = "castle.metasprites"
                selected_codec = "castle.metasprite-descriptors.v1"
                capabilities = ["edit", "preview", "restore", "undo"]
            elif kind == "metatile" and kinds[kind] == {
                    "castle.room-cell-visuals.v1"}:
                tab_id = "room-cells"
                title = "Room Cells"
                editor_kind = "castle.room-cells"
                selected_codec = "castle.room-cell-visuals.v1"
                capabilities = ["edit", "preview", "restore", "undo"]
            elif kind == "animation" and kinds[kind].issubset({
                    "doraemon.metasprites.v1", "doraemon.world2-metasprites.v1"}):
                if profile != "doraemon.v1" or resource_root.get("game") != "doraemon":
                    raise StudioProjectError("Doraemon metasprites require the Doraemon profile")
                tab_id = "animations"
                title = "Animations"
                editor_kind = "animation.mapping"
                selected_codec = "doraemon.metasprites.v1"
                capabilities = ["edit", "preview", "restore", "undo"]
            elif kind == "metatile" and kinds[kind] == {
                    "doraemon.world2-metatiles.v1"}:
                if profile != "doraemon.v1" or resource_root.get("game") != "doraemon":
                    raise StudioProjectError(
                        "World 2 metatiles require the Doraemon profile"
                    )
                tab_id = "world2-metatiles"
                title = "World 2 Metatiles"
                editor_kind = "doraemon.world2-metatiles"
                selected_codec = "doraemon.world2-metatiles.v1"
                capabilities = ["edit", "preview", "restore", "undo"]
            elif kinds[kind] != {expected_codec}:
                raise StudioProjectError(
                    f"resource document {kind} resources require codec {expected_codec!r}"
                )
            tabs.append(
                {
                    "id": tab_id,
                    "title": title,
                    "kind": editor_kind,
                    "group": group,
                    "document": portable_document,
                    "codec": selected_codec,
                    "capabilities": capabilities,
                }
            )
            if additional_tab is not None:
                tabs.append(additional_tab)
        available_ids = {str(tab["id"]) for tab in tabs}
        desired_dependencies = {
            "rooms": ("room-cells", "graphics", "palettes"),
            "maze": ("graphics",),
            "levels": ("graphics", "palettes"),
            "screens": ("graphics", "palettes"),
            "graphics": ("palettes",),
            "metatiles": ("graphics", "palettes"),
            "world2-metatiles": ("graphics", "palettes"),
            "world2-screens": ("world2-metatiles", "graphics", "palettes"),
            "world2-routes": (
                "world2-screens", "world2-metatiles", "graphics", "palettes"),
            "room-cells": ("graphics", "palettes"),
            "animations": ("graphics",),
            "player-frames": ("graphics", "palettes"),
        }
        for tab in tabs:
            requested_dependencies = desired_dependencies.get(str(tab["id"]), ())
            if tab["id"] == "rooms" and tab["codec"] == "solomon.rooms@3":
                requested_dependencies = (
                    "room-art", "sprite-frames", "animations", "graphics", "palettes"
                )
            elif tab["id"] == "room-art":
                requested_dependencies = ("graphics", "palettes")
            elif tab["id"] == "sprite-frames":
                requested_dependencies = ("graphics", "palettes")
            elif tab["id"] == "animations" \
                    and tab["codec"] == "solomon.animations@1":
                requested_dependencies = ("graphics", "palettes")
            if (tab["id"] == "animations" and tab["codec"] \
                    == "castle.metasprite-descriptors.v1"):
                requested_dependencies = ("graphics", "palettes")
            tab["depends_on"] = [
                dependency
                for dependency in requested_dependencies
                if dependency in available_ids
            ]
    tab_ids = [str(tab["id"]) for tab in tabs]
    if len(set(tab_ids)) != len(tab_ids):
        raise StudioProjectError(
            "resource documents produce duplicate Studio tab ids"
        )
    tabs.append(
        {
            "id": "code",
            "title": "Source",
            "kind": "code.analysis",
            "group": "tools",
            "codec": codec,
            "capabilities": ["navigate", "edit", "build", "run"],
        }
    )
    startup_tab = str(tabs[0]["id"])

    tab_blocks = []
    for tab in tabs:
        lines = [
            "[[workspace.tabs]]",
            f"id = {_toml_string(str(tab['id']))}",
            f"title = {_toml_string(str(tab['title']))}",
            f"kind = {_toml_string(str(tab['kind']))}",
            f"group = {_toml_string(str(tab['group']))}",
        ]
        if "document" in tab:
            lines.append(f"document = {_toml_string(str(tab['document']))}")
        lines.append(f"codec = {_toml_string(str(tab['codec']))}")
        capabilities = ", ".join(
            _toml_string(str(item)) for item in tab["capabilities"]  # type: ignore[union-attr]
        )
        lines.append(f"capabilities = [{capabilities}]")
        dependencies = tab.get("depends_on", [])
        if dependencies:
            rendered_dependencies = ", ".join(
                _toml_string(str(item)) for item in dependencies  # type: ignore[union-attr]
            )
            lines.append(f"depends_on = [{rendered_dependencies}]")
        tab_blocks.append("\n".join(lines))
    tabs_toml = "\n\n".join(tab_blocks)

    payload = f'''schema = "{SCHEMA}"
schema_version = {SCHEMA_VERSION}

[project]
id = {_toml_string(project_id)}
name = {_toml_string(name)}
platform = {_toml_string(schema_platforms[schema])}
profile = {_toml_string(profile)}
source_mode = {_toml_string(source_mode)}

[source]
disassembly = {_toml_string(relative_manifest)}
sha256 = {_toml_string(digest)}

[workspace]
layout = "auto"
startup_tab = {_toml_string(startup_tab)}

{tabs_toml}

[operations]
validate = "project.validate"
build = "project.build"
verify = "project.verify"
launch = "project.launch"
'''
    _atomic_write(output, payload)
    return load_studio_project(output)
