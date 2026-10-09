from __future__ import annotations

import copy
import unittest

from retrolarium_kit.games.smb.player_frames import (
    ENCODED_SIZE,
    FRAME_COUNT,
    FRAME_NAMES,
    SmbPlayerFrameError,
    TILES_PER_FRAME,
    decode_player_frames,
    encode_player_frames,
)


def synthetic_payload() -> bytes:
    offsets = bytes(index * TILES_PER_FRAME for index in range(16))
    frames = bytes(
        (frame * TILES_PER_FRAME + tile) & 0xFF
        for frame in range(FRAME_COUNT)
        for tile in range(TILES_PER_FRAME)
    )
    return offsets + frames + bytes((0x31, 0x46))


class SmbPlayerFrameTests(unittest.TestCase):
    def test_round_trip_preserves_fixed_engine_layout(self) -> None:
        payload = synthetic_payload()
        self.assertEqual(ENCODED_SIZE, len(payload))
        document = decode_player_frames(
            payload, {"tile_base": 0, "blank_tile": 0xFC}
        )
        self.assertEqual(list(FRAME_NAMES), document["frame_names"])
        self.assertEqual(26, len(document["frames"]))
        self.assertEqual(payload, encode_player_frames(
            document, {"tile_base": 0, "blank_tile": 0xFC}
        ))

    def test_known_frame_edit_changes_only_one_byte(self) -> None:
        original = synthetic_payload()
        document = decode_player_frames(original, {})
        edited = copy.deepcopy(document)
        edited["frames"][4][3] = 0xEA
        payload = encode_player_frames(edited, {})
        changed = [
            index for index, pair in enumerate(zip(original, payload))
            if pair[0] != pair[1]
        ]
        self.assertEqual([16 + 4 * 8 + 3], changed)

    def test_rejects_offset_between_frames(self) -> None:
        document = decode_player_frames(synthetic_payload(), {})
        document["frame_offsets"][0] = 7
        with self.assertRaisesRegex(SmbPlayerFrameError, "must select"):
            encode_player_frames(document, {})

    def test_rejects_out_of_range_tile(self) -> None:
        document = decode_player_frames(synthetic_payload(), {})
        document["frames"][0][0] = 0x100
        with self.assertRaisesRegex(SmbPlayerFrameError, "must be a byte"):
            encode_player_frames(document, {})

    def test_rejects_profile_metadata_change(self) -> None:
        document = decode_player_frames(synthetic_payload(), {"tile_base": 1})
        document["tile_base"] = 0
        with self.assertRaisesRegex(SmbPlayerFrameError, "metadata differs"):
            encode_player_frames(document, {"tile_base": 1})


if __name__ == "__main__":
    unittest.main()
