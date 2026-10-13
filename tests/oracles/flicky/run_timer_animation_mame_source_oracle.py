#!/usr/bin/env python3
"""Probe the Flicky timer-animation delay through ROM and emulated VDP."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from PIL import Image, ImageChops

from retrolarium_kit.games.flicky.content import (
    CANONICAL_SHA256, WORKSPACE_FILES, import_flicky_source,
)
from retrolarium_core.megadrive import cartridge_checksum
from retrolarium_kit.studio.project import (
    StudioProjectError,
    build_studio_project,
    verify_studio_project,
)
from retrolarium_kit.studio.create import create_flicky_studio_project
from retrolarium_kit.studio.playtest import studio_flicky_round_playtest_plan
from run_content_build_source_oracle import timer_animation_address
from run_round_playtest_source_oracle import launch

SNAPSHOT_DELAY = 180


def changed_pixels(before: Path, after: Path) -> set[tuple[int, int]]:
    with Image.open(before) as first, Image.open(after) as second:
        left = first.convert("RGB")
        right = second.convert("RGB")
        assert left.size == right.size == (256, 224)
        difference = ImageChops.difference(left, right)
        pixels = difference.load()
        return {(x, y) for y in range(224) for x in range(256)
                if pixels[x, y] != (0, 0, 0)}


def animation_trace(path: Path) -> dict[int, tuple[int, ...]]:
    rows = {}
    for line in path.read_text(encoding="ascii").splitlines():
        frame, *state = line.split(",")
        assert len(state) == 7, f"invalid MAME animation trace: {line!r}"
        rows[int(frame)] = tuple(int(field, 16) for field in state)
    assert rows, "MAME animation trace is empty"
    return rows


def assert_ui_timer_phase(rows: dict[int, tuple[int, ...]],
                          delay: int) -> tuple[int, int]:
    # Mode, UI index/countdown/completed, arrow index/countdown/completed.
    starts = [frame for frame, state in rows.items()
              if state[:4] == (0x24, 1, delay, 0)
              and rows.get(frame - 1, ())[:4] == (0x24, 0, 0, 0)]
    assert len(starts) == 1, f"UI timer start is ambiguous: {starts}"
    start = starts[0]
    ticks = delay + 1
    for slot in (1, 2, 3):
        first = start + (slot - 1) * ticks
        assert rows[first][1:4] == (slot, delay, 0), (slot, rows[first])
        assert rows[first + delay][1:4] == (slot, 0, 0), \
            (slot, rows[first + delay])
    complete = start + 3 * ticks
    assert rows[complete][1:4] == (0, delay, 1), rows[complete]
    assert rows[complete][4:7] == (1, 4, 0), \
        "entry-arrow animation did not start when UI timer completed"
    return start, complete


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--rom", type=Path, required=True)
    parser.add_argument("--native-writer", type=Path, required=True)
    parser.add_argument("--native-sequences-editor", type=Path)
    parser.add_argument("--mame", type=Path, required=True)
    parser.add_argument("--diagnostics-dir", type=Path)
    args = parser.parse_args()
    os.environ["RETROLARIUM_FLICKY_LEVEL_WRITER"] = str(args.native_writer.resolve())
    scratch = Path(__file__).resolve().parents[3] / "build"
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="flicky-timer-mame-", dir=scratch) as name:
        root = Path(name)
        import_flicky_source(args.source_root, args.rom, root)
        ram_source = (root / "flicky-source/src/memory/ram.inc").read_text(
            encoding="utf-8")
        for label, address in (("Ram_UIAnimState", "FFD254"),
                               ("Ram_ArrowAnimState", "FFD258")):
            assert re.search(rf"^{label}:\s+equ\s+\${address}\b",
                             ram_source, re.MULTILINE), \
                f"MAME animation trace address drifted from {label}"
        create_flicky_studio_project(root)
        project = root / "flicky-ue.rlj"
        build_studio_project(project)
        baseline_rom = (root / "build" / "flicky-edited.bin").read_bytes()
        assert verify_studio_project(project)["status"] == "current"
        baseline_png = root / "baseline-vdp.png"
        baseline_trace = root / "baseline-anim.csv"
        launch(studio_flicky_round_playtest_plan(project, mame=args.mame, round=1),
               1, root, baseline_png, snapshot_delay=SNAPSHOT_DELAY,
               extra_environment={"FLICKY_PLAYTEST_ANIM_TRACE": str(baseline_trace)})
        repeat_png = root / "baseline-repeat-vdp.png"
        repeat_trace = root / "baseline-repeat-anim.csv"
        launch(studio_flicky_round_playtest_plan(project, mame=args.mame, round=1),
               1, root, repeat_png, snapshot_delay=SNAPSHOT_DELAY,
               extra_environment={"FLICKY_PLAYTEST_ANIM_TRACE": str(repeat_trace)})
        assert not changed_pixels(baseline_png, repeat_png), \
            "canonical MAME timer snapshot is not deterministic"
        assert baseline_trace.read_bytes() == repeat_trace.read_bytes(), \
            "canonical MAME animation state trace is not deterministic"
        baseline_states = animation_trace(baseline_trace)

        sequences = root / "flicky-source" / WORKSPACE_FILES["graphics_sequences"]
        original = sequences.read_bytes()
        document = json.loads(original)
        animation = next(item for item in document["animations"]
                         if item["id"] == "UI_TimerAnim")
        old_delay = animation["delay"]
        assert old_delay == 0xF
        address, _ = timer_animation_address(baseline_rom, old_delay)
        new_delay = 1
        if args.native_sequences_editor:
            subprocess.run([
                str(args.native_sequences_editor.resolve()), "--edit-animation-delay",
                str(sequences), animation["id"], str(new_delay),
            ], check=True)
        else:
            print("Flicky timer animation uses document edit; Qt click unverified")
            animation["delay"] = new_delay
            sequences.write_text(json.dumps(document, indent=2) + "\n",
                                 encoding="utf-8")
        saved = json.loads(sequences.read_text(encoding="utf-8"))
        assert next(item for item in saved["animations"]
                    if item["id"] == "UI_TimerAnim")["delay"] == new_delay
        try:
            studio_flicky_round_playtest_plan(project, mame=args.mame, round=1)
        except StudioProjectError as error:
            assert "stale" in str(error)
        else:
            raise AssertionError("MAME accepted stale Flicky timer build")
        build_studio_project(project)
        assert verify_studio_project(project)["status"] == "current"
        edited_rom = (root / "build" / "flicky-edited.bin").read_bytes()
        assert edited_rom[address + 1] == new_delay
        assert edited_rom[0x18E:0x190] == cartridge_checksum(edited_rom).to_bytes(2, "big")
        expected = {address + 1} | {offset for offset in (0x18E, 0x18F)
                                   if baseline_rom[offset] != edited_rom[offset]}
        actual = {offset for offset, (before, after) in enumerate(zip(
            baseline_rom, edited_rom)) if before != after}
        assert actual == expected, f"timer edit changed unrelated bytes: {sorted(actual)[:16]}"

        edited_png = root / "edited-vdp.png"
        edited_trace = root / "edited-anim.csv"
        launch(studio_flicky_round_playtest_plan(project, mame=args.mame, round=1),
               1, root, edited_png, snapshot_delay=SNAPSHOT_DELAY,
               extra_environment={"FLICKY_PLAYTEST_ANIM_TRACE": str(edited_trace)})
        baseline_start, baseline_complete = assert_ui_timer_phase(
            baseline_states, old_delay)
        edited_start, edited_complete = assert_ui_timer_phase(
            animation_trace(edited_trace), new_delay)
        assert baseline_start == edited_start
        assert baseline_complete - edited_complete == 3 * (old_delay - new_delay)
        print(f"MAME UI timer phase: start={baseline_start}, "
              f"complete={baseline_complete}->{edited_complete}, "
              f"arrow starts {baseline_complete}->{edited_complete}")
        delta = changed_pixels(baseline_png, edited_png)
        if args.diagnostics_dir:
            args.diagnostics_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(baseline_png, args.diagnostics_dir / "timer-baseline.png")
            shutil.copyfile(edited_png, args.diagnostics_dir / "timer-edited.png")
            shutil.copyfile(baseline_trace, args.diagnostics_dir / "timer-baseline-anim.csv")
            shutil.copyfile(edited_trace, args.diagnostics_dir / "timer-edited-anim.csv")
        bounds = (min(x for x, _ in delta), min(y for _, y in delta),
                  max(x for x, _ in delta) + 1, max(y for _, y in delta) + 1) if delta else None
        print(f"Flicky timer delay {old_delay}->{new_delay}: "
              f"MAME delta={len(delta)} bounds={bounds}")
        assert len(delta) == 591 and bounds == (98, 70, 256, 183), \
            "timer delay changed an unexpected emulated VDP footprint"
        sequences.write_bytes(original)
        assert build_studio_project(project)["artifact"]["sha256"] == CANONICAL_SHA256
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
