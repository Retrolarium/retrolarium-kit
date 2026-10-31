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
import os
import shutil
import tempfile
import json
from collections.abc import Callable
from pathlib import Path

from retrolarium_kit.core import assets, source_snapshot
from retrolarium_kit.core.game_names import GameNames
from retrolarium_kit.games.alien_soldier import reader_catalog, workspace


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
        manifest = assets.load(
            snapshot / workspace.source_profile(snapshot).get("manifest", MANIFEST),
            snapshot,
        )
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
    except (
        source_snapshot.SnapshotError,
        assets.AssetsError,
        workspace.WorkspaceError,
        reader_catalog.ReaderCatalogError,
    ) as exc:
        raise AlienSoldierContentError(str(exc)) from exc
    _remember_baseline(
        Path(project_root),
        workspace.read(Path(project_root) / SOURCE_DIRECTORY / CONTENT),
    )
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
        original.write_text(
            json.dumps(before, ensure_ascii=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    return {**mine, "payloads": [ours.get(label, fresh[label]) for label in labels]}


def _follow_derived(
    mine: dict,
    exported: dict,
    keys: tuple[str, ...],
    original: Path,
    follow: Callable[[dict, dict], dict] | None = None,
) -> dict | None:
    """`mine` with the keys the source derives as `exported` has them, and
    what the document's own `follow` takes from it (the bosses' poses of a
    boss this kit reads anew), or None when it is the same already; the
    original copy follows too."""

    def followed(document: dict) -> dict:
        document = {**document, **{key: exported[key] for key in keys}}
        return follow(document, exported) if follow else document

    if original.is_file():
        before = json.loads(original.read_text(encoding="utf-8"))
        if (after := followed(before)) != before:
            original.write_text(
                json.dumps(after, ensure_ascii=False) + "\n",
                encoding="utf-8",
                newline="\n",
            )
    after = followed(mine)
    return None if after == mine else after


def refresh_source(source_root: Path, project_root: Path, original: Path) -> list[str] | None:
    """Move a snapshot of an older source to the repository's current one
    with its edits; None when it is current."""
    snapshot = Path(project_root) / SOURCE_DIRECTORY
    try:
        record = source_snapshot.load_record(snapshot)
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

        if GameNames.load(snapshot).catalog("boss_readers") is None:
            # Older snapshots predate the external symbol catalog. Remake
            # from the updated repository before asking the new reader to
            # export them; the normal refresh still verifies the old source
            # and carries edits only over an unchanged baseline.
            reader_catalog.load(source_root)
            return source_snapshot.refresh(snapshot, original, documents(), remake, check)
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
            kept = _follow_derived(
                document,
                exported[module.DOCUMENT],
                keys,
                original / CONTENT / module.DOCUMENT,
                getattr(module, "follow_source", None),
            )
            if kept is not None:
                refreshed[module.DOCUMENT] = kept
        workspace.write(refreshed, snapshot / CONTENT)
        if source_snapshot.current(snapshot, source_root, FOLDERS):
            return None
        return source_snapshot.refresh(snapshot, original, documents(), remake, check)
    except (
        source_snapshot.SnapshotError,
        assets.AssetsError,
        workspace.WorkspaceError,
        reader_catalog.ReaderCatalogError,
    ) as exc:
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


def _digest(document) -> str:
    return hashlib.sha256(json.dumps(document, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _cache_key(snapshot: Path) -> str:
    # Cache validity includes the reader/writer implementation, not just input
    # files: a changed codec must re-establish the source's baseline.
    digest = hashlib.sha256((snapshot / source_snapshot.RECORD).read_bytes())
    package = Path(__file__).resolve().parents[2]
    for folder in (
        package / "games/alien_soldier",
        package / "platform/megadrive",
        package / "core",
    ):
        for path in sorted(folder.rglob("*.py")):
            digest.update(path.relative_to(package).as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _write_cache(project: Path, value: dict) -> None:
    folder = project / "build"
    folder.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".validation-", dir=folder)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream)
        os.replace(name, folder / "alien-soldier-validation.json")
    finally:
        Path(name).unlink(missing_ok=True)


def _remember_baseline(project: Path, documents: dict) -> dict:
    snapshot = project / SOURCE_DIRECTORY
    baseline = {name: _digest(document) for name, document in documents.items()}
    cache = {"key": _cache_key(snapshot), "baseline": baseline, "validated": baseline}
    _write_cache(project, cache)
    return cache


def _inputs(project: Path):
    snapshot = project / SOURCE_DIRECTORY
    record = source_snapshot.verify(snapshot)
    _add_missing(snapshot, record["revision"])
    key = _cache_key(snapshot)
    try:
        cache = json.loads((project / "build/alien-soldier-validation.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    names = {module.DOCUMENT for module in workspace.DOCUMENTS}
    if (
        cache.get("key") != key
        or not isinstance(cache.get("baseline"), dict)
        or set(cache["baseline"]) != names
        or not isinstance(cache.get("validated"), dict)
    ):
        # Older projects pay for one source export, then use the same path as
        # newly imported projects. Never infer a baseline from editable originals.
        exported = workspace.export(snapshot, record["revision"], workspace.DOCUMENTS)
        cache = _remember_baseline(project, exported)
    current = workspace.read(snapshot / CONTENT)
    semantic = {name: _digest(document) for name, document in current.items()}
    hashes = {name: source_snapshot.sha256_file(snapshot / name) for name in documents()}
    return snapshot, record, cache, current, semantic, hashes


def validate(project_root: Path) -> dict[str, str]:
    """Check immutable source hashes and validate only changed document values."""
    project = Path(project_root)
    try:
        snapshot, record, cache, current, semantic, hashes = _inputs(project)
        pending = [
            module
            for module in workspace.DOCUMENTS
            if semantic[module.DOCUMENT] != cache["validated"].get(module.DOCUMENT)
        ]
        if pending:
            workspace.validate(snapshot, record["revision"], current, pending)
            cache["validated"] = semantic
            _write_cache(project, cache)
        return hashes
    except (
        source_snapshot.SnapshotError,
        workspace.WorkspaceError,
        reader_catalog.ReaderCatalogError,
    ) as exc:
        raise AlienSoldierContentError(str(exc)) from exc


def _base_image(project: Path, snapshot: Path, record: dict, cache: dict) -> Path:
    # Source projects may contain deliberate assembly changes even when no
    # editor document changed. Assemble that baseline once; never substitute
    # the input cartridge merely because the editable documents are pristine.
    image = project / "build/alien-soldier-source.bin"
    if (
        not image.is_file()
        or source_snapshot.sha256_file(image) != cache.get("image_sha256")
        or "demo_layout" not in cache
    ):
        section = workspace.source_profile(snapshot)
        source = workspace.tree(snapshot, section, record["revision"])
        labels = workspace.demos.pointer_table(source, workspace.demos.STREAMS)
        result = workspace.build(
            snapshot,
            record["revision"],
            {},
            image,
            modules=[],
            symbols=[workspace.demos.STAGES, workspace.demos.STREAMS, *labels],
        )
        cache["demo_layout"] = workspace.demos.layout(source, image.read_bytes(), result["symbols"])
        cache["image_sha256"] = source_snapshot.sha256_file(image)
        _write_cache(project, cache)
    return image


def build(project_root: Path) -> dict[str, object]:
    """Apply changed documents once; a pristine workspace reuses its verified ROM."""
    project = Path(project_root)
    try:
        snapshot, record, cache, current, semantic, hashes = _inputs(project)
        changed = [
            module for module in workspace.DOCUMENTS if semantic[module.DOCUMENT] != cache["baseline"][module.DOCUMENT]
        ]
        output = project / OUTPUT
        if changed == [workspace.demos]:
            base = _base_image(project, snapshot, record, cache).read_bytes()
            image, files = workspace.demos.patch(base, current[workspace.demos.DOCUMENT], cache["demo_layout"])
            section = workspace.source_profile(snapshot)
            settings = section["revisions"][record["revision"]]
            if files and "checksum" in settings:
                first, end = (int(str(value), 0) for value in settings["checksum"])
                image = workspace.stamp(image, first, end)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(image)
            result = {"size": len(image), "changed": files}
        elif changed:
            result = workspace.build(snapshot, record["revision"], current, output, modules=changed)
        else:
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(_base_image(project, snapshot, record, cache), output)
            result = {"size": output.stat().st_size, "changed": []}
        cache["validated"] = semantic
        _write_cache(project, cache)
    except (
        source_snapshot.SnapshotError,
        workspace.WorkspaceError,
        reader_catalog.ReaderCatalogError,
        ValueError,
    ) as exc:
        raise AlienSoldierContentError(str(exc)) from exc
    return {
        "artifact": str(output),
        "size": result["size"],
        "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "source_sha256": record["rom_sha256"],
        "documents": hashes,
        "changed": result["changed"],
    }
