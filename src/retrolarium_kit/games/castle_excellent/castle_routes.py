"""Synthetic castle layouts with original room transitions and persistence.

The adapter supplies generated room data and its allocation tables to the
original code. It skips presentation, not key/door/state or transition rules.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from retrolarium_kit.platform.nes.sound_effects import Cpu6502

from .reachability import (
    DOOR_SLOTS,
    RETURN_HANDLER,
    Block,
    Key,
    Oracle,
    ReachabilityError,
    RoomCase,
    State,
    _Bus,
)

LOAD_ROOM, LOAD_OBJECTS, LOAD_STATE, RESET_RUNTIME = 0xA35C, 0xEBE9, 0xEC63, 0xA3AC
COUNTS, OBJECT_OFFSETS, DOOR_OFFSETS = 0xAB4A, 0xEF06, 0xABAE
PAUSE, FINISH_TRANSITION = 0xA8E3, 0x952D
PRESENTATION = {0xC43D, 0xC55D, 0x815E, 0x816A}
# Adapter-owned shim: JSR LoadRoomObjectsAndState; JSR ResetRoomRuntimeState; RTS.
LOAD_SHIM = bytes((0x20, LOAD_STATE & 255, LOAD_STATE >> 8, 0x20, RESET_RUNTIME & 255, RESET_RUNTIME >> 8, 0x60))


@dataclass(frozen=True)
class RoomLayout:
    number: int
    cells: tuple[tuple[int, ...], ...]
    blocks: tuple[Block, ...] = ()
    keys: tuple[Key, ...] = ()

    def room_case(self) -> RoomCase:
        return RoomCase(self.cells, (0, 0), (0, 0, 0, 0), self.blocks, self.keys, number=self.number)


@dataclass(frozen=True)
class CastleCase:
    rooms: tuple[RoomLayout, ...]
    start: tuple[int, int, int]  # Room number, X, Y.
    goal: tuple[int, int, int, int, int]  # Room number, rectangle.
    inventory: tuple[int, ...] = (0, 0, 0, 0, 0, 0)

    @classmethod
    def from_dict(cls, value: dict) -> CastleCase:
        try:
            return cls(
                tuple(
                    RoomLayout(
                        room["number"],
                        tuple(tuple(row) for row in room["cells"]),
                        tuple(Block(**b) for b in room.get("blocks", [])),
                        tuple(Key(**k) for k in room.get("keys", [])),
                    )
                    for room in value["rooms"]
                ),
                tuple(value["start"]),
                tuple(value["goal"]),
                tuple(value.get("inventory", (0,) * 6)),
            )
        except (KeyError, TypeError) as exc:
            raise ReachabilityError(f"invalid castle case: {exc}") from exc

    def validate(self) -> None:
        if not self.rooms or len(self.rooms) > 100:
            raise ReachabilityError("a castle case needs 1-100 rooms")
        numbers = [room.number for room in self.rooms]
        for room in self.rooms:
            room.room_case().validate()
        if len(numbers) != len(set(numbers)):
            raise ReachabilityError("duplicate room numbers")
        if len(self.start) != 3 or len(self.goal) != 5:
            raise ReachabilityError("castle start and goal must include room numbers")
        if any(type(v) is not int for v in (*self.start, *self.goal)):
            raise ReachabilityError("castle start and goal must contain integers")
        if self.start[0] not in numbers or self.goal[0] not in numbers:
            raise ReachabilityError("start and goal rooms must exist")
        RoomCase(self.rooms[0].cells, self.start[1:], self.goal[1:], inventory=self.inventory).validate()


class _CastleBus(_Bus):
    def __init__(self, prg: bytes, ram: bytes, overlay: dict[int, int]) -> None:
        super().__init__(prg, ram)
        self.overlay = overlay

    def read(self, address: int) -> int:
        if address in self.overlay:
            return self.overlay[address]
        return super().read(address)


class CastleOracle(Oracle):
    def __init__(self, image: bytes, case: CastleCase, *, instruction_limit: int = 50_000) -> None:
        super().__init__(image, instruction_limit=instruction_limit)
        case.validate()
        self.case = case
        self.layouts = {room.number: room.room_case() for room in case.rooms}
        self.overlay = {0x7000 + i: byte for i, byte in enumerate(LOAD_SHIM)}
        object_offsets, door_offsets = [0], [0]
        for number in range(1, 101):
            room = self.layouts.get(number)
            count = len(room.blocks) + len(room.keys) if room else 0
            anchors = sum(c in DOOR_SLOTS or c == 0x54 for row in room.cells for c in row) if room else 0
            self.overlay[COUNTS + number - 1] = count
            # The original object-state loader requires at least one byte per room.
            object_offsets.append(object_offsets[-1] + max(1, (count + 7) // 8))
            door_offsets.append(door_offsets[-1] + (anchors + 7) // 8)
        if object_offsets[-1] > 141 or door_offsets[-1] > 108:
            raise ReachabilityError("synthetic castle exceeds the original persistent-state arenas")
        self.overlay.update((OBJECT_OFFSETS + i, v) for i, v in enumerate(object_offsets))
        self.overlay.update((DOOR_OFFSETS + i, v) for i, v in enumerate(door_offsets))
        for room in self.layouts.values():
            self._validate_case(room)
            ram = bytearray(0x800)
            self._load_layout(ram, room)
            bus = self._bus(bytes(ram))
            self._spawn_markers(bus, len(room.blocks) + len(room.keys))
            self._check_spawn(State(bytes(bus.ram)), include_player=False)

    def _bus(self, ram: bytes) -> _CastleBus:
        return _CastleBus(self.prg, ram, self.overlay)

    def initial(self, case: CastleCase) -> State:
        if case != self.case:
            raise ReachabilityError("oracle belongs to a different castle case")
        ram = bytearray(0x800)
        number, x, y = case.start
        ram[5:11] = bytes(case.inventory)
        ram[0x37] = number
        ram[0x38:0x3B] = bytes((x & 255, x >> 8, y + 16))
        bus = self._bus(bytes(ram))
        event = self._execute(bus, (LOAD_ROOM,))
        if event:
            raise ReachabilityError(f"initial room loading failed: {event}")
        state = State(bytes(bus.ram))
        self._check_spawn(state)
        return state

    def _intercept(self, cpu: Cpu6502, bus: _Bus) -> str | None:
        # Transitions themselves run unchanged, including coordinate alignment,
        # automatic entrance-door opening and the original two-room state cache.
        if cpu.pc == LOAD_ROOM:
            number = bus.ram[0x37]
            if number not in self.layouts:
                return "missing-room"
            self._load_layout(bus.ram, self.layouts[number])
            bus.ram[0x69E + number - 1] = 255
            cpu.pc = 0x7000
        elif cpu.pc == LOAD_OBJECTS:
            # Layout is already loaded. The original state routine applies its
            # collected-object bits and open-door bits after this returns.
            cpu.pc = RETURN_HANDLER
        elif cpu.pc in PRESENTATION:
            cpu.pc = RETURN_HANDLER
        elif cpu.pc == FINISH_TRANSITION:
            try:
                x, y = State(bytes(bus.ram)).player
                if not (0 <= x <= 304 and 0 <= y <= 144):
                    return "invalid-entry"
                self._check_spawn(State(bytes(bus.ram)))
            except ReachabilityError:
                return "blocked-entry"
        return None

    def _routines(self, bus: _Bus) -> Iterator[int]:
        yield PAUSE
        if not bus.ram[0x7C4]:
            yield from super()._routines(bus)
