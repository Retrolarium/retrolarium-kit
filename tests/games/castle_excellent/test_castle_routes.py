from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from dataclasses import asdict, replace
from pathlib import Path

from retrolarium_kit.games.castle_excellent.castle_routes import (
    CastleCase,
    CastleOracle,
    RoomLayout,
)
from retrolarium_kit.games.castle_excellent.reachability import (
    DOOR_SLOTS,
    EMPTY,
    KEY_SLOTS,
    Block,
    Key,
    Oracle,
    ReachabilityError,
    RoomCase,
    State,
    find_route,
    main,
    parse_case,
    replay_states,
)

from .route_fixtures import drive_to


def corridor() -> list[list[int]]:
    cells = [[EMPTY] * 20 for _ in range(20)]
    cells[19] = [0x18] * 20
    return cells


def door_case(door: int = 0x25, key: int | None = 0x91, *, aligned: bool = True) -> RoomCase:
    cells = corridor()
    top = 17 if aligned else 16
    if not aligned:
        cells[18] = [0x18] * 20
    for row in cells:
        row[0] = row[-1] = 0x18
    for y in range(top):
        cells[y][5] = 0x18
    cells[top][5], cells[top + 1][5] = door, door + 1
    y = 136 if aligned else 128
    return RoomCase(tuple(map(tuple, cells)), (32, y), (96, y, 104, y), keys=(Key(56, y, key),) if key else ())


def castle_case() -> CastleCase:
    left, right = corridor(), corridor()
    for row in left:
        row[0] = 0x18
    for row in right:
        row[-1] = 0x18
    for y in range(17):
        right[y][2] = 0x18
    right[17][2], right[18][2] = 0x25, 0x26
    return CastleCase(
        (RoomLayout(12, tuple(map(tuple, left)), keys=(Key(288, 136),)), RoomLayout(13, tuple(map(tuple, right)))),
        (12, 272, 136),
        (13, 64, 136, 72, 136),
    )


class CastleInputTests(unittest.TestCase):
    def test_case_round_trip(self):
        case = castle_case()
        case.validate()
        self.assertEqual(parse_case(json.loads(json.dumps(asdict(case)))), case)
        with self.assertRaises(ReachabilityError):
            parse_case([])

    def test_invalid_inventory_room_and_door_pairs(self):
        room = door_case()
        cells = [list(row) for row in room.cells]
        cells[18][5] = EMPTY
        invalids = (
            replace(room, inventory=(0,)),
            replace(room, inventory=(256,) * 6),
            replace(room, number=101),
            replace(room, cells=tuple(map(tuple, cells))),
            replace(room, keys=(Key(319, 0),)),
            replace(room, blocks=(Block(0, 0),) * 24, keys=(Key(56, 136),)),
        )
        for invalid in invalids:
            with self.assertRaises(ReachabilityError):
                invalid.validate()

    def test_invalid_castle_ids_and_coordinates(self):
        case = castle_case()
        for invalid in (
            replace(case, rooms=()),
            replace(case, rooms=(case.rooms[0],) * 2),
            replace(case, start=(11, 0, 0)),
            replace(case, goal=(99, 0, 0, 0, 0)),
            replace(case, start=(12, 305, 0)),
        ):
            with self.assertRaises(ReachabilityError):
                invalid.validate()
        with self.assertRaises(ReachabilityError):
            CastleCase.from_dict({"rooms": []})


