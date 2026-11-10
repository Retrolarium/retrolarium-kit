"""Find similar instruction blocks in 6502, 65816 and 68000 assembly sources.

The report is a source-level research aid. Local targets are compared by
instruction position. Constants, registers and external symbols remain distinct.
Macro bodies are excluded and directives break inferred instruction blocks.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from fnmatch import fnmatchcase
from itertools import combinations
from pathlib import Path
import hashlib
import os
import re

from retrolarium_kit.core.lint.ca65 import MNEMONICS, split_comment


class AssemblySimilarityError(ValueError):
    """The inputs or scan limits do not describe a bounded source scan."""


CPUS = {"nes": "6502", "snes": "65816", "genesis": "68000", "megadrive": "68000"}
INSTRUCTIONS = {
    "6502": MNEMONICS,
    "65816": MNEMONICS
    | frozenset(
        "BRA BRL COP DEA INA JML JSL MVN MVP PEA PEI PER PHB PHD PHK PHX PHY PLB PLD PLX PLY "
        "REP RTL SEP STP STZ TCD TCS TDC TRB TSB TSC TXY TYX WAI WDM XBA XCE".split()
    ),
    "68000": frozenset(
        "ABCD ADD ADDA ADDI ADDQ ADDX AND ANDI ASL ASR BCC BCS BEQ BGE BGT BHI BHS BLE BLO BLS BLT "
        "BMI BNE BPL BRA BSR BVC BVS BCHG BCLR BSET BTST CHK CLR CMP CMPA CMPI CMPM "
        "DBCC DBCS DBEQ DBF DBGE DBGT DBHI DBHS DBLE DBLO DBLS DBLT DBMI DBNE DBPL DBRA DBT DBVC DBVS "
        "DIVS DIVU EOR EORI EXG EXT ILLEGAL JMP JSR LEA LINK LSL LSR MOVE MOVEA MOVEM MOVEP MOVEQ MULS MULU "
        "NBCD NEG NEGX NOP NOT OR ORI PEA RESET ROL ROR ROXL ROXR RTE RTR RTS "
        "SBCD SCC SCS SEQ SF SGE SGT SHI SHS SLE SLO SLS SLT SMI SNE SPL ST STOP SUB SUBA SUBI SUBQ SUBX "
        "SVC SVS SWAP TAS TRAP TRAPV TST UNLK".split()
    ),
}
CALLS = frozenset(("JSR", "JSL", "BSR"))
CONTROLS = CALLS | frozenset(
    "JMP JML BRA BRL BCC BCS BEQ BGE BGT BHI BHS BLE BLO BLS BLT BMI BNE BPL BVC BVS "
    "DBCC DBCS DBEQ DBF DBGE DBGT DBHI DBHS DBLE DBLO DBLS DBLT DBMI DBNE DBPL DBRA DBT DBVC DBVS".split()
)
TERMINALS = frozenset(("RTS", "RTL", "RTI", "RTE", "RTR", "JMP", "JML", "BRA"))
MACRO_OPEN = {".macro", "macro", ".repeat", ".rept", "repeat", "rept", ".irp", "irp", ".irpc", "irpc", "while"}
MACRO_CLOSE = {".endmacro", "endmacro", ".endrepeat", ".endr", "endr", ".endm", "endm", "endw", "endwhile"}
SUFFIXES = {".asm", ".s", ".inc", ".a65", ".a"}
IGNORED = {".git", ".venv", "__pycache__", "build", "data", "node_modules", "vendor"}
LABEL = re.compile(r"^([A-Za-z_@.?][\w@.?]*|[+-]+|\d+):\s*")
PROC = re.compile(r"^\.proc\s+([A-Za-z_][\w.]*)", re.I)
END = re.compile(r";\s*End of function\s+(\S+)", re.I)
GENERATED = re.compile(r"(?:loc(?:ret)?|sub|[Ll])_[0-9A-Fa-f]+$|Bank\d+_Label_", re.I)
LEXER = re.compile(
    r"""(?P<string>"(?:[^"\\]|\\.|"")*"|'(?:[^'\\]|\\.|'')*')"""
    r"|(?P<number>\$[0-9a-fA-F]+|0[xX][0-9a-fA-F]+|%[01]+|\b\d+\b)"
    r"|(?P<symbol>[A-Za-z_@.?][\w@.?]*)|(?P<other>\S)"
)
REGISTERS = {
    "6502": frozenset(("A", "X", "Y")),
    "65816": frozenset(("A", "X", "Y", "S")),
    "68000": frozenset([*(f"D{i}" for i in range(8)), *(f"A{i}" for i in range(8)), "SP", "PC", "SR", "CCR", "USP"]),
}


