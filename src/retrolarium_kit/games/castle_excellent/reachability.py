"""Bounded room-route experiments using the original game's 6502 routines.

This is a mechanics oracle for a deliberately small subset, not a castle solver.
ROM bytes are supplied by the caller and are never written into a report.
One tick is a main-loop update (two NES video frames).
"""

from __future__ import annotations

import argparse
import hashlib
import heapq
import json
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, TypeVar

from retrolarium_kit.platform.nes.sound_effects import Cpu6502, SoundEffectError

if TYPE_CHECKING:
    from .castle_routes import CastleCase

REVISIONS = {
    "fd6214cdc441070b9edab74b9c31ad16007bd0873e32c0c883de4544ca69f261": "japan",
    "35815cd107289a8273fb46dd7c49247a8217ca4e2a7f235fef3aa54566d0ba8c": "usa",
}
# These addresses agree in both hash-pinned original revisions.
MOVEMENT, OBJECTS, CONTACT = 0x82CE, 0x9583, 0x9386
CELL_HANDLERS = (0x903F, 0x8F29, 0x9155, 0x926C)
CELL_FLAGS = (0xA95A, 0xAA22)
RETURN_HANDLER = 0x926B
TRANSITIONS = {0x867A: "up", 0x86A0: "down", 0x86D6: "left", 0x8721: "right"}
DEATH = 0x8DB0
EMPTY = 0x53
BUTTONS = {"wait": 0, "left": 0x40, "right": 0x80, "jump": 1, "jump-left": 0x41, "jump-right": 0x81}
# Inventory RAM order: red, dark blue, yellow, green, pink, blue.
KEY_SLOTS = {0x90: 1, 0x91: 0, 0x92: 4, 0x93: 3, 0x94: 5, 0x95: 2}
DOOR_SLOTS = {0x19: 4, 0x1C: 3, 0x1F: 5, 0x22: 2, 0x25: 0, 0x29: 1}
CELL_OBJECTS = {0x49: 0xA4, 0x4A: 0xA5, 0x4B: 0xA1, 0x4D: 0xA2, 0x51: 0xA3}
SPAWN_MARKERS = 0xED08
OBJECT_WIDTHS, OBJECT_HEIGHTS = 0xAAEA, 0xAB1A


class ReachabilityError(ValueError):
    """Invalid input, unsupported mechanics, or an oracle execution failure."""


@dataclass(frozen=True)
class Block:
    """Runtime coordinates: X in pixels, Y relative to the room's top edge."""

    x: int
    y: int
    kind: int = 0x8C


@dataclass(frozen=True)
class Key:
    """An eight-pixel-wide key at its actual runtime pixel coordinates."""

    x: int
    y: int
    kind: int = 0x91


