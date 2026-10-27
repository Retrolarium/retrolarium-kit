"""The Alien Soldier source snapshot a studio project edits and builds.

A revision's project holds a snapshot of the repository
(`alien-soldier-source`, see core/source_snapshot.py): its source,
configuration and asset manifest, the user's ROM, the data cut out of that
ROM for the revision, and the documents (`content/`) the studio edits. A
build writes the documents into a copy of the snapshot's source and
assembles it (workspace.py).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from retrolarium_kit.core import assets, source_snapshot
from retrolarium_kit.games.alien_soldier import workspace


SOURCE_DIRECTORY = "alien-soldier-source"
CONTENT = "content"
FOLDERS = ("assets", "config", "src")
# The assets manifest the data is cut out of the ROM by, unless the game
# profile's source section names another ("manifest").
MANIFEST = "assets/manifest.json"
OUTPUT = "build/alien-soldier-edited.bin"


class AlienSoldierContentError(ValueError):
    """A snapshot that cannot be made, trusted or built."""


def documents() -> list[str]:
    """The edited documents, relative to the snapshot."""
    return [f"{CONTENT}/{module.DOCUMENT}" for module in workspace.DOCUMENTS]


def _cut(revision: str):
    def cut(snapshot: Path, rom: Path) -> list[str]:
        manifest = assets.load(snapshot / workspace.source_profile(snapshot).get("manifest", MANIFEST), snapshot)
        assets.split(manifest, [revision], {revision: rom})
        return sorted({f"{manifest.output}/{asset.path}" for asset in manifest.assets_of(revision)})

    return cut


def _export(revision: str):
    def export(snapshot: Path) -> None:
        workspace.write(workspace.export(snapshot, revision), snapshot / CONTENT)

    return export


def import_source(source_root: Path, rom: Path, project_root: Path, revision: str, rom_name: str) -> list[str]:
    """Make the revision's snapshot in `project_root` from the repository and
    the ROM; the documents written."""
    try:
        source_snapshot.create(
            source_root,
            rom,
            Path(project_root) / SOURCE_DIRECTORY,
            folders=FOLDERS,
            revision=revision,
            rom_name=rom_name,
            cut=_cut(revision),
            export=_export(revision),
        )
    except (source_snapshot.SnapshotError, assets.AssetsError, workspace.WorkspaceError) as exc:
        raise AlienSoldierContentError(str(exc)) from exc
    return documents()


def _follow_payloads(mine: dict, exported: dict, original: Path) -> dict | None:
    """`mine` with the payload labels of `exported`, or None when they are
    the same already. The original copy of the document follows too, so an
    unedited payload still reads as unedited."""
    labels = [item.get("label") for item in exported["payloads"]]
    if [item.get("label") for item in mine.get("payloads", [])] == labels:
        return None
    before = json.loads(original.read_text(encoding="utf-8")) if original.is_file() else {"payloads": []}
    old = {item.get("label"): item for item in before.get("payloads", [])}
    ours = {item.get("label"): item for item in mine.get("payloads", [])}
    for label, item in ours.items():
        if label not in labels and old.get(label) != item:
            raise AlienSoldierContentError(
                f"{label} is edited, but this kit no longer reads it as part of {exported['schema']}"
            )
    fresh = {item["label"]: item for item in exported["payloads"]}
    if original.is_file():
        before["payloads"] = [old.get(label, fresh[label]) for label in labels]
        original.write_text(json.dumps(before, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return {**mine, "payloads": [ours.get(label, fresh[label]) for label in labels]}


def _follow_derived(mine: dict, exported: dict, keys: tuple[str, ...], original: Path) -> dict | None:
    """`mine` with the keys the source derives as `exported` has them, or None
    when they are the same already; the original copy follows too."""
    derived = {key: exported[key] for key in keys}
    if original.is_file():
        before = json.loads(original.read_text(encoding="utf-8"))
        if any(before.get(key) != value for key, value in derived.items()):
            original.write_text(
                json.dumps({**before, **derived}, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
            )
    if all(mine.get(key) == value for key, value in derived.items()):
        return None
    return {**mine, **derived}


def refresh_source(source_root: Path, project_root: Path, original: Path) -> list[str] | None:
    """Move a snapshot of an older source to the repository's current one
    with its edits; None when it is current."""
    snapshot = Path(project_root) / SOURCE_DIRECTORY
    try:
        record = source_snapshot.load_record(snapshot)
        # Documents a newer kit edits that the project lacks are added as the
        # source gives them.
        # The derived documents (what the studio draws stages with) are read
        # again each time: this kit may read more than the one that wrote them.
        missing = [name for name in documents() if not (snapshot / name).is_file()]
        exported = workspace.export(snapshot, record["revision"])
        derived = {module.DOCUMENT for module in workspace.DERIVED}
        refreshed = {
            relative: document
            for relative, document in exported.items()
            if relative in derived or f"{CONTENT}/{relative}" in missing
        }
        # An edited document whose payloads this kit reads differently (a
        # staging buffer that is not a map, art a loader sends to VRAM)
        # follows the source: payloads it no longer has go when unedited,
        # new ones come as the source gives them.
        for module in workspace.DOCUMENTS:
            relative = module.DOCUMENT
            path = snapshot / CONTENT / relative
            if relative in refreshed or not path.is_file() or "payloads" not in exported[relative]:
                continue
            mine = json.loads(path.read_text(encoding="utf-8"))
            kept = _follow_payloads(mine, exported[relative], original / CONTENT / relative)
            if kept is not None:
                refreshed[relative] = kept
        # So are the derived parts of edited documents, in the original copy
        # too: a document whose only change is what the source derives still
        # reads as unedited.
        for module in workspace.DOCUMENTS:
            keys = getattr(module, "DERIVED_KEYS", ())
            path = snapshot / CONTENT / module.DOCUMENT
            if not keys or not (module.DOCUMENT in refreshed or path.is_file()):
                continue
            document = refreshed.get(module.DOCUMENT) or json.loads(path.read_text(encoding="utf-8"))
            kept = _follow_derived(document, exported[module.DOCUMENT], keys, original / CONTENT / module.DOCUMENT)
            if kept is not None:
                refreshed[module.DOCUMENT] = kept
        workspace.write(refreshed, snapshot / CONTENT)
        if source_snapshot.current(snapshot, source_root, FOLDERS):
            return None
        revision, rom_name = record["revision"], record["rom"]

        def remake(fresh: Path) -> None:
            source_snapshot.create(
                source_root,
                snapshot / rom_name,
                fresh,
                folders=FOLDERS,
                revision=revision,
                rom_name=rom_name,
                cut=_cut(revision),
                export=_export(revision),
            )

        def check(fresh: Path) -> None:
            workspace.validate(fresh, revision, workspace.read(fresh / CONTENT))

        return source_snapshot.refresh(snapshot, original, documents(), remake, check)
    except (source_snapshot.SnapshotError, assets.AssetsError, workspace.WorkspaceError) as exc:
        raise AlienSoldierContentError(str(exc)) from exc


def machine(project_root: Path) -> str | None:
    """The MAME machine the revision runs on (its region's console)."""
    snapshot = Path(project_root) / SOURCE_DIRECTORY
    revision = source_snapshot.load_record(snapshot)["revision"]
    return workspace.source_profile(snapshot)["revisions"][revision].get("machine")


def _add_missing(snapshot: Path, revision: str) -> None:
    """The documents a newer kit edits that the project lacks, as the source
    gives them: a project an older studio opened last still builds."""
    missing = [module for module in workspace.DOCUMENTS if not (snapshot / CONTENT / module.DOCUMENT).is_file()]
    if missing:
        workspace.write(workspace.export(snapshot, revision, missing), snapshot / CONTENT)


def validate(project_root: Path) -> dict[str, str]:
    """Check the snapshot and its documents; the documents' SHA-256."""
    snapshot = Path(project_root) / SOURCE_DIRECTORY
    try:
        record = source_snapshot.verify(snapshot)
        _add_missing(snapshot, record["revision"])
        workspace.validate(snapshot, record["revision"], workspace.read(snapshot / CONTENT))
    except (source_snapshot.SnapshotError, workspace.WorkspaceError) as exc:
        raise AlienSoldierContentError(str(exc)) from exc
    return {name: source_snapshot.sha256_file(snapshot / name) for name in documents()}


def build(project_root: Path) -> dict[str, object]:
    """Build the edited documents into the project's ROM."""
    project_root = Path(project_root)
    snapshot = project_root / SOURCE_DIRECTORY
    hashes = validate(project_root)
    record = source_snapshot.load_record(snapshot)
    output = project_root / OUTPUT
    try:
        result = workspace.build(snapshot, record["revision"], workspace.read(snapshot / CONTENT), output)
    except (workspace.WorkspaceError, ValueError) as exc:
        raise AlienSoldierContentError(str(exc)) from exc
    return {
        "artifact": str(output),
        "size": result["size"],
        "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "source_sha256": record["rom_sha256"],
        "documents": hashes,
        "changed": result["changed"],
    }