@dataclass(frozen=True)
class Instruction:
    line: int
    opcode: str
    operands: str
    scope: str
    widths: tuple = ()


@dataclass
class Block:
    path: str
    section: str
    instructions: list[Instruction] = field(default_factory=list)
    labels: dict[str, list[int]] = field(default_factory=lambda: defaultdict(list))
    names: list[tuple[int, str, bool]] = field(default_factory=list)
    end_name: str | None = None
    explicit_name: str | None = None
    conditional: bool = False


@dataclass(frozen=True)
class Routine:
    path: str
    section: str
    name: str
    first_line: int
    last_line: int
    boundary: str
    strict: tuple
    shape: tuple

    def location(self) -> dict:
        return {
            "path": self.path,
            "section": self.section,
            "name": self.name,
            "first_line": self.first_line,
            "last_line": self.last_line,
            "instructions": len(self.strict),
            "boundary": self.boundary,
        }


def _qualified(name: str, scope: str) -> str:
    return scope + name if name.startswith((".", "@", "?")) else name


def _target(operand: str, scope: str, position: int, labels: dict) -> int | None:
    operand = operand.strip()
    anonymous = re.fullmatch(r":?([+-]+)", operand)
    if anonymous:
        signs = anonymous[1]
        sign = signs[0]
        if set(signs) != {sign}:
            return None
        choices = labels.get(sign, []) + labels.get(":", [])
        eligible = (
            sorted(p for p in choices if p > position)
            if sign == "+"
            else sorted((p for p in choices if p <= position), reverse=True)
        )
        return eligible[len(signs) - 1] if len(eligible) >= len(signs) else None
    matches = labels.get(_qualified(operand, scope), [])
    return matches[0] if len(matches) == 1 else None


def _tokens(
    instruction: Instruction, position: int, start: int, end: int, block: Block, cpu: str
) -> tuple[tuple, tuple]:
    strict, shape = [], []
    base = instruction.opcode.split(".")[0]
    direct = _target(instruction.operands, instruction.scope, position, block.labels) if base in CONTROLS else None
    context = (instruction.opcode, instruction.widths)
    if direct is not None and start <= direct < end:
        return (*context, ("target", direct - start)), (*context, "target")
    for token in LEXER.finditer(instruction.operands):
        kind, text = token.lastgroup, token[0]
        if kind == "number":
            radix = 16 if text.startswith(("$", "0x", "0X")) else 2 if text.startswith("%") else 10
            digits = text[2:] if text.lower().startswith("0x") else text[1:] if text.startswith(("$", "%")) else text
            strict.append(("number", int(digits, radix)))
            shape.append("number")
        elif kind == "symbol":
            if text.upper() in REGISTERS[cpu]:
                strict.append(("register", text.upper()))
                shape.append(("register", text.upper()))
                continue
            target = _target(text, instruction.scope, position, block.labels)
            if target is not None and start <= target < end:
                strict.append(("target", target - start))
            else:
                strict.append(("symbol", _qualified(text, instruction.scope)))
            shape.append("symbol")
        else:
            strict.append((kind, text))
            shape.append((kind, text))
    return (*context, *strict), (*context, *shape)


