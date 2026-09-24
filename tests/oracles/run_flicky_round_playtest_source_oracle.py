#!/usr/bin/env python3
"""Prove a built Flicky ROM boots requested rounds under MAME.

The neighboring source and ROM are read-only. All generated output is under
this repository's ignored build directory.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import struct
import tempfile

from retrolarium_kit.flicky_content import WORKSPACE_FILES, import_flicky_source
from retrolarium_kit.megadrive import cartridge_checksum
from retrolarium_kit.studio_project import (
    StudioProjectError,
    build_studio_project,
    create_flicky_studio_project,
    studio_flicky_round_playtest_plan,
)


def launch(plan: dict, round_number: int, root: Path,
           snapshot: Path | None = None, snapshot_delay: int = 60) -> None:
    result = root / f"round-{round_number}.txt"
    result.unlink(missing_ok=True)
    if snapshot is not None:
        snapshot.unlink(missing_ok=True)
    environment = os.environ.copy()
    environment.update(plan["environment"])
    environment.update({
        "FLICKY_PLAYTEST_EXIT": "1",
        "FLICKY_PLAYTEST_RESULT": str(result),
    })
    if snapshot is not None:
        environment["FLICKY_PLAYTEST_SNAPSHOT"] = str(snapshot.resolve())
        environment["FLICKY_PLAYTEST_SNAPSHOT_DELAY"] = str(snapshot_delay)
    run = subprocess.run(
        [plan["program"], *plan["arguments"],
         "-video", "none", "-sound", "none", "-nothrottle",
         "-noreadconfig", "-nowriteconfig", "-seconds_to_run", "30"],
        cwd=root, env=environment, text=True, capture_output=True, timeout=90,
    )
    if run.returncode or not result.is_file():
        raise AssertionError(
            f"Flicky round {round_number} did not start: "
            f"exit={run.returncode}\n{run.stdout}\n{run.stderr}"
        )
    fields = dict(item.split("=", 1)
                  for item in result.read_text(encoding="utf-8").split())
    expected_word = ((round_number // 10 * 16 + round_number % 10) << 8) \
        | round_number
    assert fields["status"] == "ready", (
        f"Flicky round {round_number} did not become ready: {fields}; "
        f"stdout={run.stdout[-2000:]}; stderr={run.stderr[-2000:]}"
    )
    assert int(fields["round"]) == round_number, fields
    assert int(fields["round_word"], 16) == expected_word, fields
    assert int(fields["mode"], 16) == 0x24, fields
    assert int(fields["lives"]) == 3, fields
    if snapshot is not None:
        png = snapshot.read_bytes()
        assert png[:16] == b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR", \
            "MAME did not produce a PNG from the emulated VDP screen"
        width, height = struct.unpack(">II", png[16:24])
        assert width >= 256 and height >= 224, \
            f"MAME VDP screenshot is unexpectedly small: {width}x{height}"
        print(f"round {round_number} VDP snapshot: {width}x{height}, {len(png)} bytes")
    print(f"round {round_number}: {result.read_text(encoding='utf-8').strip()}")


def compare_vdp_scene(native_renderer: Path, source: Path, snapshot: Path,
                      output: Path) -> None:
    """Compare static VDP geometry plus bounded full-frame animation drift.

    The source preview uses linear 3-bit RGB; MAME uses the Mega Drive DAC
    palette. Normalization leaves HUD and time-dependent sprites as the main
    expected differences in the whole-frame comparison.
    """
    try:
        from PIL import Image
    except ImportError as error:
        raise RuntimeError("Pillow is required for --native-renderer VDP comparison") from error

    root = source / "content" / "workspace"
    subprocess.run([
        str(native_renderer.resolve()), "--render-image", str(source),
        str(root / "level" / "levels.json"),
        str(root / "graphics" / "graphics.json"),
        str(root / "graphics" / "semantics.json"),
        str(root / "graphics" / "sequences.json"),
        "1", str(output),
    ], check=True)
    with Image.open(output) as native_image, Image.open(snapshot) as mame_image:
        native = native_image.convert("RGB")
        mame = mame_image.convert("RGB")
        assert native.size == mame.size == (256, 224), (native.size, mame.size)
        dac = {0: 0, 36: 52, 72: 87, 108: 116,
               144: 144, 180: 172, 216: 206, 252: 255}
        native_rgb = native.tobytes()
        mame_rgb = mame.tobytes()
        normalized = [tuple(dac[value] for value in native_rgb[i:i + 3])
                      for i in range(0, len(native_rgb), 3)]
        emulated = [tuple(mame_rgb[i:i + 3])
                    for i in range(0, len(mame_rgb), 3)]
    width, height = 256, 224
    matches = sum(left == right for left, right in zip(normalized, emulated))
    agreement = matches / (width * height)
    assert agreement >= 0.93, (
        f"Flicky round 1 native scene/MAME VDP agreement is only {agreement:.2%}"
    )
    # Static strips cross the tilemap, platform, and background independently
    # of the HUD and animated windows/actors on this deterministic frame.
    for left, top, right, bottom in ((0, 16, 256, 24), (32, 64, 96, 96)):
        assert all(normalized[y * width + x] == emulated[y * width + x]
                   for y in range(top, bottom) for x in range(left, right)), (
            f"Flicky static VDP region differs: {(left, top, right, bottom)}"
        )
    print(f"round 1 native/MAME VDP: {agreement:.2%} full-frame pixels; "
          "static tilemap regions pixel-exact after DAC normalization")


def compare_player_vdp(baseline: Path, edited: Path) -> None:
    """Check that moving the player changes its expected VDP footprint only."""
    try:
        from PIL import Image, ImageChops
    except ImportError as error:
        raise RuntimeError("Pillow is required for Qt player VDP comparison") from error
    with Image.open(baseline) as before_image, Image.open(edited) as after_image:
        before = before_image.convert("RGB")
        after = after_image.convert("RGB")
        assert before.size == after.size == (256, 224)
        bounds = ImageChops.difference(before, after).getbbox()
    assert bounds == (119, 176, 151, 208), (
        f"Qt player movement changed unexpected MAME VDP region: {bounds}"
    )
    print(f"round 1 Qt player movement visible in MAME VDP: bounds={bounds}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--rom", type=Path, required=True)
    parser.add_argument("--native-writer", type=Path, required=True)
    parser.add_argument("--mame", type=Path, required=True)
    parser.add_argument("--native-renderer", type=Path,
                        help="also compare the native Level scene with MAME VDP pixels")
    parser.add_argument("--native-level-editor", type=Path,
                        help="move the round-1 player via its Qt Levels widget")
    parser.add_argument("--snapshot-output", type=Path,
                        help="copy the round-1 VDP PNG for local visual inspection")
    parser.add_argument("--diagnostics-dir", type=Path,
                        help="retain zero/edited ROM images for failed-gate diagnosis")
    parser.add_argument("--snapshot-delay", type=int, default=60)
    args = parser.parse_args()
    assert 0 <= args.snapshot_delay <= 60
    if args.native_level_editor and args.snapshot_delay != 60:
        parser.error("--native-level-editor requires the deterministic 60-frame snapshot")
    os.environ["RETROLARIUM_FLICKY_LEVEL_WRITER"] = str(args.native_writer.resolve())
    scratch = Path(__file__).resolve().parents[2] / "build"
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="flicky-round-gate-", dir=scratch) as name:
        root = Path(name)
        import_flicky_source(args.source_root, args.rom, root)
        inspection = create_flicky_studio_project(root)
        levels = next(tab for tab in inspection["workspace"]["tabs"]
                      if tab["id"] == "levels")
        assert "playtest" in levels["capabilities"]
        project = root / "flicky-ue.rlj"
        build_studio_project(project)
        baseline_rom = (root / "build" / "flicky-edited.bin").read_bytes()
        if args.diagnostics_dir:
            args.diagnostics_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / "build" / "flicky-edited.bin",
                            args.diagnostics_dir / "zero-edit.bin")
        source = root / "flicky-source"
        for round_number in (1, 26, 48):
            plan = studio_flicky_round_playtest_plan(
                project, mame=args.mame, round=round_number)
            assert plan["operation"] == "retrolarium.studio.flicky-round-playtest"
            launch(plan, round_number, root,
                   root / "round-1-vdp.png" if round_number == 1 else None,
                   snapshot_delay=args.snapshot_delay)
            if round_number == 1 and args.native_renderer is not None:
                compare_vdp_scene(args.native_renderer, source,
                                  root / "round-1-vdp.png", root / "native-round-1.png")
            if round_number == 1 and args.snapshot_output is not None:
                destination = args.snapshot_output.resolve()
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(root / "round-1-vdp.png", destination)
        for invalid in (0, 49, True):
            try:
                studio_flicky_round_playtest_plan(
                    project, mame=args.mame, round=invalid)
            except StudioProjectError:
                pass
            else:
                raise AssertionError(f"invalid round was accepted: {invalid!r}")
        level_path = root / "flicky-source" / WORKSPACE_FILES["level_layouts"]
        original_level = level_path.read_bytes()
        document = json.loads(original_level)
        document["layouts"][0]["collision"][4][0] ^= 1
        level_path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        try:
            studio_flicky_round_playtest_plan(project, mame=args.mame, round=26)
        except StudioProjectError as error:
            assert "stale" in str(error)
        else:
            raise AssertionError("stale Flicky ROM was accepted")
        build_studio_project(project)
        if args.diagnostics_dir:
            shutil.copyfile(root / "build" / "flicky-edited.bin",
                            args.diagnostics_dir / "edited.bin")
        launch(studio_flicky_round_playtest_plan(
            project, mame=args.mame, round=1), 1, root,
            root / "round-1-edited-vdp.png", snapshot_delay=args.snapshot_delay)
        launch(studio_flicky_round_playtest_plan(
            project, mame=args.mame, round=26), 26, root)
        level_path.write_bytes(original_level)
        document = json.loads(original_level)
        player = document["layouts"][0]["player"]
        moved_x = player[0] + 1
        assert moved_x < 32
        if args.native_level_editor:
            subprocess.run([
                str(args.native_level_editor.resolve()), "--edit-object",
                str(level_path), "0", "player", "0", str(moved_x),
                str(player[1]),
            ], check=True)
        else:
            document["layouts"][0]["player"][0] = moved_x
            level_path.write_text(json.dumps(document, indent=2) + "\n",
                                  encoding="utf-8")
        saved = json.loads(level_path.read_text(encoding="utf-8"))
        assert saved["layouts"][0]["player"] == [moved_x, player[1]]
        build_studio_project(project)
        player_rom = (root / "build" / "flicky-edited.bin").read_bytes()
        baseline_block = bytes(document["layouts"][0]["baseline_bytes"])
        address = baseline_rom.find(baseline_block)
        assert address >= 0 and baseline_rom.find(baseline_block, address + 1) < 0
        player_address = address + len(document["layouts"][0]["baseline_collision_stream"])
        assert player_rom[player_address] == moved_x
        assert player_rom[0x18E:0x190] == cartridge_checksum(player_rom).to_bytes(2, "big")
        expected_changes = {player_address} | {
            offset for offset in (0x18E, 0x18F)
            if player_rom[offset] != baseline_rom[offset]
        }
        actual_changes = {offset for offset, pair in enumerate(
            zip(player_rom, baseline_rom)) if pair[0] != pair[1]}
        assert actual_changes == expected_changes, (
            f"player move touched unexpected ROM bytes: {sorted(actual_changes)[:16]}"
        )
        launch(studio_flicky_round_playtest_plan(
            project, mame=args.mame, round=1), 1, root,
            root / "round-1-player-vdp.png", snapshot_delay=args.snapshot_delay)
        if args.native_level_editor:
            compare_player_vdp(root / "round-1-vdp.png",
                               root / "round-1-player-vdp.png")
        if args.diagnostics_dir:
            shutil.copyfile(root / "round-1-vdp.png",
                            args.diagnostics_dir / "round-1-baseline.png")
            shutil.copyfile(root / "round-1-player-vdp.png",
                            args.diagnostics_dir / "round-1-player.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
