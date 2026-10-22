"""Creating Studio projects from a disassembly and upgrading existing ones when
a game's profile gains resources or tabs."""

from __future__ import annotations

from collections.abc import Mapping
import json
import os
from pathlib import Path
import re
import tempfile
import tomllib
from typing import Any, Sequence

from retrolarium_core.contracts import ContractError, load_disassembly_manifest
from retrolarium_kit.games.flicky.content import (
    CANONICAL_SHA256 as FLICKY_SHA256,
    ROM_NAME as FLICKY_ROM_NAME,
    SOURCE_DIRECTORY as FLICKY_SOURCE_DIRECTORY,
    WORKSPACE_FILES as FLICKY_WORKSPACE_FILES,
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
from retrolarium_kit.core.project import build_project, sha256_file
from retrolarium_kit.studio.project import (
    PROJECT_SUFFIX,
    SCHEMA,
    SCHEMA_VERSION,
    SOLOMON_REVISIONS,
    SOURCE_MODES,
    StudioProjectError,
    _AUTHORING_SCHEMAS,
    _atomic_write,
    _choice,
    _identifier,
    _mapping,
    _project_path,
    _string,
    _toml_string,
    load_studio_project,
)


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
        'manifest = "flicky-source/retrolarium-flicky-snapshot.json"',
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
    profile, decoded from the project's own source, and the newer kit's
    tables read from the game; edited resources stay."""

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
        # By name: a revision with a demo (All Night Nippon, Super Mario
        # Bros. 2) extracts the demo by default.
        extract_game(_project_source(inspection), temporary, resource="resource-bundle")
        fresh = json.loads(temporary.read_text(encoding="utf-8"))
    finally:
        temporary.unlink(missing_ok=True)
    by_id = {item.get("id"): item for item in document["resources"]}
    # Tables read from the game (nes.byte-tables.v1) are never edited: a
    # newer kit's set replaces the project's.
    refreshed = [
        item["id"] for item in fresh["resources"]
        if item["id"] in present and item.get("codec") == "nes.byte-tables.v1" and by_id[item["id"]] != item
    ]
    added = [item["id"] for item in fresh["resources"] if item["id"] not in present] + refreshed
    if not added:
        return []
    for item in fresh["resources"]:
        if item["id"] in refreshed:
            by_id[item["id"]] = item
    # Resources the newer kit no longer has stay, after the rest: they hold
    # the user's edits.
    known = {item["id"] for item in fresh["resources"]}
    document["resources"] = [by_id.get(item["id"], item) for item in fresh["resources"]] + [
        item for item in document["resources"] if item.get("id") not in known
    ]
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
    """Add what newer kits edit to a Solomon's Key project: the screen text
    (documents/text.json) and the graphics bundle's new resources (the
    tables read from the game)."""
    result = _add_document_tab(
        path, profile="solomons-key.v1", resource="text", document_name="text.json",
        tab_id="text", title="Text", kind="solomon.text", codec="solomon.text@1",
    )
    added = _add_bundle_resources(path, profile="solomons-key.v1", codec="nes.chr-2bpp.v1")
    if added:
        result["status"] = "upgraded"
    result["added_resources"] = added
    return result


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


def upgrade_castle_excellent_studio_project(path: Path) -> dict[str, Any]:
    """Add the documents newer kits edit to a Castle Excellent project: the
    title demo (documents/demo.json) and the screens (documents/screens.json)."""
    added: list[str] = []
    for resource, name, tab_id, title, kind, codec in (
        ("title-demo", "demo.json", "demo", "Demo", "castle-excellent.demo", "castle-excellent.demo@1"),
        ("screens", "screens.json", "screens", "Screens", "castle-excellent.screens", "castle-excellent.screens@1"),
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
    """Add what newer kits edit to a Pac-Man project: the attract demo
    (documents/demo.json) and the bundle's new resources (the round tables,
    the tables read from the game)."""
    result = _add_document_tab(
        path, profile="pacman.v1", resource="attract-demo", document_name="demo.json",
        tab_id="demo", title="Demo", kind="pacman.demo", codec="pacman.demo@1",
    )
    added = _add_bundle_resources(path, profile="pacman.v1", codec="pacman.maze-rle.v1")
    if added:
        result["status"] = "upgraded"
    result["added_resources"] = added
    return result


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
    objects_source = source_root / "src/game/bonus/objects.s"
    # A project's snapshot always has its record; a snapshot without the
    # source objects (a bare test fixture) has nothing to upgrade from.
    skipped_documents = [] if objects_source.exists() else ["levels.bonus_placements"]
    bonus_document_path: Path | None = None
    bonus_document_payload: str | None = None
    title_document_path: Path | None = None
    title_document_payload: str | None = None
    if objects_source.exists():
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
        if document_schema == "retrolarium.castle-excellent-levels":
            rooms = resource_root.get("rooms")
            if not isinstance(rooms, list) or not rooms:
                raise StudioProjectError(
                    "Castle Excellent resource document rooms must be a non-empty array"
                )
            resources = [{
                "id": "rooms",
                "kind": "castle-excellent",
                "codec": "castle-excellent.prefix-rooms@1",
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
        elif document_schema == "retrolarium.castle-excellent-screens":
            if profile != "castle-excellent.v1" or resource_root.get("game") != "castle-excellent" \
                    or not isinstance(resource_root.get("screens"), list):
                raise StudioProjectError("Castle Excellent screens require the castle-excellent.v1 profile")
            resources = [{"id": "screens", "kind": "castle-excellent-screens", "codec": "castle-excellent.screens@1"}]
        elif document_schema == "retrolarium.castle-excellent-demo":
            if profile != "castle-excellent.v1" or resource_root.get("game") != "castle-excellent" \
                    or not isinstance(resource_root.get("steps"), list):
                raise StudioProjectError("Castle Excellent demo requires the castle-excellent.v1 profile")
            resources = [{"id": "title-demo", "kind": "castle-excellent-demo", "codec": "castle-excellent.demo@1"}]
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
            if profile != "doraemon.v1" or resource_root.get("game") != "doraemon" \
                    or not isinstance(resource_root.get("ending_credit_rows"), list):
                raise StudioProjectError("Doraemon text requires the doraemon.v1 profile")
            resources = [{"id": "text", "kind": "doraemon-text", "codec": "doraemon.text@1"}]
        elif document_schema == "retrolarium.pacman-demo":
            if profile != "pacman.v1" or resource_root.get("game") != "pacman" \
                    or not isinstance(resource_root.get("steps"), list):
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
            ("solomon-mirrors", "mirrors", "Mirrors", "solomon.mirrors",
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
            ("castle-excellent-demo", "demo", "Demo", "castle-excellent.demo",
             "content", "castle-excellent.demo@1",
             ["edit", "preview", "restore", "undo"]),
            ("castle-excellent-screens", "screens", "Screens", "castle-excellent.screens",
             "content", "castle-excellent.screens@1",
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
            ("castle-excellent", "rooms", "Rooms & Objects", "castle-excellent.rooms", "content",
             "castle-excellent.prefix-rooms@1", ["edit", "preview", "restore", "undo"]),
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
             "data.fixed-byte-records", "content", "castle-excellent.sfx-parameters.v1",
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
            if kind == "castle-excellent" and profile == "castle-excellent.v1" \
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
                        or resource_root.get("revision") != "original"
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
            elif kind == "audio" and kinds[kind] == {"castle-excellent.mml-music.v1"}:
                title = "Music & Sounds"
                editor_kind = "castle-excellent.mml-music"
                selected_codec = "castle-excellent.mml-music.v1"
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
                    "castle-excellent.sub-command-text.v1"}:
                tab_id = "screens-text"
                title = "Screens & Text"
                editor_kind = "data.fixed-byte-records"
                selected_codec = "castle-excellent.sub-command-text.v1"
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
                    "castle-excellent.metasprite-descriptors.v1"}:
                title = "Animations & Mappings"
                editor_kind = "castle-excellent.metasprites"
                selected_codec = "castle-excellent.metasprite-descriptors.v1"
                capabilities = ["edit", "preview", "restore", "undo"]
            elif kind == "metatile" and kinds[kind] == {
                    "castle-excellent.room-cell-visuals.v1"}:
                tab_id = "room-cells"
                title = "Room Cells"
                editor_kind = "castle-excellent.room-cells"
                selected_codec = "castle-excellent.room-cell-visuals.v1"
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
                    == "castle-excellent.metasprite-descriptors.v1"):
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
