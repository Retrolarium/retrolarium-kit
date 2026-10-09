from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "retrolarium-core" / "python"))

from retrolarium_kit.games.doraemon.music import (
    DoraemonMusicError, build_music, decode_music, parse_music_document, trace_track,
)
from retrolarium_kit.core.game_profiles import packaged_game_profiles
from retrolarium_core.ines import parse_ines
from helpers import game_repository


# The cartridge image is not part of the repository; the test runs where the
# disassembly sits beside this checkout.
ROM = game_repository("doraemon_src") / "Doraemon (J) (PRG0) [!].nes"


@unittest.skipUnless(ROM.is_file(), "the Doraemon ROM is not available")
class DoraemonMusicTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = ROM.read_bytes()
        self.profile = packaged_game_profiles().get("doraemon")
        self.revision = self.profile.revisions[0]

    def test_round_trip_edit_and_trace(self) -> None:
        document = decode_music(self.source, self.profile, self.revision)
        self.assertEqual(self.source, build_music(self.source, document, self.profile, self.revision)[0])
        value = copy.deepcopy(document.as_dict())
        shell = value["drivers"][3]
        events = shell["segments"][0]["events"]
        at = next(i for i, text in enumerate(events) if text.startswith("event $") and text != "event $00")
        old = int(events[at].split("$")[1], 16)
        events[at] = f"event ${old + 1:02X}"
        output, report = build_music(self.source, parse_music_document(value, self.profile, self.revision),
                                     self.profile, self.revision)
        self.assertEqual(1, report["changed_bytes"])
        rebuilt = decode_music(output, self.profile, self.revision, _verify_identity=False)
        self.assertEqual(events[at], rebuilt.drivers[3]["segments"][0]["events"][at])
        trace = trace_track(value, parse_ines(self.source).prg, 3, 0, 120)
        self.assertEqual(["Pulse 1", "Pulse 2", "Triangle", "Noise"], [lane["name"] for lane in trace["lanes"]])
        self.assertTrue(trace["lanes"][0]["events"])

    def test_events_keep_their_width(self) -> None:
        value = copy.deepcopy(decode_music(self.source, self.profile, self.revision).as_dict())
        events = value["drivers"][3]["segments"][0]["events"]
        at = next(i for i, text in enumerate(events) if text.startswith("event $"))
        events[at] = "CallStream $00 $90"
        with self.assertRaises(DoraemonMusicError):
            parse_music_document(value, self.profile, self.revision)


if __name__ == "__main__":
    unittest.main()
