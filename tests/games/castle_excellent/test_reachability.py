from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from retrolarium_kit.games.castle_excellent.reachability import (
    BUTTONS,
    EMPTY,
    Block,
    Oracle,
    ReachabilityError,
    RoomCase,
    State,
    Step,
    _Bus,
    find_route,
    main,
    replay,
)


def primitive_case(name: str = "walk") -> RoomCase:
    """Original synthetic geometry, not room data copied from a game."""
    cells = [[EMPTY] * 20 for _ in range(20)]
    cells[18] = [0x18] * 20
    cells[19] = [0x18] * 20
    for row in cells:
        row[0] = row[-1] = 0x18
    start, goal, blocks = (32, 128), (80, 128, 88, 128), ()
    if name == "step":
        for y in (16, 17):
            cells[y][5] = cells[y][6] = 0x18
        start, goal = (64, 128), (96, 112, 112, 112)
    elif name == "push":
        start, goal, blocks = (64, 128), (96, 128, 96, 128), (Block(80, 128),)
    elif name == "fall":
        for y in (12, 13):
            cells[y][2] = cells[y][3] = 0x18
        start, goal = (32, 80), (80, 128, 112, 128)
    elif name == "wall":
        for row in cells:
            row[5] = 0x18
        goal = (112, 128, 112, 128)
    elif name != "walk":
        raise ValueError(name)
    return RoomCase(tuple(map(tuple, cells)), start, goal, blocks)


class LinearOracle:
    """A tiny deterministic graph for search tests, independent of a ROM."""

    def initial(self, case):
        ram = bytearray(0x800)
        ram[0x38] = case.start[0]
        ram[0x3A] = case.start[1] + 16
        return State(bytes(ram))

    def tick(self, state, buttons):
        ram = bytearray(state.ram)
        if buttons & 0x80:
            ram[0x38] = min(20, ram[0x38] + 2)
        elif buttons & 0x40:
            ram[0x38] = max(0, ram[0x38] - 2)
        return Step(State(bytes(ram)))


class InputAndSearchTests(unittest.TestCase):
    def test_geometry_and_pool_validation(self):
        case = primitive_case()
        case.validate()
        bad_cases = (
            replace(case, cells=case.cells[:-1]),
            replace(case, start=(-1, 0)),
            replace(case, start=(0, 145)),
            replace(case, goal=(100, 0, 20, 10)),
            replace(case, blocks=(Block(0, 0, 0xA1),)),
            replace(case, blocks=(Block(0, 0),) * 25),
        )
        for bad in bad_cases:
            with self.subTest(bad=bad), self.assertRaises(ReachabilityError):
                bad.validate()

    def test_dictionary_input_and_missing_fields(self):
        case = primitive_case("push")
        parsed = RoomCase.from_dict(
            {"cells": case.cells, "start": case.start, "goal": case.goal, "blocks": [{"x": 80, "y": 128}]}
        )
        self.assertEqual(parsed, case)
        with self.assertRaises(ReachabilityError):
            RoomCase.from_dict({})

    def test_original_rom_required(self):
        with self.assertRaisesRegex(ReachabilityError, "unmodified"):
            Oracle(b"not a game image")

    def test_hardware_access_fails_closed(self):
        bus = _Bus(bytes(0x8000), bytes(0x800))
        for address in (0x2002, 0x4016, 0x6000):
            with self.assertRaises(ReachabilityError):
                bus.read(address)
        with self.assertRaises(ReachabilityError):
            bus.write(0x8000, 1)
        bus.write(0x4015, 15)
        bus.write(0x800, 9)
        self.assertEqual(bus.read(0), 9)

    def test_instruction_budget(self):
        # A synthetic JMP-to-self at the movement entry. No commercial bytes.
        oracle = object.__new__(Oracle)
        prg = bytearray(0x8000)
        prg[0x2CE:0x2D1] = bytes((0x4C, 0xCE, 0x82))
        oracle.prg, oracle.instruction_limit = bytes(prg), 3
        with self.assertRaisesRegex(ReachabilityError, "budget exhausted"):
            oracle.tick(State(bytes(0x800)), 0)

    def test_bounded_route_and_replay(self):
        case = replace(primitive_case(), start=(0, 0), goal=(10, 0, 10, 0))
        oracle = LinearOracle()
        result = find_route(oracle, case, max_nodes=10, max_ticks=10, action_ticks=4)
        self.assertEqual(result.status, "reachable")
        self.assertEqual(result.positions[-1], (10, 0))
        self.assertEqual(len(result.buttons), 5)
        self.assertEqual(replay(oracle, case, result.buttons), result.positions)

    def test_limits_do_not_claim_impossibility(self):
        case = replace(primitive_case(), start=(0, 0), goal=(100, 0, 100, 0))
        for limits in ({"max_nodes": 1}, {"max_ticks": 2}, {"max_nodes": 50, "max_ticks": 50}):
            result = find_route(LinearOracle(), case, **limits)
            self.assertEqual(result.status, "unknown")
        with self.assertRaises(ReachabilityError):
            find_route(LinearOracle(), case, max_nodes=0)

    def test_console_rejects_nonpositive_limits_before_search_or_replay(self):
        base = ["--rom", "original.nes", "--case", "room.json", "--output", "route.json"]
        for option in ("--max-nodes", "--max-ticks", "--action-ticks"):
            for value in ("0", "-1"):
                for replay_args in ([], ["--replay", "saved.json"]):
                    with self.subTest(option=option, value=value, replay=bool(replay_args)):
                        with (
                            contextlib.redirect_stderr(io.StringIO()) as errors,
                            self.assertRaises(SystemExit) as raised,
                        ):
                            main(base + replay_args + [option, value])
                        self.assertEqual(raised.exception.code, 2)
                        self.assertIn("positive integer", errors.getvalue())

    def test_goal_at_start_and_failed_replay(self):
        case = replace(primitive_case(), start=(0, 0), goal=(0, 0, 0, 0))
        result = find_route(LinearOracle(), case)
        self.assertEqual((result.expanded, result.buttons), (0, ()))
        with self.assertRaisesRegex(ReachabilityError, "did not reach"):
            replay(LinearOracle(), replace(case, goal=(10, 0, 10, 0)), (0,))

    def test_exit_cannot_become_a_goal(self):
        class ExitOracle(LinearOracle):
            def tick(self, state, buttons):
                return Step(super().tick(state, buttons).state, "exit-right")

        case = replace(primitive_case(), start=(0, 0), goal=(2, 0, 2, 0))
        self.assertEqual(find_route(ExitOracle(), case).status, "unknown")
        with self.assertRaisesRegex(ReachabilityError, "exit-right"):
            replay(ExitOracle(), case, (0x80,))


