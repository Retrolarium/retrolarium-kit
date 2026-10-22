from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from helpers import game_repository
from retrolarium_kit.core.authoring import GameProfile, ResourceSpec, RevisionSpec
from helpers import game_profile
from retrolarium_kit.platform.nes.resources import build_resources, decode_resources, parse_resource_document
from retrolarium_kit.games.pacman.maze import (
    COLUMNS,
    GHOST_DOOR,
    ROWS,
    decode_maze_rle,
    encode_maze_grid,
    inspect_maze,
)


ROM = game_repository("pacman_src") / "Pac-Man (J) (V1.0) [!].nes"
# The round counter's start and the two "pellets eaten" sums.
COUNT_OFFSETS = (0xE98, 0xF31, 0x118C)
POWER_PELLETS = ((3, 2), (3, 20), (20, 2), (20, 20))


def valid_grid() -> list[list[int]]:
    grid = [[7 for _column in range(COLUMNS)] for _row in range(ROWS)]
    cells = [(row, column) for row in range(ROWS) for column in range(COLUMNS) if (row, column) != GHOST_DOOR]
    for row, column in cells[:4]:
        grid[row][column] = 1
    for row, column in cells[4:192]:
        grid[row][column] = 3
    grid[GHOST_DOOR[0]][GHOST_DOOR[1]] = 0x2C
    return grid


def fixture() -> tuple[bytes, GameProfile, RevisionSpec]:
    maze = encode_maze_grid(valid_grid())
    header = b"NES\x1a" + bytes((1, 1, 0, 0)) + bytes(8)
    payload = header + maze + bytes(0x4000 - len(maze)) + bytes(0x2000)
    revision = RevisionSpec(
        "probe-v1",
        "Pac-Man maze probe",
        len(payload),
        hashlib.sha1(payload).hexdigest(),
        hashlib.sha256(payload).hexdigest(),
        "ines",
        0,
    )
    profile = GameProfile(
        "pacman-probe",
        1,
        "Pac-Man probe",
        "nes",
        ("levels", "graphics"),
        (revision,),
        (
            ResourceSpec("bundle", "resource-bundle", "nes.resource-bundle.v1", "fixed"),
            ResourceSpec(
                "maze",
                "level",
                "pacman.maze-rle.v1",
                "fixed",
                {
                    "file_offset": 16,
                    "size": 416,
                    "tokens": 416,
                    "width": COLUMNS,
                    "height": ROWS,
                },
            ),
            ResourceSpec(
                "chr",
                "graphics",
                "nes.chr-2bpp.v1",
                "fixed",
                {
                    "file_offset": 0x4010,
                    "size": 0x2000,
                },
            ),
        ),
    )
    return payload, profile, revision


class PacmanMazeTests(unittest.TestCase):
    def test_packaged_profile_has_verified_compatible_revisions(self) -> None:
        profile = game_profile("pacman")
        self.assertEqual(
            [item.revision_id for item in profile.revisions],
            ["japan-v10", "japan-v11", "japan-v10-nrom256"],
        )
        self.assertEqual(profile.resources[1].codec, "pacman.maze-rle.v1")
        self.assertEqual(
            [item.resource_id for item in profile.resources if item.kind == "palette"],
            [
                "title-background",
                "attract-bg-sprite",
                "round-gameplay",
                "frightened-active-normal",
                "intermission-sprites",
                "fruit-by-stage",
            ],
        )
        self.assertEqual(
            [item.resource_id for item in profile.resources if item.kind == "screen"],
            ["screen-content"],
        )
        self.assertEqual(
            [item.resource_id for item in profile.resources if item.kind == "text"],
            [],
        )
        self.assertEqual(
            next(item for item in profile.resources if item.kind == "screen").codec,
            "pacman.screen-collection.v1",
        )

    def test_fixed_rle_and_semantics(self) -> None:
        grid = valid_grid()
        encoded = encode_maze_grid(grid)
        self.assertEqual(len(encoded), 416)
        self.assertEqual(decode_maze_rle(encoded), tuple(tuple(row) for row in grid))
        self.assertEqual(inspect_maze(grid)["errors"], [])

    def test_bundle_preserves_zero_edit_bytes_and_rebuilds_an_edit(self) -> None:
        image, profile, revision = fixture()
        document = decode_resources(image, profile, revision)
        exact, report = build_resources(image, document, profile, revision)
        self.assertEqual(exact, image)
        self.assertEqual(report["changed_bytes"], 0)

        raw = copy.deepcopy(document.as_dict())
        maze = raw["resources"][0]
        first = next(
            (row, column) for row in range(ROWS) for column in range(COLUMNS) if maze["tiles"][row][column] == 7
        )
        pellet = next(
            (row, column) for row in range(ROWS) for column in range(COLUMNS) if maze["tiles"][row][column] == 3
        )
        maze["tiles"][first[0]][first[1]] = 3
        maze["tiles"][pellet[0]][pellet[1]] = 7
        parsed = parse_resource_document(raw, profile, revision)
        edited, edited_report = build_resources(image, parsed, profile, revision)
        self.assertNotEqual(edited, image)
        self.assertGreater(edited_report["changed_bytes"], 0)
        self.assertEqual(
            decode_maze_rle(edited[16:432]),
            tuple(tuple(row) for row in maze["tiles"]),
        )

    def test_runtime_sensitive_changes_are_rejected(self) -> None:
        image, profile, revision = fixture()
        raw = decode_resources(image, profile, revision).as_dict()
        raw["resources"][0]["tiles"][0][0] = 7
        with self.assertRaisesRegex(Exception, "expects 192"):
            parse_resource_document(raw, profile, revision)


