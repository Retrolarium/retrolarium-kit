from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from retrolarium_kit.authoring import GameProfile, ResourceSpec, RevisionSpec
from retrolarium_kit.game_profiles import packaged_game_profiles
from retrolarium_kit.nes_resources import build_resources, decode_resources, parse_resource_document
from retrolarium_kit.pacman_maze import (
    COLUMNS,
    GHOST_DOOR,
    ROWS,
    decode_maze_rle,
    encode_maze_grid,
    inspect_maze,
)


def valid_grid() -> list[list[int]]:
    grid = [[7 for _column in range(COLUMNS)] for _row in range(ROWS)]
    cells = [
        (row, column)
        for row in range(ROWS)
        for column in range(COLUMNS)
        if (row, column) != GHOST_DOOR
    ]
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
        "probe-v1", "Pac-Man maze probe", len(payload),
        hashlib.sha1(payload).hexdigest(), hashlib.sha256(payload).hexdigest(),
        "ines", 0,
    )
    profile = GameProfile(
        "pacman-probe", 1, "Pac-Man probe", "nes", ("levels", "graphics"),
        (revision,),
        (
            ResourceSpec("bundle", "resource-bundle", "nes.resource-bundle.v1", "fixed"),
            ResourceSpec("maze", "level", "pacman.maze-rle.v1", "fixed", {
                "file_offset": 16, "size": 416, "tokens": 416,
                "width": COLUMNS, "height": ROWS,
            }),
            ResourceSpec("chr", "graphics", "nes.chr-2bpp.v1", "fixed", {
                "file_offset": 0x4010, "size": 0x2000,
            }),
        ),
    )
    return payload, profile, revision


class PacmanMazeTests(unittest.TestCase):
    def test_packaged_profile_has_verified_compatible_revisions(self) -> None:
        profile = packaged_game_profiles().get("pacman")
        self.assertEqual(
            [item.revision_id for item in profile.revisions],
            ["japan-v10", "japan-v11"],
        )
        self.assertEqual(profile.resources[1].codec, "pacman.maze-rle.v1")
        self.assertEqual(
            [item.resource_id for item in profile.resources if item.kind == "palette"],
            ["title-background", "attract-bg-sprite", "round-gameplay",
             "intermission-sprites", "fruit-by-stage"],
        )
        self.assertEqual(
            [item.resource_id for item in profile.resources if item.kind == "screen"],
            ["title-logo"],
        )
        self.assertEqual(
            [item.resource_id for item in profile.resources if item.kind == "text"],
            ["game-text"],
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
            (row, column)
            for row in range(ROWS)
            for column in range(COLUMNS)
            if maze["tiles"][row][column] == 7
        )
        pellet = next(
            (row, column)
            for row in range(ROWS)
            for column in range(COLUMNS)
            if maze["tiles"][row][column] == 3
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


if __name__ == "__main__":
    unittest.main()
