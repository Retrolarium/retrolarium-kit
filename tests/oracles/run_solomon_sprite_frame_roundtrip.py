"""Opt-in Solomon sprite-frame oracle and exact ROM edit gate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from retrolarium_kit.games import build_game, extract_game, inspect_game


def check(rom: Path, revision: str, oracle: object, contract: dict) -> None:
    inspection = inspect_game(rom)
    if inspection["game"] != "solomons-key" or inspection["revision"] != revision:
        raise AssertionError(f"{revision}: unexpected source image")
    source = rom.read_bytes()
    decoder = oracle.EnemySpriteDecoder(source[16:16 + 0x8000], contract)
    with tempfile.TemporaryDirectory(
        prefix=f"retrolarium-solomon-sprites-{revision}-"
    ) as temporary:
        root = Path(temporary)
        original_path = root / "sprite-frames.json"
        exact_path = root / "exact.nes"
        extract_game(rom, original_path, resource="sprite-frames")
        document = json.loads(original_path.read_text(encoding="utf-8"))
        frames = document["frames"]
        if len(frames) != 28:
            raise AssertionError(f"{revision}: expected 28 physical sprite frames")
        for enemy_type in range(0x18, 0x84):
            matched = [
                frame for frame in frames
                if enemy_type in frame["enemy_types"]
            ]
            reference = decoder.frame(enemy_type)
            if len(matched) != 1 or reference is None:
                raise AssertionError(f"{revision}: type {enemy_type:02X} binding differs")
            frame = matched[0]
            if (frame["left_tile"], frame["right_tile"], frame["flags"]) != (
                reference.left_tile, reference.right_tile, reference.flags
            ):
                raise AssertionError(f"{revision}: type {enemy_type:02X} frame differs")
        for facing, faces_left in (("right", False), ("left", True)):
            matched = [frame for frame in frames if facing in frame["wizard_facings"]]
            reference = decoder.wizard_frame(faces_left)
            if len(matched) != 1 or reference is None:
                raise AssertionError(f"{revision}: Wizard {facing} binding differs")
            frame = matched[0]
            if (frame["left_tile"], frame["right_tile"], frame["flags"]) != (
                reference.left_tile, reference.right_tile, reference.flags
            ):
                raise AssertionError(f"{revision}: Wizard {facing} frame differs")
        exact = build_game(rom, original_path, exact_path)
        if exact["changed_bytes"] or exact_path.read_bytes() != source:
            raise AssertionError(f"{revision}: sprite-frame zero-edit ROM differs")
        edited = json.loads(original_path.read_text(encoding="utf-8"))
        selected = max(
            (frame for frame in edited["frames"] if frame["enemy_types"]),
            key=lambda frame: len(frame["enemy_types"]),
        )
        selected["left_tile"] ^= 1
        edited_path = root / "edited.json"
        edited_path.write_text(json.dumps(edited), encoding="utf-8")
        output_path = root / "edited.nes"
        report = build_game(rom, edited_path, output_path)
        output = output_path.read_bytes()
        changed = [
            index for index, (a, b) in enumerate(zip(source, output))
            if a != b
        ]
        expected_offset = 16 + selected["prg_offset"]
        if changed != [expected_offset] or report["changed_bytes"] != 1:
            raise AssertionError(
                f"{revision}: changed {changed}, expected [{expected_offset}]"
            )
        rebuilt = oracle.EnemySpriteDecoder(output[16:16 + 0x8000], contract)
        for enemy_type in selected["enemy_types"]:
            if rebuilt.frame(enemy_type).left_tile != selected["left_tile"]:
                raise AssertionError(
                    f"{revision}: edited shared frame missed type {enemy_type:02X}"
                )
    print(
        f"Solomon {revision}: 28 shared frames, 108 enemy types, "
        "Wizard L/R and exact one-byte ROM edit"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--usa", type=Path, required=True)
    parser.add_argument("--europe", type=Path, required=True)
    parser.add_argument("--source-project", type=Path, required=True)
    args = parser.parse_args()
    source_project = args.source_project.resolve(strict=True)
    sys.path.insert(0, str(source_project))
    from scripts.authoring import level_preview as oracle
    from scripts.build.revision_profiles import get_profile, load_profiles

    profiles = load_profiles(source_project / "config/revision_profiles.json")
    for revision, path in (
        ("usa", args.usa), ("europe", args.europe)
    ):
        contract = get_profile(profiles, revision)["level_preview"]
        check(path.resolve(strict=True), revision, oracle, contract)


if __name__ == "__main__":
    main()
