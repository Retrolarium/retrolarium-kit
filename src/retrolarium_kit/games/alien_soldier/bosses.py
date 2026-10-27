"""Alien Soldier's jointed bosses: skeletons, poses and the animations that
move them.

Many bosses are a parent object and a chain of child objects ($60 bytes
each) that `Sprite_InitializeLinkedMetaspriteParts` builds from three
tables: a long per part (what it draws), a radius byte and a link word.

- The long is 0 (a joint that draws nothing); an inline sprite's address +1
  (three words: tile word, size word, X and Y offsets); a mapping's address
  with bit 22 set; or a table of eight mappings by direction (four with the
  four-direction renderer, a $20 later turn), whose top bits
  give an angle offset (bits 28-31, in units of $20) and a mirror (bit 27).
  Bits 24 and 25 add to the palette line.
- The radius byte is the bone's length in pixels; bit 7 gives the part
  priority.
- The link word names the parent (bits 5-13, its offset from the boss in
  bytes), the palette line (bits 14 and 15) and the depth the sprite sorts
  at (bits 0-4: the lower, the further in front).

Each frame the boss's renderer places every part at its parent's position
plus the radius along the part's angle (the cosine table, `Math_SineTable`,
gives X; the same table a quarter turn back gives Y) and picks a rotating
part's mapping by its angle: eight directions over half a turn, the other
half mirrored both ways. The boss's facing (field $54, 0 or $100) turns
every angle a half turn for X.

The angles come from pose channels: words in the shared pattern RAM, an
angle byte and a fraction. A boss's own pose code (`...ApplyPoseAngles`)
turns them into part angles; the document carries that code as a program
(see pose_code.py) for the studio to run, with the field it tests for the
boss's side (a walking boss swaps its legs) and, per part, the channel that
turns its own joint. Parts the skeleton hangs from but does not build (a
boss's hands and feet) are anchors: the code before the boss's renderer
places them, and the document carries that code too. A pose is one byte per channel at some offset of the
boss's pose table (`...PoseTargets`, or the table a field of the boss
points at); its neutral pose is where a channel's range is centred. An
animation is a run of commands:

| Words        | Meaning                                                  |
| ------------ | -------------------------------------------------------- |
| `FFSS, TTTT` | towards the pose at byte TTTT of the table, a step of    |
|              | 1/SS of the way each frame, for FF + 1 frames (`set`     |
|              | timing) or SS + FF frames, FF signed (`add` timing)      |
| `80NN`       | play sound NN                                            |
| `FFFF`       | start the animation again                                |
| `FFFE`       | stay on the last pose                                    |

An animation without either end runs on into the next label's. The code
picks the animation each state shows and moves the boss itself (speeds and
gravity are its instructions), so the document holds what the data holds:
the skeletons as the source draws them (derived), and the poses and the
animations' values, which may change where the source has numbers. Counts,
ends, the poses' offsets and the table sizes stay.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any

from retrolarium_kit.games.alien_soldier import mappings, objects, pose_code
from retrolarium_kit.platform.megadrive.asl import AslSourceError, SourceTree, parse_number


SCHEMA = "alien-soldier.bosses"
DOCUMENT = "graphics/bosses.json"
# What the source alone decides: a refreshed project takes them as the kit
# reads them, keeping its poses and animations.
DERIVED_KEYS = ("cosine", "bosses")
PART_SIZE = 0x60
ANGLE_FIELD = 0x56
# The record the first boss object takes (Entity_ObjectPool): where the
# studio runs a boss's pose code.
SLOT = 0xFFC620
INIT = "Sprite_InitializeLinkedMetaspriteParts"
INIT_MORE = "Sprite_InitializeAdditionalLinkedMetaspriteParts"
DELTAS = "Anim_CalculatePoseChannelDeltas"
# The renderers that place the parts down the skeleton.
TRAVERSALS = (
    "Sprite_BeginMetaspritePartTraversal",
    "Sprite_UpdateMetaspriteEightFrameRotation",
    "Sprite_UpdateMetaspriteFourFrameRotationAndLoadCount",
)
COSINE = "Math_SineTable"
SOUND, LOOP, HOLD = 0x80, 0xFFFF, 0xFFFE
# The bosses whose every part this reader placed where MAME's RAM has it
# (angles and 16.16 positions, several frames of their fights): the others
# read, but code of their own (anchors, orbits, chains) moves parts too.
CHECKED = {
    "Boss_AntroidInitializeBattleState",
    "Boss_TerobusterSetup",
    "Boss_ShellshogunSetupPhase",
    "Boss_JokerInitializeMetasprite",
    "Boss_XiTigerSetup",
    "Boss_ZLeoIntroInit",
}

CALL = re.compile(r"^\s*(?:jsr|jmp|bsr(?:\.[swl])?|bra(?:\.[swl])?)\s+\(?(?P<target>\w+)\)?(?:\.l|\(pc\))?\s*$")
MOVEQ = re.compile(r"^\s*moveq\s+#(?P<value>[^,]+),d(?P<register>[0-7])\s*$")
ADDRESS = re.compile(
    r"^\s*(?:movea\.l\s+#(?P<immediate>\w+)|movea\.l\s+\$(?P<field>[0-9A-Fa-f]+)\(a5\)"
    r"|lea\s+\(?(?P<lea>\w+)\)?(?:\(pc\)|\.l)?),a(?P<register>[0-7])\s*$"
)
BASE_TILE = re.compile(r"^\s*move\.w\s+#(?P<value>[^,]+),\(MetaspriteBaseTileWord\)\.w\s*$")
TARGETS = re.compile(r"^\s*(?:addi\.l\s+#(?P<label>\w+)|add\.l\s+\$(?P<field>[0-9A-Fa-f]+)\(a5\)),d0\s*$")
RAM = re.compile(r"^\s*movea\.w\s+#\((?P<name>\w+)-M68K_RAM\),a(?P<register>[0-7])\s*$")
FIELD = re.compile(r"^\s*move\.l\s+#(?P<label>\w+),\$(?P<field>[0-9A-Fa-f]+)\(a5\)\s*$")
FACING_BIT = re.compile(r"^\s*(?P<op>bset|bclr)\s+d0,\$(?P<offset>[0-9A-Fa-f]+)\(a5\)\s*$")
ADDED_TIMING = re.compile(r"^\s*add\.w\s+d\d,\$C\(a5\)\s*$")
TERM = re.compile(r"([+-]?)\s*([A-Za-z_]\w*|\$[0-9A-Fa-f]+|%[01]+|\d+)")


class BossError(ValueError):
    """A boss document that does not fit the source."""


def _code(line: str) -> str:
    return line.split(";", 1)[0].rstrip()


def _signed(value: int, bits: int) -> int:
    return value - (1 << bits) if value >= 1 << (bits - 1) else value


def _lines_from(tree: SourceTree, label: str) -> list[str]:
    """The code of a label's file from the label on, labels removed."""
    block = tree.block(label)
    lines = [_code(line) for line in block.file.lines[block.line :]]
    return [re.sub(r"^[A-Za-z_]\w*:", "", line) for line in lines]