def _blocks(path: str, text: str, cpu: str, diagnostics: Counter) -> list[Block]:
    blocks = []
    section, scope, conditional, macro, active_cpu = "", "", 0, 0, cpu
    saved_cpus = []
    widths = [None, None]
    current = Block(path, section)

    def flush():
        nonlocal current
        if current.instructions:
            blocks.append(current)
        current = Block(path, section, conditional=conditional > 0)

    for number, raw in enumerate(text.splitlines(), 1):
        code, comment = split_comment(raw)
        code = code.strip()
        op = code.split(None, 1)[0].lower() if code else ""
        if macro:
            if op in MACRO_OPEN or re.match(r"^\w+\s+macro\b", code, re.I):
                macro += 1
            elif op in MACRO_CLOSE:
                macro -= 1
            continue
        if op in MACRO_OPEN or re.match(r"^\w+\s+macro\b", code, re.I):
            flush()
            macro = 1
            diagnostics["skipped_macro_or_repeat_blocks"] += 1
            continue
        marker = END.search(";" + comment) if comment is not None else None
        if marker and not code:
            current.end_name = marker[1]
            flush()
            continue
        if not code:
            continue
        if op in {
            ".if",
            ".ifdef",
            ".ifndef",
            "if",
            "ifdef",
            "ifndef",
            ".else",
            ".elseif",
            "else",
            "elseif",
            ".endif",
            "endif",
        }:
            flush()
            if op in {".endif", "endif"}:
                conditional = max(0, conditional - 1)
            elif op not in {".else", ".elseif", "else", "elseif"}:
                conditional += 1
            current.conditional = conditional > 0
            diagnostics["conditional_directives"] += 1
            continue
        if op in {"save", "restore", "cpu", ".setcpu", ".cpu"}:
            flush()
            if op == "save":
                saved_cpus.append(active_cpu)
            elif op == "restore":
                active_cpu = saved_cpus.pop() if saved_cpus else cpu
            else:
                name = code.split(None, 1)[-1].strip('"').upper()
                active_cpu = {
                    "65C816": "65816",
                    "W65816": "65816",
                    "65816": "65816",
                    "6502": "6502",
                    "68000": "68000",
                    "68010": "68000",
                    "68020": "68000",
                }.get(name, "")
            continue
        if cpu == "65816" and op in {".a8", ".a16", ".i8", ".i16"}:
            widths[0 if op[1] == "a" else 1] = int(op[2:])
            continue
        if op in {".segment", ".bank", "bank", "section", ".section", "org", ".org"}:
            flush()
            section = code
            current.section = section
            scope = ""
            continue
        if match := PROC.match(code):
            flush()
            scope = match[1]
            current.explicit_name = scope
            current.labels[scope].append(0)
            current.names.append((0, scope, True))
            continue
        if op in {".endproc", ".scope", ".endscope", "endsection", ".endsection"}:
            flush()
            scope = ""
            continue
        if code.startswith(":"):
            current.labels[":"].append(len(current.instructions))
            code = code[1:].strip()
        while match := LABEL.match(code):
            name = match[1]
            local = name.startswith((".", "@", "?")) or name[0] in "+-" or name.isdecimal()
            if not local:
                scope = name
            key = _qualified(name, scope)
            current.labels[key].append(len(current.instructions))
            current.names.append((len(current.instructions), key, not local))
            code = code[match.end() :].strip()
        if not code:
            continue
        words = code.split(None, 1)
        opcode = words[0].upper()
        if active_cpu != cpu:
            diagnostics["other_cpu_lines"] += 1
            flush()
            continue
        if opcode.split(".")[0] not in INSTRUCTIONS[cpu] or "{" in code or "}" in code:
            flush()
            diagnostics["data_directive_or_unknown_lines"] += 1
            continue
        operands = words[1].strip() if len(words) > 1 else ""
        current.instructions.append(
            Instruction(number, opcode, operands, scope, tuple(widths) if cpu == "65816" else ())
        )
        if cpu == "65816" and opcode in {"REP", "SEP"}:
            mask = re.fullmatch(r"#(?:\$([0-9a-fA-F]+)|(\d+))", operands)
            if mask:
                value = int(mask[1], 16) if mask[1] else int(mask[2])
                for index, bit in enumerate((0x20, 0x10)):
                    if value & bit:
                        widths[index] = 16 if opcode == "REP" else 8
            else:
                widths = [None, None]
                diagnostics["unresolved_width_changes"] += 1
        elif cpu == "65816" and opcode in {"PLP", "RTI", "XCE"}:
            widths = [None, None]
        if marker:
            current.end_name = marker[1]
            flush()
    flush()
    if macro:
        diagnostics["unclosed_macro_blocks"] += 1
    return blocks