@unittest.skipUnless(os.environ.get("RETROLARIUM_CASTLE_PHYSICS_ROMS"), "optional original-ROM integration tests")
class OriginalMechanicsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.oracles = [
            Oracle(Path(path).read_bytes()) for path in os.environ["RETROLARIUM_CASTLE_PHYSICS_ROMS"].split(os.pathsep)
        ]

    def test_synthetic_rooms_have_replayable_routes(self):
        for name in ("walk", "step", "push", "fall"):
            results = []
            for oracle in self.oracles:
                with self.subTest(room=name, revision=oracle.revision):
                    case = primitive_case(name)
                    result = find_route(oracle, case, max_nodes=30, max_ticks=70)
                    self.assertEqual(result.status, "reachable")
                    self.assertEqual(replay(oracle, case, result.buttons), result.positions)
                    results.append((result.buttons, result.positions))
            self.assertTrue(all(result == results[0] for result in results))

    def test_flat_walk_delay_and_stride(self):
        for oracle in self.oracles:
            state = oracle.initial(primitive_case())
            xs = []
            for _ in range(6):
                state = oracle.tick(state, BUTTONS["right"]).state
                xs.append(state.player[0])
            self.assertEqual(xs, [32, 32, 34, 36, 38, 40])
            self.assertEqual(state.player[1], 128)

    def test_jump_height_and_fall(self):
        for oracle in self.oracles:
            state = oracle.initial(primitive_case())
            ys = []
            for _ in range(60):
                state = oracle.tick(state, BUTTONS["jump"]).state
                ys.append(state.player[1])
            self.assertEqual(min(ys), 96)  # Original jump rises 32 pixels.
            self.assertIn(128, ys[35:])

    def test_push_without_pulling_or_air_push(self):
        for oracle in self.oracles:
            initial = oracle.initial(primitive_case("push"))
            state = initial
            for _ in range(10):
                state = oracle.tick(state, BUTTONS["right"]).state
            self.assertEqual(state.blocks[0], (0x8C, 96, 128))
            self.assertEqual(state.player, (80, 128))
            for _ in range(12):
                state = oracle.tick(state, BUTTONS["left"]).state
            self.assertEqual(state.blocks[0][1], 96)
            ram = bytearray(initial.ram)
            ram[0x3C] = 0x20  # Already jumping, touching a supported block.
            airborne = oracle.tick(State(bytes(ram)), BUTTONS["right"])
            self.assertEqual(airborne.state.blocks[0][1], 80)

    def test_objects_and_player_use_different_floor_flags(self):
        for oracle in self.oracles:
            case = primitive_case("push")
            cells = tuple(tuple(0x12 if c == 0x18 else c for c in row) for row in case.cells)
            state = oracle.tick(oracle.initial(replace(case, cells=cells)), 0).state
            self.assertEqual(state.player[1], 128)
            self.assertEqual(state.blocks[0][2], 130)  # This floor does not support blocks.

    def test_invalid_spawns_and_unsupported_mechanics(self):
        for oracle in self.oracles:
            case = primitive_case()
            for invalid in (replace(case, start=(0, 0)), replace(case, blocks=(Block(*case.start),))):
                with self.assertRaises(ReachabilityError):
                    oracle.initial(invalid)
            for code in (0x16, 0x35, 0x45, 0x56):
                self.assertFalse(oracle.supported_cell(code))

    def test_full_wall_stays_unknown(self):
        for oracle in self.oracles:
            result = find_route(oracle, primitive_case("wall"), max_nodes=6, max_ticks=50)
            self.assertEqual(result.status, "unknown")

    def test_console_report_and_replay(self):
        from dataclasses import asdict

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            rom = Path(os.environ["RETROLARIUM_CASTLE_PHYSICS_ROMS"].split(os.pathsep)[0])
            case_path, report_path, replay_path = (root / name for name in ("room.json", "route.json", "replay.json"))
            case_path.write_text(json.dumps(asdict(primitive_case("push"))), encoding="utf-8")
            args = ["--rom", str(rom), "--case", str(case_path), "--max-nodes", "30"]
            self.assertEqual(main(args + ["--output", str(report_path)]), 0)
            self.assertEqual(main(args + ["--output", str(replay_path), "--replay", str(report_path)]), 0)
            report = json.loads(report_path.read_text())
            self.assertEqual(report["status"], "reachable")
            self.assertNotIn(b"\r", report_path.read_bytes())
            self.assertNotIn(str(rom), report_path.read_text())
            report["positions"][-1][0] += 1
            report_path.write_text(json.dumps(report), encoding="utf-8")
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
                main(args + ["--output", str(replay_path), "--replay", str(report_path)])
            self.assertEqual(raised.exception.code, 2)
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
                main(args + ["--output", str(case_path)])
            self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