def _following(tree: SourceTree, label: str) -> list[str]:
    """`label` and the labels after it in its file, in order."""
    path = tree.block(label).file.path
    labels = tree.labels_in(path)
    return labels[labels.index(label) :]


def _bytes(tree: SourceTree, label: str, count: int) -> bytes:
    """`count` bytes of data from `label` on, across the labels after it."""
    out = b""
    for item in _following(tree, label):
        if len(out) >= count:
            break
        out += tree.data(item)
    if len(out) < count:
        raise BossError(f"{label} holds fewer than {count} bytes")
    return out[:count]


def _longs(tree: SourceTree, label: str, count: int) -> list[str]:
    """The texts of `count` dc.l operands from `label` on."""
    out: list[str] = []
    for item in _following(tree, label):
        for directive in tree.block(item).directives:
            if directive.kind != "dc.l":
                raise BossError(f"{item} is not a table of longs")
            out += [operand.text for operand in directive.operands]
        if len(out) >= count:
            return out[:count]
    raise BossError(f"{label} holds fewer than {count} longs")


def _table_longs(tree: SourceTree, label: str, most: int) -> list[str]:
    """Up to `most` dc.l operands from `label` on, as far as the longs go: a
    directional table holds eight frames, or four for a boss with the
    four-direction renderer (Deep Strider)."""
    out: list[str] = []
    for item in _following(tree, label):
        directives = tree.block(item).directives
        if not directives or any(directive.kind != "dc.l" for directive in directives):
            break
        out += [operand.text for directive in directives for operand in directive.operands]
        if len(out) >= most:
            break
    return out[:most]


