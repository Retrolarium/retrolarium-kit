from __future__ import annotations

import os
import unittest
from dataclasses import replace
from pathlib import Path

from retrolarium_kit.games.castle_excellent.castle_routes import (
    CastleCase,
    CastleOracle,
    RoomLayout,
)
from retrolarium_kit.games.castle_excellent.reachability import (
    EMPTY,
    Block,
    Key,
    Oracle,
    ReachabilityError,
    RoomCase,
    State,
    find_route,
    replay_states,
    verified_route,
)

from .route_fixtures import drive_to


def cells() -> list[list[int]]:
    result = [[EMPTY] * 20 for _ in range(20)]
    result[19] = [0x18] * 20
    return result


def lift_case(*, wide: bool = False, descending: bool = False) -> RoomCase:
    room = cells()
    room[16][4] = 0x4D if wide else 0x4B
    if wide:
        room[16][5] = 0x4E
    if descending:
        for y in (17, 18):
            room[y][4] = 0x4F if wide else 0x4C
            if wide:
                room[y][5] = 0x50
    # Top object precedes its support in runtime order: closure must be transitive.
    return RoomCase(tuple(map(tuple, room)), (64, 80), (64, 72, 64, 72), (Block(64, 96), Block(64, 112)))


def pulley_case(*, lowering: bool = False) -> RoomCase:
    room = cells()
    for x in range(2, 9):
        room[16][x] = 0x43
    room[10][10], room[14][10] = 0x49, 0x4A
    for y in range(10):
        room[y][10] = 0x52
    return RoomCase(
        tuple(map(tuple, room)),
        (64 if lowering else 96, 136),
        (0, 0, 0, 0),
        (Block(80, 136, 0x8E), Block(160, 64), Block(160, 96)),
    )


def run(oracle, case: RoomCase, buttons: tuple[int, ...]) -> list[State]:
    state = oracle.initial(case)
    states = [state]
    for button in buttons:
        step = oracle.tick(state, button)
        if step.event:
            raise AssertionError(f"unexpected event: {step.event}")
        state = step.state
        states.append(state)
    return states


class MechanismInputTests(unittest.TestCase):
    def test_cell_objects_share_the_runtime_pool(self):
        case = replace(lift_case(), keys=(Key(240, 0),) * 22)
        with self.assertRaisesRegex(ReachabilityError, "24 slots"):
            case.validate()
        replace(case, keys=case.keys[:-1]).validate()

    def test_wide_halves_and_control_pair_validation(self):
        case = lift_case(wide=True)
        room = [list(row) for row in case.cells]
        room[16][5] = EMPTY
        with self.assertRaisesRegex(ReachabilityError, "right half"):
            replace(case, cells=tuple(map(tuple, room))).validate()
        case = pulley_case()
        room = [list(row) for row in case.cells]
        room[14][10] = EMPTY
        with self.assertRaisesRegex(ReachabilityError, "pair"):
            replace(case, cells=tuple(map(tuple, room))).validate()
        with self.assertRaisesRegex(ReachabilityError, "requires"):
            replace(lift_case(), blocks=(Block(160, 136, 0x8E),)).validate()


