"""Check the spelling of assembly definitions without formatting the source."""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
import re

from retrolarium_kit.core.lint import asl, ca65
from retrolarium_kit.core.lint.issues import Issue, LintError

MAX_LENGTH = 40
SUFFIXES = (".s", ".inc", ".asm")
EXCEPTIONS_PATH = Path("config/asm-label-exceptions.json")
ASL_LABEL = re.compile(
    r"^([A-Za-z_.][A-Za-z0-9_.]*)(?:\s*:|\s+(?:(?:equ|set|macro|function|label|struct|endstruct)\b|:=))",
    re.IGNORECASE,
)
ASL_LABEL_FIELD = re.compile(r"^([A-Za-z_.][A-Za-z0-9_.]*)(?=\s|$)")
CA65_PROC = re.compile(r"^\.proc\s+([A-Za-z_@.?][A-Za-z0-9_@.?]*)", re.IGNORECASE)
EXCEPTION_NAME = re.compile(r"[A-Za-z_.@?][A-Za-z0-9_.@?]*")


def load_exceptions(root: Path) -> dict[str, dict[str, str]]:
    path = root / EXCEPTIONS_PATH
    if not path.exists():
        return {}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LintError(f"invalid label exceptions in {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise LintError(f"{path}: expected an object keyed by source path")
    for source, names in document.items():
        relative = PurePosixPath(source)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or relative.as_posix() != source
            or "\\" in source
            or ":" in source
            or relative.suffix not in SUFFIXES
            or not isinstance(names, dict)
        ):
            raise LintError(f"{path}: invalid source entry {source!r}")
        for name, reason in names.items():
            if (
                not EXCEPTION_NAME.fullmatch(name)
                or len(name) <= MAX_LENGTH
                or not isinstance(reason, str)
                or not reason.strip()
            ):
                raise LintError(f"{path}: expected a long label and nonempty reason for {source}:{name}")
    return document


def lint_file(path: Path, dialect: str, exceptions: dict[str, str]) -> list[Issue]:
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return [Issue(path, 1, "read-error", str(exc))]
    issues = []
    for number, line in enumerate(source.splitlines(), 1):
        if dialect == "asl":
            code, _ = asl.split_comment(line)
            # AS also accepts an implicit label in the first column.
            match = ASL_LABEL.match(code.lstrip()) or ASL_LABEL_FIELD.match(code)
            name = match[1] if match else None
        else:
            code, _ = ca65.split_comment(line)
            match = ca65.LABEL_RE.match(code.lstrip())
            proc = CA65_PROC.match(code.lstrip()) if not match else None
            name = match["label"][:-1] if match else proc[1] if proc else None
        if name and len(name) > MAX_LENGTH and name not in exceptions:
            issues.append(
                Issue(path, number, "label-length", f"{name} has {len(name)} characters (maximum {MAX_LENGTH})")
            )
    return issues