def _expression(text: str) -> tuple[str | None, int]:
    """A `Label+1-$8000000` operand: the label (or None) and the addend (32
    bits)."""
    label, addend, at = None, 0, 0
    compact = text.replace(" ", "")
    for match in TERM.finditer(compact):
        if match.start() != at:
            raise BossError(f"{text!r} is not a label and an addend")
        at = match.end()
        sign, term = match.groups()
        value = parse_number(term)
        if value is None:
            if label is not None or sign == "-":
                raise BossError(f"{text!r} names more than one label")
            label = term
            continue
        addend += -value if sign == "-" else value
    if at != len(compact):
        raise BossError(f"{text!r} is not a label and an addend")
    return label, addend & 0xFFFFFFFF


def _frame(tree: SourceTree, label: str) -> list[dict[str, Any]]:
    """The pieces of the static mapping at `label` (up to its end mark)."""
    data = tree.data(label)
    for item in _following(tree, label)[1:]:
        if any(int.from_bytes(data[at : at + 2], "big") & mappings.LAST_PIECE for at in range(0, len(data) - 5, 6)):
            break
        data += tree.data(item)
    usable = len(data) - len(data) % 6
    return mappings.read_static(data[:usable])[0]["pieces"]


def _part(tree: SourceTree, text: str, radius: int, link: int, base: int, frames: dict[str, Any]) -> dict[str, Any]:
    label, addend = _expression(text)
    line = ((link >> 14) & 3) | ((addend >> 24) & 3)
    part: dict[str, Any] = {
        "parent": (link & 0x3FE0) // PART_SIZE,
        "radius": radius & 0x7F,
        "depth": link & 0x1F,
        "kind": "none",
    }
    priority = 0x8000 if radius & 0x80 else 0
    if label is None:
        part["attribute"] = priority | line << 13
        return part
    if addend & 1:
        words = [int.from_bytes(tree.data(label)[at : at + 2], "big") for at in (0, 2, 4)]
        part.update(
            kind="sprite",
            source=label,
            attribute=words[0] | (base & 0x8000) | priority | line << 13,
            size=words[1] >> 8,
            x=_signed(words[2] >> 8, 8),
            y=_signed(words[2] & 0xFF, 8),
        )
    elif addend & 0x400000:
        frames.setdefault(label, _frame(tree, label))
        part.update(kind="mapping", source=label, attribute=base | priority | line << 13)
    else:
        table = [_expression(item)[0] for item in _table_longs(tree, label, 8)]
        if len(table) not in (4, 8):
            raise BossError(f"{label} is not a table of four or eight frames")
        for frame in table:
            frames.setdefault(frame, _frame(tree, frame))
        part.update(
            kind="rotation",
            source=label,
            frames=table,
            attribute=base | priority | line << 13,
            angle=((addend >> 28) & 15) << 5,
            mirror=bool(addend & 0x8000000),
        )
    return part


def _skeleton(tree: SourceTree, label: str) -> tuple[list[dict[str, Any]], dict[str, Any], int]:
    """The parts a routine's linked-part calls build, the mappings they draw
    and the base tile word."""
    registers: dict[str, Any] = {"a4": 0}
    base, parts, frames = 0, [], {}
    for line in tree.body(label):
        if re.match(r"^\s*movea\.w\s+a5,a4\s*$", line):
            registers["a4"] = 0
        elif match := re.match(r"^\s*lea\s+\$([0-9A-Fa-f]+)\(a5\),a4\s*$", line):
            registers["a4"] = int(match[1], 16)
        elif match := MOVEQ.match(line):
            registers[f"d{match['register']}"] = parse_number(match["value"])
        elif match := ADDRESS.match(line):
            registers[f"a{match['register']}"] = match["immediate"] or match["lea"]
        elif match := BASE_TILE.match(line):
            base = parse_number(match["value"]) or 0
        elif (match := CALL.match(line)) and match["target"] in (INIT, INIT_MORE):
            count = (registers.get("d7") or 0) + 1
            if match["target"] == INIT:
                # Slots before the first part (the boss's own record, say)
                # draw nothing of the skeleton's.
                parts = [{"parent": None, "radius": 0, "depth": 0, "kind": "none", "attribute": 0}] * (
                    registers["a4"] // PART_SIZE
                )
            texts = _longs(tree, registers["a0"], count)
            radii = _bytes(tree, registers["a1"], count)
            links = _bytes(tree, registers["a2"], count * 2)
            for index in range(count):
                link = int.from_bytes(links[index * 2 : index * 2 + 2], "big")
                parts.append(_part(tree, texts[index], radii[index], link, base, frames))
            registers["a4"] += count * PART_SIZE
    if parts:
        # The boss's own record is placed by its code, not by a bone; so are
        # the anchors the parts hang from that the routine does not build.
        parts[0] = {**parts[0], "parent": None}
        last = max((part["parent"] for part in parts if part["parent"] is not None), default=0)
        parts += [{"parent": None, "radius": 0, "depth": 0, "kind": "none", "attribute": 0, "anchor": True}] * max(
            0, last + 1 - len(parts)
        )
    return parts, frames, base


