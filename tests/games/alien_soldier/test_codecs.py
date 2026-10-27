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


class CaptureTests(unittest.TestCase):
    """The capture script's lines read as fights of frames."""

    def test_a_fight_starts_whole_and_goes_on_in_changes(self) -> None:
        import base64

        from retrolarium_kit.games.alien_soldier import capture

        chunk = "11" * 32
        record = "00F4" + "00" * 94
        lines = [
            "B F4",
            "F 0",
            f"V 2 {chunk}",
            "C " + "0EEE" * 64,
            "S " + "0000" * 40,
            "R " + "00" * 24,
            f"O 0 {record}",
            "F 1",
            f"V 3 {chunk}",
            "E done",
        ]
        fights, end = capture.parse("\n".join(lines))
        self.assertEqual((end, len(fights), fights[0]["type"]), ("done", 1, 0xF4))
        first, second = fights[0]["frames"]
        whole = base64.b64decode(first["vram_all"])
        # The first frame holds all of VRAM, the chunks it wrote in place.
        self.assertEqual((len(whole), whole[64:96], whole[:64]), (0x10000, bytes([0x11]) * 32, bytes(64)))
        self.assertEqual(first["cram"][0], 0xEEE)
        self.assertEqual(base64.b64decode(first["objects"][0]["record"])[:2], bytes([0, 0xF4]))
        # The next holds only what changed.
        self.assertEqual(list(second["vram"]), ["3"])
        self.assertNotIn("cram", second)

    def test_a_gens_run_keeps_the_fight_scenes(self) -> None:
        import struct

        from retrolarium_kit.games.alien_soldier import tas_capture

        listing = "Frames, Scene\n20-300, Sega screen\n900-1900, Jetsripper boss\n33500-34220, Viblack mini-boss\n"
        listing += "36840-38000, Valkirie Force\n76840-77600, Antroid (credits)\n17820-18660, Xi-Tiger cutscene\n"
        self.assertEqual(
            tas_capture.scenes(listing),
            [(900, 1900, "Jetsripper boss"), (33500, 34220, "Viblack mini-boss"), (36840, 38000, "Valkirie Force")],
        )
        # States one by one, as Gens writes them: the boss's type from the
        # first record that holds one, VRAM in changes after the first.
        pool = (0xFFC620, 0xFFC620 + 2 * 0x60)

        def state(kind: int, tile: int) -> dict[str, bytes]:
            ram = bytearray(0x10000)
            ram[0xC620:0xC622] = kind.to_bytes(2, "big")
            vram = bytearray(0x10000)
            vram[32:64] = bytes([tile]) * 32
            return {
                "ram": bytes(ram),
                "vram": bytes(vram),
                "cram": struct.pack("<64H", *([0xEEE] * 64)),
                "vsram": bytes(80),
                "regs": bytes(24),
            }

        fight = tas_capture._Fight({0xE8}, pool)
        for kind, tile in ((0, 1), (0xE8, 1), (0xE8, 2)):
            fight.add(state(kind, tile))
        done = fight.result()
        self.assertEqual((done["type"], len(done["frames"])), (0xE8, 3))
        self.assertIn("vram_all", done["frames"][0])
        self.assertEqual((done["frames"][1]["vram"], list(done["frames"][2]["vram"])), ({}, ["1"]))


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

    def test_a_boss_read_anew_brings_its_poses_and_animations(self) -> None:
        from retrolarium_kit.games.alien_soldier import bosses

        exported = {
            "bosses": [{"poses": "Old", "sequences": ["OldRun"]}, {"poses": "New", "sequences": ["NewRun"]}],
            "poses": {"Old": [{"offset": 0, "values": [1]}], "New": [{"offset": 0, "values": [2]}]},
            "sequences": [
                {"label": "OldRun", "commands": [{"pose": 0, "frames": 1, "steps": 1}], "end": "loop"},
                {"label": "NewRun", "commands": [{"pose": 0, "frames": 3, "steps": 3}], "end": "hold"},
            ],
        }
        self.original.write_text(
            json.dumps(
                {"bosses": [], "poses": {"Old": exported["poses"]["Old"]}, "sequences": exported["sequences"][:1]}
            ),
            encoding="utf-8",
        )
        edited = [{"offset": 0, "values": [9]}]
        mine = {"bosses": [], "poses": {"Old": edited}, "sequences": exported["sequences"][:1]}
        kept = content._follow_derived(mine, exported, ("bosses",), self.original, bosses.follow_source)
        # The edit stays; the new boss's table and animation come in, in the
        # original copy too.
        self.assertEqual(kept["poses"], {"Old": edited, "New": exported["poses"]["New"]})
        self.assertEqual(kept["sequences"], exported["sequences"])
        original = json.loads(self.original.read_text(encoding="utf-8"))
        self.assertEqual(original["poses"], exported["poses"])
        self.assertIsNone(content._follow_derived(kept, exported, ("bosses",), self.original, bosses.follow_source))

    def test_a_current_document_is_left(self) -> None:
        mine = {"schema": "s", "payloads": [{"label": "A", "v": 1}, {"label": "C", "v": 7}]}
        self.assertIsNone(content._follow_payloads(mine, self.exported, self.original))


if __name__ == "__main__":
    unittest.main()
