"""Pin shared bonus placement edits to one 68000 immediate and exact restore."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile

from retrolarium_kit.games.flicky.bonus_placement import validate_bonus_placements
from retrolarium_kit.games.flicky.content import (
    CANONICAL_SHA256, SOURCE_DIRECTORY, WORKSPACE_FILES,
    build_flicky_source_snapshot, import_flicky_source,
)
from retrolarium_kit.studio.project import build_studio_project
from retrolarium_kit.studio.create import create_flicky_studio_project
from run_bonus_motion_mame_source_oracle import run_mame


def render_bonus(renderer: Path, source: Path, output: Path) -> None:
    run = subprocess.run([
        str(renderer.resolve()), "--render-bonus-phase", str(source),
        str(source / WORKSPACE_FILES["level_layouts"]),
        str(source / WORKSPACE_FILES["graphics_assets"]),
        str(source / WORKSPACE_FILES["graphics_semantics"]),
        str(source / WORKSPACE_FILES["graphics_sequences"]),
        "3", "60", str(output),
    ], text=True, capture_output=True)
    assert run.returncode == 0, run.stderr or run.stdout


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--rom", type=Path, required=True)
    parser.add_argument("--native-writer", type=Path, required=True)
    parser.add_argument("--native-editor", type=Path,
                        help="also edit and save through the Qt Levels controls")
    parser.add_argument("--native-renderer", type=Path,
                        help="render live bonus scene for optional MAME comparison")
    parser.add_argument("--mame", type=Path,
                        help="also compare edited bonus scene with MAME VDP")
    args = parser.parse_args()
    if args.mame and not args.native_renderer:
        parser.error("--mame requires --native-renderer")
    scratch = Path(__file__).resolve().parents[3] / "build"
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="flicky-bonus-placement-", dir=scratch) as name:
        project = Path(name)
        import_flicky_source(args.source_root, args.rom, project)
        source = project / SOURCE_DIRECTORY
        path = source / WORKSPACE_FILES["level_layouts"]
        original = path.read_bytes()
        document = json.loads(original)
        records = document["bonus_placements"]
        assert [record["id"] for record in records] == [
            "seesaw", "tiger", "chick_spawn"]
        assert [(record["x"], record["y"]) for record in records] == [
            ([0x140, 0xC0], 0x150), ([0x130, 0xD0], 0x150),
            ([0x80, 0x17F], 0x150)]
        zero = build_flicky_source_snapshot(
            project, native_writer=args.native_writer)
        assert zero["sha256"] == CANONICAL_SHA256, "bonus zero-edit changed ROM"
        canonical = args.rom.read_bytes()
        signature = bytes.fromhex("01400030")
        assert canonical.count(signature) == 1
        immediate = canonical.find(signature) + 1
        assert canonical[immediate] == 0x40
        if args.native_renderer:
            render_bonus(args.native_renderer, source, project / "baseline-vdp.png")

        document["bonus_placements"][0]["x"][0] = 0x148
        validate_bonus_placements(document, source)
        if args.native_editor:
            subprocess.run([
                str(args.native_editor.resolve()), "--edit-bonus-placement",
                str(path), "seesaw", str(0x148), str(0xC0), str(0x150),
            ], check=True)
            assert json.loads(path.read_text(encoding="utf-8"))[
                "bonus_placements"][0]["x"][0] == 0x148
        else:
            path.write_text(json.dumps(document, indent=2) + "\n",
                            encoding="utf-8", newline="\n")
        edited = build_flicky_source_snapshot(
            project, native_writer=args.native_writer)
        rom = Path(edited["artifact"]).read_bytes()
        changed = {index for index, (before, after) in enumerate(zip(canonical, rom))
                   if before != after}
        assert changed - {0x18E, 0x18F} == {immediate}, \
            f"bonus seesaw X touched unrelated ROM bytes: {sorted(changed)}"
        assert rom[immediate] == 0x48, "edited bonus placement did not read back"
        staged = (source / "build/content/src/game/bonus/objects.s").read_text(
            encoding="utf-8")
        assert "move.w  #$148,$30(a0)" in staged
        if args.native_renderer:
            render_bonus(args.native_renderer, source, project / "edited-native-vdp.png")
        if args.mame:
            from PIL import Image

            os.environ["RETROLARIUM_FLICKY_LEVEL_WRITER"] = str(
                args.native_writer.resolve())
            create_flicky_studio_project(project)
            studio = build_studio_project(project / "flicky-ue.rlj")
            assert studio["artifact"]["sha256"] == edited["sha256"]
            run_mame(project / "flicky-ue.rlj", args.mame, project, "edited", 60)
            dac = {0: 0, 36: 52, 72: 87, 108: 116,
                   144: 144, 180: 172, 216: 206, 252: 255}
            with (Image.open(project / "baseline-vdp.png") as baseline_image,
                  Image.open(project / "edited-native-vdp.png") as native_image,
                  Image.open(project / "edited-vdp.png") as mame_image):
                baseline_rgb = baseline_image.convert("RGB")
                native_rgb = native_image.convert("RGB")
                mame_rgb = mame_image.convert("RGB")
                assert baseline_rgb.size == native_rgb.size == mame_rgb.size == (256, 224)
                changed_pixels = sum(
                    baseline_rgb.getpixel((x, y)) != native_rgb.getpixel((x, y))
                    for y in range(224) for x in range(256))
                matching_pixels = sum(
                    tuple(dac[value] for value in native_rgb.getpixel((x, y)))
                    == mame_rgb.getpixel((x, y))
                    for y in range(224) for x in range(256))
            assert changed_pixels > 0, "shared bonus placement had no scene effect"
            assert matching_pixels == 256 * 224, \
                f"edited bonus placement scene/MAME: {matching_pixels}/57344"
            print(f"edited bonus placement: {changed_pixels} scene pixels changed; "
                  f"MAME VDP {matching_pixels}/57344 exact")

        path.write_bytes(original)
        restored = build_flicky_source_snapshot(
            project, native_writer=args.native_writer)
        assert restored["sha256"] == CANONICAL_SHA256, \
            "bonus placement restore did not recover exact canonical ROM"

        # Every exposed scalar is a fixed-width 68000 immediate. Verify the
        # remaining eight independently, not just the visible seesaw example.
        y_signature = bytes.fromhex("01500024")
        y_addresses = [index for index in range(len(canonical))
                       if canonical.startswith(y_signature, index)]
        assert len(y_addresses) == 3
        checked = 1
        for group_index, baseline_record in enumerate(json.loads(original)[
                "bonus_placements"]):
            for field, side in (("x", 0), ("x", 1), ("y", None)):
                if group_index == 0 and field == "x" and side == 0:
                    continue
                candidate = json.loads(original)
                record = candidate["bonus_placements"][group_index]
                old = record[field] if side is None else record[field][side]
                new = old - 8 if old + 8 > 0x17F else old + 8
                if side is None:
                    record[field] = new
                    address = y_addresses[group_index] + 1
                else:
                    record[field][side] = new
                    payload = old.to_bytes(2, "big") + b"\0\x30"
                    assert canonical.count(payload) == 1
                    address = canonical.find(payload) + 1
                validate_bonus_placements(candidate, source)
                path.write_text(json.dumps(candidate, indent=2) + "\n",
                                encoding="utf-8", newline="\n")
                trial = build_flicky_source_snapshot(
                    project, native_writer=args.native_writer)
                trial_rom = Path(trial["artifact"]).read_bytes()
                trial_changed = {index for index, (before, after) in enumerate(
                    zip(canonical, trial_rom)) if before != after}
                assert trial_changed - {0x18E, 0x18F} == {address}, (
                    baseline_record["id"], field, side, sorted(trial_changed))
                assert trial_rom[address] == new & 0xFF
                checked += 1
        path.write_bytes(original)
        assert build_flicky_source_snapshot(
            project, native_writer=args.native_writer)["sha256"] == CANONICAL_SHA256

        document["bonus_placements"][0]["baseline_x"][0] = 0x148
        try:
            validate_bonus_placements(document, source)
        except ValueError:
            pass
        else:
            raise AssertionError("bonus placement baseline drift was accepted")
    print("Flicky bonus placement: exact zero-edit, "
          + ("Qt→Save→" if args.native_editor else "")
          + f"{checked} one-immediate ROM readbacks, exact restore, "
            "baseline drift rejection passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