def _fields(tree: SourceTree, files: list[str]) -> dict[int, list[str]]:
    """The labels the boss's code stores in its record's fields."""
    found: dict[int, list[str]] = {}
    for path in files:
        for label in tree.labels_in(path):
            for line in tree.body(label):
                if (match := FIELD.match(line)) and match["label"] in tree.blocks:
                    names = found.setdefault(int(match["field"], 16), [])
                    if match["label"] not in names:
                        names.append(match["label"])
    return found


def _named(match: re.Match, fields: dict[int, list[str]], key: str = "label") -> list[str]:
    """What an operand names: a label, or the labels its field is given."""
    if match[key]:
        return [match[key]]
    if match["field"]:
        return fields.get(int(match["field"], 16), [])
    return []


def _angle_code(tree: SourceTree, label: str) -> list[list[Any]] | None:
    """The routine at `label` as a program when it is pose code: it runs in
    the subset and gives parts their angles."""
    try:
        program = pose_code.compile_routine(tree, label)
    except (pose_code.PoseCodeError, AslSourceError):
        return None
    stores = [
        step
        for step in program
        if step[0] == "move"
        and step[3]
        and step[3][0] == "(a)"
        and step[3][1] == 5
        and step[3][2] % PART_SIZE == ANGLE_FIELD
    ]
    reads_channels = any(step[0] == "movea" and step[2] and step[2][0] == "#" for step in program)
    # Some take a0 (the channels) and d7 (the angle mask) from their caller
    # (Medusa's): those read a0 before anything sets it.
    for step in program:
        operands = [operand for operand in step[2:] if isinstance(operand, list)]
        if any(operand[0] == "(a)" and operand[1] == 0 for operand in operands):
            reads_channels = reads_channels or "given"
            break
        if step[3] == ["a", 0]:
            break
    if len(stores) < 2 or not reads_channels:
        return None
    if reads_channels == "given":
        return [["prelude", "", None, None], *program]
    return program


def _pose_system(tree: SourceTree, files: list[str]) -> dict[str, Any] | None:
    """The pose table, neutral pose, channel base and count, timing, the
    angle code and the animations of a boss's files."""
    fields = _fields(tree, files)
    labels = [label for path in files for label in tree.labels_in(path)]
    found: dict[str, Any] = {}
    for label in labels:
        body = tree.body(label)
        for at, line in enumerate(body):
            if (match := TARGETS.match(line)) and "targets" not in found:
                tables = [name for name in _named(match, fields) if name in tree.blocks]
                if tables:
                    found["targets"] = tables[0]
                    following = _lines_from(tree, label)
                    start = next(index for index, item in enumerate(following) if TARGETS.match(item))
                    window = following[start : start + 12]
                    found["timing"] = "add" if any(ADDED_TIMING.match(item) for item in window) else "set"
        if any((call := CALL.match(line)) and call["target"] == DELTAS for line in body):
            for line in body:
                if (match := ADDRESS.match(line)) and match["register"] == "1":
                    names = [match["lea"]] if match["lea"] else _named(match, fields, "immediate")
                    if names:
                        found["neutral"] = names[0]
                elif (match := MOVEQ.match(line)) and match["register"] == "7":
                    found["channels"] = (parse_number(match["value"]) or 0) + 1
                elif (match := RAM.match(line)) and match["register"] == "2":
                    found["base"] = match["name"]
        if "angles" not in found:
            program = _angle_code(tree, label)
            if program is not None:
                found["angles"] = label
                found["program"] = program
    if not {"targets", "neutral", "channels", "base", "angles"} <= found.keys():
        return None
    if found["program"][0][0] == "prelude":
        # What the caller hands the angle code: the channels and the mask.
        found["program"] = [
            ["movea", "w", ["#", tree.equates[found["base"]] & 0xFFFF], ["a", 0]],
            ["move", "w", ["#", 0x1FE], ["d", 7]],
            *[
                [name, size, source, target + 1 if isinstance(target, int) else target]
                for name, size, source, target in found["program"][1:]
            ],
        ]
        # The branch targets moved by one more: the prelude is two steps.
        found["program"] = [
            step if not isinstance(step[3], int) else [step[0], step[1], step[2], step[3] + 1]
            for step in found["program"]
        ]
    # An animation is what a1 points at when the pose routine is called (the
    # routine most such calls go to); a1 points at other tables too. The
    # call may come a few instructions on, past a test that points a1
    # elsewhere.
    calls: dict[str, list[str]] = {}
    for label in labels:
        body = [line for line in _lines_from(tree, label)[:40] if line.strip() and line.strip() != "nop"]
        own = len([line for line in tree.body(label) if line.strip() and line.strip() != "nop"])
        for at, line in enumerate(body[:own]):
            match = ADDRESS.match(line)
            call = next((item for item in map(CALL.match, body[at + 1 : at + 8]) if item), None)
            if not match or match["register"] != "1" or not call:
                continue
            for name in _named(match, fields, "immediate") + ([match["lea"]] if match["lea"] else []):
                # Commands written as words, or as longs and words.
                if name in tree.blocks and tree.block(name).kind in ("dc.w", "dc.l", "mixed"):
                    streams = calls.setdefault(call["target"], [])
                    if name not in streams:
                        streams.append(name)
    if not calls:
        return None
    # Calls that build the skeleton or set the channels at once take tables
    # in a1 too.
    calls = {
        target: streams
        for target, streams in calls.items()
        if target not in (INIT, INIT_MORE, "Anim_InitializePoseChannelsFromBytes", "Object_InitGroupFromTable")
        and "InitializePoseChannels" not in target
    }
    if not calls:
        return None
    found["streams"] = max(calls.values(), key=len)
    return found


