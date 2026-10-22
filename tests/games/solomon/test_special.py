from __future__ import annotations

from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from retrolarium_kit.games.solomon.special import (
    SEAL_ROOMS,
    SPECIAL_SIZES,
    SolomonSpecialError,
    decode_mirror_enemy_set,
    decode_mirror_schedule,
    decode_special_rooms,
    encode_mirror_enemy_set,
    encode_mirror_schedule,
    encode_special_rooms,
    parse_mirror_enemy_set,
    parse_mirror_schedule,
    parse_special_rooms,
)


def special_fixture() -> dict[str, object]:
    position = lambda x, y: {"x": x, "y": y}
    return {
        "random_bonus_room": {
            "positions": [position(index % 16, index // 16) for index in range(32)],
            "item_types": list(range(1, 17)),
        },
        "solomon_seals": [{"room": room, "position": position(index, 3)} for index, room in enumerate(SEAL_ROOMS)],
        "princess_room_hidden_cells": [position(index, 4) for index in range(12)],
        "room_20_bat_symbols": [position(0, 0), position(15, 11)],
        "room_30_blue_opals": [position(7, 5)],
    }


class SolomonSharedDataTest(unittest.TestCase):
    def test_mirror_codecs(self) -> None:
        schedule = parse_mirror_schedule(
            {
                "initial_phase": [1, 2, 3, 4],
                "loop_phase": [5, 6, 7, 8],
            },
            "schedule",
        )
        self.assertEqual(schedule, decode_mirror_schedule(encode_mirror_schedule(schedule), 0, 8)[0])
        enemy_set = parse_mirror_enemy_set(
            {
                "enemy_types": [0x50, 0x51],
                "loop_offset": 1,
            },
            "set",
        )
        self.assertEqual(enemy_set, decode_mirror_enemy_set(encode_mirror_enemy_set(enemy_set), 0, 3)[0])
        with self.assertRaisesRegex(SolomonSpecialError, "loop_offset"):
            parse_mirror_enemy_set({"enemy_types": [0x50], "loop_offset": 1}, "set")

    def test_special_tables_round_trip_and_validation(self) -> None:
        fixture = special_fixture()
        parsed = parse_special_rooms(fixture)
        encoded = encode_special_rooms(parsed)
        self.assertEqual(116, sum(map(len, encoded.values())))
        prg = bytearray(0x8000)
        offsets = {name: 0x100 + index * 0x40 for index, name in enumerate(SPECIAL_SIZES)}
        for name, payload in encoded.items():
            prg[offsets[name] : offsets[name] + len(payload)] = payload
        self.assertEqual(fixture, decode_special_rooms(bytes(prg), offsets).as_dict())
        fixture["solomon_seals"][0]["room"] = 10
        with self.assertRaisesRegex(SolomonSpecialError, "canonical"):
            parse_special_rooms(fixture)
        fixture = special_fixture()
        fixture["room_20_bat_symbols"].append({"x": 0, "y": 0})
        with self.assertRaisesRegex(SolomonSpecialError, "duplicate"):
            parse_special_rooms(fixture)


if __name__ == "__main__":
    unittest.main()
