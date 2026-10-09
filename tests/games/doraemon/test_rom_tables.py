from __future__ import annotations

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from retrolarium_kit.games.doraemon.metasprites import WORLD2_CODEC, decode_world2
from retrolarium_kit.games.doraemon.objects import (
    DoraemonObjectError, decode_descriptors, decode_placements, decode_registry, decode_schedule,
    encode_placements, encode_registry, encode_schedule,
)
from retrolarium_kit.core.game_profiles import packaged_game_profiles
from helpers import game_repository


# The cartridge image is not part of the repository; the test runs where the
# disassembly sits beside this checkout.
ROM = game_repository("doraemon_src") / "Doraemon (J) (PRG0) [!].nes"


@unittest.skipUnless(ROM.is_file(), "the Doraemon ROM is not available")
class DoraemonRomTableTests(unittest.TestCase):
    def test_world2_enemy_metasprites_start_at_their_label(self) -> None:
        # World2_MetaspriteTileQuads ($A3DC) opens with W2Enemy1's $80 $81
        # $90 $91; W2Enemy10 (records 36-37, World 2 enemy state $0A) is drawn
        # from $4D $4E $5D $5E.
        source = ROM.read_bytes()
        profile = packaged_game_profiles().get("doraemon")
        resource = next(item for item in profile.resources_for(profile.revisions[0]) if item.codec == WORLD2_CODEC)
        p = resource.parameters
        value = decode_world2(source[p["file_offset"]:p["file_offset"] + p["size"]],
                              source[p["descriptors_file_offset"]:p["descriptors_file_offset"] + 58],
                              source[p["attributes_file_offset"]:p["attributes_file_offset"] + 36])
        self.assertEqual([0x80, 0x81, 0x90, 0x91], value["tiles"][0])
        self.assertEqual([0x4D, 0x4E, 0x5D, 0x5E], value["tiles"][36])
        # W2Enemy11's first frame: palette 3, the lower row flipped both ways.
        self.assertEqual(0x1F, value["descriptors"][32])
        self.assertEqual([0x40, 0x40, 0xC0, 0xC0], value["attributes"][0x1C:0x20])

    def test_world1_placements_round_trip_and_keep_their_order(self) -> None:
        source = ROM.read_bytes()
        city = decode_placements(source[22937:22937 + 337])
        underground = decode_placements(source[22837:22837 + 100])
        self.assertEqual((112, 33), (len(city), len(underground)))
        self.assertEqual(source[22937:22937 + 337], encode_placements(city, 337, sorted_tail=True))
        # A manhole (descriptor 0) draws metasprite $29; type $C5 is a weapon
        # upgrade hidden until shot.
        self.assertEqual(0x29, decode_descriptors(source[19482:19534])[0]["metasprite"])
        self.assertEqual({"x": 163, "y": 41, "type": 0xC5}, underground[1])
        # Past the 45 always-scanned records the game needs them ordered by X.
        unordered = [dict(r) for r in city]
        unordered[50]["x"], unordered[60]["x"] = unordered[60]["x"], unordered[50]["x"]
        if unordered[50]["x"] != unordered[60]["x"]:
            with self.assertRaises(DoraemonObjectError):
                encode_placements(unordered, 337, sorted_tail=True)

    def test_world3_registry_round_trips(self) -> None:
        source = ROM.read_bytes()
        registry = decode_registry(source[88443:88443 + 65])
        self.assertEqual({"room": 7, "type": 0x18, "x": 160, "y": 64, "state": 0}, registry[0])
        self.assertEqual(0x3F, registry[12]["room"])
        self.assertEqual(source[88443:88443 + 65], encode_registry(registry))

    def test_world3_schedule_round_trips(self) -> None:
        source = ROM.read_bytes()
        payload = source[87675:87675 + 768]
        rooms = decode_schedule(payload)
        # Room 0: channel 0 spawns one type $00 every 15, channel 3 one $05.
        self.assertEqual({"type": 0, "count": 1, "delay": 15}, rooms[0]["channels"][0])
        self.assertEqual({"type": 5, "count": 1, "delay": 100}, rooms[0]["channels"][3])
        active = sum(1 for room in rooms for channel in room["channels"] if channel["count"])
        self.assertEqual(132, active)
        self.assertEqual(payload, encode_schedule(rooms))


if __name__ == "__main__":
    unittest.main()