def _code_mappings(
    tree: SourceTree, files: list[str], parts: list[dict[str, Any]], frames: dict[str, Any], base: int
) -> None:
    """Parts the tables leave blank that the boss's code gives a mapping
    (`move.l #Claw,$248(a5)`: part 6's): they draw it, with the attribute the
    code gives them too when it does (the first of each, as play starts)."""
    fields = _fields(tree, files)
    words: dict[int, int] = {}
    for path in files:
        for label in tree.labels_in(path):
            for line in tree.body(label):
                match = re.match(r"^\s*move\.w\s+#(?P<value>[^,]+),\$(?P<field>[0-9A-Fa-f]+)\(a5\)\s*$", line)
                if match and parse_number(match["value"]) is not None:
                    words.setdefault(int(match["field"], 16), parse_number(match["value"]) & 0xFFFF)
    for index, part in enumerate(parts):
        if part["kind"] != "none" or part.get("anchor") or index == 0:
            continue
        labels = [label for label in fields.get(index * PART_SIZE + 8, []) if label in tree.blocks]
        try:
            pieces = _frame(tree, labels[0]) if labels else None
        except (BossError, AslSourceError, mappings.MappingError, IndexError):
            pieces = None
        if pieces is None:
            continue
        frames.setdefault(labels[0], pieces)
        attribute = words.get(index * PART_SIZE + 0xE, base | part["attribute"])
        parts[index] = {**part, "kind": "mapping", "source": labels[0], "attribute": attribute, "assigned": True}


def _anchor_code(tree: SourceTree, files: list[str]) -> list[list[Any]] | None:
    """The code a boss runs before its renderer places the parts (setting
    its anchors' positions), as a program; None when there is none."""
    for path in files:
        for label in tree.labels_in(path):
            body = tree.body(label)
            calls = [at for at, line in enumerate(body) if (call := CALL.match(line)) and call["target"] in TRAVERSALS]
            if not calls:
                continue
            # From where the routine starts: the labels before that fall
            # into this one.
            labels = tree.labels_in(path)
            start = labels.index(label)
            while start > 0:
                code = [line.split()[0] for line in tree.body(labels[start - 1]) if line.strip()]
                if not code or code[-1].split(".")[0] in ("rts", "rte", "bra", "jmp"):
                    break
                start -= 1
            lines = [f"{item}:\n" + "\n".join(tree.body(item)) for item in labels[start : labels.index(label)]]
            try:
                program = pose_code.compile_lines(
                    [*"\n".join(lines).split("\n"), label + ":", *body[: calls[0]], " rts"], tree.equates
                )
            except pose_code.PoseCodeError:
                return None
            places = any(
                step[0] == "move" and step[3] and step[3][0] == "(a)" and step[3][2] in (0x40, 0x44) for step in program
            )
            return program if places else None
    return None


