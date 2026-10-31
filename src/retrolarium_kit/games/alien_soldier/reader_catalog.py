"""Source-symbol bindings owned by the game repository, not by the reader."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from retrolarium_kit.core.game_names import GameNames


class ReaderCatalogError(ValueError):
    """The source snapshot lacks the bindings its reader needs."""


@dataclass(frozen=True)
class ReaderCatalog:
    checked_builders: frozenset[str]
    played_builders: frozenset[str]
    traversals: frozenset[str]
    reach_traversal: str


def load(repository: Path) -> ReaderCatalog:
    catalog = GameNames.load(repository).catalog("boss_readers")
    prefix = "config/names.json: catalogs.boss_readers"
    if (
        not isinstance(catalog, dict)
        or type(catalog.get("schema_version")) is not int
        or catalog["schema_version"] != 1
    ):
        raise ReaderCatalogError(
            f"{prefix} must have schema_version 1; refresh the game repository and its source snapshot"
        )

    def symbols(key: str) -> frozenset[str]:
        values = catalog.get(key)
        if (
            not isinstance(values, list)
            or not values
            or any(not isinstance(value, str) or not value.isascii() or not value.isidentifier() for value in values)
        ):
            raise ReaderCatalogError(f"{prefix}.{key} must be a nonempty list of source symbols")
        if len(set(values)) != len(values):
            raise ReaderCatalogError(f"{prefix}.{key} repeats a source symbol")
        return frozenset(values)

    checked = symbols("checked_builders")
    played = symbols("played_builders")
    traversals = symbols("traversals")
    reach = catalog.get("reach_traversal")
    if not isinstance(reach, str) or reach not in traversals:
        raise ReaderCatalogError(f"{prefix}.reach_traversal must name one of traversals")
    if not played <= checked:
        raise ReaderCatalogError(f"{prefix}.played_builders must be checked_builders")
    return ReaderCatalog(checked, played, traversals, reach)