@unittest.skipUnless(os.environ.get("RETROLARIUM_CASTLE_PHYSICS_ROMS"), "optional original-ROM integration tests")
class OriginalMechanismTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.images = [
            Path(path).read_bytes() for path in os.environ["RETROLARIUM_CASTLE_PHYSICS_ROMS"].split(os.pathsep)
        ]

    def test_native_marker_loading_order_sizes_and_initial_states(self):
        for image in self.images:
            oracle = Oracle(image)
            case = lift_case(wide=True)
            state = oracle.initial(case)
            self.assertEqual(state.object_records[-1], (2, 0xA2, 64, 128, 0, 0))
            self.assertEqual(state.ram[0x400 + 16 * 20 + 4], EMPTY)
            self.assertEqual(state.ram[0x400 + 16 * 20 + 5], 0x4E)
            room = cells()
            room[8][8] = 0x51
            state = oracle.initial(RoomCase(tuple(map(tuple, room)), (32, 136), (32, 136, 32, 136)))
            self.assertEqual(state.object_records, ((0, 0xA3, 128, 64, 255, 0),))

    def test_narrow_and_wide_lifts_carry_transitive_stack_every_32_ticks(self):
        for image in self.images:
            for wide in (False, True):
                oracle = Oracle(image)
                states = run(oracle, lift_case(wide=wide), (0,) * 33)
                self.assertEqual(states[31].player, (64, 80))
                self.assertEqual([r[3] for r in states[31].object_records], [96, 112, 128])
                self.assertEqual(states[32].player, (64, 72))
                self.assertEqual([r[3] for r in states[32].object_records], [88, 104, 120])
                self.assertEqual(states[33].player, states[32].player)
                self.assertEqual(states[32].object_records[-1][4], 255)

    def test_wide_lift_carries_branches_and_excludes_disconnected_block(self):
        for image in self.images:
            case = lift_case(wide=True)
            room = [list(row) for row in case.cells]
            room[16][7] = 0x18
            case = replace(
                case,
                cells=tuple(map(tuple, room)),
                start=(72, 80),
                blocks=(Block(72, 96), Block(64, 112), Block(80, 112), Block(112, 112)),
            )
            states = run(Oracle(image), case, (0,) * 32)
            self.assertEqual(states[-1].player, (72, 72))
            self.assertEqual([r[3] for r in states[-1].object_records], [88, 104, 104, 112, 120])

    def test_descending_lift_does_not_teleport_the_stack_down(self):
        for image in self.images:
            for wide in (False, True):
                states = run(Oracle(image), lift_case(wide=wide, descending=True), (0,) * 38)
                self.assertEqual([r[3] for r in states[32].object_records], [96, 112, 136])
                self.assertEqual([r[3] for r in states[33].object_records], [96, 114, 136])
                self.assertEqual([r[3] for r in states[34].object_records], [98, 116, 136])
                self.assertEqual(states[34].player, (64, 80))
                self.assertEqual(states[38].player, (64, 88))
                self.assertEqual([r[3] for r in states[38].object_records], [104, 120, 136])

    def test_lift_reverses_at_top_and_bottom(self):
        for image in self.images:
            for wide in (False, True):
                states = run(Oracle(image), lift_case(wide=wide, descending=True), (0,) * 128)
                # The shaft ends at row 18, so it reverses before the room limit.
                self.assertEqual(states[64].object_records[-1][3:5], (144, 0))
                self.assertEqual(states[96].object_records[-1][3:5], (136, 255))
                case = lift_case(wide=wide, descending=True)
                room = [list(row) for row in case.cells]
                room[19][4] = 0x4F if wide else 0x4C
                if wide:
                    room[19][5] = 0x50
                case = replace(case, cells=tuple(map(tuple, room)), start=(32, 136), blocks=())
                states = run(Oracle(image), case, (0,) * 128)
                self.assertEqual(states[96].object_records[-1][3:5], (152, 0))
                self.assertEqual(states[128].object_records[-1][3:5], (144, 255))
                # At the top it reverses onto the shaft written by its ascent.
                room = cells()
                room[1][4] = 0x4D if wide else 0x4B
                if wide:
                    room[1][5] = 0x4E
                case = RoomCase(tuple(map(tuple, room)), (32, 136), (32, 136, 32, 136))
                states = run(Oracle(image), case, (0,) * 64)
                self.assertEqual(states[32].object_records[0][3:5], (0, 255))
                self.assertEqual(states[64].object_records[0][3:5], (8, 0))

    def test_trapped_lift_fails_with_bounded_instruction_error(self):
        room = cells()
        room[0][4] = 0x4B  # No upward space and no shaft below: native reversal loop.
        case = RoomCase(tuple(map(tuple, room)), (32, 136), (32, 136, 32, 136))
        oracle = Oracle(self.images[0])
        with self.assertRaisesRegex(ReachabilityError, "instruction budget"):
            run(oracle, case, (0,) * 32)

    def test_ceiling_crushes_carried_block(self):
        for image in self.images:
            case = lift_case()
            room = [list(row) for row in case.cells]
            room[11][4] = 0x18
            case = replace(case, cells=tuple(map(tuple, room)), start=(32, 136))
            state = run(Oracle(image), case, (0,) * 32)[-1]
            self.assertEqual(state.object_records[0][1], 0xA8)
            self.assertEqual(state.object_records[1][1], 0x8C)

    def test_platform_moves_reverses_stops_and_does_not_drag_stack_sideways(self):
        for image in self.images:
            oracle = Oracle(image)
            room = cells()
            room[16][4] = 0x51
            case = RoomCase(tuple(map(tuple, room)), (64, 96), (64, 96, 64, 96), (Block(64, 112),))
            states = run(oracle, case, (0,) * 34)
            self.assertEqual(states[1].object_records[-1][2], 62)
            self.assertEqual(states[1].object_records[0][2], 64)
            self.assertEqual(states[1].player[0], 64)
            self.assertEqual(states[33].object_records[-1][2:5], (0, 128, 0))
            self.assertEqual(states[34].object_records[-1][2], 2)
            # A corridor exactly one cell wide blocks both probe directions.
            room = cells()
            room[8][8] = 0x51
            room[8][7] = room[8][9] = 0x18
            case = RoomCase(tuple(map(tuple, room)), (32, 136), (32, 136, 32, 136))
            states = run(oracle, case, (0, 0))
            self.assertEqual(states[-1].object_records[0][2:6], (128, 64, 255, 255))

    def test_side_contact_with_mechanism_is_fatal(self):
        for image in self.images:
            room = cells()
            room[16][4] = 0x51  # Moves left into the player, creating actual overlap.
            case = RoomCase(tuple(map(tuple, room)), (48, 128), (48, 128, 48, 128))
            oracle = Oracle(image)
            state = oracle.initial(case)
            event = None
            for _ in range(6):
                step = oracle.tick(state, 0)
                state, event = step.state, step.event
                if event:
                    break
            self.assertEqual(event, "death")

    def test_pulley_push_raises_both_members_and_carries_blocks(self):
        for image in self.images:
            states = run(Oracle(image), pulley_case(), (0x40,) * 10)
            self.assertEqual(states[-1].player, (80, 136))
            self.assertEqual([r[3] for r in states[-1].object_records], [136, 48, 80, 64, 96])
            self.assertEqual(states[-1].object_records[0][2], 64)

    def test_pulley_lowering_and_clearance(self):
        for image in self.images:
            case = pulley_case(lowering=True)
            states = run(Oracle(image), case, (0x80,) * 3)
            self.assertEqual([r[3] for r in states[-1].object_records[-2:]], [82, 114])
            room = [list(row) for row in case.cells]
            room[15][10] = 0x18
            blocked = replace(case, cells=tuple(map(tuple, room)))
            states = run(Oracle(image), blocked, (0x80,) * 3)
            self.assertEqual(states[-1].object_records[0][2], 82)  # Control still pushes.
            self.assertEqual([r[3] for r in states[-1].object_records[-2:]], [80, 112])

    def test_pulley_cannot_be_pushed_in_air(self):
        for image in self.images:
            oracle = Oracle(image)
            state = oracle.initial(pulley_case())
            ram = bytearray(state.ram)
            ram[0x3C] = 0x20
            step = oracle.tick(State(bytes(ram)), 0x40)
            self.assertEqual(step.state.object_records, state.object_records)

    def test_native_spawner_can_fill_last_slot(self):
        for image in self.images:
            case = replace(lift_case(), blocks=(), keys=(Key(240, 0),) * 23, start=(32, 136))
            state = Oracle(image).initial(case)
            self.assertEqual(len(state.object_records), 24)
            self.assertEqual(state.object_records[-1][:2], (23, 0xA1))

    def test_search_replays_object_motion_in_both_revisions(self):
        results = []
        for image in self.images:
            case = replace(pulley_case(), goal=(80, 136, 80, 136))
            oracle = Oracle(image)
            result = find_route(oracle, case, max_nodes=35, max_ticks=50)
            self.assertEqual(result.status, "reachable")
            self.assertEqual(
                result.objects, tuple(s.object_records for s in replay_states(oracle, case, result.buttons))
            )
            self.assertNotEqual(result.objects[0], result.objects[-1])
            results.append((result.buttons, result.positions, result.objects))
        self.assertTrue(all(result == results[0] for result in results))

    def test_rising_lift_carries_key_with_its_native_width(self):
        for image in self.images:
            case = replace(lift_case(), start=(32, 136), blocks=(), keys=(Key(68, 112),))
            state = run(Oracle(image), case, (0,) * 32)[-1]
            self.assertEqual(state.object_records[0][1:4], (0x91, 68, 104))
            self.assertEqual(state.object_records[1][3], 120)

    def test_rising_lift_reaches_upper_room_under_original_rules(self):
        room = cells()
        room[8][4] = 0x4B
        case = CastleCase(
            (RoomLayout(22, tuple(map(tuple, room))), RoomLayout(12, tuple(map(tuple, cells())))),
            (22, 64, 48),
            (12, 64, 136, 64, 136),
        )
        results = []
        for image in self.images:
            oracle = CastleOracle(image, case)
            result = verified_route(oracle, case, (0,) * 257, 0)
            self.assertEqual(result.rooms[-1], 12)
            self.assertEqual(result.positions[-1], (64, 136))
            results.append((result.positions, result.rooms, result.objects))
        self.assertTrue(all(result == results[0] for result in results))

    def test_reentering_room_respawns_mechanism_at_initial_position(self):
        room = cells()
        room[16][4] = 0x4B
        case = CastleCase(
            (RoomLayout(12, tuple(map(tuple, room))), RoomLayout(13, tuple(map(tuple, cells())))),
            (12, 272, 136),
            (12, 304, 136, 304, 136),
        )
        for image in self.images:
            oracle = CastleOracle(image, case)
            state = oracle.initial(case)
            for _ in range(32):
                state = oracle.tick(state, 0).state
            self.assertEqual(state.object_records[-1][3], 120)
            state = drive_to(oracle, state, 0x80, 13)
            state = drive_to(oracle, state, 0x40, 12)
            self.assertEqual(state.object_records[-1][1:5], (0xA1, 64, 128, 0))


if __name__ == "__main__":
    unittest.main()
