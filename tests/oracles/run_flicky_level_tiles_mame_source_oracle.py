#!/usr/bin/env python3
"""Probe a visible Flicky 4bpp level-tile edit through ROM and MAME VDP."""

from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from retrolarium_kit.flicky_content import (
    CANONICAL_SHA256, WORKSPACE_FILES, import_flicky_source,
)
from retrolarium_kit.megadrive import cartridge_checksum
from retrolarium_kit.studio_project import (
    StudioProjectError, build_studio_project, create_flicky_studio_project,
    studio_flicky_round_playtest_plan, verify_studio_project,
)
from run_flicky_palette_mame_source_oracle import render_native
from run_flicky_round_playtest_source_oracle import launch


def changed_pixels(before_path: Path, after_path: Path) -> set[int]:
    from PIL import Image
    with Image.open(before_path) as before_image, Image.open(after_path) as after_image:
        before = before_image.convert("RGB")
        after = after_image.convert("RGB")
        assert before.size == after.size == (256, 224)
        old = before.tobytes()
        new = after.tobytes()
    return {index // 3 for index in range(0, len(old), 3)
            if old[index:index + 3] != new[index:index + 3]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--rom", type=Path, required=True)
    parser.add_argument("--native-writer", type=Path, required=True)
    parser.add_argument("--native-graphics-editor", type=Path,
                        help="exercise Qt Graphics pixel edit; omit for document probe")
    parser.add_argument("--native-renderer", type=Path, required=True)
    parser.add_argument("--mame", type=Path, required=True)
    args = parser.parse_args()
    os.environ["RETROLARIUM_FLICKY_LEVEL_WRITER"] = str(args.native_writer.resolve())
    scratch = Path(__file__).resolve().parents[2] / "build"
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="flicky-tiles-mame-", dir=scratch) as name:
        root = Path(name)
        import_flicky_source(args.source_root, args.rom, root)
        create_flicky_studio_project(root)
        project = root / "flicky-ue.rlj"
        build_studio_project(project)
        baseline_rom = (root / "build" / "flicky-edited.bin").read_bytes()
        assert verify_studio_project(project)["status"] == "current"
        baseline_png = root / "baseline-vdp.png"
        launch(studio_flicky_round_playtest_plan(project, mame=args.mame, round=1),
               1, root, baseline_png)
        source = root / "flicky-source"
        render_native(args.native_renderer, source, root / "native-baseline.png")

        graphics = source / WORKSPACE_FILES["graphics_assets"]
        original = graphics.read_bytes()
        document = json.loads(original)
        asset = next(item for item in document["assets"] if item["id"] == "level_tiles")
        old_pixel = asset["tiles"][13][0][0]
        assert old_pixel == 14
        new_pixel = 15
        expected_tiles = copy.deepcopy(asset["tiles"])
        expected_tiles[13][0][0] = new_pixel
        if args.native_graphics_editor:
            subprocess.run([
                str(args.native_graphics_editor.resolve()), "--edit",
                str(graphics), "level_tiles", "13", "0", "0", str(new_pixel),
            ], check=True)
        else:
            print("Flicky tile probe uses document edit; Qt click is unverified")
            asset["tiles"][13][0][0] = new_pixel
            graphics.write_text(json.dumps(document, indent=2) + "\n",
                                encoding="utf-8")
        saved = json.loads(graphics.read_text(encoding="utf-8"))
        changed_asset = next(item for item in saved["assets"]
                             if item["id"] == "level_tiles")
        assert changed_asset["tiles"][13][0][0] == new_pixel
        try:
            studio_flicky_round_playtest_plan(project, mame=args.mame, round=1)
        except StudioProjectError as error:
            assert "stale" in str(error)
        else:
            raise AssertionError("MAME accepted stale Flicky graphics build")
        build_studio_project(project)
        assert verify_studio_project(project)["status"] == "current"
        edited_rom = (root / "build" / "flicky-edited.bin").read_bytes()
        assert edited_rom[0x18E:0x190] == cartridge_checksum(edited_rom).to_bytes(2, "big")
        reference = (source / "data/artnem/data_LevelTiles.bin").read_bytes()
        address = baseline_rom.find(reference)
        assert address >= 0 and baseline_rom.find(reference, address + 1) < 0
        changed_offsets = {index for index, pair in enumerate(
            zip(baseline_rom, edited_rom)) if pair[0] != pair[1]}
        assert changed_offsets and all(
            address <= index < address + len(reference) or index in (0x18E, 0x18F)
            for index in changed_offsets), "tile edit changed ROM outside its Nemesis slot"
        sys.path.insert(0, str(source / "scripts"))
        from formats import nemesis_dec  # noqa: E402
        from authoring.graphics_studio_model import packed_4bpp_to_tiles  # noqa: E402
        decoded = packed_4bpp_to_tiles(nemesis_dec.decompress(
            edited_rom[address:address + len(reference)]))
        assert decoded == expected_tiles, \
            "Nemesis ROM readback differs outside the single edited tile pixel"
        print(f"Flicky tile 13 ROM readback: Nemesis slot ${address:X}, "
              f"{len(changed_offsets)} changed bytes including checksum")

        edited_png = root / "edited-vdp.png"
        launch(studio_flicky_round_playtest_plan(project, mame=args.mame, round=1),
               1, root, edited_png)
        render_native(args.native_renderer, source, root / "native-edited.png")
        emulated_delta = changed_pixels(baseline_png, edited_png)
        native_delta = changed_pixels(root / "native-baseline.png",
                                      root / "native-edited.png")
        assert emulated_delta == native_delta and len(emulated_delta) == 7, (
            f"Flicky level-tile VDP delta differs: "
            f"MAME={len(emulated_delta)} native={len(native_delta)} "
            f"overlap={len(emulated_delta & native_delta)}"
        )
        print(f"Flicky tile 13 VDP delta: MAME={len(emulated_delta)} "
              f"native={len(native_delta)} overlap={len(emulated_delta & native_delta)}")
        graphics.write_bytes(original)
        assert build_studio_project(project)["artifact"]["sha256"] == CANONICAL_SHA256
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
