"""An AS (asl) assembly source read as data, and written back in place.

Mega Drive disassemblies built with AS keep their data in two shapes: a
label followed by `dc.b`, `dc.w` or `dc.l` lines, or a label on a `binclude`
of a binary file the build splits out of the ROM. A codec that edits such
data needs both directions: the values a label holds, and a way to put
changed values back without disturbing anything else, so that an unedited
document rebuilds the source byte for byte.

`SourceTree` follows the `include` chain from the entry file the way the
assembler does, keeps only the lines the build's conditionals select
(`if`/`ifdef`/`ifndef`/`else`/`endif` over the symbols the caller defines),
and indexes every label with the directives that follow it up to the next
label. Writing replaces only the numeric operands whose values changed, in
the notation they were written in, and refuses to change a symbolic one; a
`binclude` label is written by replacing its file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
import re
from typing import Iterable, Mapping


class AslSourceError(ValueError):
    """The source cannot be read as data, or a write would not fit it."""


WIDTHS = {"dc.b": 1, "dc.w": 2, "dc.l": 4}
LABEL = re.compile(r"^(?P<name>[A-Za-z_][A-Za-z0-9_]*):")
EQUATE = re.compile(r"^(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s+(?:equ|set|=)\s+(?P<value>\S+)\s*$", re.IGNORECASE)
# `db` is the Z80 programs' spelling of dc.b.
DIRECTIVE = re.compile(r"^\s*(?P<kind>dc\.[bwl]|db|binclude|include)\s+(?P<body>.*)$", re.IGNORECASE)
CONDITIONAL = re.compile(r"^\s*(?P<word>if|ifdef|ifndef|else|elseif|endif)\b\s*(?P<argument>[^;]*)", re.IGNORECASE)
NUMBER = re.compile(r"^(?P<sign>-?)(?:\$(?P<hex>[0-9A-Fa-f]+)|%(?P<binary>[01]+)|(?P<decimal>[0-9]+))$")
QUOTED = re.compile(r"\"([^\"]*)\"")
# A macro definition ("name macro args") runs to its endm; while, rept and
# irp blocks inside it end with endm too.
MACRO_START = re.compile(r"^(?:[A-Za-z_][A-Za-z0-9_]*\s+macro|\s+(?:while|rept|irp|irpc))\b", re.IGNORECASE)
MACRO_END = re.compile(r"^\s+endm\b", re.IGNORECASE)


def parse_number(text: str) -> int | None:
    """A numeric literal ($hex, %binary or decimal, maybe negative), or None
    for anything else (a symbol, an expression, a string)."""
    match = NUMBER.match(text.strip())
    if match is None:
        return None
    if match["hex"] is not None:
        value = int(match["hex"], 16)
    elif match["binary"] is not None:
        value = int(match["binary"], 2)
    else:
        value = int(match["decimal"])
    return -value if match["sign"] else value


def format_like(original: str, value: int) -> str:
    """`value` in the notation `original` was written in: $hex stays hex
    (with at least its digit count), %binary stays binary, decimal stays
    decimal."""
    text = original.strip().lstrip("-")
    sign = "-" if value < 0 else ""
    magnitude = abs(value)
    if text.startswith("$"):
        return f"{sign}${magnitude:0{len(text) - 1}X}"
    if text.startswith("%"):
        return f"{sign}%{magnitude:0{len(text) - 1}b}"
    return str(value)


def _code(line: str) -> str:
    """The line without its comment (a ';' outside quotes)."""
    quote = None
    for index, character in enumerate(line):
        if quote:
            if character == quote:
                quote = None
        elif character in "'\"":
            quote = character
        elif character == ";":
            return line[:index]
    return line


def _split_operands(body: str, offset: int) -> list[tuple[str, int, int]]:
    """Comma-separated operands with their spans in the line; commas inside
    quotes or parentheses stay in their operand."""
    operands, depth, quote, start = [], 0, None, 0
    for index, character in enumerate(body + ","):
        if quote:
            if character == quote:
                quote = None
            continue
        if character in "'\"":
            quote = character
        elif character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
        elif character == "," and depth == 0:
            raw = body[start:index]
            stripped = raw.strip()
            lead = len(raw) - len(raw.lstrip())
            operands.append((stripped, offset + start + lead, offset + start + lead + len(stripped)))
            start = index + 1
    return operands


@dataclass(frozen=True)
class Operand:
    text: str
    line: int  # index into the file's lines
    start: int  # span within that line
    end: int

    @property
    def value(self) -> int | None:
        return parse_number(self.text)


@dataclass(frozen=True)
class Directive:
    kind: str  # dc.b, dc.w, dc.l or binclude
    line: int
    operands: tuple[Operand, ...]

    @property
    def path(self) -> str:
        if self.kind != "binclude":
            raise AslSourceError("only a binclude names a file")
        match = QUOTED.match(self.operands[0].text)
        if match is None:
            raise AslSourceError(f"binclude without a quoted path: {self.operands[0].text}")
        return match.group(1)


@dataclass
class SourceFile:
    path: str  # relative to the source root, POSIX
    lines: list[str]
    changed: bool = False


@dataclass
class Block:
    """A label and the directives after it, up to the next label."""

    label: str
    file: SourceFile
    line: int
    directives: list[Directive] = field(default_factory=list)

    @property
    def kind(self) -> str | None:
        """The one directive kind the block uses, "mixed", or None (code)."""
        kinds = {directive.kind for directive in self.directives}
        if not kinds:
            return None
        return kinds.pop() if len(kinds) == 1 else "mixed"

    @property
    def operands(self) -> list[Operand]:
        return [operand for directive in self.directives for operand in directive.operands]

    def values(self) -> list[int]:
        """Every operand's value; the block must be all numeric literals."""
        values = []
        for operand in self.operands:
            value = operand.value
            if value is None:
                raise AslSourceError(f"{self.label}: {operand.text!r} is not a number")
            values.append(value)
        return values

    def data(self, root: Path) -> bytes:
        """The bytes the block assembles to: its binclude file, or its
        numeric dc operands big-endian."""
        if self.kind == "binclude":
            if len(self.directives) != 1:
                raise AslSourceError(f"{self.label} includes more than one file")
            return (root / self.directives[0].path).read_bytes()
        if not self.directives or any(directive.kind not in WIDTHS for directive in self.directives):
            raise AslSourceError(f"{self.label} is not a dc block or a binclude")
        out = bytearray()
        for directive in self.directives:
            width = WIDTHS[directive.kind]
            for operand in directive.operands:
                value = operand.value
                if value is None:
                    raise AslSourceError(f"{self.label}: {operand.text!r} is not a number")
                out += (value & ((1 << (8 * width)) - 1)).to_bytes(width, "big")
        return bytes(out)


