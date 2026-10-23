"""A game repository copied into a project to be edited and rebuilt.

Some games are edited by changing their source and assembling it again
rather than by patching their ROM. Such a project keeps a snapshot: the
repository's tracked files under the folders the build needs, the user's ROM,
the data the build cuts out of it, and the documents the studio edits. The
repository itself is never written.

The snapshot carries a record (`retrolarium-source-snapshot.json`): the
revision, the ROM's SHA-256 and the SHA-256 of every file copied or cut, so
a snapshot whose source was changed by hand is refused. Documents are not in
the record; they are the edits.

When the repository moves on (a newer release), `refresh` builds a snapshot
of the current source and carries each edited document over, provided the
new source gives that document unedited as the old one did; the documents as
first exported (kept by the caller in `original`) tell which are edited.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import shutil
import subprocess
import tempfile
import time
from typing import Callable, Iterable


RECORD = "retrolarium-source-snapshot.json"
SCHEMA = "retrolarium.source-snapshot"


class SnapshotError(ValueError):
    """A snapshot cannot be made, refreshed or trusted."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe(name: str) -> PurePosixPath:
    portable = PurePosixPath(name)
    if portable.is_absolute() or PureWindowsPath(name).is_absolute() or "\\" in name or ".." in portable.parts:
        raise SnapshotError(f"unsafe source path: {name}")
    return portable


def tracked_files(source_root: Path, folders: Iterable[str]) -> list[str]:
    """The repository's files under `folders`: Git's list in a checkout; in a
    downloaded archive (no .git), the files there but ROM slices (*.bin) and
    Python caches."""
    folders = list(folders)
    if (source_root / ".git").exists():
        result = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--", *folders],
            cwd=source_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if result.returncode:
            raise SnapshotError(f"{source_root} is not a readable Git checkout")
        names = [os.fsdecode(raw) for raw in result.stdout.split(b"\0") if raw]
    else:
        names = sorted(
            path.relative_to(source_root).as_posix()
            for folder in folders
            if (source_root / folder).is_dir()
            for path in (source_root / folder).rglob("*")
            if path.is_file() and path.suffix != ".bin" and "__pycache__" not in path.parts
        )
    for name in names:
        path = source_root.joinpath(*_safe(name).parts)
        if path.is_symlink() or not path.is_file():
            raise SnapshotError(f"source file is missing or a link: {name}")
    if not names:
        raise SnapshotError(f"{source_root} has no files under {', '.join(folders)}")
    return names


def same_document(a: Path, b: Path) -> bool:
    """Whether two documents hold the same: JSON by its values (the studio
    saves it in its own layout), anything else byte for byte."""
    if not a.is_file() or not b.is_file():
        return False
    if a.suffix == ".json":
        try:
            return json.loads(a.read_text(encoding="utf-8")) == json.loads(b.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
    return a.read_bytes() == b.read_bytes()


def rename(source: Path, target: Path) -> None:
    """Rename a folder; Windows refuses while a scanner still reads files
    just written in it, so a refusal is tried again for a few seconds."""
    for attempt in range(40):
        try:
            source.rename(target)
            return
        except PermissionError:
            if attempt == 39:
                raise
            time.sleep(0.25)


def create(
    source_root: Path,
    rom: Path,
    destination: Path,
    *,
    folders: Iterable[str],
    revision: str,
    rom_name: str,
    cut: Callable[[Path, Path], list[str]],
    export: Callable[[Path], None],
) -> list[str]:
    """Make the snapshot at `destination` (which must not exist): copy the
    tracked files, the ROM (as `rom_name`), let `cut(snapshot, rom)` cut the
    build's data out of it (returning the files it wrote) and
    `export(snapshot)` write the documents. Returns the files recorded."""
    source_root, destination = source_root.resolve(), destination.resolve()
    if destination.exists():
        raise SnapshotError(f"the snapshot already exists: {destination}")
    names = tracked_files(source_root, folders)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".snapshot-", dir=destination.parent))
    try:
        for name in names:
            target = staging.joinpath(*_safe(name).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_root / name, target)
        shutil.copy2(rom, staging / rom_name)
        recorded = names + [rom_name] + cut(staging, staging / rom_name)
        export(staging)
        record = {
            "schema": SCHEMA,
            "schema_version": 1,
            "revision": revision,
            "rom": rom_name,
            "rom_sha256": sha256_file(staging / rom_name),
            "files": {name: sha256_file(staging.joinpath(*_safe(name).parts)) for name in sorted(set(recorded))},
        }
        (staging / RECORD).write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8", newline="\n")
        rename(staging, destination)
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
    return sorted(set(recorded))


def load_record(snapshot: Path) -> dict:
    try:
        record = json.loads((snapshot / RECORD).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SnapshotError(f"cannot read the snapshot record: {exc}") from exc
    if record.get("schema") != SCHEMA or record.get("schema_version") != 1 or not isinstance(record.get("files"), dict):
        raise SnapshotError("the snapshot record is not a retrolarium.source-snapshot v1")
    return record


def verify(snapshot: Path) -> dict:
    """The record, once every recorded file is checked unchanged."""
    record = load_record(snapshot)
    for name, digest in record["files"].items():
        path = snapshot.joinpath(*_safe(name).parts)
        if not path.is_file() or path.is_symlink() or sha256_file(path) != digest:
            raise SnapshotError(f"the snapshot's source changed: {name}")
    return record


def current(snapshot: Path, source_root: Path, folders: Iterable[str]) -> bool:
    """Whether the snapshot holds the repository's tracked files as they are."""
    record = load_record(snapshot)
    names = tracked_files(source_root, folders)
    return all(record["files"].get(name) == sha256_file(source_root / name) for name in names) and set(names) <= set(
        record["files"]
    )


def refresh(
    snapshot: Path,
    original: Path,
    documents: Iterable[str],
    remake: Callable[[Path], None],
    check: Callable[[Path], None],
) -> list[str]:
    """Replace `snapshot` with one of the current source, made by
    `remake(staging)`, carrying the edited documents over; `check(fresh)`
    must accept the carried edits. A document the new source changes under
    an edit stops the refresh. The old snapshot is kept beside the new one
    (`<name>.before-refresh`) and `original` gets the new documents."""
    staging = Path(tempfile.mkdtemp(prefix=".snapshot-refresh-", dir=snapshot.parent))
    try:
        fresh = staging / snapshot.name
        remake(fresh)
        documents = list(documents)
        baselines = {relative: (fresh / relative).read_bytes() for relative in documents}
        carried, conflicts = [], []
        for relative in documents:
            mine = snapshot / relative
            if not mine.is_file() or same_document(mine, original / relative):
                continue
            if same_document(original / relative, fresh / relative):
                shutil.copyfile(mine, fresh / relative)
                carried.append(relative)
            else:
                conflicts.append(relative)
        if conflicts:
            raise SnapshotError(
                "the game's source changed under edited documents ("
                + ", ".join(conflicts)
                + "); the project keeps its older source"
            )
        check(fresh)
        backup = snapshot.with_name(snapshot.name + ".before-refresh")
        if backup.exists():
            shutil.rmtree(backup)
        rename(snapshot, backup)
        try:
            rename(fresh, snapshot)
        except OSError:
            rename(backup, snapshot)
            raise
        for relative, data in baselines.items():
            (original / relative).parent.mkdir(parents=True, exist_ok=True)
            (original / relative).write_bytes(data)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return carried
