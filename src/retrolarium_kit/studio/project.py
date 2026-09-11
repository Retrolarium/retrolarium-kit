"""Human-editable Retrolarium Studio project contract."""

from __future__ import annotations

from collections.abc import Mapping
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import tempfile
import tomllib
from typing import Any

from .contracts import ContractError, load_disassembly_manifest


class StudioProjectError(ValueError):
    """Raised when a ``.retrolarium`` project violates the public contract."""


SCHEMA = "retrolarium.project"
SCHEMA_VERSION = 1
INSPECTION_SCHEMA = "retrolarium.project-inspection"
PROJECT_SUFFIX = ".retrolarium"

SOURCE_MODES = frozenset({"binary-profile", "source-project"})
LAYOUTS = frozenset({"auto", "single-row", "two-row"})
GROUPS = frozenset({"content", "assets", "tools"})
PLATFORMS = frozenset({"nes", "snes", "megadrive"})

EDITOR_KINDS = frozenset(
    {
        "animation.mapping",
        "audio.sequencer",
        "castle.rooms",
        "code.analysis",
        "graphics.tile-set",
        "level.procedural",
        "level.tile-grid",
        "palette.set",
        "screen.tilemap",
        "text.fixed",
        "world.routing",
    }
)

CODECS = frozenset(
    {
        "castle.object-stream@1",
        "castle.prefix-rooms@1",
        "nes.chr-2bpp.v1",
        "nes.nametable.v1",
        "nes.palette-table.v1",
        "pacman.maze-rle@1",
        "pacman.sound@1",
        "retrolarium.megadrive.disassembly@1",
        "retrolarium.nes.disassembly@1",
        "retrolarium.snes.disassembly@1",
        "smb.apu@1",
        "smb.area@1",
    }
)

CAPABILITIES = frozenset(
    {
        "changed-markers",
        "eyedropper",
        "midi-import",
        "navigate",
        "paint",
        "piano-roll",
        "pixel-paint",
        "playtest",
        "preview",
        "restore",
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
    root: Path, value: object, context: str, *, must_exist: bool = True
) -> tuple[str, Path]:
    source = _string(value, context)
    if "\\" in source:
        raise _error(context, "must use portable forward slashes")
    posix = PurePosixPath(source)
    windows = PureWindowsPath(source)
    if posix.is_absolute() or windows.is_absolute() or windows.drive:
        raise _error(context, "absolute paths are forbidden")
    if not posix.parts or any(part in {"", ".", ".."} for part in posix.parts):
        raise _error(context, "must be a normalized relative path without '..'")
    resolved_root = root.resolve()
    resolved = resolved_root.joinpath(*posix.parts).resolve()
    if not resolved.is_relative_to(resolved_root):
        raise _error(context, "path escapes the project directory")
    if must_exist and not resolved.is_file():
        raise _error(context, f"file does not exist: {source}")
    return posix.as_posix(), resolved


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
    _check_keys(
        source_table,
        "source",
        {"disassembly", "sha256"},
        {"disassembly", "sha256"},
    )
    disassembly, disassembly_path = _project_path(
        root, source_table["disassembly"], "source.disassembly"
    )
    source_sha256 = _string(source_table["sha256"], "source.sha256")
    if _SHA256.fullmatch(source_sha256) is None:
        raise _error("source.sha256", "must be 64 lowercase hexadecimal characters")
    try:
        manifest = load_disassembly_manifest(disassembly_path)
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
                root, tab["document"], f"{context}.document"
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
        operations[name] = _choice(value, f"operations.{name}", OPERATIONS)

    return {
        "schema": INSPECTION_SCHEMA,
        "schema_version": 1,
        "project_file": str(project_file),
        "project_root": str(root),
        "project": normalized_metadata,
        "source": {
            "disassembly": disassembly,
            "disassembly_path": str(disassembly_path),
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
    return {
        "schema": "retrolarium.project-validation",
        "schema_version": 1,
        "status": "valid",
        "project_file": inspection["project_file"],
        "project_id": inspection["project"]["id"],
        "enabled_tabs": [
            tab["id"] for tab in inspection["workspace"]["tabs"] if tab["enabled"]
        ],
    }


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


def create_studio_project(
    manifest_path: Path,
    output_path: Path,
    *,
    project_id: str,
    name: str,
    profile: str,
    source_mode: str = "source-project",
    resource_document: Path | None = None,
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

    codec = f"{schema}@1"
    tabs: list[dict[str, object]] = []
    if resource_document is not None:
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
        if (
            resource_root.get("schema") != "retrolarium.nes-resource-bundle"
            or resource_root.get("schema_version") != 1
            or resource_root.get("source_sha256") != digest
        ):
            raise StudioProjectError(
                "resource document schema or source identity does not match the disassembly"
            )
        resources = resource_root.get("resources")
        if not isinstance(resources, list) or not resources:
            raise StudioProjectError("resource document resources must be a non-empty array")
        kinds: dict[str, set[str]] = {}
        for index, item in enumerate(resources):
            resource = _mapping(item, f"resource document resources[{index}]")
            kind = _string(resource.get("kind"), f"resource document resources[{index}].kind")
            resource_codec = _string(
                resource.get("codec"), f"resource document resources[{index}].codec"
            )
            kinds.setdefault(kind, set()).add(resource_codec)
        templates = (
            ("screen", "screens", "Screens", "screen.tilemap", "content",
             "nes.nametable.v1", ["paint", "preview", "undo"]),
            ("graphics", "graphics", "Graphics", "graphics.tile-set", "assets",
             "nes.chr-2bpp.v1", ["pixel-paint", "restore", "undo"]),
            ("palette", "palettes", "Palettes", "palette.set", "assets",
             "nes.palette-table.v1", ["paint", "preview", "undo"]),
        )
        for kind, tab_id, title, editor_kind, group, expected_codec, capabilities in templates:
            if kind not in kinds:
                continue
            if kinds[kind] != {expected_codec}:
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
                    "codec": expected_codec,
                    "capabilities": capabilities,
                }
            )
    tabs.append(
        {
            "id": "code",
            "title": "Code & Analysis",
            "kind": "code.analysis",
            "group": "tools",
            "codec": codec,
            "capabilities": ["navigate"],
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
