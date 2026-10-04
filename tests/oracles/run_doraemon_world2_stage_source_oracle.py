"""Read-only parity gate for World 2 stage bytecode and chapter starts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import zlib


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from retrolarium_kit.doraemon_world2_stage import decode_stage, encode_stage
from retrolarium_kit.doraemon_world2_branches import (
    CONDITION_NAMES, decode_branches, encode_branches,
)
from retrolarium_kit.doraemon_world2_chapters import (
    decode_chapters, encode_chapters,
)
from retrolarium_kit.doraemon_world2_routes import (
    ROUTE_NAMES, build_routes, paint_route_cell,
)
from retrolarium_kit.doraemon_world2_projection import project_screen
from retrolarium_kit.doraemon_world2_streams import decode_screens, encode_screens
from retrolarium_kit.game_profiles import packaged_game_profiles


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    args = parser.parse_args()
    root = args.source_root.resolve()
    if root.name != "doraemon_src":
        parser.error("--source-root must point directly to doraemon_src")
    profile = packaged_game_profiles().get("doraemon")
    expected_offsets = {
        "world2-stage": (0xBDDF, 229),
        "world2-branches": (0xA70F, 68),
        "world2-chapters": (0x8BA6, 6),
    }
    for revision in profile.revisions:
        resources = {item.resource_id: item
                     for item in profile.resources_for(revision)}
        for name, (address, size) in expected_offsets.items():
            parameters = resources[name].parameters
            if (parameters["file_offset"], parameters["size"]) != (
                16 + 0x8000 + address - 0x8000, size
            ):
                raise AssertionError(f"{revision.revision_id} {name} ROM span differs")
        if resources["world2-stage"].parameters["start_file_offset"] != (
            16 + 0x8000 + 0x8B38 - 0x8000
        ):
            raise AssertionError(f"{revision.revision_id} stage start span differs")
    sys.path.insert(0, str(root))
    from scripts.validation.world2 import world2_stage_sequence
    from scripts.validation.world2 import world2_stage_branches
    from scripts.validation.world2 import world2_palettes
    from scripts.authoring.world2_route_model import build_world2_routes
    from scripts.authoring.world2_level_model import World2ScreenDocument
    from scripts.validation.world2 import world2_streaming

    source = json.loads((root / "data/world2/stage_sequence.json")
                        .read_text(encoding="utf-8"))
    encoded = world2_stage_sequence.encode_authoring(source)
    checksum = f"{zlib.crc32(bytes(encoded[address] for address in sorted(encoded))) & 0xffffffff:08x}"
    if len(encoded) != 232 or checksum != "063ebda9":
        raise AssertionError(f"World 2 stage source differs: {len(encoded)}, {checksum}")
    sequence = bytes(encoded[0xBDDF + index] for index in range(229))
    starts = bytes(encoded[0x8B38 + index] for index in range(3))
    decoded = decode_stage(sequence, starts)
    if encode_stage(decoded) != (sequence, starts):
        raise AssertionError("World 2 stage zero-edit round-trip differs")
    branch = json.loads((root / "data/world2/stage_branches.json")
                        .read_text(encoding="utf-8"))
    branch_manifest = json.loads((
        root / "config/authoring/world2/world2_stage_branches.json"
    ).read_text(encoding="utf-8"))
    palette = json.loads((root / "data/world2/palettes.json")
                         .read_text(encoding="utf-8"))
    palette_bytes = world2_palettes.encode_authoring(palette)
    chapter_document = decode_chapters(bytes(
        palette_bytes[0x8BA6 + index] for index in range(6)))
    if encode_chapters(chapter_document) != bytes(
        palette_bytes[0x8BA6 + index] for index in range(6)):
        raise AssertionError("World 2 chapter selector round-trip differs")
    branch_bytes = world2_stage_branches.encode_authoring(
        branch, branch_manifest)
    branch_checksum = (
        f"{zlib.crc32(bytes(branch_bytes[address] for address in sorted(branch_bytes))) & 0xffffffff:08x}"
    )
    if len(branch_bytes) != 68 or branch_checksum != "bc4122c7":
        raise AssertionError("World 2 source branch tables differ")
    branch_document = decode_branches(bytes(
        branch_bytes[0xA70F + index] for index in range(68)))
    if encode_branches(branch_document) != bytes(
        branch_bytes[0xA70F + index] for index in range(68)):
        raise AssertionError("World 2 branch zero-edit round-trip differs")
    for actual, reference in zip(branch_document["branches"], branch["branches"]):
        if (actual["trigger_screen_id"] != int(reference["trigger_screen_id"], 0)
            or actual["destination_offset"] != int(reference["destination_offset"], 0)
            or CONDITION_NAMES[actual["condition_code"]] != reference["condition"]
            or actual["return_offset_override"] != (
                None if reference["return_offset_override"] is None else
                int(reference["return_offset_override"], 0)
            )):
            raise AssertionError("World 2 decoded branch fields differ")

    def verify_routes() -> None:
        ours = build_routes(decoded, branch_document, chapter_document)
        expected = build_world2_routes(source, branch, palette)
        if tuple(ours) != ROUTE_NAMES or tuple(expected) != ROUTE_NAMES:
            raise AssertionError("World 2 route names differ")
        for name in ROUTE_NAMES:
            actual = ours[name]
            reference = expected[name]
            if (actual.width, actual.height) != (reference.width, reference.height):
                raise AssertionError(f"{name} route bounds differ")
            if [vars(item) for item in actual.placements] != [
                vars(item) for item in reference.placements
            ]:
                raise AssertionError(f"{name} placements differ")
            if [vars(item) for item in actual.annotations] != [
                vars(item) for item in reference.annotations
            ]:
                raise AssertionError(f"{name} annotations differ")

    verify_routes()

    decoded["tokens"][3] = 2
    source["entries"][3]["raw"] = "0x02"
    source["entries"][3]["screen_id"] = "0x02"
    verify_routes()
    branch_document["branches"][0]["condition_code"] = 1
    branch["branches"][0]["condition"] = CONDITION_NAMES[1]
    edited_branches = world2_stage_branches.encode_authoring(
        branch, branch_manifest)
    if encode_branches(branch_document) != bytes(
        edited_branches[0xA70F + index] for index in range(68)):
        raise AssertionError("World 2 edited branch table differs from source")
    verify_routes()
    chapter_document["background_palette_ids"][0] = 2
    chapter_document["sprite_offsets"][0] = 32
    palette["chapters"][0]["background_palette_id"] = 2
    palette["chapters"][0]["sprite_offset"] = "0x20"
    palette["chapters"][0]["sprite_palette_record"] = 7
    edited_palettes = world2_palettes.encode_authoring(palette)
    if encode_chapters(chapter_document) != bytes(
        edited_palettes[0x8BA6 + index] for index in range(6)):
        raise AssertionError("World 2 edited chapter selectors differ from source")
    verify_routes()
    screen_path = root / "data/world2/compressed_screens.json"
    screen_source = json.loads(screen_path.read_text(encoding="utf-8"))
    screen_document = decode_screens(world2_streaming.encode_authoring(screen_source))
    screen_reference = World2ScreenDocument.load(screen_path)
    painted = False
    for route in build_routes(decoded, branch_document, chapter_document).values():
        for placement in route.visible_placements():
            if placement.selector_id not in (0, 50):
                continue
            projected = project_screen(
                screen_document, placement.selector_id, placement.direction)
            for local_y, row in enumerate(projected.cells):
                for local_x, value in enumerate(row):
                    if value >= 208 or projected.raw_coordinates[local_y][local_x] is None:
                        continue
                    global_x = (placement.screen_x - route.min_x) * 16 + local_x
                    global_y = (placement.screen_y - route.min_y) * 15 + local_y
                    raw_x, raw_y = projected.raw_coordinate(local_x, local_y)
                    brush = (value + 1) % 208
                    target = paint_route_cell(
                        screen_document, route, global_x, global_y, brush)
                    if target != (placement.selector_id, raw_x, raw_y):
                        raise AssertionError("World 2 route paint target differs")
                    screen_reference.paint(placement.selector_id, raw_x, raw_y, brush)
                    if encode_screens(screen_document) != screen_reference.encode():
                        raise AssertionError("World 2 route paint ROM payload differs")
                    painted = True
                    break
                if painted:
                    break
            if painted:
                break
        if painted:
            break
    if not painted:
        raise AssertionError("World 2 route has no editable aliased screen")
    decoded["start_offsets"][1] = 38
    source["start_offsets"][1] = 38
    edited = world2_stage_sequence.encode_authoring(source)
    changed_sequence, changed_starts = encode_stage(decoded)
    actual = {
        **{0xBDDF + index: value for index, value in enumerate(changed_sequence)},
        **{0x8B38 + index: value for index, value in enumerate(changed_starts)},
    }
    if actual != edited:
        raise AssertionError("World 2 stage edited bytes differ from source")
    print(f"World 2 stage: 232 bytes, CRC32 {checksum}, exact two-field source edit")
    print(f"World 2 branches: 68 bytes, CRC32 {branch_checksum}, exact source edit")
    print("World 2 chapters: six exact source bytes and two-field edit")
    print("World 2 routes: five geometries and annotations match after control edits")
    print("World 2 route paint: inverse coordinate and repacked bytes match source")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
