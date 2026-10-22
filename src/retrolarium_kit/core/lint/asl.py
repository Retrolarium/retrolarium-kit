"""The AS house style of the Mega Drive disassemblies, checked and applied.

The layout is the Mega Drive disassembly convention rather than the ca65 one
of the NES games: labels at column zero, the mnemonic field at column 16, the
operand eight columns later and inline comments at column 56 (or two spaces
out when the code reaches it). Mnemonics stay lowercase, which is what AS
emits and what every Sonic disassembly uses. Consecutive lines that carry a
label and a statement share one column for the statement, on a grid of four,
so an equate table reads as a table.

Everything the formatter changes is whitespace or comment text, so a formatted
file assembles to the same bytes.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from pathlib import Path

from retrolarium_kit.core.lint.issues import Issue, write_text

SUFFIXES = (".s", ".inc")

LABEL_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):(.*)$")
STATEMENT_RE = re.compile(r"^(\s*)(\S+)(.*)$")
INLINE_COMMENT_RE = re.compile(r"^(.*?\S)(\s+)(;.*)$")
BLOCK_OPEN_RE = re.compile(r"^\s*\S+\s+macro\b", re.IGNORECASE)
BLOCK_CLOSE_RE = re.compile(r"^\s*endm\b", re.IGNORECASE)
# "NAME equ value" and "NAME macro args" define NAME in the label field, so the
# name stays at column zero even though what follows reads like a mnemonic.
LABEL_FIELD_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(\s+(?:equ|set|macro)\b.*)$", re.IGNORECASE)


@dataclass(frozen=True)
class AslStyle:
    mnemonic_column: int = 16
    mnemonic_field: int = 8
    comment_column: int = 56
    # A label that carries a statement steps to the next stop of this grid.
    label_tab: int = 4
    # Whole-line comments are indented in multiples of this.
    comment_indent_step: int = 4


def split_comment(text: str) -> tuple[str, str]:
    """Split a line into (code, comment), respecting quoted strings: a
    semicolon inside a string literal is part of the data."""
    in_string = False
    for index, char in enumerate(text):
        if char == '"':
            in_string = not in_string
        elif char == ";" and not in_string:
            return text[:index], text[index:]
    return text, ""


def untab(text: str) -> str:
    """Tabs to single spaces, except inside a quoted string, where a tab is
    a byte of the data."""
    code, comment = split_comment(text)
    parts = code.split('"')
    # Even parts lie outside strings.
    return '"'.join(
        part.replace("\t", " ") if index % 2 == 0 else part for index, part in enumerate(parts)
    ) + comment.replace("\t", " ")


def _normalize_comment(comment: str) -> str:
    """One space after the semicolon, no trailing period; runs of semicolons,
    dashes or equals signs are banner lines and stay as they are."""
    body = comment[1:]
    if body[:1] in {";", "-", "="}:
        return comment.rstrip()
    text = body.strip().rstrip(".")
    return f"; {text}" if text else ";"


def _place_comment(code: str, comment: str, style: AslStyle) -> str:
    if not comment:
        return code.rstrip()
    code = code.rstrip()
    if not code:
        return comment
    gap = style.comment_column - len(code)
    return code + (" " * gap if gap >= 2 else "  ") + comment


def _pad_label(head: str, column: int) -> str:
    return head.ljust(column) if len(head) < column else head + " "


def _tab_stop(width: int, minimum: int, style: AslStyle) -> int:
    column = minimum
    while column < width + 1:
        column += style.label_tab
    return column


def _label_head(line: str) -> str | None:
    """The label field of a line that also carries a statement, else None."""
    code, _ = split_comment(line.replace("\t", " ").rstrip())
    if not code.strip() or code[:1].isspace():
        return None
    field_match = LABEL_FIELD_RE.match(code)
    if field_match:
        return field_match.group(1)
    label_match = LABEL_RE.match(code)
    if label_match and label_match.group(2).strip():
        return f"{label_match.group(1)}:"
    return None


def _is_bare_label(line: str) -> bool:
    code, _ = split_comment(line.replace("\t", " ").rstrip())
    match = LABEL_RE.match(code)
    return bool(match) and not match.group(2).strip()


def _label_columns(lines: list[str], style: AslStyle) -> list[int]:
    """One shared statement column for every run of label lines. A bare label
    does not break a run (`Name: binclude` alternates with `Name_End:`); a
    blank line, a comment or an instruction does."""
    minimum = style.mnemonic_column
    columns = [minimum] * len(lines)
    members: list[int] = []

    def flush() -> None:
        nonlocal members
        if members:
            shared = _tab_stop(max(len(_label_head(lines[i]) or "") for i in members), minimum, style)
            for index in members:
                columns[index] = shared
        members = []

    for index, line in enumerate(lines):
        if _label_head(line) is not None:
            members.append(index)
        elif not _is_bare_label(line):
            flush()
    flush()
    return columns


def _collapse_operand_spacing(code: str, style: AslStyle) -> str:
    """Align the operand field; the operand text itself is copied verbatim so
    quoted strings keep their bytes."""
    match = STATEMENT_RE.match(code)
    if not match:
        return code
    mnemonic, rest = match.group(2), match.group(3).lstrip()
    if not rest:
        return mnemonic
    if len(mnemonic) < style.mnemonic_field:
        return f"{mnemonic.ljust(style.mnemonic_field - 1)} {rest}"
    return f"{mnemonic} {rest}"


def _normalize_line(line: str, depth: int, column: int | None, style: AslStyle) -> str:
    line = untab(line).rstrip()
    if not line.strip():
        return ""
    code, comment = split_comment(line)
    stripped = code.strip()
    comment = _normalize_comment(comment) if comment else ""
    step = style.comment_indent_step
    if not stripped:
        indent = len(code) - len(code.lstrip(" "))
        indent = round(indent / step) * step if step else 0
        return " " * indent + comment

    indent = style.mnemonic_column + step * depth
    field_match = LABEL_FIELD_RE.match(code)
    if field_match:
        name, rest = field_match.group(1), field_match.group(2)
        stop = column if column is not None else _tab_stop(len(name), indent, style)
        head = _pad_label(name, stop) + _collapse_operand_spacing(rest.strip(), style)
        return _place_comment(head, comment, style)

    label_match = LABEL_RE.match(code)
    if label_match:
        name, rest = label_match.group(1), label_match.group(2)
        body = _collapse_operand_spacing(rest.strip(), style)
        head = f"{name}:"
        if body:
            stop = column if column is not None else _tab_stop(len(head), indent, style)
            head = _pad_label(head, stop) + body
        return _place_comment(head, comment, style)

    return _place_comment(" " * indent + _collapse_operand_spacing(stripped, style), comment, style)


def lint_file(path: Path, style: AslStyle = AslStyle()) -> list[Issue]:
    try:
        text = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return [Issue(path, 1, "read-error", str(exc))]
    issues: list[Issue] = []
    if "\r" in text:
        issues.append(Issue(path, 0, "line-ending", "file contains CR; use LF only"))
    if text and not text.endswith("\n"):
        issues.append(Issue(path, 0, "final-newline", "file does not end with a newline"))
    if text.endswith("\n\n"):
        issues.append(Issue(path, 0, "final-newline", "file ends with more than one newline"))

    lines = text.split("\n")
    columns = _label_columns(lines, style)
    depth = 0
    previous_blank = False
    step = style.comment_indent_step
    for number, line in enumerate(lines, 1):
        if "\t" in line:
            issues.append(Issue(path, number, "tab", "tab character; use spaces"))
        if line != line.rstrip():
            issues.append(Issue(path, number, "trailing-space", "trailing whitespace"))
        if any(ord(char) > 0x7E or (ord(char) < 0x20 and char != "\t") for char in line):
            issues.append(Issue(path, number, "charset", "non-ASCII character; rewrite in English"))

        if not line.strip():
            if previous_blank:
                issues.append(Issue(path, number, "blank-run", "more than one blank line"))
            if number == 1:
                issues.append(Issue(path, number, "leading-blank", "file starts with a blank line"))
            previous_blank = True
            continue
        previous_blank = False

        if BLOCK_CLOSE_RE.match(line):
            depth = max(0, depth - 1)

        code, comment = split_comment(line.rstrip())
        if comment:
            body = comment[1:]
            if body[:1] not in {";", "-", "="}:
                if body and not body.startswith(" ") or body.startswith("  "):
                    issues.append(Issue(path, number, "comment-space", "use exactly one space after ';'"))
                if body.strip().endswith("."):
                    issues.append(Issue(path, number, "comment-period", "drop the period ending a comment"))
            if not code.strip():
                indent = len(code) - len(code.lstrip(" "))
                if (indent % step) if step else indent:
                    issues.append(
                        Issue(path, number, "comment-indent", f"indent whole-line comments in multiples of {step}")
                    )
            else:
                inline = INLINE_COMMENT_RE.match(line.rstrip())
                if inline:
                    wanted = style.comment_column - len(inline.group(1))
                    gap = len(inline.group(2))
                    if (wanted >= 2 and gap != wanted) or (wanted < 2 and gap != 2):
                        issues.append(
                            Issue(
                                path,
                                number,
                                "comment-column",
                                f"inline comments start at column {style.comment_column}, "
                                f"or two spaces out when the code reaches it",
                            )
                        )

        expected = _normalize_line(line, depth, columns[number - 1], style)
        if line.rstrip() != expected:
            issues.append(Issue(path, number, "layout", f"expected: {expected!r}"))
        if BLOCK_OPEN_RE.match(line):
            depth += 1
    return issues


def format_text(text: str, style: AslStyle = AslStyle()) -> str:
    """The house-style form of an AS source."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    columns = _label_columns(lines, style)
    output: list[str] = []
    depth = 0
    previous_blank = False
    for index, line in enumerate(lines):
        if BLOCK_CLOSE_RE.match(line):
            depth = max(0, depth - 1)
        normalized = _normalize_line(line, depth, columns[index], style)
        if BLOCK_OPEN_RE.match(line):
            depth += 1
        if not normalized:
            if previous_blank or not output:
                continue
            previous_blank = True
            output.append("")
            continue
        previous_blank = False
        output.append(normalized)
    while output and not output[-1]:
        output.pop()
    return "\n".join(output) + "\n" if output else ""


def format_file(path: Path, style: AslStyle = AslStyle()) -> bool:
    original = path.read_text(encoding="utf-8")
    normalized = format_text(original, style)
    if normalized == original:
        return False
    write_text(path, normalized)
    return True