def _sequence(tree: SourceTree, label: str, table: int) -> dict[str, Any]:
    """One animation's commands (see the module text), its poses as offsets
    into a table of `table` bytes."""
    data = tree.data(label)
    if len(data) % 2:
        raise BossError(f"{label} is not whole words")
    words = [int.from_bytes(data[at : at + 2], "big") for at in range(0, len(data), 2)]
    commands, at = [], 0
    while at < len(words):
        word = words[at]
        if word in (LOOP, HOLD):
            # What follows the end in the same block is another animation's
            # (one the code points into) or nothing.
            return {"label": label, "commands": commands, "end": "loop" if word == LOOP else "hold"}
        if word >> 8 == SOUND:
            commands.append({"sound": word & 0xFF})
            at += 1
            continue
        if at + 1 >= len(words):
            raise BossError(f"{label} ends inside a command")
        offset = _signed(words[at + 1], 16)
        if not 0 <= offset < table:
            raise BossError(f"{label} names a pose the table does not hold (${offset & 0xFFFF:X})")
        if word & 0xFF == 0:
            raise BossError(f"{label} divides by zero")
        commands.append({"offset": offset, "frames": word >> 8, "steps": word & 0xFF})
        at += 2
    following = _following(tree, label)
    return {"label": label, "commands": commands, "end": "next", "next": following[1] if len(following) > 1 else None}


def _table(tree: SourceTree, label: str) -> bytes:
    """A pose table's bytes: its label's data and the data labels after it
    (a table may be cut by labels the disassembly put inside it)."""
    out = b""
    for item in _following(tree, label):
        if tree.block(item).kind is None:
            break
        try:
            out += tree.data(item)
        except AslSourceError:
            break
    return out


