from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from retrolarium_kit.core.authoring import AuthoringError
from retrolarium_kit.core.game_profiles import packaged_game_profiles
from retrolarium_kit.games.doraemon.behavior import DoraemonBehaviorError, decode_behavior, encode_behavior
from retrolarium_kit.platform.nes.resources import build_resources, decode_resources, parse_resource_document
from helpers import game_repository


# The cartridge image is not part of the repository; the test runs where the
# disassembly sits beside this checkout.
ROM = game_repository("doraemon_src") / "Doraemon (J) (PRG0) [!].nes"


def by_id(document) -> dict[str, dict]:
    return {item["id"]: item for item in document.resources}


@unittest.skipUnless(ROM.is_file(), "the Doraemon ROM is not available")
class DoraemonTableTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = ROM.read_bytes()
        self.profile = packaged_game_profiles().get("doraemon")
        self.revision = self.profile.revisions[0]
        self.document = decode_resources(self.source, self.profile, self.revision)
        self.resources = by_id(self.document)

    def build(self, resources: dict[str, dict]) -> bytes:
        value = self.document.as_dict()
        value["resources"] = [resources.get(item["id"], item) for item in value["resources"]]
        document = parse_resource_document(value, self.profile, self.revision)
        return build_resources(self.source, document, self.profile, self.revision)[0]

    def test_object_tables_read_what_the_game_uses(self) -> None:
        rooms = self.resources["world1-underground-rooms"]["records"]
        self.assertEqual(9, len(rooms))
        # Room 3 leads back to city return 2 both ways; room 1 only one way.
        self.assertEqual((2, 2), (rooms[2]["negative_axis_city_return"], rooms[2]["positive_axis_city_return"]))
        self.assertIsNone(rooms[0]["positive_axis_city_return"])
        states = self.resources["world2-enemy-states"]["records"]
        # State $14's attack period is the byte state $00's damage threshold
        # starts on.
        self.assertEqual(states[20]["attack_period"], states[0]["damage_threshold"])
        rooms = self.resources["world3-rooms"]["records"]
        self.assertEqual(8, sum(room["type04_tracking"] for room in rooms))
        self.assertEqual(23, sum(room["type05_blocked"] for room in rooms))
        motion = self.resources["world3-type05-motion"]["records"]
        self.assertEqual([(2, 2), (-2, 2), (2, -2), (-2, -2)], [(m["delta_x"], m["delta_y"]) for m in motion])

    def test_edits_land_on_their_bytes(self) -> None:
        resources = copy.deepcopy(self.resources)
        resources["world2-enemy-states"]["records"][3]["damage_threshold"] = 0x10
        routing = resources["world1-effect-routing"]["records"]
        routing[1]["effect"], routing[2]["effect"] = routing[2]["effect"], routing[1]["effect"]
        built = self.build(resources)
        damage = 16 + 0x8000 + (0xA511 - 0x8000)
        self.assertEqual(0x10, built[damage + 3])
        route = 16 + (0xE316 - 0x8000)
        self.assertEqual(self.source[route + 1:route + 3][::-1], built[route + 1:route + 3])
        self.assertEqual(sum(a != b for a, b in zip(self.source, built)), 3)
        # Two requests cannot play one effect, and request 0 stays the reset.
        routing[3]["effect"] = routing[4]["effect"]
        with self.assertRaises(AuthoringError):
            self.build(resources)

    def test_behavior_streams_keep_their_shape(self) -> None:
        resource = self.resources["world3-behavior"]
        streams = resource["streams"]
        self.assertEqual(532, sum(len(stream["instructions"]) for stream in streams))
        self.assertEqual(42, sum(item["command"] == "conditional_branch"
                                 for stream in streams for item in stream["instructions"]))
        sizes = next(item for item in self.profile.resources if item.resource_id == "world3-behavior").parameters["stream_sizes"]
        payload = encode_behavior(streams, sizes)
        self.assertEqual(streams, decode_behavior(payload, sizes))
        # A rate's parameter nibble is free.
        edited = copy.deepcopy(streams)
        first = edited[0]["instructions"][0]
        self.assertEqual("set_rate", first["command"])
        first["opcode"] = 0x65
        self.assertEqual(0x65, encode_behavior(edited, sizes)[0])
        # A branch into an operand, or a new command, is refused.
        edited = copy.deepcopy(streams)
        branch = edited[0]["instructions"][1]
        self.assertEqual("conditional_branch", branch["command"])
        branch["operands"][1] = 2
        with self.assertRaises(DoraemonBehaviorError):
            encode_behavior(edited, sizes)
        edited = copy.deepcopy(streams)
        edited[0]["instructions"][0]["opcode"] = 0x72
        with self.assertRaises(DoraemonBehaviorError):
            encode_behavior(edited, sizes)


if __name__ == "__main__":
    unittest.main()