@unittest.skipUnless(os.environ.get("RETROLARIUM_CASTLE_PHYSICS_ROMS"), "optional original-ROM integration tests")
class OriginalDoorAndCastleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.images = [
            Path(path).read_bytes() for path in os.environ["RETROLARIUM_CASTLE_PHYSICS_ROMS"].split(os.pathsep)
        ]

    def test_six_key_colors_open_only_the_matching_door(self):
        for image in self.images:
            oracle = Oracle(image)
            for door, slot in DOOR_SLOTS.items():
                key = next(kind for kind, key_slot in KEY_SLOTS.items() if key_slot == slot)
                with self.subTest(revision=oracle.revision, door=door, key=key):
                    case = door_case(door, key)
                    state = oracle.initial(case)
                    picked_up = False
                    for _ in range(40):
                        state = oracle.tick(state, 0x80).state
                        picked_up |= state.inventory[slot] == 1
                    self.assertTrue(picked_up)
                    self.assertGreaterEqual(state.player[0], 96)
                    self.assertEqual(state.inventory, (0,) * 6)
                    self.assertEqual(state.ram[0x400 + 17 * 20 + 5], 0x54)
                    self.assertEqual(state.ram[0x716] & 1, 1)

    def test_missing_or_wrong_key_and_bad_alignment_do_not_open_door(self):
        for image in self.images:
            oracle = Oracle(image)
            for case in (door_case(key=None), door_case(0x19, 0x91), door_case(aligned=False)):
                state = oracle.initial(case)
                for _ in range(45):
                    state = oracle.tick(state, 0x80).state
                self.assertEqual(state.player[0], 64)
                self.assertEqual(state.ram[0x716], 0)
            self.assertEqual(state.inventory[0], 1)

    def test_airborne_contact_does_not_spend_key(self):
        for image in self.images:
            oracle = Oracle(image)
            case = replace(door_case(key=None), start=(64, 136), inventory=(1, 0, 0, 0, 0, 0))
            ram = bytearray(oracle.initial(case).ram)
            ram[0x3C] = 0x20
            state = oracle.tick(State(bytes(ram)), 0x80).state
            self.assertEqual(state.inventory[0], 1)
            self.assertEqual(state.ram[0x716], 0)

    def test_transition_route_and_pause_are_replayable_in_both_revisions(self):
        case = castle_case()
        traces = []
        for image in self.images:
            oracle = CastleOracle(image, case)
            result = find_route(oracle, case, max_nodes=35, max_ticks=90)
            self.assertEqual(result.status, "reachable")
            states = replay_states(oracle, case, result.buttons)
            self.assertEqual(states[-1].room, 13)
            self.assertEqual(states[-1].inventory, (0,) * 6)
            arrival = next(s for s in states if s.room == 13)
            self.assertEqual(arrival.player, (0, 136))
            self.assertEqual(arrival.ram[0x7C4], 255)
            self.assertEqual(arrival.ram[0x76B], 1)
            paused = oracle.tick(arrival, 0x80).state
            self.assertEqual(paused.player, arrival.player)
            self.assertEqual(paused.ram[0x7C4], 255)
            self.assertEqual(paused.ram[0x76B], arrival.ram[0x76B])
            traces.append((result.buttons, result.positions, result.rooms, result.inventories))
        self.assertTrue(all(trace == traces[0] for trace in traces))

    def test_key_and_open_door_survive_cache_eviction_and_return(self):
        case = castle_case()
        middle = [list(row) for row in case.rooms[1].cells]
        for row in middle:
            row[-1] = EMPTY
        middle[-1][-1] = 0x18
        case = replace(
            case,
            rooms=(
                case.rooms[0],
                replace(case.rooms[1], cells=tuple(map(tuple, middle))),
                RoomLayout(14, tuple(map(tuple, corridor()))),
                RoomLayout(15, tuple(map(tuple, corridor()))),
            ),
        )
        for image in self.images:
            oracle = CastleOracle(image, case)
            state = drive_to(oracle, oracle.initial(case), 0x80, 15)
            state = drive_to(oracle, state, 0x40, 13)
            self.assertEqual(state.ram[0x400 + 17 * 20 + 2], 0x54)
            self.assertEqual(state.inventory, (0,) * 6)
            state = drive_to(oracle, state, 0x40, 12)
            self.assertFalse(any(kind in KEY_SLOTS for kind, _, _ in state.objects))
            self.assertEqual(state.inventory, (0,) * 6)

    def test_native_entrance_door_opens_without_spending_key(self):
        case = castle_case()
        cells = corridor()
        cells[17][0], cells[18][0] = 0x25, 0x26
        case = replace(case, rooms=(replace(case.rooms[0], keys=()), RoomLayout(13, tuple(map(tuple, cells)))))
        for image in self.images:
            oracle = CastleOracle(image, case)
            state = drive_to(oracle, oracle.initial(case), 0x80, 13)
            self.assertEqual(state.ram[0x400 + 17 * 20], 0x54)
            self.assertEqual(state.inventory, (0,) * 6)

    def test_vertical_entries_align_x_and_outer_edge_does_not_wrap(self):
        upper = corridor()
        upper[19][4] = upper[19][5] = EMPTY
        case = CastleCase(
            (RoomLayout(12, tuple(map(tuple, upper))), RoomLayout(22, tuple(map(tuple, corridor())))),
            (12, 70, 128),
            (22, 64, 0, 64, 0),
        )
        for image in self.images:
            oracle = CastleOracle(image, case)
            state = drive_to(oracle, oracle.initial(case), 0, 22, 20)
            self.assertEqual(state.player, (64, 0))
            # Exercise the native upward boundary, which later lifts can reach.
            # This is a controller-state fixture, not a claimed jump route.
            ram = bytearray(state.ram)
            ram[0x38], ram[0x3A], ram[0x7C4] = 70, 0, 0
            state = oracle.tick(State(bytes(ram)), 0).state
            self.assertEqual((state.room, state.player), (12, (64, 136)))
            edge = CastleCase(
                (RoomLayout(10, tuple(map(tuple, corridor()))),), (10, 304, 136), (10, 304, 136, 304, 136)
            )
            oracle = CastleOracle(image, edge)
            state = oracle.initial(edge)
            for _ in range(6):
                step = oracle.tick(state, 0x80)
                self.assertIsNone(step.event)
                state = step.state
            self.assertEqual((state.room, state.player), (10, (304, 136)))

    def test_missing_room_and_blocked_entry_fail_closed(self):
        case = castle_case()
        for image in self.images:
            for changed in (
                replace(case, rooms=(case.rooms[0],), goal=(12, 0, 136, 0, 136)),
                replace(
                    case,
                    rooms=(
                        case.rooms[0],
                        replace(case.rooms[1], cells=tuple((0x18,) + row[1:] for row in case.rooms[1].cells)),
                    ),
                ),
            ):
                oracle = CastleOracle(image, changed)
                state = oracle.initial(changed)
                for _ in range(25):
                    step = oracle.tick(state, 0x80)
                    state = step.state
                    if step.event:
                        break
                self.assertIn(step.event, ("missing-room", "blocked-entry"))

    def test_allocation_budgets_and_unsupported_neighbor(self):
        for image in self.images[:1]:
            rooms = tuple(RoomLayout(n, tuple(map(tuple, corridor())), (Block(16, 0),) * 24) for n in range(1, 23))
            case = CastleCase(rooms, (1, 0, 0), (2, 0, 0, 0, 0))
            with self.assertRaisesRegex(ReachabilityError, "arenas"):
                CastleOracle(image, case)
            case = castle_case()
            cells = [list(row) for row in case.rooms[1].cells]
            cells[0][0] = 0x45
            with self.assertRaisesRegex(ReachabilityError, "unsupported"):
                CastleOracle(
                    image, replace(case, rooms=(case.rooms[0], replace(case.rooms[1], cells=tuple(map(tuple, cells)))))
                )

    def test_world_console_replay_checks_inventory_and_ram(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            case_path, report_path, replay_path = (root / name for name in ("castle.json", "route.json", "replay.json"))
            case_path.write_text(json.dumps(asdict(castle_case())), encoding="utf-8")
            rom = os.environ["RETROLARIUM_CASTLE_PHYSICS_ROMS"].split(os.pathsep)[0]
            args = ["--rom", rom, "--case", str(case_path), "--max-nodes", "35", "--max-ticks", "90"]
            self.assertEqual(main(args + ["--output", str(report_path)]), 0)
            self.assertEqual(main(args + ["--output", str(replay_path), "--replay", str(report_path)]), 0)
            original = json.loads(report_path.read_text())
            for field in ("rooms", "inventories", "objects", "final_state_sha256"):
                report = original.copy()
                report[field] = [] if field != "final_state_sha256" else "invalid"
                report_path.write_text(json.dumps(report), encoding="utf-8")
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
                    main(args + ["--output", str(replay_path), "--replay", str(report_path)])
                self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
