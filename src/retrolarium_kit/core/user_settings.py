"""What the kit remembers for this user between runs (the MAME it plays
movies with, say): one JSON file beside the tool cache, %LOCALAPPDATA%\\Retrolarium
on Windows, else $XDG_CONFIG_HOME/retrolarium or ~/.config/retrolarium.
RETROLARIUM_SETTINGS names another file."""

from __future__ import annotations

import json
import os
from pathlib import Path


def settings_path() -> Path:
    override = os.environ.get("RETROLARIUM_SETTINGS")
    if override:
        return Path(override).expanduser()
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "Retrolarium" / "settings.json"
    base = os.environ.get("XDG_CONFIG_HOME")
    return (Path(base) if base else Path.home() / ".config") / "retrolarium" / "settings.json"


def _load(path: Path) -> dict:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        from retrolarium_kit.core.authoring import AuthoringError

        raise AuthoringError(f"cannot read {path}: {exc}") from exc
    return document if isinstance(document, dict) else {}


def get(name: str) -> str | None:
    value = _load(settings_path()).get(name)
    return value if isinstance(value, str) and value else None


def put(name: str, value: str) -> Path:
    path = settings_path()
    document = _load(path)
    document[name] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