@dataclass(frozen=True)
class RoomCase:
    cells: tuple[tuple[int, ...], ...]
    start: tuple[int, int]
    goal: tuple[int, int, int, int]
    blocks: tuple[Block, ...] = ()
    keys: tuple[Key, ...] = ()
    inventory: tuple[int, ...] = (0, 0, 0, 0, 0, 0)
    number: int = 12

    @classmethod
    def from_dict(cls, value: dict) -> RoomCase:
        try:
            return cls(
                tuple(tuple(row) for row in value["cells"]),
                tuple(value["start"]),
                tuple(value["goal"]),
                tuple(Block(**block) for block in value.get("blocks", [])),
                tuple(Key(**key) for key in value.get("keys", [])),
                tuple(value.get("inventory", (0,) * 6)),
                value.get("number", 12),
            )
        except (KeyError, TypeError) as exc:
            raise ReachabilityError(f"invalid room case: {exc}") from exc

    def validate(self) -> None:
        if len(self.cells) != 20 or any(len(row) != 20 for row in self.cells):
            raise ReachabilityError("a room must contain 20 by 20 cells")
        if any(type(cell) is not int or not 0 <= cell < 0x64 for row in self.cells for cell in row):
            raise ReachabilityError("room cell codes must be integers in $00-$63")
        if len(self.start) != 2 or any(type(v) is not int for v in self.start):
            raise ReachabilityError("start must contain two integer pixel coordinates")
        if not (0 <= self.start[0] <= 304 and 0 <= self.start[1] <= 144):
            raise ReachabilityError("start must fit a 16 by 16 player inside the room")
        if len(self.goal) != 4 or any(type(v) is not int for v in self.goal):
            raise ReachabilityError("goal must be an integer rectangle [left, top, right, bottom]")
        left, top, right, bottom = self.goal
        if not (0 <= left <= right <= 304 and 0 <= top <= bottom <= 144):
            raise ReachabilityError("goal rectangle must lie inside the room")
        if type(self.number) is not int or not 1 <= self.number <= 100:
            raise ReachabilityError("room number must be an integer in 1-100")
        if len(self.inventory) != 6 or any(type(n) is not int or not 0 <= n <= 255 for n in self.inventory):
            raise ReachabilityError("inventory must contain six byte-sized key counts")
        markers = sum(c in CELL_OBJECTS for row in self.cells for c in row)
        if len(self.blocks) + len(self.keys) + markers > 24:
            raise ReachabilityError("the runtime pool has only 24 slots")
        for block in self.blocks:
            if block.kind not in (0x8A, 0x8B, 0x8C, 0x8D, 0x8E):
                raise ReachabilityError("only jars, bricks, blocks, barrels and pulley controls are supported")
            if any(type(v) is not int for v in (block.x, block.y, block.kind)):
                raise ReachabilityError("block coordinates and type must be integers")
            if not (0 <= block.x <= 304 and 0 <= block.y <= 144):
                raise ReachabilityError("a block must fit inside the room")
        for key in self.keys:
            if (
                any(type(v) is not int for v in (key.x, key.y, key.kind))
                or key.kind not in KEY_SLOTS
                or not 0 <= key.x <= 312
                or not 0 <= key.y <= 144
            ):
                raise ReachabilityError("key must be type $90-$95 and fit inside the room")
        anchors = 0
        for y, row in enumerate(self.cells):
            for x, cell in enumerate(row):
                if cell in DOOR_SLOTS or cell == 0x54:
                    anchors += 1
                    if y == 19 or self.cells[y + 1][x] != cell + 1:
                        raise ReachabilityError("a door must have matching top and bottom cells")
                elif cell - 1 in DOOR_SLOTS or cell == 0x55:
                    if y == 0 or self.cells[y - 1][x] != cell - 1:
                        raise ReachabilityError("orphaned bottom door cell")
        if anchors > 39:
            raise ReachabilityError("the room state supports at most 39 door anchors")
        for row in self.cells:
            for x, cell in enumerate(row):
                if cell in (0x4D, 0x4F) and (x == 19 or row[x + 1] != cell + 1):
                    raise ReachabilityError("wide lift and shaft cells need a matching right half")
                if cell in (0x4E, 0x50) and (x == 0 or row[x - 1] != cell - 1):
                    raise ReachabilityError("orphaned right half of a wide lift or shaft")
        tops = sum(c == 0x49 for row in self.cells for c in row)
        bottoms = sum(c == 0x4A for row in self.cells for c in row)
        if tops != bottoms or tops > 1:
            raise ReachabilityError("a room supports one complete pulley-operated lift pair")
        if any(b.kind == 0x8E for b in self.blocks) and tops != 1:
            raise ReachabilityError("a pulley control requires its lift pair")


@dataclass(frozen=True)
class State:
    ram: bytes

    @property
    def player(self) -> tuple[int, int]:
        # The game stores player Y in screen coordinates, including a 16px HUD.
        return self.ram[0x38] | self.ram[0x39] << 8, self.ram[0x3A] - 16

    @property
    def blocks(self) -> tuple[tuple[int, int, int], ...]:
        return tuple(obj for obj in self.objects if 0x8A <= obj[0] <= 0x8E)

    @property
    def objects(self) -> tuple[tuple[int, int, int], ...]:
        return tuple(
            (self.ram[i], self.ram[i + 1] | self.ram[i + 2] << 8, self.ram[i + 3])
            for i in range(0x4A, 0xDA, 6)
            if self.ram[i]
        )

    @property
    def inventory(self) -> tuple[int, ...]:
        return tuple(self.ram[5:11])

    @property
    def room(self) -> int:
        return self.ram[0x37]

    @property
    def object_records(self) -> tuple[tuple[int, ...], ...]:
        """Active records: slot, type, X, Y, state, auxiliary byte."""
        return tuple(
            (i, self.ram[o], self.ram[o + 1] | self.ram[o + 2] << 8, self.ram[o + 3], self.ram[o + 4], self.ram[o + 5])
            for i in range(24)
            if self.ram[o := 0x4A + 6 * i]
        )


