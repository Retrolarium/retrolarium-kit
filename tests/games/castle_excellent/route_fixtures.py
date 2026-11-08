"""Controller helpers for optional original-ROM integration tests."""

from __future__ import annotations

from retrolarium_kit.games.castle_excellent.reachability import State


def drive_to(oracle, state: State, direction: int, room: int, limit: int = 600) -> State:
    for _ in range(limit):
        # A new press, not a permanently held button, resumes the native pause.
        buttons = direction
        if state.ram[0x7C4]:
            buttons = 0 if state.ram[0x777] & 1 else direction | 1
        step = oracle.tick(state, buttons)
        if step.event:
            raise AssertionError(step.event)
        state = step.state
        if state.room == room:
            return state
    raise AssertionError("drive budget exhausted")
