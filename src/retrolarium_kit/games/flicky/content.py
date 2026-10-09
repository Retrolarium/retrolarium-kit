"""Isolated build oracle for the canonical Flicky content workspace.

The neighboring reconstruction is an import source, never a build target.  Its
tracked files and the user-supplied ROM are copied into a relocatable snapshot
before any authoring script is run.  Normal GUI editing remains native C++.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import shutil
import subprocess
import sys
import tempfile

from retrolarium_core.megadrive import stamp_cartridge_checksum
from retrolarium_kit.games.flicky.bonus_motion import export_bonus_motion, validate_bonus_motion
from retrolarium_kit.games.flicky.demo import export_attract_demos, validate_attract_demos
from retrolarium_kit.games.flicky.bonus_placement import (
    export_bonus_placements, validate_bonus_placements,
)
from retrolarium_kit.games.flicky.title_layout import export_title_layout, validate_title_layout


CANONICAL_SHA256 = "4df1a91e08376ae773a6eb8e5abe310f94ca39d8f81583b91a9477fedcb68c11"
CANONICAL_SIZE = 131072
ROM_NAME = "Flicky (UE) [!].bin"
SOURCE_DIRECTORY = "flicky-source"
SOURCE_PATHS = ("assets", "bin", "config", "data", "mk", "scripts", "src", "third_party")
WORKSPACE_FILES = {
    "level_layouts": "content/workspace/level/levels.json",
    "graphics_assets": "content/workspace/graphics/graphics.json",
    "graphics_semantics": "content/workspace/graphics/semantics.json",
    "graphics_sequences": "content/workspace/graphics/sequences.json",
    "z80_sound_banks": "content/workspace/sound/z80_sound_data.asm",
}
SNAPSHOT_RECORD = "retrolarium-flicky-snapshot.json"


class FlickyContentError(ValueError):
    """The Flicky source snapshot or build violates its contract."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_rom(path: Path) -> None:
    if not path.is_file() or path.stat().st_size != CANONICAL_SIZE \
            or _sha256(path) != CANONICAL_SHA256:
        raise FlickyContentError("Flicky requires the exact canonical (UE) ROM")