def _routines(
    block: Block, calls: set[str], cpu: str, minimum: int, maximum: int, diagnostics: Counter
) -> list[Routine]:
    size = len(block.instructions)
    explicit = block.explicit_name or block.end_name
    starts = {0}
    if explicit:
        positions = block.labels.get(explicit, [])
        if len(positions) == 1:
            starts.add(positions[0])
    else:
        branch_targets = {
            target
            for i, ins in enumerate(block.instructions)
            if ins.opcode.split(".")[0] in CONTROLS - CALLS
            if (target := _target(ins.operands, ins.scope, i, block.labels)) is not None
        }
        for position, name, global_name in block.names:
            if not 0 < position < size or not global_name:
                continue
            after_return = block.instructions[position - 1].opcode.split(".")[0] in TERMINALS
            if name in calls or (after_return and position not in branch_targets and not GENERATED.match(name)):
                starts.add(position)
    bounds = sorted(starts | {size})
    result = []
    for start, end in zip(bounds, bounds[1:]):
        if end - start < minimum:
            diagnostics["short_blocks"] += 1
            continue
        if end - start > maximum:
            diagnostics["long_blocks"] += 1
            continue
        names = [name for pos, name, _ in block.names if pos == start]
        name = explicit if explicit in names else names[0] if names else f"line_{block.instructions[start].line}"
        pairs = [_tokens(ins, i, start, end, block, cpu) for i, ins in enumerate(block.instructions[start:end], start)]
        boundary = "explicit" if explicit == name else "inferred"
        if block.conditional:
            boundary += "-conditional"
        result.append(
            Routine(
                block.path,
                block.section,
                name,
                block.instructions[start].line,
                block.instructions[end - 1].line,
                boundary,
                tuple(pair[0] for pair in pairs),
                tuple(pair[1] for pair in pairs),
            )
        )
    return result


def _source_files(path: Path):
    if not path.is_dir():
        yield path
        return
    for directory, children, files in os.walk(path, followlinks=False):
        current = Path(directory)
        children[:] = sorted(name for name in children if name not in IGNORED and not (current / name).is_symlink())
        for name in sorted(files):
            yield current / name


