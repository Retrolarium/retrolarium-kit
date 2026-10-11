"""Opt-in real-ROM gate for Solomon USA/Europe fixed room bitplanes.

The private ROMs and neighboring source project are read only.  All extracted
documents and built images live in a temporary directory.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from retrolarium_kit.games.catalog import build_game, extract_game, inspect_game, validate_game
from retrolarium_kit.core.authoring import AuthoringError


REGIONS = {"usa": 0x602C, "europe": 0x5FAC}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def positions(plane: list[list[int]]) -> list[dict[str, int]]:
    return [
        {"x": x, "y": y}
        for y, row in enumerate(plane)
        for x, value in enumerate(row) if value
    ]


def without_raw(value: object) -> object:
    if isinstance(value, dict):
        return {key: without_raw(item) for key, item in value.items()
                if key != "raw"}
    if isinstance(value, list):
        return [without_raw(item) for item in value]
    return value


def check_streams(room: dict, enemy: dict, item: dict, revision: str,
                  index: int) -> None:
    actual_enemy = room["enemy_stream"]
    actual_item = room["item_stream"]
    require(actual_enemy["spawn_lifetime"] == enemy["spawn_lifetime"]
            and actual_enemy["enemies"] == without_raw(enemy["enemies"]),
            f"{revision}: room {index + 1} enemy stream disagrees with oracle")
    require(actual_item["commands"] == without_raw(item["commands"])
            and actual_item["metadata"] == {
                key: without_raw(item["metadata"][key])
                for key in actual_item["metadata"]
            },
            f"{revision}: room {index + 1} item stream disagrees with oracle")


def check_shared(document: dict, prg: bytes, layout: object,
                 oracle: object, revision: str) -> None:
    schedules = oracle.decode_mirror_schedules(prg, layout)
    enemy_sets = oracle.decode_mirror_enemy_sets(prg, layout)
    require(document["mirror_schedules"] == [
                {"initial_phase": item["initial_phase"],
                 "loop_phase": item["loop_phase"]}
                for item in schedules],
            f"{revision}: mirror schedules disagree with oracle")
    require(document["mirror_enemy_sets"] == [
                {"enemy_types": item["enemy_types"],
                 "loop_offset": item["loop_offset"]}
                for item in enemy_sets],
            f"{revision}: mirror enemy sets disagree with oracle")
    require(document["special_rooms"]
            == without_raw(oracle.decode_special_room_data(prg, layout)),
            f"{revision}: special-room tables disagree with oracle")


def check(rom: Path, revision: str, oracle: object,
          graphics_oracle: object, graphics_profile: dict) -> None:
    original = rom.read_bytes()
    inspection = inspect_game(rom)
    require(inspection["game"] == "solomons-key"
            and inspection["revision"] == revision
            and inspection["details"]["rooms"] == 53,
            f"{revision}: profile recognition differs")
    with tempfile.TemporaryDirectory(prefix=f"retrolarium-solomon-{revision}-") as root:
        temporary = Path(root)
        document_path = temporary / "rooms.json"
        extract_game(rom, document_path)
        document = json.loads(document_path.read_text(encoding="utf-8"))
        require(document["schema"] == "retrolarium.solomon-rooms"
                and document["schema_version"] == 3
                and len(document["rooms"]) == 53,
                f"{revision}: extracted document differs")
        layout = oracle.ROOM_DATA_LAYOUTS[revision]
        prg = original[16:16 + 0x8000]
        for index, room in enumerate(document["rooms"]):
            reference = oracle.decode_blocks(prg, index, layout)
            require(positions(room["brown"]) == reference["brown"]
                    and positions(room["white"]) == reference["white"],
                    f"{revision}: room {index + 1} disagrees with source oracle")
            check_streams(room,
                          oracle.decode_enemies(prg, index, layout),
                          oracle.decode_items(prg, index, layout),
                          revision, index)
        check_shared(document, prg, layout, oracle, revision)
        art_path = temporary / "room-art.json"
        extract_game(rom, art_path, resource="room-art")
        art = json.loads(art_path.read_text(encoding="utf-8"))
        require(art["room_map_patterns"]
                == oracle.decode_room_tile_patterns(prg, layout),
                f"{revision}: RoomMap records disagree with source oracle")
        reference_graphics = graphics_oracle.export_document(
            original, graphics_profile
        )
        require(art["room_group_colors"]
                == reference_graphics["palette_data"]["room_group_colors"],
                f"{revision}: room-group colors disagree with source oracle")
        art_exact_path = temporary / "art-exact.nes"
        require(build_game(rom, art_path, art_exact_path)["changed_bytes"] == 0
                and art_exact_path.read_bytes() == original,
                f"{revision}: room-art zero-edit build is not exact")
        validate_game(rom, document_path)
        exact_path = temporary / "exact.nes"
        exact = build_game(rom, document_path, exact_path)
        require(exact["changed_bytes"] == 0 and exact_path.read_bytes() == original,
                f"{revision}: zero-edit build is not exact")
        streams_only = deepcopy(document)
        streams_only["schema_version"] = 2
        del streams_only["mirror_schedules"]
        del streams_only["mirror_enemy_sets"]
        del streams_only["special_rooms"]
        document_path.write_text(json.dumps(streams_only), encoding="utf-8")
        streams_path = temporary / "streams-v2.nes"
        require(build_game(rom, document_path, streams_path)["changed_bytes"] == 0
                and streams_path.read_bytes() == original,
                f"{revision}: version-2 room document lost compatibility")
        legacy = deepcopy(streams_only)
        legacy["schema_version"] = 1
        for room in legacy["rooms"]:
            del room["enemy_stream"]
            del room["item_stream"]
        document_path.write_text(json.dumps(legacy), encoding="utf-8")
        legacy_path = temporary / "legacy-v1.nes"
        require(build_game(rom, document_path, legacy_path)["changed_bytes"] == 0
                and legacy_path.read_bytes() == original,
                f"{revision}: version-1 room document lost compatibility")

        document["rooms"][0]["brown"][0][0] ^= 1
        document["rooms"][52]["white"][11][15] ^= 1
        document_path.write_text(json.dumps(document), encoding="utf-8")
        edited_path = temporary / "edited.nes"
        edited = build_game(rom, document_path, edited_path)
        output = edited_path.read_bytes()
        offset = 16 + REGIONS[revision]
        differences = [index for index, (before, after) in enumerate(zip(original, output))
                       if before != after]
        require(edited["changed_bytes"] == 2
                and differences == [offset, offset + 53 * 48 - 1]
                and len(output) == len(original),
                f"{revision}: room edits escaped the two owned bitplane bytes")
        rebuilt_prg = output[16:16 + 0x8000]
        for index in (0, 52):
            reference = oracle.decode_blocks(rebuilt_prg, index, layout)
            room = document["rooms"][index]
            require(positions(room["brown"]) == reference["brown"]
                    and positions(room["white"]) == reference["white"],
                    f"{revision}: edited room {index + 1} disagrees with source oracle")

        # Both arenas are full. Reallocate one fixed-size record between two
        # rooms in each arena, then independently decode the shifted pointers.
        first, second = document["rooms"][:2]
        second["enemy_stream"]["enemies"].append(
            first["enemy_stream"]["enemies"].pop())
        second["item_stream"]["commands"].insert(
            -1, first["item_stream"]["commands"].pop(0))
        document_path.write_text(json.dumps(document), encoding="utf-8")
        repacked_path = temporary / "repacked.nes"
        repacked = build_game(rom, document_path, repacked_path)
        repacked_image = repacked_path.read_bytes()
        repacked_prg = repacked_image[16:16 + 0x8000]
        shift = 0 if revision == "usa" else -0x80
        owned = (
            range(16 + 0x5CEC + shift, 16 + 0x602C + shift),
            range(16 + 0x602C + shift, 16 + 0x6A1C + shift),
            range(16 + 0x6A1C + shift, 16 + 0x6FC4 + shift),
        )
        require(all(any(offset in span for span in owned)
                    for offset, (before, after) in enumerate(zip(original, repacked_image))
                    if before != after),
                f"{revision}: stream repack changed bytes outside owned ranges")
        for index in (0, 1):
            check_streams(document["rooms"][index],
                          oracle.decode_enemies(repacked_prg, index, layout),
                          oracle.decode_items(repacked_prg, index, layout),
                          revision, index)
        require(repacked["enemy_streams"]["free_bytes"] == 0
                and repacked["item_streams"]["free_bytes"] == 0,
                f"{revision}: repacker lost occupied arena bytes")

        # Repack the fully occupied mirror-set arena, and edit one fixed
        # schedule byte plus one byte in a disjoint special-room table.
        mirror_sets = document["mirror_enemy_sets"]
        mirror_sets[0]["enemy_types"].append(
            mirror_sets[3]["enemy_types"].pop())
        document["mirror_schedules"][0]["initial_phase"][0] ^= 1
        old_bonus_type = document["special_rooms"]["random_bonus_room"]["item_types"][0]
        document["special_rooms"]["random_bonus_room"]["item_types"][0] = (
            1 if old_bonus_type != 1 else 2)
        document_path.write_text(json.dumps(document), encoding="utf-8")
        shared_path = temporary / "shared-repacked.nes"
        shared_report = build_game(rom, document_path, shared_path)
        shared_image = shared_path.read_bytes()
        shared_prg = shared_image[16:16 + 0x8000]
        shared_owned = (*owned,
                        range(16 + layout.mirror_schedule_table,
                              16 + layout.enemy_pointer_table),
                        range(16 + layout.special_room_item_positions,
                              16 + layout.special_room_item_positions + 32),
                        range(16 + layout.special_room_item_types,
                              16 + layout.special_room_item_types + 16),
                        range(16 + layout.solomon_seal_positions,
                              16 + layout.solomon_seal_positions + 8),
                        range(16 + layout.princess_room_hidden_cells,
                              16 + layout.princess_room_hidden_cells + 12),
                        range(16 + layout.room_20_special_bitplane,
                              16 + layout.room_20_special_bitplane + 24),
                        range(16 + layout.room_30_special_bitplane,
                              16 + layout.room_30_special_bitplane + 24))
        require(all(any(offset in span for span in shared_owned)
                    for offset, (before, after) in enumerate(zip(original, shared_image))
                    if before != after)
                and shared_image[16 + layout.mirror_schedule_data]
                != original[16 + layout.mirror_schedule_data]
                and shared_image[16 + layout.special_room_item_types]
                != original[16 + layout.special_room_item_types],
                f"{revision}: shared edit changed bytes outside owned ranges")
        check_shared(document, shared_prg, layout, oracle, revision)
        require(shared_report["mirror_enemy_sets"]["free_bytes"] == 0
                and shared_report["mirror_schedules"]["free_bytes"] == 0
                and shared_report["special_rooms"]["encoded_bytes"] == 116,
                f"{revision}: shared data capacity report differs")

        # One extra enemy cannot fit unless another room releases two bytes.
        second["enemy_stream"]["enemies"].append(
            second["enemy_stream"]["enemies"][0].copy())
        document_path.write_text(json.dumps(document), encoding="utf-8")
        try:
            validate_game(rom, document_path)
        except AuthoringError as exc:
            require("arena capacity" in str(exc),
                    f"{revision}: overflow failed for the wrong reason: {exc}")
        else:
            raise AssertionError(f"{revision}: enemy arena overflow was accepted")
        second["enemy_stream"]["enemies"].pop()
        mirror_sets[0]["enemy_types"].append(mirror_sets[0]["enemy_types"][0])
        document_path.write_text(json.dumps(document), encoding="utf-8")
        try:
            validate_game(rom, document_path)
        except AuthoringError as exc:
            require("mirror-enemy-sets" in str(exc) and "arena capacity" in str(exc),
                    f"{revision}: mirror overflow failed for the wrong reason: {exc}")
        else:
            raise AssertionError(f"{revision}: mirror-set arena overflow was accepted")
    print(f"Solomon {revision}: 53 oracle rooms, 58 RoomMap patterns, "
          "14 group colors, exact v1/v2/v3 and room-art builds, "
          "room/mirror repacks, special tables and capacity rejection passed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--usa", type=Path, required=True)
    parser.add_argument("--europe", type=Path, required=True)
    parser.add_argument("--source-project", type=Path, required=True,
                        help="neighboring solomon_no_kagi_src for its room codec oracle")
    args = parser.parse_args()
    sys.path.insert(0, str(args.source_project.resolve(strict=True)))
    from scripts.authoring import room_data as oracle
    from scripts.authoring import graphics_editor as graphics_oracle
    from scripts.build.revision_profiles import get_profile, load_profiles
    profiles = load_profiles(
        args.source_project.resolve(strict=True) / "config" / "revision_profiles.json"
    )
    check(args.usa.resolve(strict=True), "usa", oracle,
          graphics_oracle, get_profile(profiles, "usa"))
    check(args.europe.resolve(strict=True), "europe", oracle,
          graphics_oracle, get_profile(profiles, "europe"))


if __name__ == "__main__":
    main()
