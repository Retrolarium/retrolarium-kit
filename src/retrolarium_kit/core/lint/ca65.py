"""The ca65 house style of the NES disassemblies, checked and applied.

Labels, symbol assignments and whole-line comments start at column zero;
statements are indented four spaces per open block (.proc, .scope, .if and
the like), with at least one level; directives are lowercase and mnemonics
uppercase, each followed by one space before the operand. Comments take one
space after the semicolon, no closing period, and sit two spaces after the
code they explain; a label stands alone on its line. Sources are printable
ASCII with LF endings, no tabs, no trailing spaces, no blank line at the start
or end and never two in a row.

A comment that only repeats an address and its bytes ("$C000 A9 00") says
nothing and is refused. The formatter changes only whitespace and comment
text, so a formatted source assembles to the same bytes.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from pathlib import Path

from retrolarium_kit.core.lint.issues import Issue, write_text

SUFFIXES = (".asm", ".inc")

MNEMONICS = frozenset(
    """
    ADC AND ASL BCC BCS BEQ BIT BMI BNE BPL BRK BVC BVS CLC CLD CLI CLV
    CMP CPX CPY DEC DEX DEY EOR INC INX INY JMP JSR LDA LDX LDY LSR NOP
    ORA PHA PHP PLA PLP ROL ROR RTI RTS SBC SEC SED SEI STA STX STY TAX
    TAY TSX TXA TXS TYA
    """.split()
)
DATA_DIRECTIVES = frozenset(
    {".addr", ".asciiz", ".byte", ".dbyt", ".faraddr", ".incbin", ".literal", ".res", ".tag", ".word"}
)
BLOCK_OPENERS = frozenset({".enum", ".macro", ".proc", ".repeat", ".scope", ".struct", ".union"})
BLOCK_CLOSERS = frozenset(
    {".endenum", ".endmacro", ".endproc", ".endrepeat", ".endscope", ".endstruct", ".endunion"}
)
BLOCK_MIDDLES = frozenset({".else", ".elseif"})
DIRECTIVE_RE = re.compile(r"^\.[A-Za-z][A-Za-z0-9]*")
LABEL_RE = re.compile(r"^(?P<label>[A-Za-z_@.?][A-Za-z0-9_@.?]*:)(?P<tail>.*)$")
TOKEN_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*")
ASSIGNMENT_RE = re.compile(r"^[A-Za-z_@.?][A-Za-z0-9_@.?]*\s*=")
MACHINE_CODE_COMMENT_RE = re.compile(r"^\$[0-9A-Fa-f]{4}(?:\s+[0-9A-Fa-f]{2})*$")


@dataclass(frozen=True)
class Ca65Style:
    """Every NES game keeps the same ca65 style; there is nothing to choose."""


def split_comment(line: str) -> tuple[str, str | None]:
    """Split at the first semicolon outside a quoted string."""
    quote: str | None = None
    escaped = False
    for index, character in enumerate(line):
        if escaped:
            escaped = False
        elif character == "\\" and quote is not None:
            escaped = True
        elif quote is not None:
            if character == quote:
                quote = None
        elif character in {'"', "'"}:
            quote = character
        elif character == ";":
            return line[:index], line[index + 1 :]
    return line, None


def is_opener(directive: str) -> bool:
    return directive in BLOCK_OPENERS or directive.startswith(".if")


def is_closer(directive: str) -> bool:
    return directive in BLOCK_CLOSERS or directive == ".endif"


def statement_indent(depth: int) -> int:
    return max(depth, 1) * 4


def directive_indent(directive: str, depth: int) -> int:
    if is_closer(directive) or directive in BLOCK_MIDDLES:
        return max(depth - 1, 0) * 4
    if is_opener(directive):
        return depth * 4
    return statement_indent(depth) if directive in DATA_DIRECTIVES else depth * 4


def next_depth(directive: str, depth: int) -> int:
    if is_closer(directive):
        return max(depth - 1, 0)
    return depth + 1 if is_opener(directive) else depth


def _text_issues(path: Path, payload: bytes, text: str) -> list[Issue]:
    issues: list[Issue] = []
    if b"\r" in payload:
        issues.append(Issue(path, 1, "line-ending", "use LF line endings"))
    if not payload.endswith(b"\n"):
        issues.append(Issue(path, 1, "final-newline", "file must end with one LF"))
    elif payload.endswith(b"\n\n"):
        issues.append(Issue(path, len(text.splitlines()) or 1, "final-newline", "file has a blank line at EOF"))
    for line_number, line in enumerate(text.splitlines(), 1):
        for column, character in enumerate(line, 1):
            if character != "\t" and not " " <= character <= "~":
                issues.append(Issue(path, line_number, "ascii-only",
                                    f"only printable ASCII and LF are allowed; found "
                                    f"U+{ord(character):04X} at column {column}"))
                break
        if "\t" in line:
            issues.append(Issue(path, line_number, "tab", "tabs are not allowed"))
        if line.rstrip(" \t") != line:
            issues.append(Issue(path, line_number, "trailing-whitespace", "remove trailing whitespace"))
    return issues


def _comment_issues(path: Path, line_number: int, before: str, comment: str) -> list[Issue]:
    issues: list[Issue] = []
    content = comment.strip()
    if not comment.startswith(" ") or comment.startswith("  "):
        issues.append(Issue(path, line_number, "comment-space", "use exactly one space after ';'"))
    if content.endswith("."):
        issues.append(Issue(path, line_number, "comment-period", "remove the period at the end of the comment"))
    if MACHINE_CODE_COMMENT_RE.fullmatch(content):
        issues.append(Issue(path, line_number, "machine-code-comment",
                            "remove the address/byte dump or add a meaningful explanation"))
    if before.strip() and len(before) - len(before.rstrip(" ")) != 2:
        issues.append(Issue(path, line_number, "inline-comment-gap",
                            "use exactly two spaces before an inline comment"))
    return issues


def lint_file(path: Path, style: Ca65Style = Ca65Style()) -> list[Issue]:
    try:
        payload = path.read_bytes()
        text = payload.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return [Issue(path, 1, "read-error", str(exc))]

    issues = _text_issues(path, payload, text)
    lines = text.splitlines()
    if lines and not lines[0].strip():
        issues.append(Issue(path, 1, "file-start", "remove blank lines at the start of the file"))
    depth = 0
    previous_blank = False
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            if previous_blank:
                issues.append(Issue(path, line_number, "blank-lines", "keep at most one consecutive blank line"))
            previous_blank = True
            continue
        previous_blank = False

        before, comment = split_comment(line)
        if comment is not None:
            issues.extend(_comment_issues(path, line_number, before, comment))
        if not before.strip():
            if before:
                issues.append(Issue(path, line_number, "comment-indent", "whole-line comments start at column zero"))
            continue

        leading = len(before) - len(before.lstrip(" "))
        statement = before.strip()
        label = LABEL_RE.match(statement)
        if label:
            if leading:
                issues.append(Issue(path, line_number, "label-indent", "labels must start at column zero"))
            if comment is not None or label.group("tail").strip():
                issues.append(Issue(path, line_number, "label-line",
                                    "a label stands alone: put its comment on the line before "
                                    "and its statement on the line after"))
            continue

        directive = DIRECTIVE_RE.match(statement)
        if directive:
            token = directive.group(0)
            normalized = token.lower()
            if token != normalized:
                issues.append(Issue(path, line_number, "directive-case", "write ca65 directives in lowercase"))
            expected = directive_indent(normalized, depth)
            if leading != expected:
                issues.append(Issue(path, line_number, "directive-indent",
                                    f"expected {expected} leading spaces, found {leading}"))
            rest = statement[len(token) :]
            if rest and (not rest.startswith(" ") or rest.startswith("  ")):
                issues.append(Issue(path, line_number, "operand-gap", "use one space before directive operands"))
            depth = next_depth(normalized, depth)
            continue

        token = TOKEN_RE.match(statement)
        if token and token.group(0).upper() in MNEMONICS:
            mnemonic = token.group(0)
            if mnemonic != mnemonic.upper():
                issues.append(Issue(path, line_number, "mnemonic-case", "write 6502 mnemonics in uppercase"))
            expected = statement_indent(depth)
            if leading != expected:
                issues.append(Issue(path, line_number, "instruction-indent",
                                    f"expected {expected} leading spaces, found {leading}"))
            rest = statement[len(mnemonic) :]
            if rest and (not rest.startswith(" ") or rest.startswith("  ")):
                issues.append(Issue(path, line_number, "operand-gap", "use one space before instruction operands"))
        elif ASSIGNMENT_RE.match(statement):
            if leading:
                issues.append(Issue(path, line_number, "assignment-indent",
                                    "symbol assignments must start at column zero"))
        else:
            expected = statement_indent(depth)
            if leading != expected:
                issues.append(Issue(path, line_number, "statement-indent",
                                    f"expected {expected} leading spaces for a macro or statement, found {leading}"))
    if depth:
        issues.append(Issue(path, len(lines) or 1, "block-depth", f"{depth} assembly block(s) not closed"))
    return issues


def _compose(code: str, comment: str | None) -> str:
    if comment is None:
        return code
    content = comment.strip().rstrip(".")
    if not content:
        return code
    if MACHINE_CODE_COMMENT_RE.fullmatch(content):
        return code
    return f"{code}  ; {content}" if code else f"; {content}"


def _normalize_statement(statement: str, depth: int) -> tuple[str, int]:
    label = LABEL_RE.match(statement)
    if label and not label.group("tail").strip():
        return label.group("label"), depth
    directive = DIRECTIVE_RE.match(statement)
    if directive:
        token = directive.group(0).lower()
        rest = statement[len(directive.group(0)) :].strip()
        code = token if not rest else f"{token} {rest}"
        return " " * directive_indent(token, depth) + code, next_depth(token, depth)
    token = TOKEN_RE.match(statement)
    if token and token.group(0).upper() in MNEMONICS:
        mnemonic = token.group(0).upper()
        rest = statement[len(token.group(0)) :].strip()
        code = mnemonic if not rest else f"{mnemonic} {rest}"
        return " " * statement_indent(depth) + code, depth
    if ASSIGNMENT_RE.match(statement):
        return statement, depth
    return " " * statement_indent(depth) + statement, depth


def format_text(original: str, style: Ca65Style = Ca65Style()) -> str:
    """The house-style form of a ca65 source."""
    logical: list[tuple[str, str | None]] = []
    for raw in original.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        before, comment = split_comment(raw.expandtabs(4).rstrip())
        statement = before.strip()
        label = LABEL_RE.match(statement)
        if label and label.group("tail").strip():
            logical.extend(((label.group("label"), None), (label.group("tail").strip(), comment)))
        elif label and comment is not None:
            logical.extend((("", comment), (label.group("label"), None)))
        else:
            logical.append((statement, comment))

    output: list[str] = []
    depth = 0
    for statement, comment in logical:
        if statement:
            code, depth = _normalize_statement(statement, depth)
            line = _compose(code, comment)
        else:
            line = _compose("", comment)
        if line:
            output.append(line)
        elif output and output[-1]:
            output.append("")
    while output and not output[-1]:
        output.pop()
    return "\n".join(output) + "\n"


def format_file(path: Path, style: Ca65Style = Ca65Style()) -> bool:
    original = path.read_text(encoding="utf-8")
    normalized = format_text(original, style)
    if normalized == original:
        return False
    write_text(path, normalized)
    return True
