"""Movies: FCEUX and Gens recordings, the neutral format, and runs in MAME."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import tempfile
import unittest

import helpers  # noqa: F401  (src/ on sys.path)
from retrolarium_kit.core import movies
from retrolarium_kit.core.movies import Cell, Check, Frame, Movie, MovieError

FM2 = """version 3
emuVersion 22020
palFlag 0
fourscore 0
port0 1
port1 0
|2|........|||
|0|.......A|||
|0|R......A|||
|1|........|||
|0|...U....|||
"""


def gmv(frames: list[tuple[int, int]], pad_one: bytes = b"3") -> bytes:
    header = bytearray(64)
    header[0:15] = b"Gens Movie TEST"
    header[0x14:0x16] = pad_one + b"3"
    return bytes(header) + b"".join(bytes((one, two, 0xFF)) for one, two in frames)


# A homebrew NROM image (no game data): its NMI reads controller one and
# stores the eight buttons, A first in bit 7, at $10, then counts frames at
# $11. Reset waits two vblanks and enables NMI.
_PROGRAM = bytes.fromhex(
    "78 A2 FF 9A 2C 02 20 10 FB 2C 02 20 10 FB A9 80 8D 00 20 4C 13 C0"  # C000 reset
    "A9 01 8D 16 40 A9 00 8D 16 40 A2 08 AD 16 40 4A 26 12 CA D0 F7"     # C016 nmi
    "A5 12 85 10 E6 11 40")                                               # C02B


def probe_rom() -> bytes:
    prg = bytearray(0x4000)
    prg[:len(_PROGRAM)] = _PROGRAM
    prg[0x3FFA:0x4000] = bytes.fromhex("16 C0 00 C0 31 C0")
    return b"NES\x1a" + bytes((1, 1, 0, 0)) + bytes(8) + bytes(prg) + bytes(0x2000)


def reversed_bits(value: int) -> int:
    return int(f"{value:08b}"[::-1], 2)


class RecordingTests(unittest.TestCase):
    def test_fm2_pads_and_commands(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "movie.fm2"
            path.write_text(FM2, encoding="utf-8")
            movie = movies.read_fm2(path)
        self.assertEqual("nes", movie.platform)
        self.assertEqual([Frame(0, 0, "power"), Frame(0x01, 0), Frame(0x81, 0), Frame(0, 0, "reset"), Frame(0x10, 0)],
                         movie.frames)
        self.assertTrue(movie.source.startswith("fm2 "))

    def test_gmv_pads_are_active_low(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "movie.gmv"
            # Up and Start held, then A, C and Right on pad one.
            path.write_bytes(gmv([(0xFF & ~0x01 & ~0x80, 0xFF), (0xFF & ~0x10 & ~0x40 & ~0x08, 0xFF)]))
            movie = movies.read_gmv(path)
            self.assertEqual([Frame(0x81, 0), Frame(0x40 | 0x20 | 0x08, 0)], movie.frames)
            path.write_bytes(gmv([(0xFF, 0xFF)], pad_one=b"6"))
            with self.assertRaisesRegex(MovieError, "6-button"):
                movies.read_gmv(path)

    def test_the_neutral_format_keeps_every_frame(self) -> None:
        movie = Movie("nes", [Frame(0, 0, "power"), Frame(1, 0), Frame(1, 0), Frame(1, 0, "reset"), Frame(0, 2)],
                      "fm2 x", offset=1)
        self.assertEqual(4, len(movie.runs()))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "movie.rlmovie"
            movies.write_rlmovie(movie, path)
            again = movies.read_rlmovie(path)
        self.assertEqual((movie.platform, movie.frames, movie.source, movie.offset),
                         (again.platform, again.frames, again.source, again.offset))

    def test_bad_runs_are_refused_before_frames_are_made(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "movie.rlmovie"
            for run in ("999999999999 00 00", "0 00 00", "x 00 00", "1 100 00"):
                path.write_text(f"# retrolarium movie 1\nplatform nes\n---\n{run}\n", encoding="utf-8")
                with self.assertRaises(MovieError, msg=run):
                    movies.read_rlmovie(path)
            path.write_text("# retrolarium movie 1\nplatform nes\noffset two\n---\n1 00 00\n", encoding="utf-8")
            with self.assertRaises(MovieError):
                movies.read_rlmovie(path)

    def test_a_movie_ported_to_mame_keeps_the_recording_emulators_power_on_ram(self) -> None:
        self.assertEqual(movies.POWER_ON["fm2"], movies.power_on(Movie("nes", [], "fm2 FCEUX 2.1.3")))
        self.assertEqual(movies.POWER_ON["fm2"], movies.power_on(Movie("nes", [], "mame from fm2 TASVideos 1795M")))
        self.assertEqual((0, 0), movies.alignment(Movie("nes", [], "mame from fm2")))
        self.assertIsNone(movies.power_on(Movie("nes", [], "mame")))
        self.assertIsNone(movies.power_on(Movie("nes", [], "")))

    def test_a_movie_keeps_its_description_and_a_studio_movie_needs_no_alignment(self) -> None:
        movie = Movie("nes", [Frame(1, 0)] * 3, "mame",
                      notes={"title": "All rooms", "kind": "longplay", "author": "D.", "comment": "dropped"})
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            movies.write_rlmovie(movie, folder / "all-rooms.rlmovie")
            again = movies.read_rlmovie(folder / "all-rooms.rlmovie")
            (folder / "tas.fm2").write_text(FM2, encoding="utf-8")
            (folder / "tas.check.json").write_text('{"movie": "tas.fm2", "title": "Any%", "kind": "tas"}',
                                                   encoding="utf-8")
            listed = {item["file"]: item for item in movies.describe_movies(folder)}
            recorded = movies.read_fm2(folder / "tas.fm2")
        self.assertEqual({"title": "All rooms", "kind": "longplay", "author": "D."}, again.notes)
        self.assertEqual((0, 0), movies.alignment(again))
        self.assertEqual(movies.ALIGNMENT["nes"], movies.alignment(recorded))
        self.assertEqual(("All rooms", "mame", 3), (listed["all-rooms.rlmovie"]["title"],
                                                    listed["all-rooms.rlmovie"]["source"],
                                                    listed["all-rooms.rlmovie"]["frames"]))
        self.assertEqual(("Any%", "tas", True), (listed["tas.fm2"]["title"], listed["tas.fm2"]["kind"],
                                                 listed["tas.fm2"]["checked"]))

    def test_snapshots_compare_cell_by_cell(self) -> None:
        expected = {"100": {"lives": "03"}, "end": {"lives": "01", "score": "0120"}}
        self.assertEqual([], movies.compare(expected, {"100": {"lives": "03"}, "end": {"lives": "01", "score": "0120"}}))
        self.assertEqual(["frame end: score is 0100, expected 0120"],
                         movies.compare(expected, {"100": {"lives": "03"}, "end": {"lives": "01", "score": "0100"}}))

    def test_a_tolerance_lets_a_number_trail_a_little(self) -> None:
        x = Cell("x", 0x38, 2, tolerance=8, endian="little")
        expected = {"100": {"x": "1c01"}}
        self.assertEqual([], movies.compare(expected, {"100": {"x": "1601"}}, (x,)))
        self.assertEqual(["frame 100: x is 0001, expected 1c01"],
                         movies.compare(expected, {"100": {"x": "0001"}}, (x,)))
        self.assertEqual(["frame 100: x is 1601, expected 1c01"], movies.compare(expected, {"100": {"x": "1601"}}))


@unittest.skipUnless(os.environ.get("RETROLARIUM_MAME") and shutil.which(os.environ.get("RETROLARIUM_MAME", "")),
                     "RETROLARIUM_MAME names the MAME that plays movies")
class MameTests(unittest.TestCase):
    def test_a_pad_reaches_the_game_on_the_frame_the_recording_says(self) -> None:
        frames = [Frame()] * 60 + [Frame(0x01)] * 30 + [Frame(0x82)] * 30
        check = Check((Cell("pad", 0x10), Cell("frames", 0x11)), (60, 61, 90, 91, "end"))
        with tempfile.TemporaryDirectory() as directory:
            rom = Path(directory) / "probe.nes"
            rom.write_bytes(probe_rom())
            snapshot = movies.run_in_mame(Movie("nes", frames), rom, check,
                                          mame=Path(shutil.which(os.environ["RETROLARIUM_MAME"])))
        pads = {label: int(cells["pad"], 16) for label, cells in snapshot.items()}
        self.assertEqual({"60": 0, "61": reversed_bits(0x01), "90": reversed_bits(0x01),
                          "91": reversed_bits(0x82), "end": reversed_bits(0x82)}, pads)


if __name__ == "__main__":
    unittest.main()
