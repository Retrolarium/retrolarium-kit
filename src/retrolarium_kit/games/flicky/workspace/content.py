"""Flicky's content workspace: the five documents the studio edits.

The documents are taken from a snapshot of the Flicky source repository
(its tracked files and the assets split from the canonical ROM) and staged
back into a copy of its source to build: levels, graphics assets, text and
palettes, sprite mappings and animations, and the Z80 sound banks as
assembly. The game repository keeps the source and its build scripts; this
package owns the documents and how they are read and written.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Mapping


class FlickyWorkspaceError(ValueError):
    """A Flicky content document is missing or does not fit the source."""


WORKSPACE = "content/workspace"
OUTPUT = "build/content/flicky.bin"
IMAGE_SIZE = 131072
IMAGE_SHA1 = "83d8bbf0a9b38c42a0bf492d105cc3abe9644a96"
# The documents, in the order they are made and checked.
ARTIFACTS: tuple[Mapping[str, Any], ...] = (
    {"id": "graphics_assets", "kind": "graphics_document", "workspace": "graphics/graphics.json"},
    {"id": "graphics_semantics", "kind": "graphics_semantics_document", "workspace": "graphics/semantics.json"},
    {"id": "graphics_sequences", "kind": "graphics_sequences_document", "workspace": "graphics/sequences.json"},
    {"id": "level_layouts", "kind": "level_document", "workspace": "level/levels.json",
     "capacities": {"level_data_bytes": 21101, "special_tiles_bytes": 1676}},
    {"id": "z80_sound_banks", "kind": "assembly_source", "baseline": "src/sound/z80/data.asm",
     "workspace": "sound/z80_sound_data.asm", "output": "build/content/z80_sound_data.bin", "capacity": 2804},
)


def artifact(identifier: str) -> Mapping[str, Any]:
    return next(item for item in ARTIFACTS if item["id"] == identifier)


def atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    shutil.copyfile(source, temporary)
    temporary.replace(destination)


def initialize(root: Path, force: bool = False) -> int:
    """Make the documents a snapshot lacks (all of them with `force`)."""
    from retrolarium_kit.games.flicky.workspace import graphics, levels, semantics, sequences

    created = 0
    for item in ARTIFACTS:
        workspace = root / WORKSPACE / item["workspace"]
        if workspace.exists() and not force:
            continue
        if item["kind"] == "assembly_source":
            baseline = root / item["baseline"]
            if not baseline.is_file():
                raise FlickyWorkspaceError(f"baseline artifact not found: {baseline}")
            atomic_copy(baseline, workspace)
        else:
            model = {"level_document": levels, "graphics_document": graphics,
                     "graphics_semantics_document": semantics, "graphics_sequences_document": sequences}[item["kind"]]
            model.atomic_write_json(workspace, model.export_document(root))
        created += 1
    return created


def workspace_path(root: Path, item: Mapping[str, Any], overrides: Mapping[str, Path] | None = None) -> Path:
    """The effective path of one document."""
    if overrides and item["id"] in overrides:
        return overrides[item["id"]].resolve()
    return (root / WORKSPACE / item["workspace"]).resolve()


def validate_workspace(root: Path, zero_edit: bool = False, overrides: Mapping[str, Path] | None = None) -> None:
    """Check every document against the snapshot's source; raise on the first fault."""
    from retrolarium_kit.games.flicky.workspace import graphics, levels, semantics, sequences, sound

    for item in ARTIFACTS:
        workspace = workspace_path(root, item, overrides)
        if not workspace.is_file():
            raise FlickyWorkspaceError(f"workspace artifact not found: {workspace}")
        if item["kind"] == "assembly_source":
            baseline = root / item["baseline"]
            if not baseline.is_file():
                raise FlickyWorkspaceError(f"baseline artifact not found: {baseline}")
            if zero_edit and workspace.read_bytes() != baseline.read_bytes():
                raise FlickyWorkspaceError(f"zero-edit check found a modified artifact: {workspace}")
            sound.validate_document(sound.export_document(workspace), baseline)
            continue
        model = {"level_document": levels, "graphics_document": graphics,
                 "graphics_semantics_document": semantics, "graphics_sequences_document": sequences}[item["kind"]]
        model.validate_document(model.load_document(workspace), root)


def stage_sources(root: Path, destination: Path, driver_binary: Path, sound_binary: Path) -> Path:
    """Copy the source tree to `destination` with the Z80 includes pointing
    at the freshly assembled driver and sound banks; its main.s."""
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(root / "src", destination)
    replacements = {
        destination / "data" / "bank0.s": [(
            'binclude "build/z80_driver.bin"',
            f'binclude "{driver_binary.as_posix()}"',
        )],
        destination / "sound" / "z80" / "load_data.s": [
            ('binclude "build/z80_sound_data_sfx.bin"',
             f'binclude "{sound_binary.with_name(sound_binary.stem + "_sfx.bin").as_posix()}"'),
            ('binclude "build/z80_sound_data_music.bin"',
             f'binclude "{sound_binary.with_name(sound_binary.stem + "_music.bin").as_posix()}"'),
        ],
    }
    for path, pairs in replacements.items():
        text = path.read_text(encoding="utf-8")
        for old, new in pairs:
            if text.count(old) != 1:
                raise FlickyWorkspaceError(f"content staging expected one include in {path}: {old}")
            text = text.replace(old, new)
        path.write_text(text, encoding="utf-8", newline="\n")
    return destination / "main.s"
