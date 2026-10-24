"""Alien Soldier's documents as one workspace: exported from the source,
validated, and built back into a ROM.

The game repository's profile (config/game_profile.json, section `source`)
says how the game builds: which folders a build needs, the assembly steps,
each revision's assembler defines, the symbols its conditionals test, and
the range its boot code checksums; and which screens are static.

A build copies those folders into a fresh tree, writes every document into
the copy, assembles it with the kit's tools and, when anything was edited,
stamps the header checksum the revision's boot code expects. Unedited
documents leave the copy as it was, so the image is the original.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
from typing import Any, Mapping

from retrolarium_kit.games.alien_soldier import (
    animations,
    art,
    compressed_tiles,
    demos,
    envelopes,
    levels,
    mappings,
    maps,
    objects,
    palettes,
    pcm,
    screens,
    sound,
    stages,
    text,
    tiles,
)
from retrolarium_kit.platform.megadrive.asl import SourceTree
from retrolarium_kit.platform.megadrive.build import Step, build as assemble, stamp


PROFILE = Path("config/game_profile.json")
# Edited documents, in the order a build writes them. The screens come after
# the maps they share labels with... they share none: each label has one
# owner (see maps.py and screens.py).
DOCUMENTS = (
    levels,
    maps,
    demos,
    tiles,
    compressed_tiles,
    palettes,
    mappings,
    animations,
    screens,
    text,
    sound,
    envelopes,
    pcm,
)
DERIVED = (stages, objects, art)


class WorkspaceError(ValueError):
    """The workspace cannot be exported, validated or built."""


def source_profile(root: Path) -> dict[str, Any]:
    """The `source` section of the repository's game profile."""
    try:
        profile = json.loads((Path(root) / PROFILE).read_text(encoding="utf-8"))
        section = profile["source"]
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise WorkspaceError(f"{PROFILE.as_posix()} has no usable source section: {error}") from None
    return section


def _revision(section: Mapping[str, Any], revision: str) -> Mapping[str, Any]:
    try:
        return section["revisions"][revision]
    except (KeyError, TypeError):
        raise WorkspaceError(f"the profile does not build revision {revision!r}") from None


def tree(root: Path, section: Mapping[str, Any], revision: str) -> SourceTree:
    entry = section["steps"][-1]["entry"]
    return SourceTree(Path(root), entry=entry, defines=_revision(section, revision)["symbols"])


def _call(module, function: str, source: SourceTree, section: Mapping[str, Any], revision: str, *document):
    if module is screens or module is art:
        return getattr(module, function)(source, *document, section.get("screens", []), revision)
    return getattr(module, function)(source, *document)


def export(root: Path, revision: str) -> dict[str, dict[str, Any]]:
    """Every document, by its path in the workspace."""
    section = source_profile(root)
    source = tree(root, section, revision)
    return {module.DOCUMENT: _call(module, "export", source, section, revision) for module in DOCUMENTS + DERIVED}


def write(documents: Mapping[str, Mapping[str, Any]], folder: Path) -> list[str]:
    written = []
    for relative, document in documents.items():
        path = Path(folder) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        written.append(relative)
    return written


def read(folder: Path) -> dict[str, dict[str, Any]]:
    documents = {}
    for module in DOCUMENTS:
        path = Path(folder) / module.DOCUMENT
        try:
            documents[module.DOCUMENT] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise WorkspaceError(f"cannot read {module.DOCUMENT}: {error}") from None
    return documents


def apply(root: Path, revision: str, documents: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """Write the documents into the source at `root`; the files changed."""
    section = source_profile(root)
    source = tree(root, section, revision)
    for module in DOCUMENTS:
        try:
            _call(module, "apply", source, section, revision, documents[module.DOCUMENT])
        except KeyError:
            raise WorkspaceError(f"the workspace has no {module.DOCUMENT}") from None
        except ValueError as error:
            raise WorkspaceError(f"{module.DOCUMENT}: {error}") from None
    return source.save()


def validate(root: Path, revision: str, documents: Mapping[str, Mapping[str, Any]]) -> None:
    """Raise unless every document would write into the source."""
    section = source_profile(root)
    source = tree(root, section, revision)
    for module in DOCUMENTS:
        try:
            _call(module, "apply", source, section, revision, documents[module.DOCUMENT])
        except KeyError:
            raise WorkspaceError(f"the workspace has no {module.DOCUMENT}") from None
        except ValueError as error:
            raise WorkspaceError(f"{module.DOCUMENT}: {error}") from None


def build(root: Path, revision: str, documents: Mapping[str, Mapping[str, Any]], output: Path) -> dict[str, Any]:
    """The ROM the documents make, written to `output`."""
    section = source_profile(root)
    settings = _revision(section, revision)
    steps = [Step.parse(step) for step in section["steps"]]
    with tempfile.TemporaryDirectory(prefix=".alien-soldier-build-") as directory:
        staged = Path(directory)
        for folder in section["copy"]:
            shutil.copytree(Path(root) / folder, staged / folder)
        (staged / PROFILE).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path(root) / PROFILE, staged / PROFILE)
        changed = apply(staged, revision, documents)
        image = assemble(staged, steps, settings.get("defines", {})).read_bytes()
    if changed and "checksum" in settings:
        first, end = (int(str(value), 0) for value in settings["checksum"])
        image = stamp(image, first, end)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(image)
    return {"artifact": str(output), "size": len(image), "changed": changed}
