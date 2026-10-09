from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from retrolarium_kit.games.doraemon.demo import (
    DoraemonDemoError, build_demo, capacity, decode_demo, parse_demo_document,
)
from retrolarium_kit.core.game_profiles import packaged_game_profiles


# The cartridge image is not part of the repository; the test runs where the
# disassembly sits beside this checkout.
ROM = Path(__file__).resolve().parents[3] / "oranguthang/doraemon_src/Doraemon (J) (PRG0) [!].nes"
ROM = ROM if ROM.is_file() else Path("D:/Projects/oranguthang/doraemon_src/Doraemon (J) (PRG0) [!].nes")
STREAM = 16 + 0xFCBF - 0x8000


@unittest.skipUnless(ROM.is_file(), "the Doraemon ROM is not available")
class DoraemonDemoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = ROM.read_bytes()
        self.profile = packaged_game_profiles().get("doraemon")
        self.revision = self.profile.revisions[0]

    def build(self, value: dict) -> bytes:
        return build_demo(self.source, parse_demo_document(value, self.profile, self.revision),
                          self.profile, self.revision)[0]

    def test_decodes_the_stream_and_builds_it_back(self) -> None:
        document = decode_demo(self.source, self.profile, self.revision)
        self.assertEqual(50, len(document.steps))
        self.assertEqual({"index": 0, "frames": 21, "buttons": []}, document.steps[0])
        self.assertEqual({"index": 1, "frames": 1, "buttons": ["b", "down"]}, document.steps[1])
        self.assertEqual(256, document.steps[-1]["frames"])
        self.assertEqual(412, capacity(document.layout))
        self.assertEqual(self.source, self.build(document.as_dict()))

    def test_edits_grow_and_shrink_the_stream(self) -> None:
        value = copy.deepcopy(decode_demo(self.source, self.profile, self.revision).as_dict())
        value["steps"][0] = {"index": 0, "frames": 30, "buttons": ["right", "a"]}
        value["steps"].append({"index": 50, "frames": 256, "buttons": ["left"]})
        output = self.build(value)
        self.assertEqual(bytes((30, 0x81)), output[STREAM:STREAM + 2])
        self.assertEqual(bytes((0, 0x02, 0xFF, 0xFF)), output[STREAM + 100:STREAM + 104])
        rebuilt = decode_demo(output, self.profile, self.revision, _verify_identity=False)
        self.assertEqual(["a", "right"], rebuilt.steps[0]["buttons"])
        value["steps"] = value["steps"][:3]
        output = self.build(value)
        self.assertEqual(b"\xFF" * 100, output[STREAM + 6:STREAM + 106])

    def test_rejects_what_the_stream_cannot_hold(self) -> None:
        value = decode_demo(self.source, self.profile, self.revision).as_dict()
        for steps in (
            [],
            [{"index": 0, "frames": 257, "buttons": []}],
            [{"index": 0, "frames": 1, "buttons": ["start"]}],
            [{"index": i, "frames": 1, "buttons": []} for i in range(413)],
        ):
            with self.assertRaises(DoraemonDemoError):
                parse_demo_document({**value, "steps": steps}, self.profile, self.revision)


if __name__ == "__main__":
    unittest.main()
