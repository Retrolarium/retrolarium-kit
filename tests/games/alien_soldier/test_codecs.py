from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from retrolarium_kit.games.alien_soldier import content, demos, lzss, palettes, pcm


def noise(length: int, seed: int = 1) -> bytes:
    """Deterministic bytes with little to match (a linear congruence)."""
    out, value = bytearray(), seed
    for _ in range(length):
        value = (value * 1103515245 + 12345) & 0x7FFFFFFF
        out.append(value >> 16 & 0xFF)
    return bytes(out)


class LzssTests(unittest.TestCase):
    def test_every_token_kind_round_trips(self) -> None:
        samples = [
            bytes(0x1000),  # runs of one byte
            b"\x12\x34" * 0x300,  # runs of a pair
            bytes(value for index in range(0x200) for value in (0x11, index & 0xFF)),  # a fixed byte between
            noise(0x900),  # literals
            (noise(0x40) + b"\xaa" * 9) * 30,  # matches
            b"\x01",
        ]
        for data in samples:
            payload = lzss.compress(data)
            self.assertEqual(len(payload) % 2, 0)
            self.assertEqual(lzss.decompress(payload), data)
            self.assertEqual(lzss.decompress(payload, blocks=True), data)

    def test_matches_never_reach_into_an_earlier_block(self) -> None:
        # The VRAM loader restarts its output every $400 bytes.
        data = noise(0x400) * 3
        self.assertEqual(lzss.decompress(lzss.compress(data), blocks=True), data)

    def test_a_ram_stream_matches_across_blocks(self) -> None:
        # The RAM loaders go on writing where the last block stopped.
        data = noise(0x400) * 3
        whole = lzss.compress(data, blocks=False)
        self.assertEqual(lzss.decompress(whole), data)
        self.assertLess(len(whole), len(lzss.compress(data)) // 2)
        original = whole + bytes(0x20)
        edited = data[:-1] + b"\x00"
        self.assertEqual(lzss.decompress(lzss.fit(edited, original, blocks=False)), edited)
        with self.assertRaises(lzss.LzssError):
            lzss.fit(edited, original)

    def test_fit_keeps_an_unchanged_payload_and_pads_a_new_one(self) -> None:
        original = lzss.compress(noise(0x300)) + bytes(0x40)
        self.assertEqual(lzss.fit(noise(0x300), original), original)
        smaller = lzss.fit(bytes(0x300), original)
        self.assertEqual(len(smaller), len(original))
        self.assertEqual(lzss.decompress(smaller), bytes(0x300))
        with self.assertRaises(lzss.LzssError):
            lzss.fit(noise(0x800, seed=7), original)


class PaletteTests(unittest.TestCase):
    def test_commands_round_trip(self) -> None:
        commands = [
            {"destination": 0x00, "colours": [0x0000, 0x0EEE, 0x0224]},
            {"destination": 0x40, "colours": [0x0A08]},
        ]
        data = palettes.encode(commands)
        self.assertEqual(data, bytes.fromhex("00020000 0EEE0224 4000 0A08".replace(" ", "")))
        read = palettes.read_commands(data, "test")
        self.assertEqual([{k: v for k, v in item.items() if k != "offset"} for item in read], commands)

    def test_a_command_past_the_colour_ram_is_refused(self) -> None:
        with self.assertRaises(palettes.PaletteError):
            palettes.read_commands(bytes((0x7E, 0x01, 0, 0, 0, 0)), "test")


# Delta pages of the same shape as the Z80's: powers of two up, then down.
STEPS = [0, 1, 2, 4, 8, 16, 32, 64, 128, 255, 254, 252, 248, 240, 224, 192]
HIGH = [STEPS[byte >> 4] for byte in range(256)]
LOW = [STEPS[byte & 15] for byte in range(256)]


class PcmTests(unittest.TestCase):
    def test_decoded_levels_encode_to_the_same_bytes(self) -> None:
        data = noise(0x200, seed=3)
        levels = pcm.decode(data, HIGH, LOW)
        self.assertEqual(len(levels), 2 * len(data))
        self.assertEqual(pcm.encode(levels, HIGH, LOW), data)

    def test_a_bank_lists_its_samples_end_to_end(self) -> None:
        bank = bytes((8, 0x80, 3, 0, 11, 0x80, 2, 0)) + b"abcde" + b"\xff"
        self.assertEqual(pcm.samples(bank), [(8, 3), (11, 2)])
        with self.assertRaises(pcm.PcmError):
            pcm.samples(bytes((8, 0, 3, 0, 12, 0, 2, 0)) + bytes(8))


class DemoTests(unittest.TestCase):
    def test_steps_round_trip_and_end_with_the_demo(self) -> None:
        steps = [
            {"held": 0, "pressed": 0, "frames": 30},
            {"held": 0x80, "pressed": 0x80, "frames": 1},
            {"held": 0x08, "pressed": 0x08, "frames": demos.DEMO_FRAMES},
        ]
        data = demos.encode(steps, 32)
        self.assertEqual(len(data), 32)
        self.assertEqual(demos.read_steps(data), steps)
        with self.assertRaises(demos.DemoError):
            demos.encode(steps * 4, 16)

    def test_a_recording_follows_the_demo_s_own_start(self) -> None:
        steps = [
            {"held": 0, "pressed": 0, "frames": 10},
            {"held": 0x80, "pressed": 0x80, "frames": 5},
            {"held": 0, "pressed": 0, "frames": 100},
        ]
        recording = "prefix 12\nstep 4 8 8\nstep 20 40 32\nend\n"
        self.assertEqual(
            demos.recorded_steps(steps, recording),
            [
                {"held": 0, "pressed": 0, "frames": 10},
                {"held": 0x80, "pressed": 0x80, "frames": 2},
                {"held": 8, "pressed": 8, "frames": 4},
                {"held": 40, "pressed": 32, "frames": 20},
            ],
        )
        with self.assertRaises(demos.DemoError):
            demos.recorded_steps(steps, "step 1 0 0\n")


class PayloadUpgradeTests(unittest.TestCase):
    """An older project's document follows the payloads this kit reads."""

    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp())
        self.original = self.folder / "maps.json"
        self.exported = {"schema": "s", "payloads": [{"label": "A", "v": 1}, {"label": "C", "v": 3}]}

    def tearDown(self) -> None:
        for path in self.folder.iterdir():
            path.unlink()
        self.folder.rmdir()

    def write_original(self, payloads: list[dict]) -> None:
        self.original.write_text(json.dumps({"schema": "s", "payloads": payloads}), encoding="utf-8")

    def test_an_unedited_payload_goes_and_a_new_one_comes(self) -> None:
        self.write_original([{"label": "A", "v": 1}, {"label": "B", "v": 2}])
        mine = {"schema": "s", "payloads": [{"label": "A", "v": 9}, {"label": "B", "v": 2}]}
        kept = content._follow_payloads(mine, self.exported, self.original)
        self.assertEqual(kept["payloads"], [{"label": "A", "v": 9}, {"label": "C", "v": 3}])
        original = json.loads(self.original.read_text(encoding="utf-8"))
        self.assertEqual(original["payloads"], [{"label": "A", "v": 1}, {"label": "C", "v": 3}])

    def test_an_edited_payload_that_went_is_refused(self) -> None:
        self.write_original([{"label": "A", "v": 1}, {"label": "B", "v": 2}])
        mine = {"schema": "s", "payloads": [{"label": "A", "v": 1}, {"label": "B", "v": 5}]}
        with self.assertRaises(content.AlienSoldierContentError):
            content._follow_payloads(mine, self.exported, self.original)

    def test_derived_keys_follow_in_the_original_copy_too(self) -> None:
        self.original.write_text(json.dumps({"screens": ["old"], "payloads": [1]}), encoding="utf-8")
        mine = {"screens": ["old"], "payloads": [2]}
        kept = content._follow_derived(mine, {"screens": ["new"], "payloads": []}, ("screens",), self.original)
        self.assertEqual(kept, {"screens": ["new"], "payloads": [2]})
        original = json.loads(self.original.read_text(encoding="utf-8"))
        # The original reads the same as the document but for the edit, so
        # the edit is still the only difference.
        self.assertEqual(original, {"screens": ["new"], "payloads": [1]})
        self.assertIsNone(content._follow_derived(kept, {"screens": ["new"]}, ("screens",), self.original))

    def test_a_current_document_is_left(self) -> None:
        mine = {"schema": "s", "payloads": [{"label": "A", "v": 1}, {"label": "C", "v": 7}]}
        self.assertIsNone(content._follow_payloads(mine, self.exported, self.original))


if __name__ == "__main__":
    unittest.main()
