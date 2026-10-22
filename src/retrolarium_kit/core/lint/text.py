"""The text-format contract for a game repository's own files.

Text files (sources, configs, docs, scripts) use LF endings, carry no
trailing spaces and end with exactly one newline (an empty file stays empty).
JSON must parse; a game that lays its JSON out by machine asks for two-space
indentation with the key order kept (`"json": "indent"`), one that lays it out
by hand keeps its layout (`"json": "valid"`, the default).

The files are the ones Git sees in a checkout (tracked, or untracked and not
ignored), else every file. Trees the repository does not author are skipped:
Git's, the build output, the Retrolarium project folders (.retrolarium, hack),
Python caches and whatever the game's config adds.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

from retrolarium_kit.core.lint.issues import Issue, write_text

TEXT_SUFFIXES = frozenset({".asm", ".cfg", ".inc", ".java", ".json", ".lua", ".md", ".mk", ".py", ".s", ".txt"})
TEXT_NAMES = frozenset({".editorconfig", ".gitattributes", ".gitignore", "Makefile"})
ALWAYS_SKIPPED = (".git", ".retrolarium", "build", "hack")


def _skipped(relative: Path, skip: tuple[tuple[str, ...], ...]) -> bool:
    parts = relative.parts
    if "__pycache__" in parts:
        return True
    return any(parts[: len(prefix)] == prefix for prefix in skip)


def _candidates(root: Path) -> list[Path]:
    if (root / ".git").exists():
        listed = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=root,
            capture_output=True,
            check=False,
        )
        if listed.returncode == 0:
            return [root / os.fsdecode(raw) for raw in listed.stdout.split(b"\0") if raw]
    return list(root.rglob("*"))


def project_files(root: Path, skip: tuple[str, ...] = ()) -> list[Path]:
    """The text files the contract covers, under root."""
    prefixes = tuple(tuple(Path(item).parts) for item in (*ALWAYS_SKIPPED, *skip))
    paths: list[Path] = []
    for path in _candidates(root):
        relative = path.relative_to(root)
        if not path.is_file() or _skipped(relative, prefixes):
            continue
        if path.suffix.lower() in TEXT_SUFFIXES or path.name in TEXT_NAMES:
            paths.append(path)
    return sorted(set(paths))


def normalize_text(text: str) -> str:
    if not text:
        return text
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(line.rstrip() for line in lines).rstrip("\n") + "\n"


def formatted(path: Path, original: str, json_layout: str = "valid") -> str:
    if path.suffix.lower() == ".json":
        document = json.loads(original)
        if json_layout == "indent":
            return json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    return normalize_text(original)


def lint_file(path: Path, json_layout: str = "valid") -> list[Issue]:
    try:
        original = path.read_bytes().decode("utf-8")
        expected = formatted(path, original, json_layout)
    except (OSError, UnicodeDecodeError) as exc:
        return [Issue(path, 0, "read-error", str(exc))]
    except json.JSONDecodeError as exc:
        return [Issue(path, exc.lineno, "json", f"invalid JSON: {exc.msg}")]
    if original == expected:
        return []
    wanted = "LF, no trailing spaces, one final newline"
    if json_layout == "indent" and path.suffix.lower() == ".json":
        wanted += ", JSON indented by 2"
    return [Issue(path, 0, "text-format", f"needs formatting ({wanted})")]


def format_file(path: Path, json_layout: str = "valid") -> bool:
    original = path.read_bytes().decode("utf-8")
    expected = formatted(path, original, json_layout)
    if original == expected:
        return False
    write_text(path, expected)
    return True