def scan(
    root: Path,
    paths: list[str],
    *,
    platform: str,
    minimum: int = 6,
    maximum: int = 256,
    threshold: float = 0.8,
    operand_threshold: float = 0.5,
    limit: int = 100,
    left: str | None = None,
    right: str | None = None,
    include: list[str] = (),
    exclude: list[str] = (),
    max_candidates: int = 250_000,
) -> dict:
    root = root.resolve()
    if platform not in CPUS or minimum < 2 or maximum < minimum or limit < 1 or max_candidates < 1:
        raise AssemblySimilarityError("Invalid platform, routine size or report limit")
    if not 0 <= threshold <= 1 or not 0 <= operand_threshold <= 1 or bool(left) != bool(right):
        raise AssemblySimilarityError("Scores must be between zero and one; --left and --right are used together")
    files = set()
    for name in paths or ["src"]:
        path = (root / name).resolve()
        if not path.is_relative_to(root) or not path.exists():
            raise AssemblySimilarityError(f"Source input is missing or outside the root: {name}")
        for item in _source_files(path):
            if not item.is_file() or item.is_symlink() or item.suffix.lower() not in SUFFIXES:
                continue
            relative = item.relative_to(root).as_posix()
            if any(part in IGNORED for part in item.relative_to(root).parts):
                continue
            if include and not any(fnmatchcase(relative, pattern) for pattern in include):
                continue
            if any(fnmatchcase(relative, pattern) for pattern in exclude):
                continue
            files.add(relative)
    if not files:
        raise AssemblySimilarityError("No assembly inputs matched")
    for pattern in (left, right):
        if pattern and not any(fnmatchcase(name, pattern) for name in files):
            raise AssemblySimilarityError(f"No source inputs match side pattern: {pattern}")
    diagnostics = Counter()
    blocks = []
    cpu = CPUS[platform]
    source_hash = hashlib.sha256()
    for name in sorted(files):
        data = (root / name).read_bytes()
        source_hash.update(name.encode("utf-8") + b"\0" + data + b"\0")
        try:
            text = data.decode("utf-8-sig")
        except UnicodeError as error:
            raise AssemblySimilarityError(f"Source input is not UTF-8: {name}") from error
        blocks.extend(_blocks(name, text, cpu, diagnostics))
    calls = {
        _qualified(ins.operands.strip(), ins.scope)
        for block in blocks
        for ins in block.instructions
        if ins.opcode.split(".")[0] in CALLS and re.fullmatch(r"[A-Za-z_@.?][\w@.?]*", ins.operands.strip())
    }
    routines = [routine for block in blocks for routine in _routines(block, calls, cpu, minimum, maximum, diagnostics)]
    seeds = defaultdict(set)
    seed_size = min(4, minimum)
    for i, routine in enumerate(routines):
        for at in range(len(routine.shape) - seed_size + 1):
            seeds[routine.shape[at : at + seed_size]].add(i)
    pairs = Counter()

    def consider(a, b):
        if left:
            return (
                fnmatchcase(a.path, left)
                and fnmatchcase(b.path, right)
                or fnmatchcase(b.path, left)
                and fnmatchcase(a.path, right)
            )
        return True

    for group in seeds.values():
        if not 1 < len(group) <= 64:
            if len(group) > 64:
                diagnostics["common_seeds_omitted"] += 1
            continue
        for i, j in combinations(sorted(group), 2):
            if consider(routines[i], routines[j]):
                pairs[i, j] += 1
                if len(pairs) > max_candidates:
                    raise AssemblySimilarityError("Candidate limit exceeded; narrow the source paths or --left/--right")
    exact = defaultdict(list)
    for i, routine in enumerate(routines):
        exact[routine.strict].append(i)
    for group in exact.values():
        for i, j in combinations(group, 2):
            if consider(routines[i], routines[j]):
                pairs.setdefault((i, j), 1)
                if len(pairs) > max_candidates:
                    raise AssemblySimilarityError("Exact candidate limit exceeded; narrow the source paths")
    matches = []
    for i, j in sorted(pairs):
        a, b = routines[i], routines[j]
        if min(len(a.strict), len(b.strict)) < max(len(a.strict), len(b.strict)) * threshold / (2 - threshold):
            continue
        shape = SequenceMatcher(None, a.shape, b.shape, autojunk=False)
        if shape.quick_ratio() < threshold:
            continue
        shape_score = shape.ratio()
        if shape_score < threshold:
            continue
        strict = SequenceMatcher(None, a.strict, b.strict, autojunk=False)
        if strict.quick_ratio() < operand_threshold:
            continue
        operand_score = strict.ratio()
        if operand_score < operand_threshold:
            continue
        matched = shape_score * (len(a.strict) + len(b.strict)) / 2
        matches.append(
            {
                "left": a.location(),
                "right": b.location(),
                "kind": "same-normalized-source" if a.strict == b.strict else "similar-instructions",
                "shape_score": round(shape_score, 6),
                "operand_score": round(operand_score, 6),
                "weight": round(matched * shape_score * (0.3 + 0.7 * operand_score), 6),
            }
        )
    matches.sort(
        key=lambda row: (
            -row["weight"],
            -row["operand_score"],
            row["left"]["path"],
            row["left"]["first_line"],
            row["right"]["path"],
            row["right"]["first_line"],
        )
    )
    return {
        "schema_version": 1,
        "platform": platform,
        "cpu": cpu,
        "source_sha256": source_hash.hexdigest(),
        "comparison": "source-instructions",
        "byte_identity_verified": False,
        "conditional_assembly_evaluated": False,
        "settings": {
            "min_instructions": minimum,
            "max_instructions": maximum,
            "shape_threshold": threshold,
            "operand_threshold": operand_threshold,
            "left": left,
            "right": right,
            "limit": limit,
        },
        "counts": {
            "files": len(files),
            "routines": len(routines),
            "candidate_pairs": len(pairs),
            "matches": len(matches),
            "reported": min(len(matches), limit),
        },
        "diagnostics": dict(sorted(diagnostics.items())),
        "matches": matches[:limit],
    }