def _run(command: list[str], root: Path, *, env: dict[str, str] | None = None) -> None:
    result = subprocess.run(command, cwd=root, env=env, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if result.returncode:
        raise FlickyContentError(
            f"Flicky source command failed ({result.returncode}): "
            + " ".join(command[:3]) + "\n" + result.stdout[-8000:]
        )


def _source_names(source_root: Path) -> list[str]:
    """The repository's files under SOURCE_PATHS: Git's list in a checkout;
    in a downloaded archive (no .git), the files there but what the
    repository ignores: ROM slices (*.bin) and Python caches."""
    if not (source_root / ".git").exists():
        return sorted(
            path.relative_to(source_root).as_posix()
            for top in SOURCE_PATHS if (source_root / top).is_dir()
            for path in (source_root / top).rglob("*")
            if path.is_file() and path.suffix != ".bin" and "__pycache__" not in path.parts
        )
    result = subprocess.run(["git", "ls-files", "-z", "--cached", "--",
                             *SOURCE_PATHS], cwd=source_root,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        raise FlickyContentError("Flicky source must be a readable Git checkout")
    return [os.fsdecode(raw) for raw in result.stdout.split(b"\0") if raw]


def _tracked_files(source_root: Path) -> list[Path]:
    files: list[Path] = []
    for name in _source_names(source_root):
        portable = PurePosixPath(name)
        if portable.is_absolute() or PureWindowsPath(name).is_absolute() \
                or "\\" in name or ".." in portable.parts:
            raise FlickyContentError(f"unsafe source path: {name}")
        path = source_root.joinpath(*portable.parts)
        if path.is_symlink() or not path.is_file():
            raise FlickyContentError(f"source file is missing or symlinked: {name}")
        files.append(Path(*portable.parts))
    if not files:
        raise FlickyContentError("Flicky source checkout has no tracked files")
    return files


def _extract_assets(snapshot: Path, rom: Path) -> None:
    """Recreate ignored source assets from the verified ROM, not local caches."""

    manifest_path = snapshot / "assets/manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise FlickyContentError(f"cannot read Flicky asset manifest: {exc}") from exc
    if manifest.get("schema_version") != 1 or not isinstance(manifest.get("assets"), list):
        raise FlickyContentError("Flicky asset manifest is unsupported")
    image = rom.read_bytes()
    for asset in manifest["assets"]:
        try:
            relative = PurePosixPath(asset["path"])
            address = int(asset["address"], 0)
            size = asset["size"]
            expected_sha1 = asset["sha1"]
        except (KeyError, TypeError, ValueError) as exc:
            raise FlickyContentError("invalid Flicky asset entry") from exc
        if relative.is_absolute() or PureWindowsPath(asset["path"]).is_absolute() \
                or "\\" in asset["path"] or ".." in relative.parts or not relative.parts \
                or not isinstance(size, int) or size <= 0 or address < 0 \
                or address + size > len(image):
            raise FlickyContentError(f"invalid Flicky asset range: {relative}")
        content = image[address:address + size]
        if hashlib.sha1(content).hexdigest() != expected_sha1:
            raise FlickyContentError(f"Flicky asset hash differs: {relative}")
        target = snapshot / "data" / Path(*relative.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


def _snapshot_files(root: Path, files: list[Path]) -> dict[str, str]:
    return {relative.as_posix(): _sha256(root / relative) for relative in files}


def _validate_snapshot(source: Path) -> None:
    try:
        record = json.loads((source / SNAPSHOT_RECORD).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise FlickyContentError(f"cannot read Flicky snapshot record: {exc}") from exc
    if record.get("schema") != "retrolarium.flicky-source-snapshot" \
            or record.get("schema_version") != 1 \
            or record.get("canonical_sha256") != CANONICAL_SHA256 \
            or not isinstance(record.get("files"), dict):
        raise FlickyContentError("Flicky snapshot record is unsupported")
    for name, expected in record["files"].items():
        relative = PurePosixPath(name)
        if relative.is_absolute() or PureWindowsPath(name).is_absolute() \
                or "\\" in name or ".." in relative.parts or not relative.parts:
            raise FlickyContentError(f"unsafe Flicky snapshot path: {name}")
        path = source.joinpath(*relative.parts)
        if not path.resolve().is_relative_to(source.resolve()) \
                or not path.is_file() or path.is_symlink() or _sha256(path) != expected:
            raise FlickyContentError(f"Flicky source snapshot changed: {name}")


def import_flicky_source(
    source_root: Path, canonical_rom: Path, project_root: Path
) -> dict[str, object]:
    """Create a self-contained source/workspace copy without touching source_root."""

    source_root = source_root.resolve()
    canonical_rom = canonical_rom.resolve()
    project_root = project_root.resolve()
    _canonical_rom(canonical_rom)
    files = _tracked_files(source_root)
    destination = project_root / SOURCE_DIRECTORY
    if destination.exists():
        raise FlickyContentError(f"Flicky snapshot already exists: {destination}")
    project_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".flicky-import-", dir=project_root))
    try:
        for relative in files:
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_root / relative, target)
        shutil.copy2(canonical_rom, staging / ROM_NAME)
        _extract_assets(staging, staging / ROM_NAME)
        _run([sys.executable, "scripts/run.py", "authoring.content_workspace",
              "init"], staging)
        levels_path = staging / WORKSPACE_FILES["level_layouts"]
        levels = json.loads(levels_path.read_text(encoding="utf-8"))
        levels["bonus_placements"] = export_bonus_placements(staging)
        levels["attract_demos"] = export_attract_demos(staging)
        levels_path.write_text(json.dumps(levels, indent=2) + "\n",
                               encoding="utf-8", newline="\n")
        sequences_path = staging / WORKSPACE_FILES["graphics_sequences"]
        sequences = json.loads(sequences_path.read_text(encoding="utf-8"))
        sequences["bonus_motion"] = export_bonus_motion(staging)
        sequences_path.write_text(json.dumps(sequences, indent=2) + "\n",
                                  encoding="utf-8", newline="\n")
        semantics_path = staging / WORKSPACE_FILES["graphics_semantics"]
        semantics = json.loads(semantics_path.read_text(encoding="utf-8"))
        semantics["title_layout"] = export_title_layout(staging)
        semantics_path.write_text(json.dumps(semantics, indent=2) + "\n",
                                  encoding="utf-8", newline="\n")
        for relative in WORKSPACE_FILES.values():
            if not (staging / relative).is_file():
                raise FlickyContentError(f"Flicky workspace missing {relative}")
        asset_manifest = json.loads(
            (staging / "assets/manifest.json").read_text(encoding="utf-8")
        )
        asset_files = [Path("data", *PurePosixPath(item["path"]).parts)
                       for item in asset_manifest["assets"]]
        snapshot_record = {
            "schema": "retrolarium.flicky-source-snapshot",
            "schema_version": 1,
            "canonical_sha256": CANONICAL_SHA256,
            "files": _snapshot_files(staging, files + asset_files),
        }
        (staging / SNAPSHOT_RECORD).write_text(
            json.dumps(snapshot_record, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        staging.rename(destination)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return {"source": str(destination), "source_sha256": CANONICAL_SHA256,
            "tracked_files": len(files), "documents": WORKSPACE_FILES.copy()}


def validate_flicky_source_snapshot(project_root: Path) -> dict[str, str]:
    """Check pinned source inputs and all five edited workspace artifacts."""

    source = project_root.resolve() / SOURCE_DIRECTORY
    _canonical_rom(source / ROM_NAME)
    _validate_snapshot(source)
    for relative in WORKSPACE_FILES.values():
        if not (source / relative).is_file():
            raise FlickyContentError(f"Flicky workspace missing {relative}")
    sequences = json.loads((source / WORKSPACE_FILES["graphics_sequences"])
                           .read_text(encoding="utf-8"))
    validate_bonus_motion(sequences, source)
    levels = json.loads((source / WORKSPACE_FILES["level_layouts"])
                        .read_text(encoding="utf-8"))
    validate_bonus_placements(levels, source)
    validate_attract_demos(levels, source)
    semantics = json.loads((source / WORKSPACE_FILES["graphics_semantics"])
                           .read_text(encoding="utf-8"))
    validate_title_layout(semantics, source)
    _run([sys.executable, "scripts/run.py", "authoring.content_workspace",
          "validate"], source)
    return {key: _sha256(source / relative)
            for key, relative in WORKSPACE_FILES.items()}


def build_flicky_source_snapshot(
    project_root: Path, *, native_writer: Path | None = None,
) -> dict[str, object]:
    """Build edited Flicky documents in the imported snapshot and publish a ROM."""

    project_root = project_root.resolve()
    source = project_root / SOURCE_DIRECTORY
    document_hashes = validate_flicky_source_snapshot(project_root)
    manifest_path = source / "config/authoring/content_studios.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise FlickyContentError(f"cannot read Flicky content manifest: {exc}") from exc
    if manifest.get("profile") != "canonical" or manifest.get("image_size") != CANONICAL_SIZE \
            or manifest.get("image_sha1") != "83d8bbf0a9b38c42a0bf492d105cc3abe9644a96":
        raise FlickyContentError("Flicky content manifest is not canonical")
    tool_dir = source / "bin" / ("windows_i386" if os.name == "nt" else "linux_x86_64")
    as_bin = tool_dir / ("asw.exe" if os.name == "nt" else "asl")
    p2bin = tool_dir / ("p2bin.exe" if os.name == "nt" else "p2bin")
    if not as_bin.is_file() or not p2bin.is_file():
        raise FlickyContentError(f"Flicky assembler tools are missing: {tool_dir}")
    env = os.environ.copy()
    env["AS_MSGPATH"] = str(tool_dir)
    selected_writer = native_writer or env.get("RETROLARIUM_FLICKY_LEVEL_WRITER") \
        or shutil.which("retrolarium-flicky-level-writer")
    if not selected_writer:
        raise FlickyContentError(
            "native Flicky level writer is required; pass "
            "--flicky-level-writer, set RETROLARIUM_FLICKY_LEVEL_WRITER, "
            "or install it on PATH"
        )
    writer = Path(selected_writer).resolve()
    if not writer.is_file():
        raise FlickyContentError(f"native Flicky level writer is missing: {writer}")
    _run([sys.executable, str(Path(__file__).with_name("native_build.py")),
          "--source", str(source), "--writer", str(writer),
          "--as-bin", str(as_bin), "--p2bin", str(p2bin)], source, env=env)
    built = source / "build/content/flicky.bin"
    if not built.is_file() or built.stat().st_size != CANONICAL_SIZE:
        raise FlickyContentError("Flicky content build did not produce a 128 KiB ROM")
    output_dir = project_root / "build"
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / "flicky-edited.bin"
    descriptor, temporary_name = tempfile.mkstemp(prefix=".flicky-", suffix=".bin",
                                                   dir=output_dir)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            # The source assembler leaves the canonical header checksum in
            # place after edits. The tested MAME boot path rejects it, so publish a
            # stamped ROM; the zero-edit image stays byte-for-byte canonical.
            stream.write(stamp_cartridge_checksum(built.read_bytes()))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return {"artifact": str(output), "size": output.stat().st_size,
            "sha256": _sha256(output), "source_sha256": CANONICAL_SHA256,
            "documents": document_hashes}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="operation", required=True)
    importing = subcommands.add_parser("import", help="create isolated source snapshot")
    importing.add_argument("source_root", type=Path)
    importing.add_argument("canonical_rom", type=Path)
    importing.add_argument("project_root", type=Path)
    building = subcommands.add_parser("build", help="build imported Flicky documents")
    building.add_argument("project_root", type=Path)
    building.add_argument("--native-writer", type=Path)
    args = parser.parse_args()
    if args.operation == "import":
        result = import_flicky_source(
            args.source_root, args.canonical_rom, args.project_root
        )
    else:
        result = build_flicky_source_snapshot(
            args.project_root, native_writer=args.native_writer)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
