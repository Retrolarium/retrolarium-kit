from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from retrolarium_kit.games.pacman.actor import (
    ALTERNATE_FRAMES,
    ENCODED_SIZE,
    STANDARD_FRAMES,
    PacmanActorError,
    decode_actor_mapping,
    encode_actor_mapping,
)


def document() -> dict[str, object]:
    return {
        "standard_tile_quads": [[index & 0xFF] * 4 for index in range(STANDARD_FRAMES)],
        "alternate_tile_quads": [[index & 0xFF] * 4 for index in range(ALTERNATE_FRAMES)],
        "standard_attribute_quads": [[0, 1, 2, 3] for _ in range(STANDARD_FRAMES)],
        "alternate_attribute_quads": [[0x40, 0x80, 0xC0, 0] for _ in range(ALTERNATE_FRAMES)],
        "oam_offsets_yx": [[0, 0], [0, 8], [8, 0], [8, 8]],
    }


class PacmanActorTests(unittest.TestCase):
    def test_all_77_frames_round_trip_in_fixed_arena(self) -> None:
        value = document()
        payload = encode_actor_mapping(value)
        self.assertEqual(len(payload), ENCODED_SIZE)
        self.assertEqual(decode_actor_mapping(payload), value)

    def test_frame_edit_changes_only_its_tile_and_attribute_bytes(self) -> None:
        original = encode_actor_mapping(document())
        edited = document()
        edited["standard_tile_quads"][5][2] = 0xA5
        edited["standard_attribute_quads"][5][2] = 0xC3
        payload = encode_actor_mapping(edited)
        differences = [index for index, pair in enumerate(zip(original, payload)) if pair[0] != pair[1]]
        self.assertEqual(differences, [5 * 4 + 2, 256 + 52 + 5 * 4 + 2])

    def test_rejects_reserved_attribute_bits_and_non_quad_offsets(self) -> None:
        invalid = copy.deepcopy(document())
        invalid["standard_attribute_quads"][0][0] = 0x04
        with self.assertRaisesRegex(PacmanActorError, "unsupported"):
            encode_actor_mapping(invalid)
        invalid = copy.deepcopy(document())
        invalid["oam_offsets_yx"][3] = [9, 8]
        with self.assertRaisesRegex(PacmanActorError, "16x16"):
            encode_actor_mapping(invalid)


if __name__ == "__main__":
    unittest.main()
