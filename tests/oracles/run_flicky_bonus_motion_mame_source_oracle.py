#!/usr/bin/env python3
"""Qt bonus-motion words -> isolated ROM -> live MAME chick behavior."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile

from retrolarium_kit.games.flicky.content import (
    CANONICAL_SHA256, WORKSPACE_FILES, import_flicky_source,
)
from retrolarium_core.megadrive import cartridge_checksum
from retrolarium_kit.studio.project import build_studio_project, verify_studio_project
from retrolarium_kit.studio.create import create_flicky_studio_project
from retrolarium_kit.studio.playtest import studio_flicky_round_playtest_plan


def run_mame(project: Path, mame: Path, root: Path, label: str,
             duration: int = 60) -> list[tuple[int, ...]]:
    plan = studio_flicky_round_playtest_plan(project, mame=mame, round=3)
    trace = root / f"{label}-bonus.csv"
    result = root / f"{label}-result.txt"
    snapshot = root / f"{label}-vdp.png"
    environment = os.environ.copy()
    environment.update(plan["environment"])
    environment.update({
        "FLICKY_PLAYTEST_EXIT": "1",
        "FLICKY_PLAYTEST_RESULT": str(result),
        "FLICKY_PLAYTEST_SNAPSHOT": str(snapshot),
        "FLICKY_PLAYTEST_SNAPSHOT_DELAY": str(duration),
        "FLICKY_PLAYTEST_BONUS_TRACE": str(trace),
    })
    run = subprocess.run([
        plan["program"], *plan["arguments"], "-video", "none", "-sound", "none",
        "-nothrottle", "-noreadconfig", "-nowriteconfig", "-seconds_to_run", "30",
    ], cwd=root, env=environment, text=True, capture_output=True, timeout=90)
    assert run.returncode == 0 and result.is_file() and snapshot.is_file(), (
        f"MAME bonus playtest failed: {run.returncode}\n{run.stdout}\n{run.stderr}")
    assert "status=ready" in result.read_text(encoding="utf-8")
    rows = [tuple(int(value, 16) if index else int(value)
                  for index, value in enumerate(line.split(",")))
            for line in trace.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == duration + 1 and all(
        len(row) == 10 and row[1] == 0x2C for row in rows
    ), (
        f"bonus trace did not remain in mode 2C: {rows[:3]}")
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--rom", type=Path, required=True)
    parser.add_argument("--native-writer", type=Path, required=True)
    parser.add_argument("--native-sequences-editor", type=Path, required=True)
    parser.add_argument("--native-renderer", type=Path,
                        help="compare the edited live Qt bonus scene with MAME")
    parser.add_argument("--mame", type=Path, required=True)
    args = parser.parse_args()
    os.environ["RETROLARIUM_FLICKY_LEVEL_WRITER"] = str(
        args.native_writer.resolve())
    scratch = Path(__file__).resolve().parents[1] / "build"
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="flicky-bonus-motion-", dir=scratch) as name:
        root = Path(name)
        import_flicky_source(args.source_root, args.rom, root)
        create_flicky_studio_project(root)
        project = root / "flicky-ue.rlj"
        zero = build_studio_project(project)
        assert zero["artifact"]["sha256"] == CANONICAL_SHA256
        baseline = (root / zero["artifact"]["path"]).read_bytes()
        baseline_trace = run_mame(project, args.mame, root, "baseline", 300)
        sequences_path = root / "flicky-source" / WORKSPACE_FILES["graphics_sequences"]
        original_document = sequences_path.read_bytes()
        document = json.loads(original_document)
        table = next(record for record in document["bonus_motion"]
                     if record["id"] == "Bonus_ChickDelays0")
        payload = b"".join(word.to_bytes(2, "big") for word in table["words"])
        assert baseline.count(payload) == 1
        address = baseline.find(payload) + 2
        assert table["words"][1] == 15
        subprocess.run([
            str(args.native_sequences_editor.resolve()), "--edit-bonus-word",
            str(sequences_path), table["id"], "1", "0",
        ], check=True)
        assert verify_studio_project(project)["status"] == "mismatch"
        edited = build_studio_project(project)
        assert verify_studio_project(project)["status"] == "current"
        image = (root / edited["artifact"]["path"]).read_bytes()
        assert image[address:address + 2] == b"\0\0"
        assert int.from_bytes(image[0x18E:0x190], "big") == cartridge_checksum(image)
        differences = {index for index, (old, new) in enumerate(zip(baseline, image))
                       if old != new}
        assert differences - {0x18E, 0x18F} == {address + 1}, differences
        edited_trace = run_mame(project, args.mame, root, "edited")
        if args.native_renderer:
            from PIL import Image

            source = root / "flicky-source"
            native_png = root / "edited-native-vdp.png"
            native = subprocess.run([
                str(args.native_renderer.resolve()), "--render-bonus-phase",
                str(source),
                str(source / WORKSPACE_FILES["level_layouts"]),
                str(source / WORKSPACE_FILES["graphics_assets"]),
                str(source / WORKSPACE_FILES["graphics_semantics"]),
                str(sequences_path), "3", "60", str(native_png),
            ], text=True, capture_output=True)
            assert native.returncode == 0, native.stderr or native.stdout
            dac = {0: 0, 36: 52, 72: 87, 108: 116,
                   144: 144, 180: 172, 216: 206, 252: 255}
            with (Image.open(native_png) as native_image,
                  Image.open(root / "edited-vdp.png") as mame_image):
                preview = native_image.convert("RGB")
                screenshot = mame_image.convert("RGB")
                assert preview.size == screenshot.size == (256, 224)
                matches = sum(
                    tuple(dac[value] for value in preview.getpixel((x, y)))
                    == screenshot.getpixel((x, y))
                    for y in range(224) for x in range(256))
            print(f"edited bonus delay Qt scene/MAME VDP: {matches}/57344 pixels")
            assert matches == 256 * 224, (
                f"edited bonus delay scene differs from MAME: {matches}/57344")
        differences_in_timer = [(before[0], before[4], after[4])
                                for before, after in zip(baseline_trace, edited_trace)
                                if before[4] != after[4]]
        assert differences_in_timer, "bonus delay ROM edit did not affect MAME chick timer"
        print(f"Flicky bonus GUI→ROM→MAME: table word at ${address:05X} "
              f"15→0; {len(differences_in_timer)} differing chick-timer frames; "
              f"first={differences_in_timer[0]}")
        sequences_path.write_bytes(original_document)
        velocity = next(record for record in document["bonus_motion"]
                        if record["id"] == "Bonus_ChickVelocities0")
        velocity_payload = b"".join(word.to_bytes(2, "big")
                                    for word in velocity["words"])
        assert baseline.count(velocity_payload) == 1
        velocity_address = baseline.find(velocity_payload)
        assert velocity["words"][0] == 0x0EB4
        subprocess.run([
            str(args.native_sequences_editor.resolve()), "--edit-bonus-word",
            str(sequences_path), velocity["id"], "0", str(0x18B4),
        ], check=True)
        velocity_build = build_studio_project(project)
        velocity_image = (root / velocity_build["artifact"]["path"]).read_bytes()
        assert velocity_image[velocity_address:velocity_address + 2] == bytes.fromhex("18B4")
        assert int.from_bytes(velocity_image[0x18E:0x190], "big") == cartridge_checksum(velocity_image)
        velocity_differences = {
            index for index, (old, new) in enumerate(zip(baseline, velocity_image))
            if old != new
        }
        assert velocity_differences - {0x18E, 0x18F} == {velocity_address}
        velocity_trace = run_mame(project, args.mame, root, "velocity", 300)
        fly_differences = [(before[0], before[6], after[6], before[8], after[8])
                           for before, after in zip(baseline_trace, velocity_trace)
                           if before[5] & 0x7C == 4 and after[5] & 0x7C == 4
                           and before[6] != after[6]]
        assert fly_differences, "bonus velocity ROM edit did not move chick slot 0 in MAME"
        print(f"Flicky bonus velocity GUI→ROM→MAME: table word at "
              f"${velocity_address:05X} 0EB4→18B4; "
              f"{len(fly_differences)} differing flight X frames; "
              f"first={fly_differences[0]}")
        sequences_path.write_bytes(original_document)
        trajectory_index = next(record for record in document["bonus_motion"]
                                if record["id"] == "Bonus_ChickTrajIndex0")
        assert trajectory_index["words"] == [0] * 10
        next_index_table = bytes.fromhex("0101" * 10)
        assert baseline.count(next_index_table) == 1
        index_address = baseline.find(next_index_table) - 20
        assert baseline[index_address:index_address + 20] == bytes(20)
        subprocess.run([
            str(args.native_sequences_editor.resolve()), "--edit-bonus-word",
            str(sequences_path), trajectory_index["id"], "0", str(0x0101),
        ], check=True)
        index_build = build_studio_project(project)
        index_image = (root / index_build["artifact"]["path"]).read_bytes()
        assert index_image[index_address:index_address + 2] == bytes.fromhex("0101")
        assert int.from_bytes(index_image[0x18E:0x190], "big") == cartridge_checksum(index_image)
        index_differences = {
            index for index, (old, new) in enumerate(zip(baseline, index_image))
            if old != new
        }
        assert index_differences - {0x18E, 0x18F} == {
            index_address, index_address + 1
        }
        index_trace = run_mame(project, args.mame, root, "trajectory-index", 300)
        index_flight = [(before[0], before[6], after[6])
                        for before, after in zip(baseline_trace, index_trace)
                        if before[5] & 0x7C == 4 and after[5] & 0x7C == 4
                        and before[6] != after[6]]
        assert index_flight, "bonus trajectory index edit did not move chick slot 0"
        print(f"Flicky bonus trajectory index GUI→ROM→MAME: word at "
              f"${index_address:05X} 0000→0101; "
              f"{len(index_flight)} differing flight X frames; "
              f"first={index_flight[0]}")
        trajectory = next(record for record in document["bonus_motion"]
                          if record["id"] == "Bonus_Trajectory1")
        trajectory_payload = b"".join(word.to_bytes(2, "big")
                                       for word in trajectory["words"])
        assert baseline.count(trajectory_payload) == 1
        trajectory_address = baseline.find(trajectory_payload)
        assert trajectory["words"][1] == 0xF4E8
        subprocess.run([
            str(args.native_sequences_editor.resolve()), "--edit-bonus-word",
            str(sequences_path), trajectory["id"], "1", str(0xF8E8),
        ], check=True)
        trajectory_build = build_studio_project(project)
        trajectory_image = (root / trajectory_build["artifact"]["path"]).read_bytes()
        assert trajectory_image[trajectory_address + 2:trajectory_address + 4] == bytes.fromhex("F8E8")
        assert int.from_bytes(trajectory_image[0x18E:0x190], "big") == cartridge_checksum(trajectory_image)
        trajectory_differences = {
            index for index, (old, new) in enumerate(zip(index_image, trajectory_image))
            if old != new
        }
        assert trajectory_differences - {0x18E, 0x18F} == {trajectory_address + 2}
        trajectory_trace = run_mame(project, args.mame, root, "trajectory", 300)
        trajectory_flight = [(before[0], before[6], after[6])
                             for before, after in zip(index_trace, trajectory_trace)
                             if before[5] & 0x7C == 4 and after[5] & 0x7C == 4
                             and before[6] != after[6]]
        assert trajectory_flight, "bonus trajectory data edit did not move chick slot 0"
        print(f"Flicky bonus trajectory GUI→ROM→MAME: word at "
              f"${trajectory_address + 2:05X} F4E8→F8E8; "
              f"{len(trajectory_flight)} differing flight X frames; "
              f"first={trajectory_flight[0]}")
        sequences_path.write_bytes(original_document)
        restored = build_studio_project(project)
        assert restored["artifact"]["sha256"] == CANONICAL_SHA256
        print("Flicky bonus restore: exact canonical ROM SHA")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