@dataclass(frozen=True)
class Step:
    state: State
    event: str | None = None


CaseT_contra = TypeVar("CaseT_contra", RoomCase, "CastleCase", contravariant=True)


class RouteOracle(Protocol[CaseT_contra]):
    """The search/replay interface, also usable by synthetic test oracles."""

    def initial(self, case: CaseT_contra) -> State: ...

    def tick(self, state: State, buttons: int) -> Step: ...


class _Bus:
    def __init__(self, prg: bytes, ram: bytes) -> None:
        self.prg = prg
        self.ram = bytearray(ram)
        self.gameplay_update = False

    def read(self, address: int) -> int:
        if address < 0x2000:
            return self.ram[address & 0x7FF]
        if address >= 0x8000:
            return self.prg[address - 0x8000]
        raise ReachabilityError(f"unsupported hardware read at ${address:04X}")

    def write(self, address: int, value: int) -> None:
        if address < 0x2000:
            self.ram[address & 0x7FF] = value
        elif 0x4000 <= address <= 0x4015:
            pass  # Audio register writes do not affect this mechanics subset.
        else:
            raise ReachabilityError(f"unsupported hardware write at ${address:04X}")


class Oracle:
    """Execute original movement, pushable physics and contact procedures.

    Rendering/audio scheduling and animated cells are outside this subset.
    Room transitions terminate a trace; they are not treated as a solved goal.
    """

    def __init__(self, image: bytes, *, instruction_limit: int = 20_000) -> None:
        digest = hashlib.sha256(image).hexdigest()
        if digest not in REVISIONS:
            raise ReachabilityError("expected an unmodified supported Japan or USA ROM")
        if type(instruction_limit) is not int or instruction_limit < 1:
            raise ReachabilityError("instruction_limit must be a positive integer")
        self.revision = REVISIONS[digest]
        self.rom_sha256 = digest
        self.prg = image[16 : 16 + 0x8000]
        self.instruction_limit = instruction_limit

    def _word(self, address: int) -> int:
        index = address - 0x8000
        return int.from_bytes(self.prg[index : index + 2], "little")

    def supported_cell(self, cell: int) -> bool:
        # Door handlers, consumption and opening run in the original ROM code.
        if cell in (EMPTY, 0x54, 0x55) or cell in DOOR_SLOTS or cell - 1 in DOOR_SLOTS:
            return True
        if not (0 <= cell < 0x35 or 0x41 <= cell <= 0x44 or 0x49 <= cell <= 0x52) or cell == 0x16:
            return False
        if any(self._word(table + 2 * cell) != RETURN_HANDLER for table in CELL_HANDLERS):
            return False
        return all(self.prg[table - 0x8000 + 2 * cell + half] in (0, 1) for table in CELL_FLAGS for half in (0, 1))

    def _validate_case(self, case: RoomCase) -> None:
        case.validate()
        unsupported = sorted({cell for row in case.cells for cell in row if not self.supported_cell(cell)})
        if unsupported:
            raise ReachabilityError("unsupported cells: " + ", ".join(f"${cell:02X}" for cell in unsupported))

    @staticmethod
    def _load_layout(ram: bytearray, case: RoomCase) -> None:
        ram[0x400:0x590] = bytes(cell for row in case.cells for cell in row)
        ram[0x4A:0xDA] = bytes(144)
        for index, obj in enumerate((*case.blocks, *case.keys)):
            offset = 0x4A + 6 * index
            ram[offset : offset + 6] = bytes((obj.kind, obj.x & 255, obj.x >> 8, obj.y, 0, 0))

    def initial(self, case: RoomCase) -> State:
        self._validate_case(case)
        ram = bytearray(0x800)
        ram[5:11] = bytes(case.inventory)
        ram[0x37] = case.number
        ram[0x712] = case.number
        self._load_layout(ram, case)
        ram[0x38:0x3B] = bytes((case.start[0] & 255, case.start[0] >> 8, case.start[1] + 16))
        bus = self._bus(bytes(ram))
        self._spawn_markers(bus, len(case.blocks) + len(case.keys))
        state = State(bytes(bus.ram))
        self._check_spawn(state)
        return state

    def _spawn_markers(self, bus: _Bus, count: int) -> None:
        if not any(c in CELL_OBJECTS for c in bus.ram[0x400:0x590]):
            return
        # Same setup as ApplyRoomPersistentState before ScanRoomCellObjectMarkers.
        bus.ram[0x16] = 6 * count
        bus.ram[0x21], bus.ram[0x22] = 0, 4
        event = self._execute(bus, (SPAWN_MARKERS,))
        if event:
            raise ReachabilityError(f"mechanism loading failed: {event}")

    def _solid(self, state: State, x: int, y: int, *, objects: bool = False) -> bool:
        cell = state.ram[0x400 + y // 8 * 20 + x // 16]
        table = CELL_FLAGS[int(objects)]
        return bool(self.prg[table - 0x8000 + 2 * cell + (x % 16 >= 8)] & 1)

    def _check_spawn(self, state: State, *, include_player: bool = True) -> None:
        bodies = [(*state.player, 16, 16, "player")] if include_player else []
        for kind, x, y in state.objects:
            if 0x8A <= kind <= 0x8E or 0xA1 <= kind <= 0xA5:
                width = self.prg[OBJECT_WIDTHS - 0x8000 + kind - 0x80]
                height = self.prg[OBJECT_HEIGHTS - 0x8000 + kind - 0x80]
                bodies.append((x, y, width, height, "mechanism" if kind >= 0xA1 else "block"))
        for x, y, width, height, name in bodies:
            if not (0 <= x and x + width <= 320 and 0 <= y and y + height <= 160):
                raise ReachabilityError(f"{name} extends outside the room")
            # Lift bodies deliberately occupy their own shaft/surface cells.
            if name == "mechanism":
                continue
            if any(
                self._solid(state, px, py, objects=name == "block")
                for px in range(x, x + width)
                for py in range(y, y + height)
            ):
                raise ReachabilityError(f"{name} starts inside solid geometry")
        for index, (x, y, width, height, _) in enumerate(bodies):
            if any(
                x < ox + ow and ox < x + width and y < oy + oh and oy < y + height
                for ox, oy, ow, oh, _ in bodies[:index]
            ):
                raise ReachabilityError("initial bodies overlap")
        for kind, x, y in state.objects:
            if kind in KEY_SLOTS and any(
                self._solid(state, px, py) for px in range(x, x + 8) for py in range(y, y + 16)
            ):
                raise ReachabilityError("key starts inside solid geometry")

    def _bus(self, ram: bytes) -> _Bus:
        return _Bus(self.prg, ram)

    def _intercept(self, cpu: Cpu6502, bus: _Bus) -> str | None:
        if cpu.pc in TRANSITIONS:
            return "exit-" + TRANSITIONS[cpu.pc]
        return None

    def _routines(self, bus: _Bus) -> Iterator[int]:
        bus.gameplay_update = True
        yield from (MOVEMENT, OBJECTS, CONTACT)

    def tick(self, state: State, buttons: int) -> Step:
        if type(buttons) is not int or buttons not in BUTTONS.values():
            raise ReachabilityError("only wait, left, right and A+jump combinations are supported")
        if len(state.ram) != 0x800:
            raise ReachabilityError("invalid RAM snapshot size")
        bus = self._bus(state.ram)
        bus.ram[0x775] = buttons
        event = self._execute(bus, self._routines(bus))
        # FinishPlayerSprite clears this latch after each main-loop update.
        bus.ram[0x3E] = 0
        if not event and bus.gameplay_update:  # INC RoomFrameCounter in MainFrameLoop.
            bus.ram[0x76B] = (bus.ram[0x76B] + 1) & 255
        return Step(State(bytes(bus.ram)), event)

    def _execute(self, bus: _Bus, routines: Iterable[int]) -> str | None:
        cpu = Cpu6502(bus.read, bus.write)
        remaining = self.instruction_limit
        event = None
        try:
            for address in routines:
                # A sentinel on the emulated stack gives a bounded subroutine call.
                cpu.a = cpu.x = cpu.y = 0
                cpu.s = 0xFD
                bus.ram[0x1FD], bus.ram[0x1FC] = 0x5F, 0xFF
                cpu.s = 0xFB
                cpu.pc = address
                while cpu.pc != 0x6000:
                    event = self._intercept(cpu, bus)
                    if event:
                        break
                    if cpu.pc == DEATH:
                        event = "death"
                        break
                    if remaining == 0:
                        raise ReachabilityError("instruction budget exhausted in a mechanics tick")
                    cpu.step()
                    remaining -= 1
                if event:
                    break
        except SoundEffectError as exc:
            raise ReachabilityError(str(exc)) from exc
        return event


def at_goal(state: State, goal: tuple[int, ...]) -> bool:
    if len(goal) == 5 and state.room != goal[0]:
        return False
    x, y = state.player
    left, top, right, bottom = goal[-4:]
    return left <= x <= right and top <= y <= bottom


@dataclass(frozen=True)
class SearchResult:
    status: str
    expanded: int
    buttons: tuple[int, ...] = ()
    positions: tuple[tuple[int, int], ...] = ()
    reason: str = ""
    rooms: tuple[int, ...] = ()
    inventories: tuple[tuple[int, ...], ...] = ()
    final_state_sha256: str = ""
    objects: tuple[tuple[tuple[int, ...], ...], ...] = ()


def find_route(
    oracle: RouteOracle[CaseT_contra],
    case: CaseT_contra,
    *,
    max_nodes: int = 300,
    max_ticks: int = 180,
    action_ticks: int = 4,
) -> SearchResult:
    """Best-first bounded search. A negative result is never an impossibility proof.

    Actions hold a button for action_ticks updates, so even an exhausted frontier
    does not cover every possible frame-by-frame controller sequence.
    """
    if any(type(v) is not int or v < 1 for v in (max_nodes, max_ticks, action_ticks)):
        raise ReachabilityError("search limits must be positive integers")
    initial = oracle.initial(case)
    if at_goal(initial, case.goal):
        return verified_route(oracle, case, (), 0)
    left, top, right, bottom = case.goal[-4:]

    def priority(state: State, ticks: int) -> float:
        x, y = state.player
        if len(case.goal) == 5 and state.room != case.goal[0]:
            sy, sx = divmod(state.room - 1, 10)
            gy, gx = divmod(case.goal[0] - 1, 10)
            edges = []
            if gx != sx:
                edges.append(304 - x if gx > sx else x)
            if gy != sy:
                edges.append(144 - y if gy > sy else y)
            return (abs(gx - sx) + abs(gy - sy)) * 320 + min(edges) + ticks * 0.1
        return max(left - x, 0, x - right) + max(top - y, 0, y - bottom) + ticks * 0.1

    # Node records share parent chains instead of copying traces into every node.
    nodes: list[tuple[State, int, int, tuple[int, ...]]] = [(initial, 0, -1, ())]
    frontier = [(priority(initial, 0), 0)]
    seen = {initial.ram: 0}
    expanded = 0
    while frontier and expanded < max_nodes:
        _, parent = heapq.heappop(frontier)
        state, depth, _, _ = nodes[parent]
        if seen[state.ram] < depth or depth >= max_ticks:
            continue
        expanded += 1
        for buttons in BUTTONS.values():
            current = state
            segment = []
            for _ in range(min(action_ticks, max_ticks - depth)):
                step = oracle.tick(current, buttons)
                if step.event:
                    break
                current = step.state
                segment.append(buttons)
                if at_goal(current, case.goal):
                    pieces = [tuple(segment)]
                    index = parent
                    while index >= 0:
                        _, _, previous, inputs = nodes[index]
                        pieces.append(inputs)
                        index = previous
                    trace = tuple(b for piece in reversed(pieces) for b in piece)
                    return verified_route(oracle, case, trace, expanded)
            else:
                ticks = depth + len(segment)
                if seen.get(current.ram, max_ticks + 1) <= ticks:
                    continue
                seen[current.ram] = ticks
                index = len(nodes)
                nodes.append((current, ticks, parent, tuple(segment)))
                heapq.heappush(frontier, (priority(current, ticks), index))
    return SearchResult(
        "unknown", expanded, reason="node budget reached" if frontier else "bounded action search exhausted"
    )


def replay(
    oracle: RouteOracle[CaseT_contra], case: CaseT_contra, buttons: tuple[int, ...]
) -> tuple[tuple[int, int], ...]:
    return tuple(state.player for state in replay_states(oracle, case, buttons))


def replay_states(oracle: RouteOracle[CaseT_contra], case: CaseT_contra, buttons: tuple[int, ...]) -> tuple[State, ...]:
    state = oracle.initial(case)
    states = [state]
    for value in buttons:
        step = oracle.tick(state, value)
        if step.event:
            raise ReachabilityError(f"replay terminated: {step.event}")
        state = step.state
        states.append(state)
    if not at_goal(state, case.goal):
        raise ReachabilityError("replay did not reach the goal")
    return tuple(states)


def verified_route(
    oracle: RouteOracle[CaseT_contra], case: CaseT_contra, buttons: tuple[int, ...], expanded: int, reason: str = ""
) -> SearchResult:
    states = replay_states(oracle, case, buttons)
    return SearchResult(
        "reachable",
        expanded,
        buttons,
        tuple(state.player for state in states),
        reason,
        tuple(state.room for state in states),
        tuple(state.inventory for state in states),
        hashlib.sha256(states[-1].ram).hexdigest(),
        tuple(state.object_records for state in states),
    )


def parse_case(value: object) -> RoomCase | CastleCase:
    if not isinstance(value, dict):
        raise ReachabilityError("case must be a JSON object")
    if "rooms" in value:
        from .castle_routes import CastleCase

        return CastleCase.from_dict(value)
    return RoomCase.from_dict(value)


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rom", required=True, type=Path)
    parser.add_argument("--case", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--max-nodes", type=_positive_int, default=300)
    parser.add_argument("--max-ticks", type=_positive_int, default=180)
    parser.add_argument("--action-ticks", type=_positive_int, default=4)
    parser.add_argument("--replay", type=Path, help="verify a saved report instead of searching")
    args = parser.parse_args(argv)
    try:
        inputs = (args.rom, args.case) + ((args.replay,) if args.replay else ())
        if any(args.output.resolve() == path.resolve() for path in inputs):
            raise ReachabilityError("output must not overwrite an input file")
        case = parse_case(json.loads(args.case.read_text(encoding="utf-8")))
        if isinstance(case, RoomCase):
            oracle = Oracle(args.rom.read_bytes())
        else:
            from .castle_routes import CastleOracle

            oracle = CastleOracle(args.rom.read_bytes(), case)
        if args.replay:
            previous = json.loads(args.replay.read_text(encoding="utf-8"))
            if (
                not isinstance(previous, dict)
                or previous.get("schema") != "retrolarium.castle-excellent-route.v3"
                or previous.get("rom_sha256") != oracle.rom_sha256
                or previous.get("case") != json.loads(json.dumps(asdict(case)))
            ):
                raise ReachabilityError("replay report does not match this ROM and room case")
            buttons = previous.get("buttons")
            if (
                not isinstance(buttons, list)
                or len(buttons) > args.max_ticks
                or any(type(b) is not int or b not in BUTTONS.values() for b in buttons)
            ):
                raise ReachabilityError("invalid or over-budget replay inputs")
            result = verified_route(oracle, case, tuple(buttons), 0, "saved trace verified")
            current = json.loads(json.dumps(asdict(result)))
            for name in ("positions", "rooms", "inventories", "objects", "final_state_sha256"):
                if current[name] != previous.get(name):
                    raise ReachabilityError(f"replay {name} differ from the saved report")
        else:
            result = find_route(
                oracle, case, max_nodes=args.max_nodes, max_ticks=args.max_ticks, action_ticks=args.action_ticks
            )
        report = {
            "schema": "retrolarium.castle-excellent-route.v3",
            "revision": oracle.revision,
            "rom_sha256": oracle.rom_sha256,
            "tick_video_frames": 2,
            "case": asdict(case),
            "limits": {"max_nodes": args.max_nodes, "max_ticks": args.max_ticks, "action_ticks": args.action_ticks},
            **asdict(result),
        }
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")
    except (OSError, ValueError) as exc:
        parser.exit(2, f"{exc}\n")
    return 0 if result.status == "reachable" else 1


if __name__ == "__main__":
    raise SystemExit(main())
