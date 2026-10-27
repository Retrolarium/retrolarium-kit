"""A boss's pose code as a small program, and a 68000 subset that runs it.

A jointed boss turns its pose channels into part angles (and sometimes
offsets) with a short routine of its own (`...ApplyPoseAngles`): sums of
channels, constants, fields of the boss's record, fixed-point shifts. Rather
than guess formulas, the kit hands the studio that routine as instructions
(`compile`) and both run it (`run` here, the studio's own copy in C++) on the
channel RAM and the boss's record, so a pose is drawn exactly as the game
draws it.

An instruction is `[mnemonic, size, source, destination]`; an operand is
`["d", n]`, `["a", n]`, `["#", value]`, `["(a)", n, displacement]`,
`["(a)+", n]`, `["-(a)", n]`, `["abs", address]` or `["(x)", base, n, m, size]`
(base plus address register n, -1 for none, plus data register m as a
word or a long: `Table(pc,d0.w)`, `4(a0,d1.w)`), and a branch target is the
instruction's index. The
subset is what those routines use: moves, arithmetic and logic (divs and
divu too), shifts and rotations, swap, ext, neg, not, exg, tests and
compares, bit tests and changes, conditional branches, dbf, calls of other routines of the source
(compiled after the routine, `bsr` to their index), a state table's
dispatch (`lea States(pc,d0.w),a0` / `adda.w (a0),a0` / `jmp (a0)` over
`dc.w State-*` offsets: `["switch", "", register, [index, ...]]`, a jump to
the state the register's word picks, by twos, -1 for one left out) and rts. Compiled
`lenient`ly, code that also does what the subset leaves out (spawns sparks,
queues tiles) keeps what it can: a routine ends where the disassembly says
it does, a call the subset cannot follow does nothing, a jump out of the
routine ends it. Memory is a dictionary of bytes; what is not in it
reads as zero. The source's tables the code reads sit at their labels'
stand-in addresses (`Symbols`, `tables`).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from retrolarium_kit.platform.megadrive.asl import SourceTree, parse_number


class PoseCodeError(ValueError):
    """Pose code outside the subset."""


class Symbols(dict):
    """The source's equates, and its labels as stand-in addresses (from
    $7F0000, where nothing is): code that loads a mapping's address
    (`move.l #Claw,d5`) runs, and what it stores names the label again
    through `given`."""

    def __init__(self, equates: dict[str, int], labels):
        super().__init__(equates)
        self.labels = labels
        self.given: dict[str, int] = {}

    # Room for each label's table before the next stand-in.
    SPACING = 0x1000

    def __contains__(self, key) -> bool:
        return dict.__contains__(self, key) or key in self.labels

    def __getitem__(self, key: str) -> int:
        if dict.__contains__(self, key):
            return dict.__getitem__(self, key)
        if key not in self.labels:
            raise KeyError(key)
        if key not in self.given:
            self.given[key] = 0x7F0000 + self.SPACING * len(self.given)
        return self.given[key]


SIZES = {"b": 1, "w": 2, "l": 4}
CONDITIONS = ("eq", "ne", "mi", "pl", "cs", "cc", "lo", "hs", "ge", "lt", "gt", "le", "hi", "ls", "vs", "vc")
ARITHMETIC = {"add", "adda", "addi", "addq", "sub", "suba", "subi", "subq", "and", "andi", "or", "ori", "eor", "eori"}
SHIFTS = {"asl", "asr", "lsl", "lsr", "rol", "ror"}
BITS = {"btst", "bset", "bclr", "bchg"}
UNARY = {"clr", "ext", "neg", "not", "swap", "tst"}
LABEL = re.compile(r"^([A-Za-z_]\w*):")
LIMIT = 20000


def _code(line: str) -> str:
    return line.split(";", 1)[0].rstrip()


def routine_lines(lines: list[str]) -> list[str]:
    """`lines` up to the disassembly's end of the routine they start: the
    first end mark after a return or jump (a routine that runs on into the
    next one goes on with it: Caterpillar's segment setup)."""
    last = ""
    for at, line in enumerate(lines):
        if "; End of function" in line:
            word = last.split()[0].split(".")[0].lower() if last.split() else ""
            if word in ("rts", "rte", "bra", "jmp"):
                return lines[:at]
        code = re.sub(r"^\s*[A-Za-z_]\w*:", "", _code(line)).strip()
        if code:
            last = code
    return lines


def _split(text: str) -> list[str]:
    out, depth, start = [], 0, 0
    for index, character in enumerate(text):
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
        elif character == "," and depth == 0:
            out.append(text[start:index].strip())
            start = index + 1
    out.append(text[start:].strip())
    return [item for item in out if item]


def evaluate(text: str, equates: dict[str, int]) -> int:
    """A number, or an expression of equates (Name-M68K_RAM, Name+2)."""
    text = text.strip().strip("()")
    number = parse_number(text)
    if number is not None:
        return number
    total = 0
    for sign, term in re.findall(r"([+-]?)\s*([^+\s-]+)", text.replace(" ", "")):
        value = parse_number(term)
        if value is None:
            if term not in equates:
                raise PoseCodeError(f"cannot evaluate {text!r}")
            value = equates[term]
        total += -value if sign == "-" else value
    return total


def _operand(text: str, equates: dict[str, int]) -> list[Any]:
    text = text.strip()
    if re.fullmatch(r"d[0-7]", text):
        return ["d", int(text[1])]
    if re.fullmatch(r"a[0-7]|sp", text):
        return ["a", 7 if text == "sp" else int(text[1])]
    if text.startswith("#"):
        return ["#", evaluate(text[1:], equates) & 0xFFFFFFFF]
    match = re.fullmatch(r"\((?P<reg>a[0-7])\)\+", text)
    if match:
        return ["(a)+", int(match["reg"][1])]
    match = re.fullmatch(r"-\((?P<reg>a[0-7])\)", text)
    if match:
        return ["-(a)", int(match["reg"][1])]
    match = re.fullmatch(r"(?P<disp>[^()]*)\((?P<base>a[0-7]|pc),(?P<index>[ad][0-7])(?:\.(?P<size>[wl]))?\)", text)
    if match:
        displacement = evaluate(match["disp"], equates) if match["disp"].strip() else 0
        register = -1 if match["base"] == "pc" else int(match["base"][1])
        if match["index"][0] == "a":
            raise PoseCodeError(f"an operand outside the subset: {text}")
        return ["(x)", displacement & 0xFFFFFF, register, int(match["index"][1]), match["size"] or "w"]
    match = re.fullmatch(r"(?P<label>[A-Za-z_]\w*)\(pc\)", text)
    if match:
        # A label by the program counter: its address.
        return ["abs", evaluate(match["label"], equates) & 0xFFFFFF]
    match = re.fullmatch(r"(?P<disp>[^()]*)\((?P<reg>a[0-7])\)", text)
    if match:
        displacement = evaluate(match["disp"], equates) if match["disp"].strip() else 0
        return ["(a)", int(match["reg"][1]), displacement]
    match = re.fullmatch(r"\((?P<expr>[^()]+)\)\.[wl]", text)
    if match:
        return ["abs", evaluate(match["expr"], equates) & 0xFFFFFF]
    raise PoseCodeError(f"an operand outside the subset: {text}")


def compile_lines(
    lines: list[str],
    equates: dict[str, int],
    tree: SourceTree | None = None,
    lenient: bool = False,
    follow: Callable[[str], bool] | None = None,
) -> list[list[Any]]:
    """`lines` (a label's code, labels included) as a program, up to the
    first rts that no branch jumps past; it may call only its own labels,
    or with `tree` the source's routines too, those `follow` names when it
    is given (see the module text for `lenient`)."""
    program, labels = _compile(
        routine_lines(lines) if lenient else lines, equates, lenient, tree.defines if tree is not None else None
    )
    if tree is not None:
        return _link(program, labels, tree, equates, lenient, follow)
    for step in program:
        if step[0] == "switch":
            raise PoseCodeError(f"a state table the routine alone cannot name: {step[3]}")
        if step[0] == "bsr" and isinstance(step[3], str):
            if labels.get(step[3], len(program)) >= len(program):
                raise PoseCodeError(f"a call out of the routine: {step[3]}")
            step[3] = labels[step[3]]
    return program


def _compile(
    lines: list[str], equates: dict[str, int], lenient: bool = False, defines: dict[str, int] | None = None
) -> tuple[list[list[Any]], dict[str, int]]:
    labels: dict[str, int] = {}
    pending: list[tuple[str, str, list[str]]] = []
    # The assembler's conditionals (`if BuildProfileJP`) keep the lines the
    # source assembles: an unknown condition counts as true.
    active: list[bool] = []
    for raw in lines:
        code = _code(raw)
        directive = code.split(None, 1)
        if directive and directive[0].lower() in ("if", "ifdef", "ifndef", "else", "endif", "elseif"):
            word = directive[0].lower()
            if word in ("if", "ifdef", "ifndef"):
                condition = directive[1].strip() if len(directive) > 1 else ""
                known = {**equates, **(defines or {})} if defines else equates
                if word == "if":
                    try:
                        value = evaluate(condition, known) != 0
                    except PoseCodeError:
                        value = True
                else:
                    value = (condition in known) == (word == "ifdef")
                active.append(value)
            elif word == "else" and active:
                active[-1] = not active[-1]
            elif word == "endif" and active:
                active.pop()
            continue
        if not all(active):
            continue
        while match := LABEL.match(code.strip()):
            labels[match[1]] = len(pending)
            code = code.strip()[match.end() :]
        words = code.split(None, 1)
        if not words:
            continue
        mnemonic, rest = words[0].lower(), (words[1] if len(words) > 1 else "")
        name, _, size = mnemonic.partition(".")
        pending.append((name, size, _split(rest)))
    program: list[list[Any]] = []
    furthest = 0
    # Lenient: branches out of the routine (to its caller's return) end it.
    exits: list[list[Any]] = []

    def finished() -> tuple[list[list[Any]], dict[str, int]]:
        if exits:
            for step in exits:
                step[3] = len(program)
            program.append(["rts", "", None, None])
        return program, labels

    dispatched: set[int] = set()
    for index, (name, size, operands) in enumerate(pending):
        dispatch = (
            re.fullmatch(r"(\w+)\(pc,([ad][0-7])\.w\)", operands[0].strip()) if name == "lea" and operands else None
        )
        if (
            dispatch
            and len(operands) == 2
            and operands[1].strip() == "a0"
            and pending[index + 1 : index + 3] == [("adda", "w", ["(a0)", "a0"]), ("jmp", "", ["(a0)"])]
        ):
            # A state table's dispatch: its states are linked as routines.
            register = dispatch[2]
            program.append(["switch", "", [register[0], int(register[1])], dispatch[1]])
            dispatched.update((index + 1, index + 2))
            continue
        if index in dispatched:
            # The dispatch's own steps, kept so indices stay the labels'.
            program.append(["nop", "", None, None])
            continue
        if name in ("bsr", "jsr"):
            target = operands[0].strip()
            target = re.sub(r"^\((\w+)\)(?:\.[wl])?$|^(\w+)\(pc\)$", lambda m: m[1] or m[2], target)
            program.append(["bsr", "", None, target])
            continue
        if (
            lenient
            and name in ("bra", "jmp")
            and re.sub(r"^\((\w+)\)(?:\.[wl])?$|^(\w+)\(pc\)$", lambda m: m[1] or m[2], operands[0].strip())
            not in labels
        ):
            # A jump out of the routine: what it jumps to is left out.
            name = "rts"
        if name in BITS:
            # A data register's bit of 32, else a byte's of 8.
            if len(operands) != 2:
                raise PoseCodeError(f"{name} with {len(operands)} operands")
            target = _operand(operands[1], equates)
            program.append([name, "l" if target[0] == "d" else "b", _operand(operands[0], equates), target])
            continue
        if name in ("b" + condition for condition in CONDITIONS) or name in ("bra", "dbf", "dbra"):
            target = operands[-1]
            if target not in labels and lenient and name not in ("dbf", "dbra"):
                program.append([name, "", None, None])
                exits.append(program[-1])
                continue
            if target not in labels:
                raise PoseCodeError(f"a branch out of the routine: {target}")
            furthest = max(furthest, labels[target])
            source = _operand(operands[0], equates) if name in ("dbf", "dbra") else None
            program.append(["dbf" if name == "dbra" else name, "", source, labels[target]])
            continue
        if name == "rts":
            program.append(["rts", "", None, None])
            if index >= furthest:
                return finished()
            continue
        if name == "nop":
            # Kept, so instruction indices stay the labels'.
            program.append(["nop", "", None, None])
            continue
        size = size if size in SIZES else ("l" if name in ("moveq", "exg", "swap", "lea") else "w")
        if (
            name
            in {"move", "movea", "moveq", "lea", "exg", "cmp", "cmpi", "cmpa", "muls", "mulu", "divs", "divu"}
            | ARITHMETIC
            | SHIFTS
        ):
            if len(operands) == 1 and name in SHIFTS:
                operands = ["#1", operands[0]]
            if len(operands) != 2:
                raise PoseCodeError(f"{name} with {len(operands)} operands")
            program.append([name, size, _operand(operands[0], equates), _operand(operands[1], equates)])
        elif name in UNARY:
            program.append([name, size, None, _operand(operands[0], equates)])
        else:
            raise PoseCodeError(f"an instruction outside the subset: {name}")
    if lenient:
        # A routine that runs on into the next one ends where it does.
        program.append(["rts", "", None, None])
    return finished()


def compile_routine(tree: SourceTree, label: str, equates: dict[str, int] | None = None) -> list[list[Any]]:
    """The routine at `label` and the routines it calls, each after the
    last, its calls pointing at their first instruction."""
    equates = tree.equates if equates is None else equates
    block = tree.block(label)
    program, labels = _compile(block.file.lines[block.line :], equates, defines=tree.defines)
    return _link(program, labels, tree, equates)


def _link(
    program: list[list[Any]],
    labels: dict[str, int],
    tree: SourceTree,
    equates: dict[str, int],
    lenient: bool = False,
    follow: Callable[[str], bool] | None = None,
) -> list[list[Any]]:
    """The program with the routines it calls compiled after it. Its own
    labels are the ones up to its rts: `labels` runs on to the file's end."""
    own = len(program)
    placed: dict[str, int] = {}

    def place(name: str) -> int:
        """`name` compiled after the program, once: its first index."""
        if name not in placed:
            callee = tree.block(name)
            lines = callee.file.lines[callee.line :]
            routine, _ = _compile(routine_lines(lines) if lenient else lines, equates, lenient, tree.defines)
            start = len(program)
            for part in routine:
                if part[0] in ("bra", "dbf") or (part[0].startswith("b") and part[0][1:] in CONDITIONS):
                    part[3] += start
            program.extend(routine)
            placed[name] = start
        return placed[name]

    # States count apart from calls (a state table names seven or eight).
    states = 0
    at = 0
    while at < len(program):
        step = program[at]
        at += 1
        if step[0] == "switch" and isinstance(step[3], str):
            targets = []
            for state in state_table(tree, step[3]):
                try:
                    if follow and not follow(state):
                        raise PoseCodeError(f"a state the subset cannot follow: {state}")
                    targets.append(labels[state] if state in labels and labels[state] < own else place(state))
                except (PoseCodeError, KeyError):
                    if not lenient:
                        raise
                    targets.append(-1)
            step[3] = targets
            states += len(targets)
            continue
        if step[0] != "bsr" or not isinstance(step[3], str):
            continue
        name = step[3]
        if name in labels and labels[name] < own and name not in placed:
            step[3] = labels[name]
            continue
        if name not in placed:
            try:
                if name not in tree.blocks or len(placed) - states >= 8 or (follow and not follow(name)):
                    raise PoseCodeError(f"a call the subset cannot follow: {name}")
                place(name)
            except PoseCodeError:
                if not lenient:
                    raise
                program[at - 1] = ["nop", "", None, None]
                continue
        step[3] = placed[name]
    return program


def state_table(tree: SourceTree, label: str) -> list[str]:
    """The states a `dc.w State-*` table at `label` names, in order."""
    block = tree.block(label)
    states = []
    for at, raw in enumerate(block.file.lines[block.line :]):
        code = _code(raw)
        code = code.split(":", 1)[1] if at == 0 and ":" in code else code
        if not code.strip():
            if states:
                break
            continue
        match = re.fullmatch(r"dc\.w\s+(\w+)\s*-\s*\*", code.strip(), re.IGNORECASE)
        if match is None:
            break
        states.append(match[1])
    if not states:
        raise PoseCodeError(f"not a state table: {label}")
    return states


class Machine:
    """Registers, flags and a byte memory for `run`."""

    def __init__(self, memory: dict[int, int], registers: dict[str, int] | None = None):
        self.memory = memory
        self.d = [0] * 8
        self.a = [0] * 8
        for name, value in (registers or {}).items():
            (self.d if name[0] == "d" else self.a)[int(name[1])] = value & 0xFFFFFFFF
        self.n = self.z = self.v = self.c = False

    def read(self, address: int, size: int) -> int:
        value = 0
        for offset in range(size):
            value = value << 8 | self.memory.get((address + offset) & 0xFFFFFF, 0)
        return value

    def write(self, address: int, size: int, value: int) -> None:
        for offset in range(size):
            self.memory[(address + offset) & 0xFFFFFF] = (value >> (8 * (size - 1 - offset))) & 0xFF

    def address(self, operand: list[Any]) -> int:
        if operand[0] == "(a)":
            return (self.a[operand[1]] + operand[2]) & 0xFFFFFF
        if operand[0] == "(x)":
            index = self.d[operand[3]]
            index = _sign(index, 2) if operand[4] == "w" else index
            base = operand[1] + (self.a[operand[2]] if operand[2] >= 0 else 0)
            return (base + index) & 0xFFFFFF
        return operand[1]

    def get(self, operand: list[Any], size: int) -> int:
        mask = (1 << (8 * size)) - 1
        kind = operand[0]
        if kind == "d":
            return self.d[operand[1]] & mask
        if kind == "a":
            return self.a[operand[1]] & mask
        if kind == "#":
            return operand[1] & mask
        return self.read(self.address(operand), size)

    def put(self, operand: list[Any], size: int, value: int) -> None:
        mask = (1 << (8 * size)) - 1
        value &= mask
        kind = operand[0]
        if kind == "d":
            self.d[operand[1]] = (self.d[operand[1]] & ~mask & 0xFFFFFFFF) | value
        elif kind == "a":
            # An address register takes the whole long, words sign-extended.
            self.a[operand[1]] = _sign(value, size) & 0xFFFFFFFF
        else:
            self.write(self.address(operand), size, value)

    def flags(self, value: int, size: int, carry: bool = False, overflow: bool = False) -> None:
        bits = 8 * size
        value &= (1 << bits) - 1
        self.n = bool(value >> (bits - 1))
        self.z = value == 0
        self.c, self.v = carry, overflow

    def test(self, condition: str) -> bool:
        n, z, v, c = self.n, self.z, self.v, self.c
        return {
            "eq": z,
            "ne": not z,
            "mi": n,
            "pl": not n,
            "cs": c,
            "lo": c,
            "cc": not c,
            "hs": not c,
            "vs": v,
            "vc": not v,
            "ge": n == v,
            "lt": n != v,
            "gt": not z and n == v,
            "le": z or n != v,
            "hi": not c and not z,
            "ls": c or z,
        }[condition]


def _sign(value: int, size: int) -> int:
    bits = 8 * size
    value &= (1 << bits) - 1
    return value - (1 << bits) if value >> (bits - 1) else value


def _settle(machine: Machine, operand: Any, size: int) -> Any:
    """An auto-increment operand as the address it uses, the register moved."""
    if not isinstance(operand, list) or operand[0] not in ("(a)+", "-(a)"):
        return operand
    register = operand[1]
    if operand[0] == "-(a)":
        machine.a[register] = (machine.a[register] - size) & 0xFFFFFFFF
        return ["abs", machine.a[register] & 0xFFFFFF]
    address = machine.a[register] & 0xFFFFFF
    machine.a[register] = (machine.a[register] + size) & 0xFFFFFFFF
    return ["abs", address]


def run(program: list[list[Any]], machine: Machine) -> Machine:
    """Run the program to its rts."""
    at, steps = 0, 0
    returns: list[int] = []
    while 0 <= at < len(program):
        steps += 1
        if steps > LIMIT:
            raise PoseCodeError("the pose code does not end")
        name, size_name, source, target = program[at]
        at += 1
        size = SIZES.get(size_name, 2)
        # Post-increment and pre-decrement move the register once, the
        # source's first.
        source, target = _settle(machine, source, size), _settle(machine, target, size)
        mask = (1 << (8 * size)) - 1
        if name == "rts":
            if not returns:
                break
            at = returns.pop()
            continue
        if name == "nop":
            continue
        if name == "bsr":
            returns.append(at)
            at = target
            continue
        if name == "switch":
            # The state's rts returns to the dispatch's caller.
            state = _sign(machine.get(source, 2) & 0xFFFF, 2) // 2
            at = target[state] if 0 <= state < len(target) and target[state] >= 0 else -1
            if at < 0:
                if not returns:
                    break
                at = returns.pop()
            continue
        if name == "bra":
            at = target
        elif name.startswith("b") and name[1:] in CONDITIONS:
            if machine.test(name[1:]):
                at = target
        elif name == "dbf":
            counter = (machine.get(source, 2) - 1) & 0xFFFF
            machine.put(source, 2, counter)
            if counter != 0xFFFF:
                at = target
        elif name in ("move", "movea"):
            value = machine.get(source, size)
            machine.put(target, size, value)
            if name == "move" and target[0] != "a":
                machine.flags(value, size)
        elif name == "moveq":
            value = _sign(source[1], 1) & 0xFFFFFFFF
            machine.put(target, 4, value)
            machine.flags(value, 4)
        elif name == "lea":
            machine.put(target, 4, machine.address(source))
        elif name == "exg":
            first, second = machine.get(source, 4), machine.get(target, 4)
            machine.put(source, 4, second)
            machine.put(target, 4, first)
        elif name in ARITHMETIC:
            left, right = machine.get(target, size), machine.get(source, size)
            if name in ("adda", "suba") or target[0] == "a":
                value = machine.a[target[1]] + (_sign(right, size) if name.startswith("add") else -_sign(right, size))
                machine.a[target[1]] = value & 0xFFFFFFFF
                continue
            base = name.rstrip("iq") if name not in ("andi", "ori", "eori") else name[:-1]
            if base == "add":
                total = left + right
                value = total & mask
                overflow = _sign(left, size) + _sign(right, size) != _sign(value, size)
                machine.flags(value, size, total > mask, overflow)
            elif base == "sub":
                total = left - right
                value = total & mask
                overflow = _sign(left, size) - _sign(right, size) != _sign(value, size)
                machine.flags(value, size, total < 0, overflow)
            else:
                value = {"and": left & right, "or": left | right, "eor": left ^ right}[base]
                machine.flags(value, size)
            machine.put(target, size, value)
        elif name in ("cmp", "cmpi", "cmpa"):
            left, right = machine.get(target, size), machine.get(source, size)
            total = left - right
            value = total & mask
            machine.flags(value, size, total < 0, _sign(left, size) - _sign(right, size) != _sign(value, size))
        elif name in SHIFTS:
            count = machine.get(source, 4) & 63 if source[0] != "#" else source[1]
            value = machine.get(target, size)
            bits = 8 * size
            carry = False
            for _ in range(count):
                if name in ("asl", "lsl"):
                    carry = bool(value >> (bits - 1) & 1)
                    value = (value << 1) & mask
                elif name == "asr":
                    carry = bool(value & 1)
                    value = (value >> 1) | (value & (1 << (bits - 1)))
                elif name == "lsr":
                    carry = bool(value & 1)
                    value >>= 1
                elif name == "rol":
                    carry = bool(value >> (bits - 1) & 1)
                    value = ((value << 1) | carry) & mask
                else:
                    carry = bool(value & 1)
                    value = (value >> 1) | (carry << (bits - 1))
            machine.put(target, size, value)
            machine.flags(value, size, carry)
        elif name in ("muls", "mulu"):
            left, right = machine.get(target, 2), machine.get(source, 2)
            value = _sign(left, 2) * _sign(right, 2) if name == "muls" else left * right
            machine.put(target, 4, value & 0xFFFFFFFF)
            machine.flags(value, 4)
        elif name in ("divs", "divu"):
            dividend, divisor = machine.get(target, 4), machine.get(source, 2)
            if name == "divs":
                dividend, divisor = _sign(dividend, 4), _sign(divisor, 2)
            if divisor == 0:
                raise PoseCodeError("the pose code divides by zero")
            quotient = abs(dividend) // abs(divisor) * (1 if (dividend >= 0) == (divisor >= 0) else -1)
            remainder = dividend - quotient * divisor
            fits = -0x8000 <= quotient <= 0x7FFF if name == "divs" else 0 <= quotient <= 0xFFFF
            if fits:
                machine.put(target, 4, (remainder & 0xFFFF) << 16 | (quotient & 0xFFFF))
                machine.flags(quotient, 2)
            else:
                # An overflowing quotient leaves the register as it was.
                machine.v = True
        elif name == "clr":
            machine.put(target, size, 0)
            machine.flags(0, size)
        elif name == "ext":
            value = machine.get(target, 4)
            value = _sign(value, 1) & 0xFFFF if size == 2 else _sign(value, 2) & 0xFFFFFFFF
            machine.put(target, size, value)
            machine.flags(value, size)
        elif name == "neg":
            value = (-machine.get(target, size)) & mask
            machine.put(target, size, value)
            machine.flags(value, size, value != 0)
        elif name == "not":
            value = ~machine.get(target, size) & mask
            machine.put(target, size, value)
            machine.flags(value, size)
        elif name == "swap":
            value = machine.get(target, 4)
            value = ((value << 16) | (value >> 16)) & 0xFFFFFFFF
            machine.put(target, 4, value)
            machine.flags(value, 4)
        elif name == "tst":
            machine.flags(machine.get(target, size), size)
        elif name in BITS:
            value = machine.get(target, size)
            bit = 1 << (machine.get(source, 4) & (8 * size - 1))
            machine.z = not value & bit
            if name != "btst":
                value = value | bit if name == "bset" else value & ~bit if name == "bclr" else value ^ bit
                machine.put(target, size, value)
        else:
            raise PoseCodeError(f"cannot run {name}")
    return machine
