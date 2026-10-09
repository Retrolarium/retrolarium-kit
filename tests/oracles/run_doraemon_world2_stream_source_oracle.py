"""Read-only World 2 selector-stream oracle against Doraemon source data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import zlib


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from retrolarium_kit.games.doraemon.world2.streams import (
    decode_screens, encode_screens, PAYLOAD_SIZE,
)
from retrolarium_kit.games.doraemon.world2.projection import project_screen


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True,
                        help="exact path to doraemon_src")
    parser.add_argument("--native-model-exe", type=Path,
                        help="optional compiled C++ route-model test executable")
    args = parser.parse_args()
    root = args.source_root.resolve()
    if root.name != "doraemon_src":
        parser.error("--source-root must point directly to doraemon_src")
    sys.path.insert(0, str(root))
    from scripts.validation.world2 import world2_streaming

    source = json.loads((root / "data/world2/compressed_screens.json")
                        .read_text(encoding="utf-8"))
    payload = world2_streaming.encode_authoring(source)
    checksum = f"{zlib.crc32(payload) & 0xffffffff:08x}"
    if len(payload) != PAYLOAD_SIZE or checksum != "030e0045":
        raise AssertionError(f"World 2 source differs: {len(payload)} bytes, {checksum}")
    decoded = decode_screens(payload)
    if encode_screens(decoded) != payload:
        raise AssertionError("World 2 zero-edit payload did not round-trip")
    if len(decoded["screens"]) != 119 or any(
        len(screen) != 16 for screen in decoded["screens"]
    ):
        raise AssertionError("World 2 selector geometry differs")
    from scripts.authoring.world2_level_model import World2ScreenDocument
    from scripts.authoring.world2_route_model import build_world2_routes

    reference = World2ScreenDocument.load(
        root / "data/world2/compressed_screens.json"
    )
    routes = build_world2_routes(
        json.loads((root / "data/world2/stage_sequence.json")
                   .read_text(encoding="utf-8")),
        json.loads((root / "data/world2/stage_branches.json")
                   .read_text(encoding="utf-8")),
        json.loads((root / "data/world2/palettes.json")
                   .read_text(encoding="utf-8")),
    )
    route_faces = {
        (placement.selector_id, placement.direction)
        for route in routes.values()
        for placement in route.placements
    }

    def verify_projections(faces: set[tuple[int, int]]) -> None:
        for selector, direction in sorted(faces):
            actual = project_screen(decoded, selector, direction)
            expected = reference.oriented_screen(selector, direction)
            if len(actual.cells) != 15 or any(len(row) != 16 for row in actual.cells):
                raise AssertionError("World 2 projected screen is not 16×15")
            for y, row in enumerate(expected):
                for x, source_cell in enumerate(row):
                    value = (
                        239 if source_cell.token_kind == "padding"
                        else 208 + source_cell.enemy_state
                        if source_cell.token_kind == "spawn"
                        else source_cell.metatile
                    )
                    if actual.cells[y][x] != value:
                        raise AssertionError(
                            f"World 2 selector {selector}, direction {direction}, "
                            f"cell ({x}, {y}) differs")
                    try:
                        coordinate = reference.raw_coordinate(
                            selector, direction, x, y)
                    except ValueError:
                        coordinate = None
                    if actual.raw_coordinates[y][x] != coordinate:
                        raise AssertionError(
                            f"World 2 selector {selector}, direction {direction}, "
                            f"raw coordinate ({x}, {y}) differs")

    verify_projections(route_faces)
    if args.native_model_exe is not None:
        command_input = payload.hex() + "\n" + "".join(
            f"{selector} {direction}\n"
            for selector, direction in sorted(route_faces)
        )
        process = subprocess.run(
            [str(args.native_model_exe.resolve()), "--projection-digests"],
            input=command_input, text=True, capture_output=True, check=True,
        )
        lines = process.stdout.splitlines()
        if len(lines) != len(route_faces):
            raise AssertionError("native World 2 projection count differs")
        for line, (selector, direction) in zip(lines, sorted(route_faces)):
            actual_selector, actual_direction, actual_digest = line.split()
            if (int(actual_selector), int(actual_direction)) != (selector, direction):
                raise AssertionError("native World 2 projection ordering differs")
            digest = 14695981039346656037
            projected = project_screen(decoded, selector, direction)
            for row, coordinates in zip(
                projected.cells, projected.raw_coordinates
            ):
                for value, coordinate in zip(row, coordinates):
                    raw_x, raw_y = coordinate if coordinate is not None else (-1, -1)
                    for byte in (value, raw_x + 1, raw_y + 1):
                        digest = ((digest ^ byte) * 1099511628211) & ((1 << 64) - 1)
            if int(actual_digest, 16) != digest:
                raise AssertionError(
                    f"native World 2 selector {selector}, direction {direction} differs")
    for selector in (0, 50):
        decoded["screens"][selector][0][0] = 1
    repacked = encode_screens(decoded)
    reference.paint(0, 0, 0, 1)
    if repacked != reference.encode():
        raise AssertionError("World 2 edited repack differs from source model")
    verify_projections({
        (selector, direction) for selector, direction in route_faces
        if selector in (0, 50)
    })
    print(f"World 2: {PAYLOAD_SIZE} bytes, CRC32 {checksum}, 119 exact selectors")
    print("World 2: aliased cell edit, exact fixed-capacity source repack")
    print(f"World 2: {len(route_faces)} directional route faces match source cells and inverse coordinates")
    if args.native_model_exe is not None:
        print("World 2: native C++ projection digests match all 119 source faces")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