@unittest.skipUnless(ROM.is_file(), "the Pac-Man ROM is not available")
class PacmanMazeRomTests(unittest.TestCase):
    def setUp(self) -> None:
        self.image = ROM.read_bytes()
        self.profile = game_profile("pacman")
        self.revision = next(item for item in self.profile.revisions if item.revision_id == "japan-v10")
        self.raw = copy.deepcopy(decode_resources(self.image, self.profile, self.revision).as_dict())
        self.maze = next(item for item in self.raw["resources"] if item["id"] == "maze")

    def build(self) -> bytes:
        parsed = parse_resource_document(self.raw, self.profile, self.revision)
        output, _report = build_resources(self.image, parsed, self.profile, self.revision)
        return output

    def test_the_counter_operands_hold_192(self) -> None:
        for offset in COUNT_OFFSETS:
            self.assertEqual(self.image[offset], 0xC0)
        self.assertEqual(self.build(), self.image)

    def test_the_round_counts_the_maze_pellets(self) -> None:
        # Two corridor cells get pellets: the round now ends after 194.
        added = 0
        for row in range(ROWS):
            for column in range(COLUMNS):
                if (
                    added < 2
                    and self.maze["tiles"][row][column] == 0
                    and (row, column) not in ((20, 11), (10, 11), (13, 10), (13, 11), (13, 12))
                ):
                    self.maze["tiles"][row][column] = 3
                    added += 1
        self.assertEqual(added, 2)
        output = self.build()
        for offset in COUNT_OFFSETS:
            self.assertEqual(output[offset], 194)
        self.assertEqual(
            decode_maze_rle(output[11400 : 11400 + 416]),
            tuple(tuple(row) for row in self.maze["tiles"]),
        )

    def test_power_pellets_stay_in_the_corners(self) -> None:
        tiles = [list(row) for row in self.maze["tiles"]]
        self.assertEqual([cell for cell in POWER_PELLETS if tiles[cell[0]][cell[1]] == 1], list(POWER_PELLETS))
        row, column = POWER_PELLETS[0]
        self.maze["tiles"][row][column] = 3
        with self.assertRaisesRegex(Exception, r"power pellet at \(3,2\) must stay"):
            self.build()
        self.maze["tiles"][row][column] = 1
        pellet = next((r, c) for r in range(ROWS) for c in range(COLUMNS) if self.maze["tiles"][r][c] == 3)
        self.maze["tiles"][pellet[0]][pellet[1]] = 1
        with self.assertRaisesRegex(Exception, "outside the cells the blinking table draws"):
            self.build()

    def test_the_counter_holds_up_to_255(self) -> None:
        for row in self.maze["tiles"]:
            for column, tile in enumerate(row):
                if tile in (3, 9):
                    row[column] = 0
        for row, column in POWER_PELLETS:
            self.maze["tiles"][row][column] = 1
        # Four power pellets still count: an empty corridor maze passes...
        self.build()
        tiles = sum(row.count(3) for row in self.maze["tiles"])
        self.assertEqual(tiles, 0)
        # ...and 256 pellets do not fit the counter.
        grid = valid_grid()
        cells = [(r, c) for r in range(ROWS) for c in range(COLUMNS) if grid[r][c] == 7]
        for r, c in cells[: 256 - 192]:
            grid[r][c] = 3
        errors = inspect_maze(grid, counted=True)["errors"]
        self.assertTrue(any("holds 1..255" in error for error in errors), errors)
        grid[cells[0][0]][cells[0][1]] = 7
        self.assertEqual(inspect_maze(grid, counted=True)["errors"], [])


if __name__ == "__main__":
    unittest.main()
