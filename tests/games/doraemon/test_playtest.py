from __future__ import annotations

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from retrolarium_kit.studio.project import (
    DORAEMON_UNDERGROUND_WRITES, DORAEMON_WORLD1_START_WRITES, DORAEMON_WORLD3_START_WRITES,
    StudioProjectError, _doraemon_playtest_point,
)

# World1_UndergroundRoomCameraProfiles: camera X, camera Y, axis limit and
# axis start of the nine rooms (data/world1/underground_rooms.json).
ROOMS = [(0x40, 0x22, 0x20, 0x00), (0x80, 0x22, 0x20, 0x00), (0xCA, 0x22, 0x20, 0x0A),
         (0x00, 0x42, 0x20, 0x00), (0x40, 0x42, 0x40, 0x00), (0x20, 0x02, 0xC0, 0x00),
         (0xE0, 0x02, 0xC0, 0xC0), (0xA0, 0x42, 0x40, 0x40), (0x60, 0x42, 0x40, 0x00)]


class DoraemonPlaytestPointTests(unittest.TestCase):
    def test_city_block_sets_camera_player_and_area(self) -> None:
        # The game's own start (camera $E0/$D8, player $78/$80) is block 59, 58.
        point, writes, described = _doraemon_playtest_point(1, (59, 58), None, None)
        self.assertEqual("camera 223 218 120 128 0", point)
        self.assertEqual(DORAEMON_WORLD1_START_WRITES, writes)
        self.assertEqual([223, 218], described["camera"])
        # The mountain above camera row $40 takes the area palette 1; the
        # camera stops at the map's edge and the player moves instead.
        self.assertEqual("camera 0 0 16 16 1", _doraemon_playtest_point(1, (0, 0), None, None)[0])

    def test_stage_and_room(self) -> None:
        self.assertEqual("stage 2", _doraemon_playtest_point(2, None, 2, None)[0])
        point, writes, _ = _doraemon_playtest_point(3, None, None, 9)
        self.assertEqual("room 9", point)
        self.assertEqual(DORAEMON_WORLD3_START_WRITES, writes)
        self.assertEqual(("", (), None), _doraemon_playtest_point(2, None, None, None))

    def test_underground_block_finds_its_room_and_scroll(self) -> None:
        point, writes, described = _doraemon_playtest_point(1, None, None, None, (20, 12), ROOMS)
        self.assertEqual("underground 0 128 128 66 2", point)
        self.assertEqual(DORAEMON_UNDERGROUND_WRITES, writes)
        # Rooms 5 and 6 share one long region; the first is used, scrolled.
        self.assertEqual("underground 5 128 96 146 114",
                         _doraemon_playtest_point(1, None, None, None, (40, 3), ROOMS)[0])
        # The vertical finale is not a room a manhole leads to.
        with self.assertRaises(StudioProjectError):
            _doraemon_playtest_point(1, None, None, None, (4, 10), ROOMS)

    def test_rejects_points_of_other_worlds(self) -> None:
        for args in ((2, (1, 1), None, None), (1, None, 1, None), (3, None, None, 0), (1, (64, 0), None, None)):
            with self.assertRaises(StudioProjectError):
                _doraemon_playtest_point(*args)


if __name__ == "__main__":
    unittest.main()
