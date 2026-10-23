"""Games a project rebuilds from their source rather than patching their ROM.

Their projects keep a snapshot of the game repository in the revision's
folder (`.retrolarium/<revision>/<directory>`), and the studio edits the
documents inside it; a build writes them into the source and assembles it.
Each game says how (its module under games/), and this registry says which
games work this way, by profile:

- the snapshot folder and its record, which the project names as its source
  and which carries the revision's ROM hash;
- `revisions(root)`: the ROM SHA-256 of each revision the repository builds;
- `documents()`: every document a project edits, relative to the snapshot
  (the views show some of them; all of them are kept and checked);
- `create`, `refresh`, `validate` and `build`, working on the project folder;
- `original(project)`: the revision's unedited ROM, kept in the snapshot.

Flicky came first and keeps its own project mode, `flicky-content`; later
games use `source`.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Callable


class SourceGameError(ValueError):
    """A source-built project that cannot be prepared, checked or built."""


@dataclass(frozen=True)
class SourceGame:
    profile: str
    mode: str
    directory: str
    record: str
    record_schema: str
    image_size: int
    revisions: Callable[[Path], dict[str, str]]
    documents: Callable[[], list[str]]
    create: Callable[[Path, Path, Path, str, str], Any]  # repository, ROM, project folder, revision, ROM name
    refresh: Callable[[Path, Path, Path], Any]  # repository, project folder, originals
    validate: Callable[[Path], dict[str, str]]  # project folder -> document hashes
    build: Callable[..., dict[str, Any]]  # project folder -> the build's report
    errors: tuple[type[Exception], ...] = (ValueError,)
    machine: Callable[[Path], str | None] = lambda project: None  # the MAME machine a project runs on
    original: Callable[[Path], Path] | None = None  # project folder -> the unedited ROM in its snapshot

    @property
    def manifest(self) -> str:
        """The project's [source] manifest: the snapshot record."""
        return f"{self.directory}/{self.record}"


def _flicky() -> SourceGame:
    from retrolarium_kit.games.flicky import content

    return SourceGame(
        profile="flicky-ue.v1",
        mode="flicky-content",
        directory=content.SOURCE_DIRECTORY,
        record=content.SNAPSHOT_RECORD,
        record_schema="retrolarium.flicky-source-snapshot",
        image_size=content.CANONICAL_SIZE,
        revisions=lambda root: {"ue": content.CANONICAL_SHA256},
        documents=lambda: list(content.WORKSPACE_FILES.values()),
        create=lambda root, rom, project, revision, name: content.import_flicky_source(root, rom, project),
        refresh=content.refresh_flicky_source,
        validate=lambda project: {
            content.WORKSPACE_FILES[key]: digest
            for key, digest in content.validate_flicky_source_snapshot(project).items()
        },
        build=lambda project, native_writer=None, **_: content.build_flicky_source_snapshot(
            project, native_writer=native_writer
        ),
        errors=(content.FlickyContentError,),
        original=lambda project: project / content.SOURCE_DIRECTORY / content.ROM_NAME,
    )


def _alien_soldier() -> SourceGame:
    from retrolarium_kit.core import source_snapshot
    from retrolarium_kit.games.alien_soldier import content

    def revisions(root: Path) -> dict[str, str]:
        profile = json.loads((root / "config/game_profile.json").read_text(encoding="utf-8"))
        return {item["id"]: item["sha256"] for item in profile["revisions"]}

    return SourceGame(
        profile="alien-soldier.v1",
        mode="source",
        directory=content.SOURCE_DIRECTORY,
        record=source_snapshot.RECORD,
        record_schema=source_snapshot.SCHEMA,
        image_size=0x200000,
        revisions=revisions,
        documents=content.documents,
        create=content.import_source,
        refresh=content.refresh_source,
        validate=content.validate,
        build=lambda project, **_: content.build(project),
        errors=(content.AlienSoldierContentError,),
        machine=content.machine,
        original=lambda project: (
            project / content.SOURCE_DIRECTORY / source_snapshot.load_record(project / content.SOURCE_DIRECTORY)["rom"]
        ),
    )


_FACTORIES: dict[str, Callable[[], SourceGame]] = {"flicky-ue.v1": _flicky, "alien-soldier.v1": _alien_soldier}
MODES = frozenset({"flicky-content", "source"})


def source_game(profile: str) -> SourceGame | None:
    """The source-built game of a profile, or None for ROM-patched games."""
    factory = _FACTORIES.get(profile)
    return factory() if factory else None


def require(profile: str, mode: str) -> SourceGame:
    game = source_game(profile)
    if game is None or game.mode != mode:
        raise SourceGameError(f"profile {profile} is not a {mode} game")
    return game
