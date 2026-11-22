"""TileCodec_DecodeTile on synthetic tiles (no ROM needed)."""

from __future__ import annotations

from pathlib import Path
import random
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from retrolarium_kit.core.bounded_cache import BoundedCache
from retrolarium_kit.games.alien_soldier import lzss, tile_codec
from retrolarium_kit.games.alien_soldier.tile_codec import TILE_BYTES, TileCodecError, decompress, fit


def tile(pixels: list[int]) -> bytes:
    return tile_codec._pack(pixels)


def encode(tiles: list[bytes]) -> bytes:
    """A payload as plain runs, the way fit writes a changed tile."""
    writer = tile_codec._Writer()
    for data in tiles:
        tile_codec._runs(writer, tile_codec._unpack(data))
    return writer.payload(len(tiles))


SOLID = tile([3] * 64)
STRIPES = tile([(index // 8) % 16 for index in range(64)])
CHECKER = tile([(index + index // 8) % 2 * 9 for index in range(64)])


class TileCodecTests(unittest.TestCase):
    def test_plain_runs_decode_to_their_tiles(self) -> None:
        tiles = [SOLID, STRIPES, CHECKER]
        payload = encode(tiles)
        self.assertEqual(len(tiles) - 1, payload[0])
        self.assertEqual(0, len(payload) % 2)
        self.assertEqual(b"".join(tiles), decompress(payload))

    def test_unchanged_tiles_keep_the_original_payload(self) -> None:
        original = encode([STRIPES, CHECKER])
        self.assertIs(original, fit(decompress(original), original))

    def test_a_changed_tile_round_trips_and_keeps_its_neighbours_bits(self) -> None:
        original = encode([STRIPES, CHECKER, SOLID])
        edited = bytearray(decompress(original))
        edited[TILE_BYTES * 2 : TILE_BYTES * 3] = tile([5] * 64)  # recoloured, same shape
        payload = fit(bytes(edited), original)
        self.assertEqual(len(original), len(payload))
        self.assertEqual(bytes(edited), decompress(payload))
        # The two unchanged tiles keep their bits (all but the padded tail).
        kept = len(encode([STRIPES, CHECKER])) - 2
        self.assertEqual(original[1:kept], payload[1:kept])

    def test_repeated_tiles_round_trip(self) -> None:
        original = encode([CHECKER] * 4)
        edited = tile([1] * 32 + [2] * 32) * 4
        self.assertEqual(edited, decompress(fit(edited, original)))

    def test_random_tiles_round_trip_when_they_fit(self) -> None:
        generator = random.Random(1234)
        for _ in range(20):
            tiles = [tile([generator.choice((0, 1, 7)) for _ in range(64)]) for _ in range(3)]
            original = encode(tiles)
            self.assertEqual(b"".join(tiles), decompress(original))
            shuffled = b"".join(reversed(tiles))
            self.assertEqual(shuffled, decompress(fit(shuffled, original)))

    def test_empty_input_is_refused(self) -> None:
        for payload in (b"", b"\x00"):
            with self.subTest(payload=payload), self.assertRaises(TileCodecError):
                decompress(payload)
        with self.assertRaisesRegex(TileCodecError, "holds 1 tiles"):
            fit(b"", encode([SOLID]))

    def test_a_stream_that_runs_out_is_refused(self) -> None:
        with self.assertRaisesRegex(TileCodecError, "runs out"):
            decompress(encode([STRIPES])[:4])

    def test_tiles_that_exceed_the_slot_are_refused(self) -> None:
        original = encode([SOLID, SOLID])
        noisy = random.Random(7)
        edited = b"".join(tile([noisy.randrange(16) for _ in range(64)]) for _ in range(2))
        with self.assertRaisesRegex(TileCodecError, "the slot holds"):
            fit(edited, original)
        with self.assertRaisesRegex(TileCodecError, "holds 2 tiles"):
            fit(SOLID, original)


class BoundedCacheTests(unittest.TestCase):
    def test_results_are_kept_within_the_byte_budget(self) -> None:
        cache = BoundedCache(10)
        computed = []

        def make(value: bytes):
            return lambda: computed.append(value) or value

        self.assertEqual(b"aaaa", cache.get(b"1", None, make(b"aaaa")))
        self.assertEqual(b"aaaa", cache.get(b"1", None, make(b"xxxx")))
        cache.get(b"2", None, make(b"bbbb"))
        cache.get(b"1", None, make(b"aaaa"))  # used: 2 is now the oldest
        cache.get(b"3", None, make(b"cccc"))
        self.assertEqual((2, 8), (len(cache), cache.size))
        self.assertEqual(b"aaaa", cache.get(b"1", None, make(b"zzzz")))
        self.assertEqual(b"bbbb", cache.get(b"2", None, make(b"bbbb")))
        self.assertEqual([b"aaaa", b"bbbb", b"cccc", b"bbbb"], computed)
        cache.get(b"4", None, make(b"too large to keep"))
        self.assertLessEqual(cache.size, 10)

    def test_the_codecs_key_by_hash_and_bound_their_caches(self) -> None:
        for module in (lzss, tile_codec):
            with self.subTest(module=module.__name__):
                self.assertIsInstance(module._CACHE, BoundedCache)
                self.assertLessEqual(module._CACHE.max_bytes, 32 << 20)
        data = bytes(range(256)) * 8
        payload = lzss.compress(data)
        lzss._CACHE.clear()
        self.assertEqual(lzss.decompress(payload), lzss.decompress(bytearray(payload)))
        self.assertEqual(1, len(lzss._CACHE))
        self.assertEqual(data, lzss.decompress(payload, blocks=True))
        self.assertEqual(2, len(lzss._CACHE))


if __name__ == "__main__":
    unittest.main()
