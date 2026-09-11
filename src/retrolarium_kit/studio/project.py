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
from .games import build_game, validate_game_document_for_revision
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
        "nes.fixed-text-fields.v1",
        "nes.nametable.v1",
        "nes.palette-table.v1",
        "pacman.maze-rle.v1",
        "pacman.maze-rle@1",
        "pacman.actor-mapping.v1",
        "pacman.palette-table.v1",
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
    {"retrolarium.castle-levels", "retrolarium.nes-resource-bundle"}
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
    if len(authoring_documents) > 1:
        raise StudioProjectError(
            "Studio build currently requires one composed authoring document"
        )

    authoring: dict[str, object] | None = None
    if authoring_documents:
        authoring = build_game(base_artifact, authoring_documents[0], output)
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
            ("level", "maze", "Maze", "level.tile-grid", "content",
             "pacman.maze-rle.v1", ["paint", "eyedropper", "semantic-overlays", "undo"]),
            ("screen", "screens", "Screens", "screen.tilemap", "content",
             "nes.nametable.v1", ["paint", "preview", "undo"]),
            ("text", "text", "Text", "text.fixed", "content",
             "nes.fixed-text-fields.v1", ["edit", "restore", "undo"]),
            ("graphics", "graphics", "Graphics", "graphics.tile-set", "assets",
             "nes.chr-2bpp.v1", ["pixel-paint", "restore", "undo"]),
            ("palette", "palettes", "Palettes", "palette.set", "assets",
             "nes.palette-table.v1", ["paint", "preview", "undo"]),
            ("animation", "animations", "Animations & Mappings", "animation.mapping", "assets",
             "pacman.actor-mapping.v1", ["paint", "preview", "restore", "undo"]),
        )
        for kind, tab_id, title, editor_kind, group, expected_codec, capabilities in templates:
            if kind not in kinds:
                continue
            selected_codec = expected_codec
            if kind == "palette" and kinds[kind] == {"pacman.palette-table.v1"}:
                selected_codec = "pacman.palette-table.v1"
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
        available_ids = {str(tab["id"]) for tab in tabs}
        desired_dependencies = {
            "maze": ("graphics",),
            "screens": ("graphics", "palettes"),
            "graphics": ("palettes",),
            "animations": ("graphics",),
        }
        for tab in tabs:
            tab["depends_on"] = [
                dependency
                for dependency in desired_dependencies.get(str(tab["id"]), ())
                if dependency in available_ids
            ]
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
