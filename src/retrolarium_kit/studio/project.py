"""Human-editable Retrolarium Studio project contract."""

from __future__ import annotations

from collections.abc import Mapping
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import tempfile
import tomllib
from typing import Any, Sequence

from .contracts import ContractError, load_disassembly_manifest
from .games import (
    build_game,
    build_game_documents,
    validate_game_document_for_revision,
)
from .game_profiles import packaged_game_profiles
from .ines import load_ines
from .megadrive import load_megadrive
from .project import (
    build_project,
    launch_image_plan,
    launch_image_smoke,
    sha256_file,
    verify_project,
    write_document,
)
from .snes import load_snes


class StudioProjectError(ValueError):
    """Raised when a ``.rlj`` project violates the public contract."""


SCHEMA = "retrolarium.project"
SCHEMA_VERSION = 1
INSPECTION_SCHEMA = "retrolarium.project-inspection"
PROJECT_SUFFIX = ".rlj"

SOURCE_MODES = frozenset({"binary-profile", "source-project"})
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
        "text.fixed",
        "world.routing",
    }
)

CODECS = frozenset(
    {
        "castle.object-stream@1",
        "castle.prefix-rooms@1",
        "fds.synthesis.v1",
        "fds.music.v1",
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
     "retrolarium.solomon-presentation"}
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


def _studio_output(root: Path, project_id: str, platform: str) -> Path:
    extension = {"nes": ".nes", "megadrive": ".bin", "snes": ".sfc"}[platform]
    return root / "build" / f"{project_id}-edited{extension}"


def _validate_container(path: Path, platform: str) -> None:
    if platform == "nes":
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
) -> dict[str, Any]:
    """Build the exact source project, then transactionally apply typed resources."""

    inspection = load_studio_project(path)
    _require_declared_operation(inspection, "build")
    _validate_editor_documents(inspection)
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
    output = _studio_output(
        root, inspection["project"]["id"], inspection["project"]["platform"]
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
    _validate_container(output, inspection["project"]["platform"])

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
                "path": document.relative_to(root).as_posix(),
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
        document.relative_to(root).as_posix(): sha256_file(document)
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
    manifest = load_disassembly_manifest(
        Path(inspection["source"]["disassembly_path"]), verify_rebuilt=False
    )
    if artifact.stat().st_size != manifest["input"]["size"]:
        differences.append("artifact-size")
    _validate_container(artifact, inspection["project"]["platform"])

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
        machine=machine,
        attach_window=attach_window,
    )
    plan["operation"] = "retrolarium.studio.launch"
    plan["project_file"] = inspection["project_file"]
    return plan


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
            or document.get("game") != "smb1" or document.get("revision") != "ju":
        raise StudioProjectError(
            "SMB point playtest currently requires an smb1/ju resource document"
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
    if not isinstance(areas, list) or not any(
        isinstance(item, Mapping) and item.get("name") == area for item in areas
    ):
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
    plan = launch_image_plan(
        artifact, platform="nes", mame=executable,
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
            or revision_id not in {"usa", "europe"} \
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
    if len(source) != revision.size or source[:4] != b"NES\x1a" \
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
                    or resource_root.get("revision") not in {"usa", "europe"} \
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
                    or resource_root.get("revision") not in {"usa", "europe"} \
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
                    or resource_root.get("revision") not in {"usa", "europe"} \
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
                    or resource_root.get("revision") not in {"usa", "europe"} \
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
                or resource_root.get("revision") not in {"usa", "europe"}
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
                or resource_root.get("revision") not in {"usa", "europe"}
                or not isinstance(resource_root.get("streams"), list)
                or len(resource_root["streams"]) != 2
                or not isinstance(resource_root.get("demo_steps"), list)
                or len(resource_root["demo_steps"]) != 34
            ):
                raise StudioProjectError(
                    "Solomon presentation requires USA/Europe title streams "
                    "and 34 demo input steps"
                )
            resources = [{
                "id": "screens", "kind": "solomon-title",
                "codec": "solomon.screens@1",
            }, {
                "id": "demo", "kind": "solomon-demo",
                "codec": "solomon.presentation@1",
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
            ("castle", "rooms", "Rooms & Objects", "castle.rooms", "content",
             "castle.prefix-rooms@1", ["edit", "preview", "restore", "undo"]),
            ("world", "world", "World", "world.routing", "content",
             "smb.world.v1", ["edit", "restore", "undo"]),
            ("level", "maze", "Maze", "level.tile-grid", "content",
             "pacman.maze-rle.v1", ["paint", "eyedropper", "semantic-overlays", "undo"]),
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
                    and resource_root.get("revision") in {"usa", "europe"}:
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
            elif kind == "audio" and "smb.apu-music.v1" in kinds[kind] \
                    and kinds[kind].issubset({
                        "smb.apu-music.v1", "smb.apu-envelope.v1",
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
                        "smb.apu-envelope.v1"
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