class SourceTree:
    """The source the build assembles from `entry`, as data."""

    def __init__(self, root: Path, entry: str = "src/main.s", defines: Mapping[str, int] | None = None):
        self.root = Path(root)
        self.defines = dict(defines or {})
        self.files: dict[str, SourceFile] = {}
        self.blocks: dict[str, Block] = {}
        self.order: list[str] = []  # labels in assembly order
        # `name equ value`, where the value is a number or a sum of numbers and
        # other equates (`Base+2`); anything harder is left out.
        self.equates: dict[str, int] = {}
        self._expressions: dict[str, str] = {}
        self._payloads: dict[str, bytes] = {}
        # Included files read once (a write puts its bytes in _payloads).
        self._included: dict[str, bytes] = {}
        self._read(entry)
        self._resolve_equates()

    # Reading

    def _evaluate(self, argument: str, word: str) -> bool:
        argument = argument.strip()
        if word == "ifdef":
            return argument in self.defines
        if word == "ifndef":
            return argument not in self.defines
        value = parse_number(argument)
        if value is None:
            if argument not in self.defines:
                raise AslSourceError(f"cannot evaluate the condition {argument!r}: define it")
            value = self.defines[argument]
        return value != 0

    def _read(self, relative: str) -> None:
        path = PurePosixPath(relative)
        if path.is_absolute() or ".." in path.parts:
            raise AslSourceError(f"include outside the source root: {relative}")
        name = path.as_posix()
        if name in self.files:
            raise AslSourceError(f"{name} is included twice")
        try:
            text = (self.root / name).read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise AslSourceError(f"cannot read {name}: {exc}") from exc
        source = SourceFile(name, text.split("\n"))
        self.files[name] = source
        # Each level: (this branch is taken, a branch of it was taken already).
        stack: list[tuple[bool, bool]] = []
        block: Block | None = None
        macro_depth = 0
        for index, line in enumerate(source.lines):
            code = _code(line)
            if MACRO_START.match(code):
                macro_depth += 1
                block = None
                continue
            if macro_depth:
                macro_depth -= bool(MACRO_END.match(code))
                continue
            conditional = CONDITIONAL.match(code)
            if conditional is not None and not LABEL.match(code):
                word = conditional["word"].lower()
                if word in {"if", "ifdef", "ifndef"}:
                    taken = all(level for level, _ in stack) and self._evaluate(conditional["argument"], word)
                    stack.append((taken, taken))
                elif not stack:
                    raise AslSourceError(f"{name}:{index + 1}: {word} without if")
                elif word == "endif":
                    stack.pop()
                else:
                    _, done = stack.pop()
                    outer = all(level for level, _ in stack)
                    taken = outer and not done and (word == "else" or self._evaluate(conditional["argument"], "if"))
                    stack.append((taken, done or taken))
                continue
            if not all(level for level, _ in stack):
                continue
            equate = EQUATE.match(code)
            if equate is not None:
                self._expressions[equate["name"]] = equate["value"]
                continue
            rest = 0
            label = LABEL.match(code)
            if label is not None:
                block = Block(label["name"], source, index)
                if block.label in self.blocks:
                    raise AslSourceError(f"{name}:{index + 1}: {block.label} is defined twice")
                self.blocks[block.label] = block
                self.order.append(block.label)
                rest = label.end()
            directive = DIRECTIVE.match(code[rest:])
            if directive is None:
                continue
            kind = directive["kind"].lower().replace("db", "dc.b")
            if kind == "include":
                include = QUOTED.match(directive["body"].strip())
                if include is None:
                    raise AslSourceError(f"{name}:{index + 1}: include without a quoted path")
                self._read(include.group(1))
                block = None
                continue
            if block is None:
                continue
            offset = rest + directive.start("body")
            operands = tuple(
                Operand(text, index, start, end) for text, start, end in _split_operands(directive["body"], offset)
            )
            block.directives.append(Directive(kind, index, operands))
        if stack:
            raise AslSourceError(f"{name}: an if has no endif")

    def _resolve_equates(self) -> None:
        pending = dict(self._expressions)
        while pending:
            progress = False
            for name, text in list(pending.items()):
                total, known = 0, True
                for sign, term in re.findall(r"([+-]?)\s*([^+\s-]+)", text.replace(" ", "")):
                    value = parse_number(term)
                    if value is None:
                        value = self.equates.get(term)
                    if value is None:
                        known = False
                        break
                    total += -value if sign == "-" else value
                if known:
                    self.equates[name] = total
                    del pending[name]
                    progress = True
            if not progress:
                break

    def block(self, label: str) -> Block:
        try:
            return self.blocks[label]
        except KeyError:
            raise AslSourceError(f"the source has no label {label}") from None

    def data(self, label: str) -> bytes:
        """What a label assembles to (a write not yet saved included)."""
        if label in self._payloads:
            return self._payloads[label]
        block = self.block(label)
        if block.kind != "binclude":
            return block.data(self.root)
        if label not in self._included:
            self._included[label] = block.data(self.root)
        return self._included[label]

    def body(self, label: str) -> list[str]:
        """A label's source lines, comments removed, up to the next label of
        its file (a routine's instructions, say)."""
        block = self.block(label)
        following = self.order.index(label) + 1
        end = len(block.file.lines)
        if following < len(self.order) and self.blocks[self.order[following]].file is block.file:
            end = self.blocks[self.order[following]].line
        lines = [_code(line) for line in block.file.lines[block.line : end]]
        lines[0] = lines[0][LABEL.match(lines[0]).end() :]
        return lines

    def labels_in(self, path: str) -> list[str]:
        """The labels of one file, in order."""
        return [label for label in self.order if self.blocks[label].file.path == path]

    # Writing

    def set_values(self, label: str, values: Iterable[int | None]) -> bool:
        """Give a block's dc operands new values, one per operand (None keeps
        an operand as it is); True when anything changed. A changed value
        must fit its directive's width, and only a numeric operand may
        change."""
        block = self.block(label)
        if any(directive.kind not in WIDTHS for directive in block.directives) or not block.directives:
            raise AslSourceError(f"{label} is not a block of dc.b, dc.w and dc.l")
        values = list(values)
        operands = [(directive.kind, operand) for directive in block.directives for operand in directive.operands]
        if len(values) != len(operands):
            raise AslSourceError(f"{label} holds {len(operands)} values, not {len(values)}")
        changes: dict[int, list[tuple[Operand, str]]] = {}
        for (kind, operand), value in zip(operands, values):
            current = operand.value
            if value is None or value == current:
                continue
            if current is None:
                raise AslSourceError(f"{label}: {operand.text!r} is symbolic and cannot be written")
            bits = 8 * WIDTHS[kind]
            if not -(1 << (bits - 1)) <= value < (1 << bits):
                raise AslSourceError(f"{label}: {value} does not fit {kind}")
            changes.setdefault(operand.line, []).append((operand, format_like(operand.text, value)))
        for line, edits in changes.items():
            text = block.file.lines[line]
            for operand, replacement in sorted(edits, key=lambda item: item[0].start, reverse=True):
                text = text[: operand.start] + replacement + text[operand.end :]
            block.file.lines[line] = text
            block.file.changed = True
        if changes:
            self._reindex(block)
        return bool(changes)

    def set_text(self, label: str, index: int, text: str) -> bool:
        """Write operand `index` of a label as `text` (an expression such as
        `Other-*`); True when it changed. The caller vouches that the
        expression assembles to the directive's width."""
        block = self.block(label)
        operands = block.operands
        if not 0 <= index < len(operands):
            raise AslSourceError(f"{label} has no operand {index}")
        operand = operands[index]
        if operand.text == text:
            return False
        if not text or "," in text or ";" in text or "\n" in text:
            raise AslSourceError(f"{label}: {text!r} is not one operand")
        line = block.file.lines[operand.line]
        block.file.lines[operand.line] = line[: operand.start] + text + line[operand.end :]
        block.file.changed = True
        self._reindex(block)
        return True

    def set_data(self, label: str, data: bytes) -> bool:
        """Give a label new bytes of the same length: its binclude file, or
        its dc operands. True when anything changed."""
        block = self.block(label)
        current = self.data(label)
        if len(data) != len(current):
            raise AslSourceError(f"{label} holds {len(current)} bytes, not {len(data)}")
        if block.kind == "binclude":
            if bytes(data) == current:
                return False
            self._payloads[label] = bytes(data)
            return True
        values, at = [], 0
        for directive in block.directives:
            width = WIDTHS[directive.kind]
            bits = 8 * width
            for operand in directive.operands:
                value = int.from_bytes(data[at : at + width], "big")
                # A value the source writes negative stays negative.
                if (operand.value or 0) < 0 and value >= 1 << (bits - 1):
                    value -= 1 << bits
                values.append(value)
                at += width
        return self.set_values(label, values)

    def _reindex(self, block: Block) -> None:
        """Operand spans after a rewrite of the block's lines."""
        directives = []
        for directive in block.directives:
            code = _code(block.file.lines[directive.line])
            label = LABEL.match(code)
            rest = label.end() if label else 0
            match = DIRECTIVE.match(code[rest:])
            assert match is not None
            offset = rest + match.start("body")
            operands = tuple(
                Operand(text, directive.line, start, end) for text, start, end in _split_operands(match["body"], offset)
            )
            directives.append(Directive(directive.kind, directive.line, operands))
        block.directives = directives

    def save(self, root: Path | None = None) -> list[str]:
        """Write the changed files (under `root`, by default the tree's own)
        and return their paths."""
        target = Path(root) if root is not None else self.root
        written = []
        for source in self.files.values():
            if source.changed:
                path = target / source.path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("\n".join(source.lines), encoding="utf-8", newline="\n")
                written.append(source.path)
        for label, data in self._payloads.items():
            relative = self.block(label).directives[0].path
            path = target / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            written.append(relative)
        return sorted(written)
