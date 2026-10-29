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
places them, and the document carries that code too, as it does the code
right after the renderer that moves parts further (Madam Barbar's groups;
Wolf Garopa's code moves parts by their pixel places, with the routines of
its files it calls: see pose_code.py's lenient code). Records the builder
fills in by hand besides the skeleton (Wolf Garopa's horse's head and its
rider's, Flying-Neo's head and tail) are parts too (`hand_built`), with no
bone: a mapping, or a sprite of their own (a size byte and offsets in place
of the mapping). Tables the code builds in RAM before the fight (Flying-Neo's
tail swings on a sine table of its own) travel in `tables` too. What the
builder leaves in the records but their mappings and places (Wolf Garopa's leg anchors'
angles; the attribute bits it sets, Shellshogun's arms over its belly, go
into the parts' attributes) travels in `ram`, in the same form, with where
code after the renderer that turns parts itself leaves them after
POST_FRAMES frames (Flying-Neo's tail, which waves on random pushes from a
heap).
Back Stringer's renderer lets a part keep a point half a bone past its end
and a child hang from that point; its setup marks those parts, and the
document marks them too (`reach`, `from_reach`). The tables the code
indexes (Back Stringer's arms pick their mappings from one by angle) travel
with it, at the stand-in addresses its labels get (`tables`). Sirene's pose
code keeps a history of the frames before for its tentacles to follow: the
document says how many runs fill it for a held pose (`settle`). The Seven
Force's forms share their files' name; each reads from its own file and
the files it calls into, and keeps the art of the form before it in colours
of its own (its set loads no art, only a palette). Sharpsteel writes its animations as bytes. Deep
Strider has no pose system at all: its code turns the parts from a few
fields of its own (and a history of one of them), so the document carries
that code run on the values its setup gives them, with no channels, poses
or animations (`poses` is null). Bosses with no skeleton but a chain of
records their code places each frame (Jetsripper's chain, Gusthead's arms,
Caterpillar's pods, Sunset Sting's ring, see _chain) bring the routine that
places them (`chain`, as `pose_code`), each record type's own routine
(`record_code`), the field pair the positions sit in (`position_field`), and
the RAM as their code leaves it after SETTLE_FRAMES frames, positions shown
about `centre`; the Caterpillar's ship, which its stage streams, is its
`body`. Shield Viper's head lays its body down its own path, which takes
more frames than that: its records are played whole (see PLAYED), and its
`chain` is the head's own routine. Bosses whose body is a plane's tiles
their code queues frame by frame (Missiray, Destroyer-Mk2, Jampan, Bugmax,
Victor, Viblack, the cut Love Penguin) are played, their messages held on
screen, until it settles (see boss_planes.py): their `body` then, with the
16-pixel columns' own drops under column scroll (`columns`, from
`column_x`), and their records where they stand (a record that is one sprite
of its own, Victor's ring, is a "sprite" part). A boss its stage brings
plays on the RAM the stage has filled, its camera where the stage holds it;
one drawn of its stage's art (Viblack) takes the stage's loads and colours.
A pose is one byte per channel at some offset of the
boss's pose table (`...PoseTable`, or the table a field of the boss
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

A big boss's body is no sprite: its setup builds it into plane B from the
patterns its asset set loads into RAM (`Tilemap_QueueIndexedRows`: a VRAM
command word, the patterns' RAM, the block's size in patterns less one and
the patterns' indices), and each frame its code scrolls plane B with the
boss (`SecondaryCameraXPos = C - x`, `SecondaryCameraYPos = y + D`: the
plane's pixel P shows at the boss plus P - C + $80 across, P + D + $80
down, wrapping with the plane). The document carries the body's name-table
words and where they sit against the boss, and the facing the setup gives
the boss (`faces`): the body is read for it. When the body's tiles carry the
scene behind round it (Flying-Neo's sky), `scene` says so. Flying-Neo writes plane B's line
and column scroll itself (A + x, B - y: C is -A, D is -B). Wolf Garopa writes the scroll
its own way (the code runs to find C and D); Z-Leo's body sits in plane A,
which its code scrolls the same way with the primary camera, and comes in
row by row as it rises (a table of name-table addresses and one of index
rows), from patterns its setup moves to its art. The debug viewer queues its
body in columns (laid out as rows are) and scrolls it with its first part:
the body then rides on that part (`part`).

An animation without either end runs on into the next label's. The code
picks the animation each state shows and moves the boss itself (speeds and
gravity are its instructions), so the document holds what the data holds:
the skeletons as the source draws them (derived), and the poses and the
animations' values, which may change where the source has numbers. Counts,
ends, the poses' offsets and the table sizes stay.
"""

from __future__ import annotations

from collections import Counter
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
# The object records from SLOT ($FFC620 to $FFDCA0).
POOL_RECORDS = 60
# The frames a chain boss's code runs before it is shown.
SETTLE_FRAMES = 64
# Chains whose boss's own routine places the records, down the path its
# head has taken (Shield Viper's body follows the head's history): played
# whole with boss_planes.Run, its message on screen all the while (the head
# circles until it ends), until every record drawn lies on the path the
# head has taken in the state it is in.
PLAYED = {"Boss_ShieldViperInitialize"}
ANGLE_FIELD = 0x56
# What of a record's setup the document does not carry: its mapping
# pointer ($8, a stand-in of the builder's own: hand-built parts name their
# mapping), its places and speeds ($10-$1F) and its 16.16 places ($40-$47).
UNCARRIED = frozenset(range(0x8, 0xC)) | frozenset(range(0x10, 0x20)) | frozenset(range(0x40, 0x48))
# The frames code after the renderer that turns parts itself runs before
# the boss is shown (Flying-Neo's tail has waved a while).
POST_FRAMES = 300
FRAME_COUNTER = 0xFFA000
RANDOM_STATE = 0xFFFF08
# The record the first boss object takes (Entity_ObjectPool): where the
# studio runs a boss's pose code.
SLOT = 0xFFC620
INIT = "Sprite_InitializeLinkedMetaspriteParts"
INIT_MORE = "Sprite_InitializeAdditionalLinkedMetaspriteParts"
DELTAS = "Anim_CalculatePoseChannelDeltas"
# The renderers that place the parts down the skeleton.
TRAVERSALS = (
    "Sprite_BeginMetaspritePartTraversal",
    "Sprite_SetMetaspriteTraversalPointers",
    "Sprite_UpdateMetaspriteEightFrameRotation",
    "Sprite_UpdateMetaspriteFourFrameRotationAndLoadCount",
    "Boss_BackStringerUpdateSegmentChainAndLoadCount",
)
# Back Stringer's renderer: bit 0 of a part's field $20 also keeps a point
# half a bone further along it (`reach`), bit 1 hangs the part from its
# parent's such point instead of its position (`from_reach`).
CHAIN = "Boss_BackStringerUpdateSegmentChainAndLoadCount"
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
    "Boss_FlyingNeoSetup",
    "Boss_BackStringerInitializeState",
    "Entity_InitValkirieBattleState0",
    "Boss_InitSylpheed",
    "Boss_InitMedusaState0",
    "Boss_InitSireneMetasprite",
    "Boss_InitArtemisState0",
    "Boss_SharpsteelInitializeState",
    "Boss_DeepStriderIntroRise",
    "Boss_XiTigerSetup",
    "Boss_ZLeoIntroInit",
    "Boss_MadamBarbarSetupState",
    "Boss_WolfGaropaInitialize",
    # Chains: their records' positions and mappings as Gens's states of the
    # fights have them, the frame after.
    "Boss_JetsripperInitBody",
    "Boss_GustheadInitializeRootAndParts",
    "Boss_CaterpillarInitSegments",
    "Boss_SunsetStingInit",
    # Its head and body records, the frames its message is on screen.
    "Boss_ShieldViperInitialize",
    # Plane bodies: the name-table cells, and the corner from the boss as
    # the scroll shows it, as in Gens's states of their fights.
    "Boss_MissirayMain",
    "Boss_DestroyerMk2Main",
    "Boss_JampanMain",
    "Boss_BugmaxMain",
    # Its core, eyes and ring as it flies in, its own sprites.
    "Entity_VictorSubtypeDispatcher",
    # Its stage's blocks it lifts into plane B, lying level between rocks.
    "Boss_ViblackMain",
    # The cut boss's face and tentacles, as MAME runs it in Antroid's place.
    "Boss_LovePenguinInitDispatcher",
    # Its bands and wings as it waits to fight, column scroll included.
    "Boss_Epsilon1Main",
    # The same builders as sources before 3.0 spell them, which projects
    # made from those sources still hold.
    "Boss_SharpssteelInitializeState",
    "Boss_DestroyerMK2Main",
    "EntityType1C0_InitDispatcher",
}

ENTITY_TYPE = re.compile(r"^\s*move\.w\s+#([^,]+),\(a5\)\s*$")
CALL = re.compile(r"^\s*(?:jsr|jmp|bsr(?:\.[swl])?|bra(?:\.[swl])?)\s+\(?(?P<target>\w+)\)?(?:\.l|\(pc\))?\s*$")
MOVEQ = re.compile(r"^\s*moveq\s+#(?P<value>[^,]+),d(?P<register>[0-7])\s*$")
ADDRESS = re.compile(
    r"^\s*(?:movea\.l\s+#(?P<immediate>\w+)|movea\.l\s+\$(?P<field>[0-9A-Fa-f]+)\(a5\)"
    r"|lea\s+\(?(?P<lea>\w+)\)?(?:\(pc\)|\.l)?),a(?P<register>[0-7])\s*$"
)
BASE_TILE = re.compile(r"^\s*move\.w\s+#(?P<value>[^,]+),\(MetaspriteBaseTileWord\)\.w\s*$")
TARGETS = re.compile(r"^\s*(?:addi\.l\s+#(?P<label>\w+)|add\.l\s+\$(?P<field>[0-9A-Fa-f]+)\(a5\)),d0\s*$")
RAM = re.compile(r"^\s*movea\.w\s+#\((?P<name>\w+)-M68K_RAM\),a(?P<register>[0-7])\s*$")
PALETTE_SOURCE = re.compile(r"^\s*lea\s+\(?(?P<label>\w+)\)?(?:\.l|\(pc\))?,a0\s*$")
PALETTE_LOAD = re.compile(r"^\s*jsr\s+\(?Gfx_LoadPaletteCommand\)?(?:\.l)?\s*$")
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
        elif (match := RAM.match(line)) and match["register"] == "4" and match["name"] in tree.equates:
            # A slot by name (Flying-Neo builds from the boss's second).
            registers["a4"] = (tree.equates[match["name"]] - SLOT) & 0xFFFF
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


def _falling_into(tree: SourceTree, label: str) -> list[str]:
    """The lines of the labels before `label` that run on into it."""
    labels = tree.labels_in(tree.block(label).file.path)
    start = labels.index(label)
    while start > 0:
        code = [line.split()[0] for line in tree.body(labels[start - 1]) if line.strip()]
        if not code or code[-1].split(".")[0] in ("rts", "rte", "bra", "jmp"):
            break
        start -= 1
    return [line for item in labels[start : labels.index(label)] for line in tree.body(item)]


def _angle_code(tree: SourceTree, label: str, symbols: pose_code.Symbols) -> list[list[Any]] | None:
    """The routine at `label` as a program when it is pose code: it runs in
    the subset and gives parts their angles."""
    try:
        program = pose_code.compile_routine(tree, label, symbols)
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
    # Some take a0 (the channels) and d7 (the angle mask) from their caller
    # (Medusa's): those read a0 before anything sets it.
    given = False
    for step in program:
        operands = [operand for operand in step[2:] if isinstance(operand, list)]
        if any(operand[0] == "(a)" and operand[1] == 0 for operand in operands):
            given = True
            break
        if step[3] == ["a", 0]:
            break
    reads_channels = given or any(step[0] == "movea" and step[2] and step[2][0] == "#" for step in program)
    # A loop's head inside a routine (its code branches back to it) is not
    # where the routine starts.
    loops = any(isinstance(step[3], int) and step[3] == 0 and step[0] != "bsr" for step in program)
    if len(stores) < 2 or not reads_channels or loops:
        return None
    if given:
        return [["prelude", "", None, None], *program]
    return program


def _pose_system(tree: SourceTree, files: list[str]) -> dict[str, Any] | None:
    """The pose table, neutral pose, channel base and count, timing, the
    angle code and the animations of a boss's files."""
    fields = _fields(tree, files)
    labels = [label for path in files for label in tree.labels_in(path)]
    found: dict[str, Any] = {}
    candidates: list[tuple[str, list[list[Any]]]] = []
    # Labels the pose code loads as values (mappings it picks) get stand-in
    # addresses; the document lists them.
    symbols = pose_code.Symbols(tree.equates, tree.blocks)
    found["symbols"] = symbols
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
        if any((call := CALL.match(line)) and call["target"] == DELTAS for line in body) and "channels" not in found:
            # From where the routine starts (Artemis's sets a1 and d7 a label
            # before the call). The boss's own file's call first: a form
            # that shares its renderer's file (Valkirie's alternate) steps
            # channels of its own.
            for line in [*_falling_into(tree, label), *body]:
                if (match := ADDRESS.match(line)) and match["register"] == "1":
                    names = [match["lea"]] if match["lea"] else _named(match, fields, "immediate")
                    if names:
                        found["neutral"] = names[0]
                elif (match := MOVEQ.match(line)) and match["register"] == "7":
                    found["channels"] = (parse_number(match["value"]) or 0) + 1
                elif (match := RAM.match(line)) and match["register"] == "2":
                    found["base"] = match["name"]
        program = _angle_code(tree, label, symbols)
        if program is not None:
            candidates.append((label, program))
    # Code that calls nothing first: a routine that steps the channels and
    # then falls into the angle code would run them on.
    if candidates:
        label, program = min(candidates, key=lambda item: any(step[0] == "bsr" for step in item[1]))
        found["angles"], found["program"] = label, program
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
            [step[0], step[1], step[2], step[3] + 1]
            if isinstance(step[3], int)
            else [step[0], step[1], step[2], [index + 1 if index >= 0 else index for index in step[3]]]
            if step[0] == "switch"
            else step
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
                if name in tree.blocks and tree.block(name).kind in ("dc.b", "dc.w", "dc.l", "mixed"):
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


def _label_data(tree: SourceTree, symbols: pose_code.Symbols) -> dict[int, int]:
    """The bytes of the labels `symbols` gave stand-ins, at them."""
    memory: dict[int, int] = {}
    for name, address in symbols.given.items():
        try:
            data = tree.data(name)[: pose_code.Symbols.SPACING]
        except (AslSourceError, KeyError, ValueError):
            continue
        memory.update({address + offset: value for offset, value in enumerate(data)})
    return memory


def _ram_tables(tree: SourceTree, files: list[str]) -> dict[str, str]:
    """The tables the boss's code builds in RAM before the fight with the
    game's table builders (`Math_...Table`: Flying-Neo's tail swings on a
    sine table of its own), as hex address: hex bytes: each call runs with
    the code that sets it up."""
    out: dict[str, str] = {}
    for path in files:
        for label in tree.labels_in(path):
            body = tree.body(label)
            for at, line in enumerate(body):
                call = CALL.match(line)
                if not call or not re.fullmatch(r"Math_\w*Table", call["target"]):
                    continue
                start = at
                while start > 0 and not CALL.match(body[start - 1]):
                    start -= 1
                symbols = pose_code.Symbols(tree.equates, tree.blocks)
                for first in range(start, at + 1):
                    try:
                        program = pose_code.compile_lines(
                            [*body[first : at + 1], " rts"], symbols, tree, follow=lambda name: name == call["target"]
                        )
                        machine = pose_code.run(program, pose_code.Machine(_label_data(tree, symbols), {"a5": SLOT}))
                    except (pose_code.PoseCodeError, AslSourceError):
                        continue
                    # Each run of the RAM it wrote (the builder saves a register
                    # somewhere else too).
                    written = sorted(address for address in machine.memory if address >= 0xFF0000)
                    runs: list[list[int]] = []
                    for address in written:
                        if runs and address - runs[-1][-1] <= 4:
                            runs[-1].append(address)
                        else:
                            runs.append([address])
                    for run in runs:
                        low, high = run[0], run[-1]
                        out[f"{low:06X}"] = bytes(machine.read(address, 1) for address in range(low, high + 1)).hex()
                    break
    return out


def _built(
    tree: SourceTree, label: str, files: list[str], parts: list[dict[str, Any]] | None = None
) -> tuple[pose_code.Machine, dict[int, str]] | None:
    """The records as the builder leaves them: its code after the skeleton
    call runs on records holding only the skeleton's attributes (`parts`',
    whose bits it may change), leniently (see pose_code.py), with the
    routines of the boss's files it calls (not the game's, that queue tiles
    or build groups from tables); and the labels behind the stand-ins it
    stored."""
    block = tree.block(label)
    lines = [_code(line) for line in block.file.lines[block.line :]]
    built = next((at for at, line in enumerate(lines) if (call := CALL.match(line)) and call["target"] == INIT), None)
    if built is None:
        return None
    symbols = pose_code.Symbols(tree.equates, tree.blocks)
    following = block.file.lines[block.line + built + 1 :]
    try:
        program = pose_code.compile_lines(
            following, symbols, tree, lenient=True, follow=lambda name: tree.block(name).file.path in files
        )
        # The data it reads by label (Flying-Neo's tail's sprite words).
        machine = pose_code.Machine(_label_data(tree, symbols), {"a5": SLOT})
        for index, part in enumerate(parts or []):
            if index:
                machine.write(SLOT + index * PART_SIZE + 0xE, 2, part["attribute"])
        pose_code.run(program, machine)
    except (pose_code.PoseCodeError, AslSourceError):
        return None
    return machine, {address: name for name, address in symbols.given.items()}


def _hand_built(
    tree: SourceTree,
    found: tuple[pose_code.Machine, dict[int, str]] | None,
    parts: list[dict[str, Any]],
    frames: dict[str, Any],
) -> None:
    """Records the builder fills in itself after building the skeleton
    (Wolf Garopa's horse's head and neck and its rider's head and arm,
    Flying-Neo's head and tail): past the skeleton they join the parts,
    placed by the boss's code; a joint of the skeleton the builder gives a
    sprite draws it. Each draws the mapping it is given (bit 14 of its
    flags), else a sprite of its own (a size byte and X and Y offsets where
    the mapping would be), with its attribute and depth. The skeleton's parts take the attribute
    bits the builder changes (Shellshogun's arms and shoulders draw over
    its belly)."""
    if found is None:
        return
    machine, names = found
    for index in range(1, len(parts)):
        if not parts[index].get("anchor"):
            parts[index] = {**parts[index], "attribute": machine.read(SLOT + index * PART_SIZE + 0xE, 2)}
    for index in range(1, 64):
        record = SLOT + index * PART_SIZE
        if not machine.read(record, 2):
            if index >= len(parts):
                break
            continue
        if index < len(parts) and (parts[index]["kind"] != "none" or parts[index].get("anchor")):
            continue
        part: dict[str, Any] = {
            "parent": None,
            "radius": 0,
            "depth": machine.read(record + 0x20, 1) >> 2,
            "kind": "none",
            "attribute": machine.read(record + 0xE, 2),
            "hand_built": True,
        }
        if machine.read(record + 2, 2) & 0x4000:
            name = names.get(machine.read(record + 8, 4))
            try:
                pieces = _frame(tree, name) if name else None
            except (BossError, AslSourceError, mappings.MappingError, IndexError):
                pieces = None
            if pieces is not None:
                frames.setdefault(name, pieces)
                part.update(kind="mapping", source=name)
        elif machine.read(record + 2, 2) & 0x8000 and part["attribute"] & 0x7FF:
            part.update(
                kind="sprite",
                size=machine.read(record + 8, 1),
                x=_signed(machine.read(record + 0xA, 1), 8),
                y=_signed(machine.read(record + 0xB, 1), 8),
            )
        if index < len(parts):
            if part["kind"] != "none":
                parts[index] = {**parts[index], **{key: part[key] for key in part if key not in ("parent", "radius")}}
            continue
        parts.extend([{"parent": None, "radius": 0, "depth": 0, "kind": "none", "attribute": 0}] * (index - len(parts)))
        parts.append(part)


def _runs(memory: dict[int, int]) -> dict[str, str]:
    """Bytes by address as `tables` holds them: runs of hex by their first
    address."""
    runs: dict[str, str] = {}
    start, data = None, bytearray()
    for address in sorted(memory):
        if start is None or address != start + len(data):
            if start is not None:
                runs[f"{start:06X}"] = data.hex()
            start, data = address, bytearray()
        data.append(memory[address])
    if start is not None:
        runs[f"{start:06X}"] = data.hex()
    return runs


def _uncarried(address: int) -> bool:
    """Whether a byte is a record's that the document does not carry
    (UNCARRIED)."""
    return SLOT <= address < SLOT + 64 * PART_SIZE and (address - SLOT) % PART_SIZE in UNCARRIED


def _setup_ram(machine: pose_code.Machine) -> dict[int, int]:
    """What the builder leaves in the boss's records (Wolf Garopa's leg
    anchors' angles) but their mappings and places: the bytes it set. The studio puts them
    in before running the boss's code."""
    return {
        address: value
        for address, value in machine.memory.items()
        if value and SLOT <= address < SLOT + 64 * PART_SIZE and not _uncarried(address)
    }


def _post_turns(boss: dict[str, Any], ram: dict[int, int]) -> dict[int, int]:
    """Code after the renderer that turns parts itself, frame after frame
    (Flying-Neo's tail waves on random pushes, from rest): run POST_FRAMES
    frames, each on the next frame count and random number as the game's,
    it leaves the parts somewhere along the way. The bytes it changed, but
    mappings and places; nothing if it turns no part."""
    if not boss["post_code"]:
        return {}
    memory = {
        int(at, 16) + index: byte
        for at, data in boss["tables"].items()
        for index, byte in enumerate(bytes.fromhex(data))
    }
    memory.update(ram)
    machine = pose_code.Machine(dict(memory), {"a5": SLOT})
    angles = [SLOT + index * PART_SIZE + ANGLE_FIELD for index in range(len(boss["parts"]))]
    before = [machine.read(address, 2) for address in angles]
    random = 0
    try:
        for frame in range(POST_FRAMES):
            # The game's RandomNumber, once a frame.
            random = random or 0x2A6D365A
            step = ((((random << 2) + random) << 3) + random) & 0xFFFFFFFF
            low = (step + (step >> 16)) & 0xFFFF
            random = (low << 16) | low
            machine.write(RANDOM_STATE, 4, random)
            machine.write(FRAME_COUNTER, 2, frame)
            machine.a[5] = SLOT
            pose_code.run(boss["post_code"], machine)
    except pose_code.PoseCodeError:
        return {}
    if [machine.read(address, 2) for address in angles] == before:
        return {}
    return {
        address: value
        for address, value in machine.memory.items()
        if memory.get(address, 0) != value and not _uncarried(address)
    }


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


QUEUE_ROWS = "Tilemap_QueueIndexedRows"
QUEUE_BLOCKS = (QUEUE_ROWS, "Tilemap_QueueIndexedColumns")
PLANE_A, PLANE_B = 0xC000, 0xE000
PLANE_CELLS = 64
CAMERA_X = re.compile(r"^\s*move\.w\s+#(?P<value>[^,]+),d0\s*$")


def _camera(tree: SourceTree, files: list[str]) -> tuple[int, int, int] | None:
    """The constants a boss's code scrolls plane B with: C of `C - x` and D
    of `y + D`, and the record whose x and y they are (0, the boss's own,
    but for the debug viewer's)."""
    for path in files:
        for label in tree.labels_in(path):
            body = [line.strip() for line in tree.body(label) if line.strip()]
            for at in range(len(body) - 5):
                window = body[at : at + 6]
                first = CAMERA_X.match(window[0])
                second = re.match(r"^addi\.w\s+#(?P<value>[^,]+),d0$", window[4])
                if (
                    first
                    and re.match(r"^sub\.w\s+\$10\(a5\),d0$", window[1])
                    and window[2].replace(" ", "") == "move.wd0,(SecondaryCameraXPos).w"
                    and re.match(r"^move\.w\s+\$14\(a5\),d0$", window[3])
                    and second
                    and window[5].replace(" ", "") == "move.wd0,(SecondaryCameraYPos).w"
                ):
                    across, down = parse_number(first["value"]), parse_number(second["value"])
                    if across is not None and down is not None:
                        return across, down, 0
    # Joker scrolls across so, and down line by line (its body stretches):
    # at rest every line takes V - y, so D is -V.
    for path in files:
        for label in tree.labels_in(path):
            body = [line.strip() for line in tree.body(label) if line.strip()]
            for at in range(len(body) - 2):
                first = CAMERA_X.match(body[at])
                if not (
                    first
                    and re.match(r"^sub\.w\s+\$10\(a5\),d0$", body[at + 1])
                    and body[at + 2].replace(" ", "") == "move.wd0,(SecondaryCameraXPos).w"
                ):
                    continue
                for line in _lines_from(tree, label)[:80]:
                    raster = re.match(r"^\s*move\.w\s+#(?P<value>[^,]+),d3\s*$", line)
                    if raster and parse_number(raster["value"]) is not None:
                        following = _lines_from(tree, label)
                        index = following.index(line)
                        if re.match(r"^\s*sub\.w\s+\$14\(a5\),d3\s*$", following[index + 1]):
                            return parse_number(first["value"]), -parse_number(raster["value"]), 0
    # Flying-Neo writes plane B's line and column scroll itself: A + x into
    # the lines its body crosses, B - y into its columns (so C is -A, D -B).
    for path in files:
        for label in tree.labels_in(path):
            body = [line.strip() for line in tree.body(label) if line.strip()]
            if not any("HScrollPlaneB" in line for line in body):
                continue
            lines = _lines_from(tree, _routine_start(tree, label))[:200]
            lines = [line.strip() for line in lines if line.strip()]
            across = down = None
            for at in range(len(lines) - 1):
                first = re.match(r"^move(?:q|\.w)\s+#(?P<value>[^,]+),d0$", lines[at])
                if not first or parse_number(first["value"]) is None:
                    continue
                if re.match(r"^add\.w\s+\$10\(a5\),d0$", lines[at + 1]) and across is None:
                    across = -parse_number(first["value"])
                elif re.match(r"^sub\.w\s+\$14\(a5\),d0$", lines[at + 1]) and down is None:
                    down = -parse_number(first["value"])
            if across is not None and down is not None:
                return across, down, 0
    # Any other way of writing it (Wolf Garopa keeps the boss's position in
    # fields first).
    return _scroll_by_code(tree, files, "SecondaryCameraXPos", "SecondaryCameraYPos")


def _routine_start(tree: SourceTree, label: str) -> str:
    """The first of the labels that run on into `label`."""
    labels = tree.labels_in(tree.block(label).file.path)
    start = labels.index(label)
    while start > 0:
        code = [line.split()[0] for line in tree.body(labels[start - 1]) if line.strip()]
        if not code or code[-1].split(".")[0] in ("rts", "rte", "bra", "jmp"):
            break
        start -= 1
    return labels[start]


def _scroll_by_code(
    tree: SourceTree, files: list[str], across_name: str, down_name: str
) -> tuple[int, int, int] | None:
    """C and D of a camera the boss's code scrolls with `C - x` and
    `y + D`, however written, and the record whose x and y they are: the
    code from its last call to both writes (or from the label before, which
    runs into them: the debug viewer picks C there) runs on two positions of
    the boss's record, else of its first part's, and the scroll must follow
    them."""
    for path in files:
        labels = tree.labels_in(path)
        for label in labels:
            body = tree.body(label)
            writes = [
                at
                for at, line in enumerate(body)
                if re.match(rf"^\s*move\.w\s+d[0-7],\(({across_name}|{down_name})\)\.w\s*$", line)
            ]
            if len(writes) < 2:
                continue
            start = max((at + 1 for at, line in enumerate(body[: writes[0]]) if CALL.match(line)), default=0)
            codes = [[*body[start : writes[1] + 1], " rts"]]
            before = labels.index(label) - 1
            if not start and before >= 0:
                block, own = tree.block(labels[before]), tree.block(label)
                lines = block.file.lines[block.line : own.line + 1] + [
                    line for line in body[: writes[1] + 1] if line.strip()
                ]
                codes.insert(0, [*lines, " rts"])
            for code, record in ((code, record) for code in codes for record in (0, 1)):
                try:
                    program = pose_code.compile_lines(code, tree.equates)
                except pose_code.PoseCodeError:
                    continue
                found = []
                for x, y in ((0x100, 0x80), (0x140, 0xA0)):
                    machine = pose_code.Machine({}, {"a5": SLOT})
                    machine.write(SLOT + record * PART_SIZE + 0x10, 2, x)
                    machine.write(SLOT + record * PART_SIZE + 0x14, 2, y)
                    try:
                        pose_code.run(program, machine)
                    except pose_code.PoseCodeError:
                        break
                    across = _signed(machine.read(tree.equates[across_name] & 0xFFFFFF, 2), 16) + x
                    down = _signed(machine.read(tree.equates[down_name] & 0xFFFFFF, 2), 16) - y
                    found.append((across, down))
                if len(found) == 2 and found[0] == found[1]:
                    return (*found[0], record)
    return None


def _ram_loads(tree: SourceTree, asset_set: str | None, files: list[str]) -> dict[int, int]:
    """The RAM the boss's asset set and the lists its code plays fill (their
    LZSS-to-RAM loads, later ones over earlier), as bytes by offset in the
    68000's RAM."""
    from retrolarium_kit.games.alien_soldier import lzss
    from retrolarium_kit.games.alien_soldier.assets import LZSS_TO_RAM, asset_lists

    lists = asset_lists(tree)
    names = []
    if asset_set in tree.blocks:
        fields = [operand.text for directive in tree.block(asset_set).directives for operand in directive.operands]
        names += [fields[1]] if len(fields) > 1 and fields[1] in lists else []
    code = "\n".join(line for path in files for label in tree.labels_in(path) for line in tree.body(label))
    names += [name for name in objects.LIST_REFERENCE.findall(code) if name in lists and name not in names]
    memory: dict[int, int] = {}
    for command in (command for name in names for command in lists[name]):
        if command.command != LZSS_TO_RAM or command.source not in tree.blocks:
            continue
        data = lzss.decompress(tree.data(command.source))
        for index, value in enumerate(data):
            memory[(command.ram_offset + index) & 0xFFFF] = value
    return memory


def _run_before(tree: SourceTree, label: str, line: str, memory: dict[int, int]) -> dict[int, int]:
    """`memory` after the code just before the queue command (`line` in
    `label`'s routine) runs on it, as far back as the subset follows it,
    the routines it calls included: Xi-Tiger's setup moves the patterns its
    body uses down $80 tiles, Wolf Garopa's adds to their tile indices."""
    block = tree.block(label)
    lines = block.file.lines
    end = next(
        index for index in range(block.line, len(lines)) if pose_code._code(lines[index]).strip() == line.strip()
    )
    start = end
    while start > 0:
        code = pose_code._code(lines[start - 1]).strip()
        bare = re.sub(r"^[A-Za-z_]\w*:", "", code).strip()
        if bare.split(" ")[0] in ("rts", "rte", "jmp", "bra.w", "bra.s", "bra"):
            break
        try:
            if bare and not re.match(r"^(b[a-z]{2}|dbf|dbra)(\.[sw])?\s", bare):
                pose_code.compile_lines([bare, " rts"], tree.equates, tree)
        except (pose_code.PoseCodeError, AslSourceError):
            break
        start -= 1
    # Branches into code left out would leave the block: start later then.
    while start < end:
        try:
            program = pose_code.compile_lines([*lines[start:end], " rts"], tree.equates, tree)
            break
        except (pose_code.PoseCodeError, AslSourceError):
            start += 1
    else:
        return memory
    machine = pose_code.Machine({0xFF0000 + offset: value for offset, value in memory.items()}, {"a5": SLOT})
    try:
        pose_code.run(program, machine)
    except pose_code.PoseCodeError:
        return memory
    return {address - 0xFF0000: value for address, value in machine.memory.items() if address >= 0xFF0000}


def _streamed_rows(tree: SourceTree, files: list[str]) -> list[tuple[str, int, int, bytes]]:
    """The rows a boss's code queues one by one as it rises (Z-Leo's): a
    table of name-table addresses (`lea D(pc),a4`) and one of index rows
    (`lea T(pc),a1`, longs), the patterns' RAM written between them. As
    (table, address word, source, indices) per row, all of them."""
    for path in files:
        for label in tree.labels_in(path):
            body = [line.strip() for line in tree.body(label) if line.strip()]
            places = [match["lea"] for line in body if (match := ADDRESS.match(line)) and match["register"] == "4"]
            rows = [match["lea"] for line in body if (match := ADDRESS.match(line)) and match["register"] == "1"]
            source = next(
                (
                    parse_number(match[1])
                    for line in body
                    if (match := re.match(r"^move\.w\s+#([^,]+),\(a3\)\+$", line))
                ),
                None,
            )
            if not places or not rows or source is None or places[0] not in tree.blocks or rows[0] not in tree.blocks:
                continue
            if tree.block(places[0]).kind != "dc.w" or tree.block(rows[0]).kind != "dc.l":
                continue
            addresses = tree.block(places[0]).values()
            names = _table_longs(tree, rows[0], len(addresses))
            if len(names) != len(addresses) or any(name not in tree.blocks for name in names):
                continue
            return [(places[0], address, source, tree.data(name)) for address, name in zip(addresses, names)]
    return []


def _run_after_build(tree: SourceTree, builder: str, memory: dict[int, int]) -> dict[int, int]:
    """`memory` after the code right after the builder's skeleton call runs
    on it, as far as the subset follows it (Z-Leo's moves its patterns'
    tile indices to where its art sits)."""
    block = tree.block(builder)
    lines = [pose_code._code(line) for line in block.file.lines[block.line :]]
    start = next(
        (at + 1 for at, line in enumerate(lines) if (call := CALL.match(line)) and call["target"] == INIT), None
    )
    if start is None:
        return memory
    end = start
    while end < len(lines):
        bare = re.sub(r"^[A-Za-z_]\w*:", "", lines[end]).strip()
        if bare.split(" ")[0] in ("rts", "rte", "jmp", "bra", "bra.w", "bra.s") or re.match(
            r"^(b[a-z]{2}|dbf|dbra)", bare
        ):
            break
        try:
            if bare:
                pose_code.compile_lines([bare, " rts"], tree.equates, tree)
        except (pose_code.PoseCodeError, AslSourceError):
            break
        end += 1
    try:
        program = pose_code.compile_lines([*lines[start:end], " rts"], tree.equates, tree)
        machine = pose_code.Machine({0xFF0000 + offset: value for offset, value in memory.items()}, {"a5": SLOT})
        pose_code.run(program, machine)
    except (pose_code.PoseCodeError, AslSourceError):
        return memory
    return {address - 0xFF0000: value for address, value in machine.memory.items() if address >= 0xFF0000}


def _body(tree: SourceTree, files: list[str], builder: str, asset_set: str | None) -> dict[str, Any] | None:
    """A boss's body in a plane (see the module text), or None."""
    camera, plane = _camera(tree, files), PLANE_B
    if camera is None:
        # Z-Leo's sits in plane A, which scrolls with it.
        camera, plane = _scroll_by_code(tree, files, "PrimaryCameraXPosition", "PrimaryCameraYPosition"), PLANE_A
    if camera is None:
        return None

    def address_of(word: int) -> int:
        return 0xC000 | (word & 0xEFFE & 0x3FFF)

    # The routines the builder calls (Flying-Neo's queues its body in one),
    # itself before those the code after it calls (the debug viewer loads
    # its left-facing body so, and the right-facing one when turned).
    own = {call["target"] for line in tree.body(builder) if (call := CALL.match(line))}
    called = {call["target"] for line in _lines_from(tree, builder)[:200] if (call := CALL.match(line))}
    commands = []
    for path in files:
        for label in tree.labels_in(path):
            body = [line for line in tree.body(label) if line.strip() and line.strip() != "nop"]
            for at, line in enumerate(body[:-1]):
                match = ADDRESS.match(line)
                call = CALL.match(body[at + 1])
                # Rows or columns, laid out alike (the debug viewer's body
                # comes in columns).
                if match and match["register"] == "0" and call and call["target"] in QUEUE_BLOCKS:
                    name = match["immediate"] or match["lea"]
                    if (
                        name in tree.blocks
                        and plane <= address_of(int.from_bytes(_bytes(tree, name, 2), "big")) < plane + 0x2000
                    ):
                        start = _routine_start(tree, label)
                        rank = 0 if label == builder else 1 if start in own else 2 if start in called else 3
                        commands.append((rank, name, label, line))
    blocks: list[tuple[int, int, int, int, bytes]] = []
    memory = _ram_loads(tree, asset_set, files)
    # Rows queued one by one as the boss rises make the whole body; the
    # blocks queued at once then only patch it (Z-Leo's face).
    streamed = [row for row in _streamed_rows(tree, files) if plane <= address_of(row[1]) < plane + 0x2000]
    if commands and not streamed:
        # The one the routine that builds the skeleton queues or calls, else
        # the first.
        _, name, label, line = min(commands, key=lambda item: item[0])
        data = _bytes(tree, name, 6)
        across, rows = data[4] + 1, data[5] + 1
        data = _bytes(tree, name, 6 + across * rows)
        blocks.append(
            (address_of(int.from_bytes(data[0:2], "big")), int.from_bytes(data[2:4], "big"), across, rows, data[6:])
        )
        memory = _run_before(tree, label, line, memory)
    else:
        if not streamed:
            return None
        name = streamed[0][0]
        blocks = [(address_of(word), source, len(indices), 1, indices) for _, word, source, indices in streamed]
        memory = _run_after_build(tree, builder, memory)
    # The blocks' cells on the plane, in one grid from the first's corner.
    origin = min(address for address, *_ in blocks)
    placed: dict[tuple[int, int], int] = {}
    for address, source, across, rows, indices in blocks:
        cell = (address - origin) // 2
        for group in range(rows):
            for row in range(4):
                for column in range(across):
                    pattern = (source + indices[group * across + column] * 32 + row * 8) & 0xFFFF
                    for word in range(4):
                        at = pattern + 2 * word
                        placed[(cell // PLANE_CELLS + group * 4 + row, cell % PLANE_CELLS + column * 4 + word)] = (
                            memory.get(at, 0) << 8 | memory.get(at + 1, 0)
                        )
    height = max(row for row, _ in placed) + 1
    width = max(column for _, column in placed) + 1
    cells = [[placed.get((row, column), 0) for column in range(width)] for row in range(height)]
    if not any(any(row) for row in cells):
        return None
    # A tile that fills most of the edge is the scene behind (Flying-Neo's
    # sky): it draws nothing of the body, and the body's own tiles carry
    # the scene round it too (`scene`: the studio leaves that out).
    edge = Counter([*cells[0], *cells[-1], *(row[0] for row in cells), *(row[-1] for row in cells)])
    backdrop, count = edge.most_common(1)[0]
    scene = bool(backdrop & 0x7FF and count * 2 > sum(edge.values()))
    if scene:
        cells = [[0 if word == backdrop else word for word in row] for row in cells]
    cell = (origin - plane) // 2
    plane_x, plane_y = (cell % PLANE_CELLS) * 8, (cell // PLANE_CELLS) * 8
    x = (plane_x - camera[0] + 0x80) % (PLANE_CELLS * 8)
    y = (plane_y + camera[1] + 0x80) % 256
    return {
        "command": name,
        "plane": "A" if plane == PLANE_A else "B",
        "x": x - PLANE_CELLS * 8 if x >= PLANE_CELLS * 4 else x,
        "y": y - 256 if y >= 128 else y,
        "cells": cells,
        **({"scene": True} if scene else {}),
        # From that part's place, not the boss's (the debug viewer's body
        # rides on its first part).
        **({"part": camera[2]} if camera[2] else {}),
    }


def _anchor_code(
    tree: SourceTree, files: list[str], symbols: pose_code.Symbols, reads: set[int] = frozenset()
) -> list[list[Any]] | None:
    """The code a boss runs before its renderer places the parts (setting
    its anchors' positions, or the mappings of parts it turns itself:
    Back Stringer's arms, or fields of its own `reads`, what its code after
    the renderer reads: Shellshogun turns its sword with its hand), as a
    program; None when there is none."""
    for path in files:
        for label in tree.labels_in(path):
            body = tree.body(label)
            calls = [at for at, line in enumerate(body) if (call := CALL.match(line)) and call["target"] in TRAVERSALS]
            if not calls:
                continue
            # From where the routine starts: the labels before that fall
            # into this one, across the boss's files (Shellshogun's falls
            # in from the file before).
            labels = tree.order
            start = labels.index(label)
            while start > 0 and tree.block(labels[start - 1]).file.path in files:
                code = [line.split()[0] for line in tree.body(labels[start - 1]) if line.strip()]
                if not code or code[-1].split(".")[0] in ("rts", "rte", "bra", "jmp"):
                    break
                start -= 1
            lines = [f"{item}:\n" + "\n".join(tree.body(item)) for item in labels[start : labels.index(label)]]
            try:
                program = pose_code.compile_lines(
                    [*"\n".join(lines).split("\n"), label + ":", *body[: calls[0]], " rts"], symbols, tree
                )
            except pose_code.PoseCodeError:
                return None
            places = any(
                step[0] == "move"
                and isinstance(step[3], list)
                and step[3][0] == "(a)"
                and (
                    step[3][2] % PART_SIZE in (0x40, 0x44)
                    or (step[1] == "l" and step[3][2] == 8 and step[3][1] != 5)
                    or (step[3][1] == 5 and step[3][2] in reads)
                )
                for step in program
            )
            return program if places else None
    return None


def _reaches(tree: SourceTree, files: list[str], label: str, parts: list[dict[str, Any]]) -> None:
    """Mark the parts Back Stringer's renderer reaches past: its setup sets
    the bits of their field $20 right after building them (the code up to
    its next call runs on an empty record)."""
    if not any(
        (call := CALL.match(line)) and call["target"] == CHAIN
        for path in files
        for name in tree.labels_in(path)
        for line in tree.body(name)
    ):
        return
    body = tree.body(label)
    built = [at for at, line in enumerate(body) if (call := CALL.match(line)) and call["target"] == INIT]
    if not built:
        return
    # Labels kept: the code loops over the parts.
    block = tree.block(label)
    lines = []
    for line in [_code(line) for line in block.file.lines[block.line :]][built[0] + 1 :]:
        word = re.sub(r"^[A-Za-z_]\w*:", "", line).strip().split(" ")[0]
        if CALL.match(line) or word in ("rts", "rte"):
            break
        lines.append(line)
    try:
        machine = pose_code.run(
            pose_code.compile_lines([*lines, " rts"], tree.equates), pose_code.Machine({}, {"a5": SLOT})
        )
    except pose_code.PoseCodeError:
        return
    for index, part in enumerate(parts):
        flags = machine.read(SLOT + index * PART_SIZE + 0x20, 1)
        # (Padding slots share one record: each marked part gets its own.)
        if flags & 1:
            parts[index] = part = {**part, "reach": True}
        if flags & 2:
            parts[index] = {**part, "from_reach": True}


def _block_bytes(tree: SourceTree, item: str, symbols: pose_code.Symbols) -> bytes | None:
    """A data label's bytes: numbers as they assemble, labels as their
    stand-ins, a file's bytes as they are; None for what is no data."""
    directives = tree.block(item).directives
    if [directive.kind for directive in directives] == ["binclude"]:
        return tree.data(item)
    if not directives or any(directive.kind not in ("dc.b", "dc.w", "dc.l") for directive in directives):
        return None
    data = bytearray()
    for directive in directives:
        width = {"dc.b": 1, "dc.w": 2, "dc.l": 4}[directive.kind]
        for operand in directive.operands:
            try:
                value = pose_code.evaluate(operand.text, symbols)
            except (pose_code.PoseCodeError, KeyError):
                value = 0
            data += (value & ((1 << (8 * width)) - 1)).to_bytes(width, "big")
    return bytes(data)


def _tables(tree: SourceTree, symbols: pose_code.Symbols, programs: list[Any]) -> dict[str, str]:
    """The bytes of the source's tables the boss's code indexes
    (`Table(pc,d0.w)`), at their labels' stand-in addresses (hex address:
    hex bytes): numbers as they assemble, labels as their stand-ins, the
    data labels right after taken in too (up to the stand-ins' spacing),
    and those right before when the code reads before the label (Jetsripper
    reads the cosine as `-$80(a3,d3.w)` from the sine table)."""
    out: dict[str, str] = {}
    names = {address: name for name, address in symbols.given.items()}
    bases = {
        operand[1]
        for program in programs
        for step in program or []
        for operand in step[2:]
        if isinstance(operand, list) and operand and operand[0] == "(x)" and operand[2] < 0
    }
    # A table read through a register the code points at a label
    # (`lea Math_SineTable,a0`, then `Quarter-Math_SineTable(a0,d0.w)`): the
    # label, and the one the displacement reaches from it.
    pointed = {
        step[2][1]
        for program in programs
        for step in program or []
        if step[0] in ("lea", "movea")
        and isinstance(step[2], list)
        and step[2][0] in ("abs", "#")
        and step[2][1] in names
    }
    before = 0
    for program in programs:
        for step in program or []:
            for operand in step[2:]:
                if isinstance(operand, list) and operand and operand[0] == "(x)" and operand[2] >= 0:
                    bases |= {
                        (base + operand[1]) & 0xFFFFFF for base in pointed if (base + operand[1]) & 0xFFFFFF in names
                    } | pointed
                    if operand[1] >= 0x800000:
                        before = max(before, 0x1000000 - operand[1])
    for base in sorted(bases):
        name = names.get(base)
        if name is None:
            continue
        data = bytearray()
        for item in _following(tree, name):
            block = _block_bytes(tree, item, symbols)
            if block is None:
                break
            data += block
            if len(data) >= pose_code.Symbols.SPACING:
                break
        data = data[: pose_code.Symbols.SPACING]
        lead = bytearray()
        if before and base in pointed:
            labels = tree.labels_in(tree.block(name).file.path)
            for item in reversed(labels[: labels.index(name)]):
                block = _block_bytes(tree, item, symbols)
                if block is None or len(lead) >= before:
                    break
                lead[:0] = block
            lead = lead[-before:] if len(lead) >= before else lead
        if data or lead:
            out[f"{symbols.given[name] - len(lead):06X}"] = bytes(lead + data).hex()
    # Tables of labels the tables name (Love Penguin's parts point at their
    # mappings by angle so), as their stand-ins too.
    done = set(names.values())
    while waiting := [name for name in symbols.given if name not in done]:
        for name in waiting:
            done.add(name)
            if name not in tree.blocks or tree.block(name).kind != "dc.l":
                continue
            block = _block_bytes(tree, name, symbols)
            if block:
                out.setdefault(f"{symbols.given[name]:06X}", bytes(block[: pose_code.Symbols.SPACING]).hex())
    return out


def _traversal(tree: SourceTree, files: list[str], parts: int) -> list[int]:
    """The slots the renderer places down the skeleton: the first and how
    many (Flying-Neo's starts at its third; the others at the second)."""
    for path in files:
        for label in tree.labels_in(path):
            body = tree.body(label)
            first, count = 1, None
            for line in body:
                if (match := RAM.match(line)) and match["register"] == "4" and match["name"] in tree.equates:
                    first = ((tree.equates[match["name"]] - SLOT) & 0xFFFF) // PART_SIZE
                elif (match := MOVEQ.match(line)) and match["register"] == "7":
                    count = (parse_number(match["value"]) or 0) + 1
                elif (call := CALL.match(line)) and call["target"] in TRAVERSALS and count is not None:
                    if call["target"] in (
                        "Sprite_BeginMetaspritePartTraversal",
                        "Sprite_SetMetaspriteTraversalPointers",
                    ):
                        first = 1
                    return [first, count]
    return [1, parts - 1]


def _post_code(tree: SourceTree, files: list[str], symbols: pose_code.Symbols) -> list[list[Any]] | None:
    """The code right after a boss's renderer places the parts, up to its
    next call, when it moves parts (their 16.16 places, $40 and $44): a
    program, else None. When that code moves none, the rest of the routine
    as far as the subset follows it, when it moves parts by their pixel
    places ($10 and $14: Wolf Garopa's horse's head and rider)."""
    for path in files:
        for label in tree.labels_in(path):
            body = tree.body(label)
            calls = [at for at, line in enumerate(body) if (call := CALL.match(line)) and call["target"] in TRAVERSALS]
            if not calls:
                continue
            # The file's lines after the call, labels kept for the loops.
            block = tree.block(label)
            following = block.file.lines[block.line + calls[0] + 1 :]
            lines = []
            for raw in map(_code, following):
                line = re.sub(r"^[A-Za-z_]\w*:", "", raw)
                word = line.strip().split(" ")[0] if line.strip() else ""
                if CALL.match(line) or word in ("rts", "rte"):
                    break
                lines.append(raw)
            try:
                program = pose_code.compile_lines([*lines, " rts"], symbols)
            except pose_code.PoseCodeError:
                return None
            moves = any(
                step[0] in ("add", "sub", "move", "addi", "subi")
                and isinstance(step[3], list)
                and step[3][0] == "(a)"
                and step[3][2] in (0x40, 0x44)
                for step in program
            )
            if moves:
                return program
            try:
                program = pose_code.compile_lines(
                    following, symbols, tree, lenient=True, follow=lambda name: tree.block(name).file.path in files
                )
            except (pose_code.PoseCodeError, AslSourceError):
                return None
            # (Through a5 past the boss's own record, or through a register
            # pointed at a record: Flying-Neo's tail.)
            places = any(
                step[0] == "move"
                and isinstance(step[3], list)
                and step[3][0] == "(a)"
                and step[3][2] % PART_SIZE in (0x10, 0x14)
                and (step[3][1] != 5 or step[3][2] >= PART_SIZE)
                for step in program
            )
            return program if places else None
    return None


def _code_driven(tree: SourceTree, files: list[str], builder: str) -> dict[str, Any] | None:
    """For a boss with no pose system, the code before its renderer when it
    turns the parts from fields of the boss (Deep Strider's), run on the
    values its setup gives them: a pose system of no channels, poses or
    animations; None when there is no such code."""
    symbols = pose_code.Symbols(tree.equates, tree.blocks)
    setup = [
        line
        for line in tree.body(builder)
        if (match := re.match(r"^\s*move\.w\s+#[^,]+,\$(?P<field>[0-9A-Fa-f]+)\(a5\)\s*$", line))
        # The facing is the stance's.
        and int(match["field"], 16) != 0x54
    ]
    for path in files:
        for label in tree.labels_in(path):
            body = tree.body(label)
            calls = [at for at, line in enumerate(body) if (call := CALL.match(line)) and call["target"] in TRAVERSALS]
            if not calls:
                continue
            start = _routine_start(tree, label)
            block = tree.block(start)
            lines = block.file.lines[block.line :]
            end = next(
                at
                for at in range(len(lines))
                if pose_code._code(lines[at]).strip() == body[calls[0]].strip()
                and at >= tree.block(label).line - block.line
            )
            try:
                program = pose_code.compile_lines([*setup, *lines[:end], " rts"], symbols, tree)
            except (pose_code.PoseCodeError, AslSourceError):
                continue
            turned = {
                step[3][2] // PART_SIZE
                for step in program
                if step[0] == "move"
                and isinstance(step[3], list)
                and step[3][0] == "(a)"
                and step[3][1] == 5
                and step[3][2] % PART_SIZE == ANGLE_FIELD
                and step[3][2] >= PART_SIZE
            }
            if len(turned) < 2:
                continue
            return {
                "targets": None,
                "timing": "set",
                "neutral": None,
                "channels": 0,
                "base": None,
                "angles": label,
                "program": program,
                "streams": [],
                "symbols": symbols,
            }
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


def _boss_files(tree: SourceTree, path: str, shared: bool = False) -> list[str]:
    """The files named like a boss file (antroid_*.s), its own folder first,
    then others (a boss's pose code may sit with its projectiles). Forms
    that share the name (Valkirie's) take their own file and the files it
    calls into."""
    own = PurePosixPath(path)
    prefix = own.stem.split("_")[0]
    found = [
        item
        for item in tree.files
        if PurePosixPath(item).stem.split("_")[0] == prefix
        and (item == path or not item.startswith("src/debug/"))
        and tree.labels_in(item)
    ]
    if shared:
        named = {
            word
            for label in tree.labels_in(path)
            for line in tree.body(label)
            for word in re.findall(r"[A-Za-z_]\w*", line)
        }
        found = [item for item in found if item == path or named & set(tree.labels_in(item))]
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
    for _ in range(boss.get("settle", 1)):
        machine.a[5] = SLOT
        pose_code.run(boss["pose_code"], machine)
    return [machine.read(SLOT + index * PART_SIZE + ANGLE_FIELD, 2) for index in range(len(boss["parts"]))]


def _settle(boss: dict[str, Any]) -> int:
    """How many times the pose code runs before a held pose stops moving the
    parts: once, but Sirene's tentacles take their angles from a history
    of the frames before (the studio runs it as often)."""
    machine = pose_code.Machine({}, {"a5": SLOT})
    for channel, value in enumerate(boss["neutral_pose"]):
        # Off the neutral pose: a history of zeros would look settled.
        machine.write(boss["channel_base"] + 4 * channel, 2, ((value + 0x10) & 0xFF) << 8)
    # A history fills a step a run: the last run that still moves a part.
    angles, settle = None, 1
    for run in range(1, 65):
        machine.a[5] = SLOT
        pose_code.run(boss["pose_code"], machine)
        now = [machine.read(SLOT + index * PART_SIZE + ANGLE_FIELD, 2) for index in range(1, len(boss["parts"]))]
        if now != angles:
            settle = run
        angles = now
    return settle


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


def _title(stem: str, names: list[str], whole: bool = False) -> str:
    """A boss's name from its file's: the leading words that spell a name
    the game uses (xi_tiger_battle_states is Xi-Tiger), else the file's
    words; all of them for forms that share a name (Valkirie Battle)."""
    words = stem.split("_")
    known = {re.sub(r"[^a-z0-9]", "", name.lower()) for name in names}
    for count in range(len(words), 0, -1):
        if not whole and "".join(words[:count]).lower() in known:
            return objects.title("_".join(words[:count]), names)
    # The game's name as it spells it when the source doubles a letter
    # (sources before 3.0 spell Sharpsteel with a double s).
    single = {re.sub(r"(.)\1+", r"\1", re.sub(r"[^a-z0-9]", "", name.lower())): name for name in names}
    if not whole and (name := single.get(re.sub(r"(.)\1+", r"\1", words[0].lower()))):
        return name
    return objects.title(stem, names)


def _draws_with(
    tree: SourceTree,
    sets: dict[str, Any],
    recorded: dict[str, Any],
    label: str,
    path: str,
    kinds: list[int],
    frames: dict[str, Any],
    parts: list[dict[str, Any]],
) -> tuple[str | None, dict[str, Any]]:
    """The asset set a boss draws with and what it loads and colours."""
    prefix = PurePosixPath(path).stem.split("_")[0]
    # The set of the entity type the builder gives the boss, else one its
    # files' name points at (forms that share it have sets of their own).
    asset_set = next((name for name, item in sets.items() if item.get("type") in kinds), None) or next(
        (name for name, item in sets.items() if item.get("prefix") == prefix), None
    )
    if asset_set is None:
        # A set with no palette of its own (Flying-Neo's) is named after
        # the builder: its loads still fill the RAM its body is built from.
        words = re.findall(r"[A-Z][a-z0-9]*", label.split("_", 1)[-1])
        asset_set = next(
            (
                f"Boss_{''.join(words[:count])}AssetSet"
                for count in range(len(words), 0, -1)
                if f"Boss_{''.join(words[:count])}AssetSet" in tree.blocks
            ),
            None,
        )
    context = sets.get(asset_set, {})
    if not context.get("loads"):
        own = context.get("palettes")
        # A Seven Force form keeps the art of the form before it: the set
        # fought on the same stage list that loads art (Valkirie's), in
        # the form's own colours.
        kept = next(
            (
                item
                for item in sets.values()
                if item.get("loads") and own and len(own) > 2 and item["palettes"][:-1] == own[:-1]
            ),
            None,
        )
        if kept is None:
            # A boss whose code loads its art (Flying-Neo) draws with what
            # Objects records for its mappings, as the art first loaded
            # where later loads (the next stage's) write again.
            if not recorded:
                recorded.update(objects.export(tree)["contexts"])
            labels = [*frames, *(part.get("source") for part in parts)]
            kept = next((recorded[name] for name in labels if name in recorded), context)
            places = [load.get("vram") for load in kept.get("loads", [])]
            kept = {
                **kept,
                "loads": [load for at, load in enumerate(kept.get("loads", [])) if load.get("vram") not in places[:at]],
            }
        context = {**kept, "palettes": own} if own else kept
    return asset_set, context


def _read(tree: SourceTree) -> list[dict[str, Any]]:
    from retrolarium_kit.core.game_names import GameNames

    game_names = GameNames.load(tree.root)
    names = [part.strip() for value in game_names.names.get("stages", {}).values() for part in value.split("/")]
    sets = objects.boss_contexts(tree, artless=True)
    # What Objects records, read once when a boss needs it.
    recorded: dict[str, Any] = {}
    found = []
    # (The debug folder holds a viewer that builds a boss the game cut: the
    # entity type $3EC set's.)
    builders = [
        label
        for label in tree.order
        if tree.block(label).file.path.startswith(("src/bosses/", "src/debug/"))
        and any((call := CALL.match(line)) and call["target"] == INIT for line in tree.body(label))
    ]
    # Forms that share their files' name (Valkirie's) read from their own
    # file and what it calls.
    paths = [tree.block(label).file.path for label in builders]
    prefixes = [PurePosixPath(path).stem.split("_")[0] for path in paths]
    for label, path, prefix in zip(builders, paths, prefixes):
        shared = len({other for other, name in zip(paths, prefixes) if name == prefix}) > 1
        files = _boss_files(tree, path, shared)
        try:
            parts, frames, base = _skeleton(tree, label)
            # The four-direction renderer (Deep Strider's) reads four frames:
            # the longs after them are the next table's.
            if any(
                (call := CALL.match(line)) and call["target"] == "Sprite_UpdateMetaspriteFourFrameRotationAndLoadCount"
                for path in files
                for name in tree.labels_in(path)
                for line in tree.body(name)
            ):
                parts = [
                    {**part, "frames": part["frames"][:4]} if part["kind"] == "rotation" else part for part in parts
                ]
            system = _pose_system(tree, files) or _code_driven(tree, files, label)
            if system is None:
                continue
            # A boss its code alone turns (Deep Strider) has no poses or
            # animations: it shows as its setup leaves it.
            driven = system["targets"] is None
            table = _table(tree, system["targets"]) if not driven else b""
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
            if (not sequences and not driven) or any(
                tree.block(item["label"]).file.path not in files for item in sequences
            ):
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
                "channel_base": tree.equates[system["base"]] & 0xFFFFFF if not driven else 0,
                "neutral_pose": list(_bytes(tree, system["neutral"], system["channels"])) if not driven else [],
                "pose_code": program,
                "side_field": side,
            }
            settle = _settle(boss)
            if settle > 1:
                boss["settle"] = settle
            joints = _joints(boss)
            # Pose code the channels do not move is not what this reader
            # takes it for.
            if all(joint is None for joint in joints) and not driven:
                continue
            _code_mappings(tree, files, parts, frames, base)
            setup = _built(tree, label, files, parts)
            _hand_built(tree, setup, parts, frames)
            _reaches(tree, files, label, parts)
        except (BossError, AslSourceError, pose_code.PoseCodeError, KeyError, TypeError, IndexError, StopIteration):
            continue
        kinds = [parse_number(match[1]) for line in tree.body(label) if (match := ENTITY_TYPE.match(line))]
        asset_set, context = _draws_with(tree, sets, recorded, label, path, kinds, frames, parts)
        boss.update(
            # A cut boss no text names takes the game repository's name for
            # its entity type, else its files'.
            title=next((game_names.name("bosses", kind) for kind in kinds if game_names.name("bosses", kind)), "")
            or _title(PurePosixPath(path).stem, names, shared),
            asset_set=asset_set,
            loads=context.get("loads", []),
            palettes=context.get("palettes", []),
            base_tile=base,
            frames=frames,
            facing=_facing(tree, files),
            neutral=system["neutral"],
            anchor_code=None,
            post_code=_post_code(tree, files, system["symbols"]),
            traversed=_traversal(tree, files, len(parts)),
            body=_body(tree, files, label, asset_set),
            joints=joints,
            timing=system["timing"],
            poses=system["targets"],
            sequences=[item["label"] for item in sequences],
            slot=SLOT,
            checked=label in CHECKED,
            _sequences=sequences,
            _table=table,
        )
        # Before the renderer: what places anchors, or sets what the code
        # after it reads.
        reads = {
            step[2][2] for step in boss["post_code"] or [] if isinstance(step[2], list) and step[2][:2] == ["(a)", 5]
        }
        boss["anchor_code"] = _anchor_code(tree, files, system["symbols"], reads)
        # A body is read as the setup builds it, for the facing the setup
        # gives the boss: the boss shows facing so.
        if boss["body"]:
            boss["faces"] = setup[0].read(SLOT + 0x54, 2) if setup else 0
        programs = [boss["pose_code"], boss["anchor_code"], boss["post_code"]]
        boss["tables"] = _tables(tree, system["symbols"], programs)
        boss["tables"].update(_ram_tables(tree, files))
        ram = _setup_ram(setup[0]) if setup else {}
        ram.update(_post_turns(boss, ram))
        boss["ram"] = _runs(ram)
        boss["symbols"] = dict(system["symbols"].given)
        # The mappings the code picks draw as the tables' do.
        for name in boss["symbols"]:
            try:
                frames.setdefault(name, _frame(tree, name))
            except (BossError, AslSourceError, mappings.MappingError, IndexError):
                continue
        found.append(boss)
    built = {PurePosixPath(tree.block(label).file.path).stem.split("_")[0] for label in builders}
    chains = _read_chains(tree, sets, recorded, game_names, names, built)
    found += chains
    taken = built | {PurePosixPath(tree.block(boss["id"]).file.path).stem.split("_")[0] for boss in chains}
    found += _read_planes(tree, sets, recorded, game_names, names, taken)
    return found


def _run_lenient(
    tree: SourceTree, label: str, files: list[str], symbols: pose_code.Symbols, machine: pose_code.Machine
) -> list[list[Any]] | None:
    """The routine at `label` compiled leniently (calling the boss's files'
    routines and the game's sprite helpers) and run on `machine`; None when
    it does not compile or run."""
    block = tree.block(label)
    try:
        program = pose_code.compile_lines(
            block.file.lines[block.line :],
            symbols,
            tree,
            lenient=True,
            follow=lambda name: tree.block(name).file.path in files or name.startswith(("Sprite_", "Math_")),
        )
        _preload(tree, symbols, [program], machine)
        machine.a[5] = SLOT
        pose_code.run(program, machine)
    except (pose_code.PoseCodeError, AslSourceError, KeyError, IndexError, ValueError):
        return None
    return program


def _preload(tree: SourceTree, symbols: pose_code.Symbols, programs: list[Any], machine: pose_code.Machine) -> None:
    """The data the programs read by label, at the labels' stand-ins (tables
    of labels as their stand-ins), where the machine holds nothing yet."""
    for address, value in _label_data(tree, symbols).items():
        machine.memory.setdefault(address, value)
    for at, data in _tables(tree, symbols, programs).items():
        for offset, value in enumerate(bytes.fromhex(data)):
            machine.memory[int(at, 16) + offset] = value


def _jump_target(line: str) -> str | None:
    """The label a call, jump or branch goes to."""
    if call := CALL.match(line):
        return call["target"]
    match = re.match(r"^\s*b[a-z]{2}(?:\.[sw])?\s+(\w+)\s*$", line)
    return match[1] if match else None


def _records(machine: pose_code.Machine, field: int, size: int) -> list[int]:
    return [machine.read(SLOT + index * PART_SIZE + field, size) for index in range(POOL_RECORDS)]


def _compile_lenient(
    tree: SourceTree, label: str, files: list[str], symbols: pose_code.Symbols
) -> list[list[Any]] | None:
    block = tree.block(label)
    try:
        return pose_code.compile_lines(
            block.file.lines[block.line :],
            symbols,
            tree,
            lenient=True,
            follow=lambda name: tree.block(name).file.path in files or name.startswith(("Sprite_", "Math_")),
        )
    except (pose_code.PoseCodeError, AslSourceError, KeyError, IndexError, ValueError):
        return None


def _chain(tree: SourceTree, files: list[str]) -> dict[str, Any] | None:
    """A boss built of records its code places each frame, not a skeleton:
    the routine that fills four or more records with a type (its setup, the
    first such in its files); the routine that, run after it, places the
    most of them (Jetsripper's chain, Gusthead's arms), if any; and the
    routines each record's type runs (Caterpillar's segments place
    themselves on its wave and pick their mappings). Run in that order they
    must place four records or more."""
    from retrolarium_kit.games.alien_soldier.levels import handlers

    own = [path for path in files if path.startswith("src/bosses/")]
    labels = [label for path in own for label in tree.labels_in(path)]
    by_type = handlers(tree)
    for setup in labels:
        if tree.block(setup).kind is not None:
            # Data (a state table) is no setup.
            continue
        symbols = pose_code.Symbols(tree.equates, tree.blocks)
        machine = pose_code.Machine({}, {"a5": SLOT})
        setup_code = _run_lenient(tree, setup, files, symbols, machine)
        # A state dispatcher runs its first state: the setup is that state.
        if setup_code is None or any(step[0] == "switch" for step in setup_code):
            continue
        kinds = _records(machine, 0, 2)
        if sum(1 for kind in kinds[1:] if kind) < 4:
            continue
        # Places relative to the boss's ($40 and $44: Jetsripper's), or
        # where they are ($10 and $14, longs: Gusthead's arms).
        before = {
            field: list(zip(_records(machine, field, 4), _records(machine, field + 4, 4))) for field in (0x40, 0x10)
        }

        def placed(trial: pose_code.Machine, field: int) -> int:
            after = list(zip(_records(trial, field, 4), _records(trial, field + 4, 4)))
            return sum(1 for index in range(1, POOL_RECORDS) if after[index] != before[field][index])

        # Of the routines that place as many, the one the others reach (its
        # states call or jump to the shared placing code).
        reached: Counter[str] = Counter(
            target
            for path in own
            for name in tree.labels_in(path)
            for line in tree.body(name)
            if (target := _jump_target(line))
        )
        best = None
        for label in labels:
            trial = pose_code.Machine(dict(machine.memory), {"a5": SLOT})
            program = _run_lenient(tree, label, files, symbols, trial)
            if program is None:
                continue
            for field in (0x40, 0x10):
                count = placed(trial, field)
                if count >= 4 and (best is None or (count, reached[label]) > (best[0], best[4])):
                    best = (count, label, program, field, reached[label])
        # The records' own routines, by type (not the boss's own).
        record_code = {}
        for kind in sorted(set(kinds[1:]) - {0, kinds[0]}):
            handler = by_type.get(f"${kind:X}")
            if handler in tree.blocks and tree.block(handler).file.path in files:
                program = _compile_lenient(tree, handler, files, symbols)
                if program is not None:
                    record_code[f"{kind:X}"] = program

        def frame(state: pose_code.Machine) -> pose_code.Machine:
            """`state` a frame on: the placing routine, then each record's."""
            state = pose_code.Machine(dict(state.memory), {"a5": SLOT})
            if best is not None:
                pose_code.run(best[2], state)
            for index in range(1, POOL_RECORDS):
                program = record_code.get(f"{state.read(SLOT + index * PART_SIZE, 2):X}")
                if program is not None:
                    state.a[5] = SLOT + index * PART_SIZE
                    pose_code.run(program, state)
            return state

        _preload(tree, symbols, [best[2] if best else [], *record_code.values()], machine)
        try:
            shown = frame(machine)
        except pose_code.PoseCodeError:
            continue
        field = best[3] if best is not None else max((0x10, 0x40), key=lambda item: placed(shown, item))
        if placed(shown, field) < 4:
            continue
        # The boss as it settles (Sunset Sting's ring opens over its first
        # frames), as far as its code runs.
        settled = machine
        for _ in range(SETTLE_FRAMES):
            try:
                settled = frame(settled)
            except pose_code.PoseCodeError:
                break
        try:
            shown = frame(settled)
        except pose_code.PoseCodeError:
            shown = settled
        return {
            "setup": setup,
            "machine": machine,
            "settled": settled,
            "shown": shown,
            "symbols": symbols,
            "places": best[1] if best is not None else None,
            "program": best[2] if best is not None else [],
            "record_code": record_code,
            "field": field,
        }
    return None


def _played(tree: SourceTree, files: list[str], sets: dict[str, Any], chain: dict[str, Any]) -> dict[str, Any] | None:
    """`chain` (see PLAYED) as its boss's routines leave it: the boss is the
    type an asset set of its files spawns, its setup the first frame, shown
    the frame every record drawn then lies on the path the boss has taken
    in its state, `chain` its own routine."""
    from retrolarium_kit.games.alien_soldier import boss_planes
    from retrolarium_kit.games.alien_soldier.levels import handlers

    by_type = handlers(tree)
    kind = next(
        (
            item["type"]
            for item in sets.values()
            if by_type.get(f"${item.get('type', 0):X}") in tree.blocks
            and tree.block(by_type[f"${item['type']:X}"]).file.path in files
        ),
        None,
    )
    if kind is None:
        return None
    machine = pose_code.Machine({}, {"a5": SLOT})
    machine.write(SLOT, 2, kind)
    machine.write(tree.equates["BossHealth"] & 0xFFFFFF, 2, 0x100)
    machine.write(tree.equates["MessageSequenceState"] & 0xFFFFFF, 2, 0x10)
    run = boss_planes.Run(tree, files, machine, SLOT, PART_SIZE)
    run.step(by_type)
    setup = pose_code.Machine(dict(machine.memory), {"a5": SLOT})

    def place(state: pose_code.Machine, index: int) -> tuple[int, int]:
        return state.read(SLOT + index * PART_SIZE + 0x10, 2), state.read(SLOT + index * PART_SIZE + 0x14, 2)

    drawn = [index for index in range(1, POOL_RECORDS) if setup.read(SLOT + index * PART_SIZE + 2, 1) & 0xC0 == 0xC0]
    settled, path, state = setup, set(), None
    while run.frame < boss_planes.FRAMES:
        settled = pose_code.Machine(dict(machine.memory), {"a5": SLOT})
        run.step(by_type)
        if machine.read(SLOT + 4, 2) != state:
            state, path = machine.read(SLOT + 4, 2), set()
        path.add(place(machine, 0))
        if all(place(machine, index) in path for index in drawn):
            break
    else:
        return None
    program = run.programs.get(kind)
    if not program:
        return None
    return {
        **chain,
        "machine": setup,
        "settled": settled,
        "shown": pose_code.Machine(dict(machine.memory), {"a5": SLOT}),
        "symbols": run.symbols,
        "places": by_type[f"${kind:X}"],
        "program": program,
        "record_code": {f"{other:X}": code for other, code in run.programs.items() if code and other != kind},
        "field": 0x10,
        "drawn_only": True,
    }


def _centre(machine: pose_code.Machine, parts: list[dict[str, Any]], field: int) -> list[int]:
    """The median position of the parts drawn, from the boss's (none when
    the boss draws itself)."""
    drawn = [index for index, part in enumerate(parts) if part["kind"] == "mapping"]
    if not drawn or drawn[0] == 0:
        return [0, 0]
    centre = []
    for offset in (0, 4):
        base = machine.read(SLOT + field + offset, 2)
        values = sorted(
            (machine.read(SLOT + index * PART_SIZE + field + offset, 2) - base + 0x8000) % 0x10000 - 0x8000
            for index in drawn
        )
        centre.append(values[len(values) // 2])
    return centre


def _record_parts(
    tree: SourceTree,
    machine: pose_code.Machine,
    shown: pose_code.Machine,
    symbols: pose_code.Symbols,
    drawn_only: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The records from SLOT as parts, up to the last one set up: each with
    the mapping `shown` gives it (a label `symbols` stands in for), or the
    sprite it holds itself, its attribute word as `machine` has it. With
    `drawn_only`, only the records
    the game draws then: bits 7 and 6 of byte 2 set (Destroyer-Mk2 hides its
    linked parts so; a chain's setup leaves parts it shows later hidden)."""
    stand_ins = {address: name for name, address in symbols.given.items()}
    parts, frames = [], {}
    last = max(index for index, kind in enumerate(_records(machine, 0, 2)) if kind or not index)
    for index in range(last + 1):
        record = SLOT + index * PART_SIZE
        part: dict[str, Any] = {
            "parent": None,
            "radius": 0,
            "depth": shown.read(record + 0x20, 1) >> 2,
            "kind": "none",
            "attribute": machine.read(record + 0xE, 2),
            "hand_built": True,
        }
        drawn = not drawn_only or shown.read(record + 2, 1) & 0xC0 == 0xC0
        # The 68000 reads 24 address bits (Love Penguin flags its pointers).
        name = stand_ins.get(shown.read(record + 8, 4) & 0xFFFFFF) if drawn else None
        try:
            pieces = _frame(tree, name) if name else None
        except (BossError, AslSourceError, mappings.MappingError, IndexError):
            pieces = None
        if pieces is not None:
            frames[name] = pieces
            part.update(kind="mapping", source=name)
        elif shown.read(record + 2, 1) & 0xC0 == 0x80 and part["attribute"] & 0x7FF:
            # Bit 7 without bit 6: the record is one sprite of its own, its
            # size at $8 and offsets at $A and $B (Victor's core and ring).
            part.update(
                kind="sprite",
                size=shown.read(record + 8, 1),
                x=_signed(shown.read(record + 0xA, 1), 8),
                y=_signed(shown.read(record + 0xB, 1), 8),
            )
        parts.append(part)
    return parts, frames


def _read_chains(
    tree: SourceTree,
    sets: dict[str, Any],
    recorded: dict[str, Any],
    game_names: Any,
    names: list[str],
    built: set[str],
) -> list[dict[str, Any]]:
    """The bosses built of records their own code places (see _chain), one
    per boss's files that no skeleton builder reads: their parts as the
    setup leaves them, drawing the mappings it gives, placed by that code
    (the studio runs it as a code-driven boss's pose code, on the setup's
    RAM). A boss whose stage streams a plane from the secondary camera once
    it spawns it has that as its body, where the records (which place
    themselves by that camera) stand on it."""
    from retrolarium_kit.games.alien_soldier import boss_planes, stages
    from retrolarium_kit.games.alien_soldier.levels import handlers

    found = []
    seen = set(built)
    by_type = handlers(tree)
    streams = {
        tree.block(by_type[f"${kind:X}"]).file.path: stream
        for kind, stream in stages.secondary_streams(tree).items()
        if by_type.get(f"${kind:X}") in tree.blocks
    }
    for kind, handler in by_type.items():
        if handler not in tree.blocks or not tree.block(handler).file.path.startswith("src/bosses/"):
            continue
        path = tree.block(handler).file.path
        prefix = PurePosixPath(path).stem.split("_")[0]
        if prefix in seen:
            continue
        seen.add(prefix)
        files = _boss_files(tree, path)
        try:
            chain = _chain(tree, files)
        except (BossError, AslSourceError, pose_code.PoseCodeError, KeyError, TypeError, IndexError):
            continue
        if chain is None or chain["setup"] not in CHECKED:
            # Records other code places too (plane bodies' Jampan, Bugmax)
            # wait for a reader of their own.
            continue
        if chain["setup"] in PLAYED:
            chain = _played(tree, files, sets, chain)
            if chain is None:
                continue
        machine, settled, shown, symbols = chain["machine"], chain["settled"], chain["shown"], chain["symbols"]
        # As the code that places them leaves them (Caterpillar's segments
        # pick their mappings).
        parts, frames = _record_parts(tree, machine, shown, symbols, chain.get("drawn_only", False))
        # Fewer than four parts drawn is not a boss this reader takes in
        # (Missiray's segments are its plane-B body's columns).
        if sum(1 for part in parts if part["kind"] != "none") < 4:
            continue
        kinds = [int(kind[1:], 16), machine.read(SLOT, 2)]
        asset_set, context = _draws_with(tree, sets, recorded, chain["setup"], path, kinds, frames, parts)
        program = chain["program"]
        tables = _tables(tree, symbols, [program, *chain["record_code"].values()])
        # The RAM the setup leaves (the records' fields, a history the code
        # reads), in runs.
        written = sorted(address for address in settled.memory if 0xFF0000 <= address)
        runs: list[list[int]] = []
        for address in written:
            if runs and address - runs[-1][-1] <= 16:
                runs[-1].append(address)
            else:
                runs.append([address])
        for run in runs:
            tables[f"{run[0]:06X}"] = bytes(settled.read(address, 1) for address in range(run[0], run[-1] + 1)).hex()
        for name in symbols.given:
            try:
                frames.setdefault(name, _frame(tree, name))
            except (BossError, AslSourceError, mappings.MappingError, IndexError):
                continue
        centre = _centre(shown, parts, chain["field"])
        body = None
        stream = next((streams[other] for other in files if other in streams), None)
        if stream is not None and chain["field"] == 0x10:
            camera = tree.equates["SecondaryCameraXPos"] & 0xFFFFFF
            body = boss_planes.streamed_body(
                tree,
                stream,
                shown.read(camera, 2),
                shown.read(SLOT + 0x10, 2) + centre[0],
                shown.read(SLOT + 0x14, 2) + centre[1],
            )
        found.append(
            {
                "id": chain["setup"],
                "parts": parts,
                "channels": 0,
                "channel_base": 0,
                "neutral_pose": [],
                "pose_code": program,
                "side_field": None,
                "title": next(
                    (game_names.name("bosses", kind) for kind in kinds if game_names.name("bosses", kind)), ""
                )
                or _title(PurePosixPath(path).stem, names),
                "asset_set": asset_set,
                "loads": context.get("loads", []),
                "palettes": context.get("palettes", []),
                "base_tile": 0,
                "frames": frames,
                "facing": {},
                "neutral": None,
                "anchor_code": None,
                "post_code": None,
                "traversed": [1, 0],
                "body": body,
                "joints": [None] * len(parts),
                "timing": "set",
                "poses": None,
                "sequences": [],
                "slot": SLOT,
                "checked": chain["setup"] in CHECKED,
                # The setup's field $54 (Jetsripper's chain length) stays.
                "faces": machine.read(SLOT + 0x54, 2),
                "chain": chain["places"],
                "position_field": chain["field"],
                # Positions are relative to the boss's, less this: nothing when
                # the boss draws, else the middle of the parts drawn (Sunset
                # Sting's ring, Caterpillar's segments far along the train).
                "centre": centre,
                "record_code": chain["record_code"],
                "tables": tables,
                "symbols": dict(symbols.given),
                "_sequences": [],
                "_table": b"",
            }
        )
    return found


def _palettes_loaded(tree: SourceTree, files: list[str]) -> list[str]:
    """The palette commands the files' code loads (`lea Command,a0` before
    a call of Gfx_LoadPaletteCommand), in order, once each."""
    found: list[str] = []
    for path in files:
        for label in tree.labels_in(path):
            source = None
            for line in tree.body(label):
                if match := PALETTE_SOURCE.match(line):
                    source = match["label"]
                elif PALETTE_LOAD.match(line) and source in tree.blocks and source not in found:
                    found.append(source)
    return found


def _read_planes(
    tree: SourceTree,
    sets: dict[str, Any],
    recorded: dict[str, Any],
    game_names: Any,
    names: list[str],
    taken: set[str],
) -> list[dict[str, Any]]:
    """The bosses whose body is a plane's tiles their code queues (see
    boss_planes.py), one per boss's files that queue tiles and that no
    other reader takes: the body as it settles, the records drawn then as
    parts where they stand (`position_field` $10/$14, no code to run)."""
    from retrolarium_kit.games.alien_soldier import boss_planes, stages
    from retrolarium_kit.games.alien_soldier.levels import handlers

    found = []
    by_type = handlers(tree)
    seen = set(taken)
    spawned = stages.spawning_scenes(tree)
    for kind, label in sorted(by_type.items(), key=lambda item: int(item[0][1:], 16)):
        if label not in tree.blocks or not tree.block(label).file.path.startswith("src/bosses/"):
            continue
        path = tree.block(label).file.path
        prefix = PurePosixPath(path).stem.split("_")[0]
        number = int(kind[1:], 16)
        if prefix in seen or label not in CHECKED:
            continue
        files = _boss_files(tree, path)
        if not boss_planes.queues_tiles(tree, files):
            continue
        seen.add(prefix)
        asset_set, context = _draws_with(tree, sets, recorded, label, path, [number], {}, [])
        # The RAM the stage that brings it has filled (Viblack's patterns
        # are its stage's), under the boss's own loads.
        memory = {}
        if number in spawned:
            ram = stages.scene_ram(tree, spawned[number]["scene"])
            memory = {0xFF0000 + offset: value for offset, value in enumerate(ram) if value}
        memory.update({0xFF0000 + offset: value for offset, value in _ram_loads(tree, asset_set, files).items()})
        machine = pose_code.Machine(memory, {"a5": SLOT})
        machine.write(SLOT, 2, number)
        # A boss with no health is beaten at once.
        machine.write(tree.equates["BossHealth"] & 0xFFFFFF, 2, 0x100)
        # Its message stays on screen: a boss shows as it waits through it
        # (Love Penguin bobs at the edge of the arena).
        machine.write(tree.equates["MessageSequenceState"] & 0xFFFFFF, 2, 0x10)
        run = boss_planes.Run(tree, files, machine, SLOT, PART_SIZE)
        # Where the stage holds its camera for the fight (Viblack heads for
        # a place in the stage, not on the screen).
        if spawned.get(number, {}).get("camera_x") is not None:
            machine.write(tree.equates["PrimaryCameraXPosition"] & 0xFFFFFF, 2, spawned[number]["camera_x"])
        body = run.play(by_type)
        if body is None:
            continue
        parts, frames = _record_parts(tree, machine, machine, run.symbols, drawn_only=True)
        asset_set, context = _draws_with(tree, sets, recorded, label, path, [number], frames, parts)
        if not context.get("loads") and number in spawned:
            # A body of its stage's art (Viblack's): the stage's loads and
            # colours, then the colours its own code loads.
            context = objects.scene_context(tree, spawned[number]["scene"])
            context["palettes"] += [[name, 0] for name in _palettes_loaded(tree, files)]
        records = bytes(machine.read(SLOT + offset, 1) for offset in range(len(parts) * PART_SIZE))
        found.append(
            {
                "id": label,
                "parts": parts,
                "channels": 0,
                "channel_base": 0,
                "neutral_pose": [],
                "pose_code": [],
                "side_field": None,
                "title": game_names.name("bosses", number) or _title(PurePosixPath(path).stem, names),
                "asset_set": asset_set,
                "loads": context.get("loads", []),
                "palettes": context.get("palettes", []),
                "base_tile": 0,
                "frames": frames,
                "facing": {},
                "neutral": None,
                "anchor_code": None,
                "post_code": None,
                "traversed": [1, 0],
                "body": body,
                "joints": [None] * len(parts),
                "timing": "set",
                "poses": None,
                "sequences": [],
                "slot": SLOT,
                "checked": True,
                "faces": machine.read(SLOT + 0x54, 2),
                "chain": None,
                "position_field": 0x10,
                "centre": [0, 0],
                "record_code": {},
                "tables": {f"{SLOT:06X}": records.hex()},
                "symbols": {name: address for name, address in run.symbols.given.items() if name in frames},
                "_sequences": [],
                "_table": b"",
            }
        )
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
        if boss["poses"] is None:
            boss.pop("_sequences")
            continue
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


def follow_source(document: dict[str, Any], exported: dict[str, Any]) -> dict[str, Any]:
    """`document` with the pose tables and animations of `exported` it lacks
    or holds in another shape (a newer kit reads more bosses, or other poses
    of one) as the source gives them; its own are kept otherwise. A table
    taken anew takes its animations with it: they name its poses by index."""
    poses = document.get("poses") if isinstance(document.get("poses"), dict) else {}
    taken = set()
    kept_poses = {}
    for label, rows in exported["poses"].items():
        mine = poses.get(label)
        if isinstance(mine, list) and [row.get("offset") if isinstance(row, dict) else None for row in mine] == [
            row["offset"] for row in rows
        ]:
            kept_poses[label] = mine
        else:
            kept_poses[label], taken = rows, taken | {label}
    renewed = {name for boss in exported["bosses"] if boss["poses"] in taken for name in boss["sequences"]}

    def shape(item: dict[str, Any]) -> tuple[Any, list[Any]]:
        commands = item.get("commands") if isinstance(item.get("commands"), list) else []
        return item.get("end"), [sorted(command) if isinstance(command, dict) else None for command in commands]

    mine = {item.get("label"): item for item in document.get("sequences") or [] if isinstance(item, dict)}
    sequences = [
        mine[item["label"]]
        if item["label"] in mine and item["label"] not in renewed and shape(mine[item["label"]]) == shape(item)
        else item
        for item in exported["sequences"]
    ]
    return {**document, "poses": kept_poses, "sequences": sequences}


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