def _facing(tree: SourceTree, files: list[str]) -> dict[str, dict[str, list[int]]]:
    """The parts a boss's facing code mirrors (bit 3 of the attribute's high
    byte, $800): for facing 0 and for $100, the parts set and cleared."""
    for path in files:
        for label in tree.labels_in(path):
            if not label.endswith("ApplyFacingToParts"):
                continue
            halves: list[dict[str, list[int]]] = [{"set": [], "clear": []}]
            block = tree.block(label)
            returns = 0
            for raw in block.file.lines[block.line + 1 :]:
                code = _code(raw)
                if re.match(r"^[A-Za-z_]\w*:", code):
                    halves.append({"set": [], "clear": []})
                    code = re.sub(r"^[A-Za-z_]\w*:", "", code)
                if match := FACING_BIT.match(code):
                    offset = int(match["offset"], 16)
                    halves[-1][{"bset": "set", "bclr": "clear"}[match["op"]]].append((offset - 0xE) // PART_SIZE)
                if code.strip() == "rts":
                    returns += 1
                    if returns == 2:
                        break
            used = [half for half in halves if half["set"] or half["clear"]]
            if len(used) == 2:
                return {"0": used[0], "256": used[1]}
    return {}


def _cosine(tree: SourceTree) -> list[int]:
    data = tree.data(COSINE)
    return [_signed(int.from_bytes(data[at : at + 2], "big"), 16) for at in range(0, 512, 2)]


def _boss_files(tree: SourceTree, path: str) -> list[str]:
    """The files named like a boss file (antroid_*.s), its own folder first,
    then others (a boss's pose code may sit with its projectiles)."""
    own = PurePosixPath(path)
    prefix = own.stem.split("_")[0]
    found = [
        item
        for item in tree.files
        if PurePosixPath(item).stem.split("_")[0] == prefix
        and not item.startswith("src/debug/")
        and tree.labels_in(item)
    ]
    return sorted(found, key=lambda item: (item != path, PurePosixPath(item).parent != own.parent, item))


def _run_angles(boss: dict[str, Any], words: list[int], side: int = 0) -> list[int]:
    """The parts' angle fields after the boss's pose code runs on channel
    words `words` (the studio runs the same program)."""
    memory: dict[int, int] = {}
    machine = pose_code.Machine(memory, {"a5": SLOT})
    base = boss["channel_base"]
    for channel, word in enumerate(words):
        machine.write(base + 4 * channel, 2, word)
    if boss["side_field"] is not None:
        machine.write(SLOT + boss["side_field"], 2, side)
    pose_code.run(boss["pose_code"], machine)
    return [machine.read(SLOT + index * PART_SIZE + ANGLE_FIELD, 2) for index in range(len(boss["parts"]))]


def _joints(boss: dict[str, Any]) -> list[int | None]:
    """For each part, the channel that turns its own joint: of the channels
    that move it, the one that moves the fewest parts."""
    neutral = [value << 8 for value in boss["neutral_pose"]]
    still = _run_angles(boss, neutral)
    moved: list[set[int]] = []
    for channel in range(boss["channels"]):
        words = list(neutral)
        words[channel] = (words[channel] + 0x1000) & 0xFFFF
        angles = _run_angles(boss, words)
        moved.append({index for index, (before, after) in enumerate(zip(still, angles)) if before != after})
    joints = []
    for index in range(len(boss["parts"])):
        channels = [channel for channel in range(boss["channels"]) if index in moved[channel]]
        joints.append(min(channels, key=lambda channel: (len(moved[channel]), -channel)) if channels else None)
    return joints


def _title(stem: str, names: list[str]) -> str:
    """A boss's name from its file's: the leading words that spell a name
    the game uses (xi_tiger_battle_states is Xi-Tiger), else the file's
    words."""
    words = stem.split("_")
    known = {re.sub(r"[^a-z0-9]", "", name.lower()) for name in names}
    for count in range(len(words), 0, -1):
        if "".join(words[:count]).lower() in known:
            return objects.title("_".join(words[:count]), names)
    return objects.title(stem, names)


def _read(tree: SourceTree) -> list[dict[str, Any]]:
    from retrolarium_kit.core.game_names import GameNames

    names = [
        part.strip()
        for value in GameNames.load(tree.root).names.get("stages", {}).values()
        for part in value.split("/")
    ]
    sets = objects.boss_contexts(tree)
    found = []
    builders = [
        label
        for label in tree.order
        if tree.block(label).file.path.startswith("src/bosses/")
        and any((call := CALL.match(line)) and call["target"] == INIT for line in tree.body(label))
    ]
    # Bosses that share their files' name (Valkirie's forms) share a pose
    # engine this reader cannot yet tell apart: they are left out.
    prefixes = [PurePosixPath(tree.block(label).file.path).stem.split("_")[0] for label in builders]
    for label in builders:
        path = tree.block(label).file.path
        if prefixes.count(PurePosixPath(path).stem.split("_")[0]) > 1:
            continue
        files = _boss_files(tree, path)
        try:
            parts, frames, base = _skeleton(tree, label)
            system = _pose_system(tree, files)
            if system is None:
                continue
            table = _table(tree, system["targets"])
            sequences = []
            for name in system["streams"]:
                # A table a1 held for something else does not read as an
                # animation: it is left out.
                try:
                    sequences.append(_sequence(tree, name, len(table)))
                except BossError:
                    continue
            # So are bosses whose animations sit in another boss's files (the
            # Seven Force plays Valkirie's).
            if not sequences or any(tree.block(item["label"]).file.path not in files for item in sequences):
                continue
            # Animations that run on into another take it in too.
            for item in sequences:
                if item.get("next") and item["next"] not in [other["label"] for other in sequences]:
                    sequences.append(_sequence(tree, item["next"], len(table)))
            program = system["program"]
            side = next(
                (step[3][2] for step in program if step[0] == "tst" and step[3][0] == "(a)" and step[3][1] == 5),
                None,
            )
            boss = {
                "id": label,
                "parts": parts,
                "channels": system["channels"],
                "channel_base": tree.equates[system["base"]] & 0xFFFFFF,
                "neutral_pose": list(_bytes(tree, system["neutral"], system["channels"])),
                "pose_code": program,
                "side_field": side,
            }
            joints = _joints(boss)
            # Pose code the channels do not move is not what this reader
            # takes it for.
            if all(joint is None for joint in joints):
                continue
            _code_mappings(tree, files, parts, frames, base)
        except (BossError, AslSourceError, pose_code.PoseCodeError, KeyError, TypeError, IndexError, StopIteration):
            continue
        prefix = PurePosixPath(path).stem.split("_")[0]
        asset_set = next((name for name, item in sets.items() if item.get("prefix") == prefix), None)
        context = sets.get(asset_set, {})
        boss.update(
            title=_title(PurePosixPath(path).stem, names),
            asset_set=asset_set,
            loads=context.get("loads", []),
            palettes=context.get("palettes", []),
            base_tile=base,
            frames=frames,
            facing=_facing(tree, files),
            neutral=system["neutral"],
            anchor_code=_anchor_code(tree, files),
            joints=joints,
            timing=system["timing"],
            poses=system["targets"],
            sequences=[item["label"] for item in sequences],
            slot=SLOT,
            checked=label in CHECKED,
            _sequences=sequences,
            _table=table,
        )
        found.append(boss)
    return found


def export(tree: SourceTree) -> dict[str, Any]:
    bosses = _read(tree)
    poses: dict[str, list[dict[str, Any]]] = {}
    sequences: list[dict[str, Any]] = []
    # A table's poses are the offsets its animations name.
    offsets: dict[str, set[int]] = {}
    for boss in bosses:
        for item in boss["_sequences"]:
            offsets.setdefault(boss["poses"], set()).update(
                command["offset"] for command in item["commands"] if "offset" in command
            )
    for boss in bosses:
        table, channels = boss.pop("_table"), boss["channels"]
        listed = sorted(offsets.get(boss["poses"], ()))
        poses[boss["poses"]] = [
            {"offset": offset, "values": list(table[offset : offset + channels].ljust(channels, b"\0"))}
            for offset in listed
        ]
        for item in boss.pop("_sequences"):
            if item["label"] in [other["label"] for other in sequences]:
                continue
            commands = [
                {"pose": listed.index(command.pop("offset")), **command} if "offset" in command else command
                for command in (dict(command) for command in item["commands"])
            ]
            sequences.append({**item, "commands": commands})
    return {
        "schema": SCHEMA,
        "schema_version": 1,
        "cosine": _cosine(tree),
        "bosses": bosses,
        "poses": poses,
        "sequences": sequences,
    }


def apply(tree: SourceTree, document: dict[str, Any]) -> None:
    if document.get("schema") != SCHEMA or document.get("schema_version") != 1:
        raise BossError("not an Alien Soldier boss document")
    source = export(tree)
    tables = {boss["poses"]: boss for boss in source["bosses"]}
    for label, rows in (document.get("poses") or {}).items():
        boss = tables.get(label)
        if boss is None:
            raise BossError(f"{label} is not a pose table this kit reads")
        listed = [row["offset"] for row in source["poses"][label]]
        if (
            not isinstance(rows, list)
            or [row.get("offset") if isinstance(row, dict) else None for row in rows] != listed
        ):
            raise BossError(f"{label}: the poses and their offsets are the source's")
        data = bytearray(tree.data(label))
        written: dict[int, int] = {}
        for row in rows:
            values = row.get("values")
            if (
                not isinstance(values, list)
                or len(values) != boss["channels"]
                or any(
                    isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xFF for value in values
                )
            ):
                raise BossError(f"{label}: the pose at {row['offset']} is not {boss['channels']} bytes")
            for index, value in enumerate(values):
                at = row["offset"] + index
                if at >= len(data):
                    continue
                if written.get(at, value) != value:
                    raise BossError(f"{label}: two poses that share byte {at} give it different values")
                written[at] = value
                data[at] = value
        tree.set_data(label, bytes(data))
    known = {item["label"]: item for item in source["sequences"]}
    for item in document.get("sequences") or []:
        label = item.get("label") if isinstance(item, dict) else None
        original = known.get(label)
        if original is None:
            raise BossError(f"{label} is not an animation this kit reads")
        commands = item.get("commands")
        shape = [sorted(command) for command in original["commands"]]
        if (
            item.get("end") != original["end"]
            or not isinstance(commands, list)
            or [sorted(command) if isinstance(command, dict) else None for command in commands] != shape
        ):
            raise BossError(f"{label}: the commands and the end are the source's")
        boss = next(boss for boss in source["bosses"] if label in boss["sequences"])
        listed = [row["offset"] for row in source["poses"][boss["poses"]]]
        words = []
        for command in commands:
            if "sound" in command:
                value = command["sound"]
                if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xFF:
                    raise BossError(f"{label}: a sound is a byte")
                words.append(SOUND << 8 | value)
                continue
            values = [command.get(key) for key in ("pose", "frames", "steps")]
            if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
                raise BossError(f"{label}: a command's values are numbers")
            pose, frames, steps = values
            # A count of $80 would read as a sound; no steps divides by zero.
            if not 0 <= pose < len(listed) or not 0 <= frames <= 0xFF or frames == SOUND or not 1 <= steps <= 0xFF:
                raise BossError(f"{label}: a listed pose, frames 0-255 but 128, steps 1-255")
            words += [frames << 8 | steps, listed[pose]]
        if original["end"] != "next":
            words.append(LOOP if original["end"] == "loop" else HOLD)
        # What follows the end in the label's block stays as it is.
        written = b"".join(word.to_bytes(2, "big") for word in words)
        tree.set_data(label, written + tree.data(label)[len(written) :])
