"""Read a game's config/lint.json and run the checks it asks for.

    {
      "schema": "retrolarium.lint",
      "schema_version": 1,
      "assembly": {"dialect": "ca65", "paths": ["src"]},
      "text": {"json": "indent", "skip": ["assets/generated", "references"]}
    }

`assembly` is the house style of the game's assembler (ca65, or asl with its
column settings); `text` is the text-format contract, with its JSON layout
("indent" or "valid") and the trees it skips beyond the ones every repository
skips. A section left out is not run.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
import json
from pathlib import Path
from typing import Any, Callable

from retrolarium_kit.core.lint import asl, ca65, text
from retrolarium_kit.core.lint.issues import Issue, LintError

SCHEMA = "retrolarium.lint"
CONFIG_PATH = Path("config/lint.json")
SECTIONS = ("assembly", "text")
DIALECTS: dict[str, tuple[Any, type]] = {"ca65": (ca65, ca65.Ca65Style), "asl": (asl, asl.AslStyle)}


@dataclass(frozen=True)
class LintConfig:
    dialect: str | None = None
    paths: tuple[str, ...] = ("src",)
    style: Any = None
    text_skip: tuple[str, ...] | None = None
    json_layout: str = "valid"
    sections: tuple[str, ...] = field(default=())


def _style(dialect: str, settings: dict[str, Any]) -> Any:
    module, style_type = DIALECTS[dialect]
    names = {item.name for item in fields(style_type)}
    unknown = sorted(set(settings) - names)
    if unknown:
        raise LintError(f"lint config: {dialect} has no setting {', '.join(unknown)}")
    return style_type(**settings)


def parse_config(document: dict[str, Any]) -> LintConfig:
    if document.get("schema") != SCHEMA or document.get("schema_version") != 1:
        raise LintError(f"lint config must be {SCHEMA} version 1")
    unknown = sorted(set(document) - {"schema", "schema_version", *SECTIONS})
    if unknown:
        raise LintError(f"lint config has unknown sections: {', '.join(unknown)}")
    sections: list[str] = []
    dialect = None
    paths: tuple[str, ...] = ("src",)
    style = None
    assembly = document.get("assembly")
    if assembly is not None:
        settings = dict(assembly)
        dialect = settings.pop("dialect", None)
        if dialect not in DIALECTS:
            raise LintError(f"lint config: assembly dialect must be one of {', '.join(DIALECTS)}")
        paths = tuple(settings.pop("paths", ["src"]))
        style = _style(dialect, settings)
        sections.append("assembly")
    text_skip = None
    json_layout = "valid"
    if document.get("text") is not None:
        settings = document["text"]
        unknown = sorted(set(settings) - {"skip", "json"})
        if unknown:
            raise LintError(f"lint config: text has no setting {', '.join(unknown)}")
        text_skip = tuple(settings.get("skip", []))
        json_layout = settings.get("json", "valid")
        if json_layout not in ("indent", "valid"):
            raise LintError("lint config: text json must be indent or valid")
        sections.append("text")
    return LintConfig(dialect, paths, style, text_skip, json_layout, tuple(sections))


def load_config(root: Path, path: Path | None = None) -> LintConfig:
    config = path if path is not None else root / CONFIG_PATH
    if not config.is_file():
        raise LintError(f"no lint config at {config}")
    try:
        return parse_config(json.loads(config.read_text(encoding="utf-8")))
    except json.JSONDecodeError as exc:
        raise LintError(f"lint config {config} is not JSON: {exc}") from exc


def assembly_files(root: Path, config: LintConfig, paths: tuple[str, ...] = ()) -> list[Path]:
    module, _ = DIALECTS[config.dialect]
    files: set[Path] = set()
    for entry in paths or config.paths:
        path = root / entry
        if path.is_dir():
            for suffix in module.SUFFIXES:
                files.update(path.rglob(f"*{suffix}"))
        elif path.suffix.lower() in module.SUFFIXES:
            files.add(path)
    return sorted(files, key=lambda item: item.as_posix().lower())


def run(root: Path, config: LintConfig, *, fix: bool = False, only: tuple[str, ...] = (),
        paths: tuple[str, ...] = (), report: Callable[[str], None] = print) -> list[Issue]:
    """Check (and with fix, first normalize) the tree; return what is left.
    `paths` narrows the assembly section to those files or folders."""
    root = root.resolve()
    selected = [section for section in config.sections if not only or section in only]
    issues: list[Issue] = []
    if "assembly" in selected:
        module, _ = DIALECTS[config.dialect]
        files = assembly_files(root, config, paths)
        if not files:
            raise LintError(f"no {config.dialect} sources under {', '.join(paths or config.paths)}")
        changed = sum(module.format_file(path, config.style) for path in files) if fix else 0
        found = [issue for path in files for issue in module.lint_file(path, config.style)]
        issues += found
        action = f"formatted {changed}, checked" if fix else "checked"
        report(f"[{'OK' if not found else 'FAIL'}] {config.dialect} style: {action} {len(files)} source(s)"
               + (f", {len(found)} issue(s)" if found else ""))
    if "text" in selected:
        files = text.project_files(root, config.text_skip or ())
        changed = 0
        if fix:
            for path in files:
                try:
                    changed += text.format_file(path, config.json_layout)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    pass  # reported by the check below
        found = [issue for path in files for issue in text.lint_file(path, config.json_layout)]
        issues += found
        action = f"formatted {changed}, checked" if fix else "checked"
        report(f"[{'OK' if not found else 'FAIL'}] text format: {action} {len(files)} file(s)"
               + (f", {len(found)} issue(s)" if found else ""))
    return issues
