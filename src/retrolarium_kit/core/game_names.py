"""The names inside a game, from its repository.

A game's characters, enemies, items and places, and the lists that carry
them (an animation's frames, the source files a codec reads), belong to the
game: its repository keeps them in config/names.json, in English, and the kit
reads them from there. The kit keeps none of them.

    {"schema": "retrolarium.names", "schema_version": 1,
     "names": {"enemies": {"0x06": "...", ...}},
     "catalogs": {"animation_sources": ["..."]},
     "guard": ["..."]}
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

PATH = Path("config/names.json")
SCHEMA = "retrolarium.names"


class GameNamesError(ValueError):
    """A names document is not one."""


def _numeric(key: str) -> int | None:
    try:
        if key[:2].lower() == "0x":
            return int(key[2:], 16)
        if key[:1] == "$":
            return int(key[1:], 16)
        return int(key, 10)
    except ValueError:
        return None


@dataclass(frozen=True)
class GameNames:
    names: dict[str, dict[str, str]] = field(default_factory=dict)
    catalogs: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, repository: Path) -> "GameNames":
        """The names a game repository (or a snapshot of its source) keeps;
        empty when it has none."""
        path = repository / PATH
        if not path.is_file():
            return cls()
        document = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(document, dict) or document.get("schema") != SCHEMA:
            raise GameNamesError(f"{path} is not a {SCHEMA} document")
        names = {
            str(category): {str(key): str(value) for key, value in entries.items()}
            for category, entries in document.get("names", {}).items()
        }
        return cls(names, dict(document.get("catalogs", {})))

    def name(self, category: str, key: str | int, fallback: str = "") -> str:
        entries = self.names.get(category, {})
        if isinstance(key, int):
            for text, value in entries.items():
                if _numeric(text) == key:
                    return value
            return fallback
        return entries.get(key, fallback)

    def catalog(self, key: str, default: Any = None) -> Any:
        return self.catalogs.get(key, default)
