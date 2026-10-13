#!/usr/bin/env python3
"""Prove a built Flicky ROM boots requested rounds under MAME.

The neighboring source and ROM are read-only. All generated output is under
this repository's ignored build directory.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
import shutil
import subprocess
import struct
import sys
import tempfile

from retrolarium_kit.games.flicky.content import WORKSPACE_FILES, import_flicky_source
from retrolarium_core.megadrive import cartridge_checksum
from retrolarium_kit.studio.project import StudioProjectError, build_studio_project
from retrolarium_kit.studio.create import create_flicky_studio_project
from retrolarium_kit.studio.playtest import studio_flicky_round_playtest_plan


def launch(plan: dict, round_number: int, root: Path,
           snapshot: Path | None = None, snapshot_delay: int = 60,
           extra_environment: dict[str, str] | None = None) -> None:
    result = root / f"round-{round_number}.txt"
    result.unlink(missing_ok=True)
    if snapshot is not None:
        snapshot.unlink(missing_ok=True)
    environment = os.environ.copy()
    environment.update(plan["environment"])
    if extra_environment:
        environment.update(extra_environment)
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
    expected_mode = 0x2C if round_number % 4 == 3 else 0x24
    assert int(fields["mode"], 16) == expected_mode, fields
    assert int(fields["lives"]) == 3, fields
    if snapshot is not None:
        png = snapshot.read_bytes()
        assert png[:16] == b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR", \
            "MAME did not produce a PNG from the emulated VDP screen"
        width, height = struct.unpack(">II", png[16:24])
        assert width >= 256 and height >= 224, \
            f"MAME VDP screenshot is unexpectedly small: {width}x{height}"
        print(f"round {round_number} VDP snapshot: {width}x{height}, {len(png)} bytes")
        capture_lines = [line.strip() for line in run.stdout.splitlines()
                         if "Flicky VDP snapshot:" in line]
        assert len(capture_lines) == 1, "MAME did not report screenshot mode"
        capture = capture_lines[0]
        assert (f"mode={expected_mode:02X}" in capture
                and f"round_word={expected_word:04X}" in capture), (
            f"MAME captured a different round/mode: {capture}")
        print(capture)
    print(f"round {round_number}: {result.read_text(encoding='utf-8').strip()}")


def compare_vdp_scene(snapshot: Path, output: Path, round_number: int,
                      label: str) -> float:
    """Compare static VDP geometry plus bounded full-frame animation drift.

    The source preview uses linear 3-bit RGB; MAME uses the Mega Drive DAC
    palette. Normalization leaves HUD and time-dependent sprites as the main
    expected differences in the whole-frame comparison.
    """
    try:
        from PIL import Image
    except ImportError as error:
        raise RuntimeError("Pillow is required for VDP scene comparison") from error

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
    print(f"round {round_number} {label}/MAME VDP: {agreement:.2%} "
          "full-frame pixels after DAC normalization")
    # Static strips cross the tilemap, platform, and background independently
    # of the HUD and animated windows/actors on this deterministic frame.
    if round_number == 1:
        for left, top, right, bottom in ((0, 16, 256, 24), (32, 64, 96, 96)):
            assert all(normalized[y * width + x] == emulated[y * width + x]
                       for y in range(top, bottom) for x in range(left, right)), (
                f"Flicky static VDP region differs: {(left, top, right, bottom)}"
            )
        print("round 1 static tilemap regions pixel-exact after DAC normalization")
    return agreement


def compare_bonus_static_ground(snapshot: Path, output: Path,
                                round_number: int, phase: int) -> None:
    """Prove a source-owned bonus strip without claiming animated-scene parity."""
    from PIL import Image

    dac = {0: 0, 36: 52, 72: 87, 108: 116,
           144: 144, 180: 172, 216: 206, 252: 255}
    with Image.open(output) as native_image, Image.open(snapshot) as mame_image:
        native = native_image.convert("RGB")
        mame = mame_image.convert("RGB")
        assert native.size == mame.size == (256, 224)
        matches = sum(
            tuple(dac[value] for value in native.getpixel((x, y)))
            == mame.getpixel((x, y))
            for y in range(208, 224) for x in range(32, 192))
    agreement = matches / 2560
    assert agreement >= 0.95, (
        f"bonus lower-ground static VDP below 95%: {agreement:.2%}")
    label_matches = sum(
        tuple(dac[value] for value in native.getpixel((x, y)))
        == mame.getpixel((x, y))
        for y in range(24, 32) for x in range(80, 168))
    # Later flying chicks can cross the title/HUD. The canonical +60 probe
    # has no sprite occlusion in these masks; do not claim other phases exact.
    if phase == 60:
        assert label_matches == 88 * 8, (
            f"bonus title label differs from MAME: {label_matches}/704 pixels")
    hud_regions = {
        "1UP/score": (32, 8, 104, 16),
        "HI/high score": (144, 8, 224, 16),
        "lives": (16, 208, 32, 216),
        "RD/round": (208, 208, 248, 216),
    }
    for label, (left, top, right, bottom) in hud_regions.items():
        hud_matches = sum(
            tuple(dac[value] for value in native.getpixel((x, y)))
            == mame.getpixel((x, y))
            for y in range(top, bottom) for x in range(left, right))
        hud_pixels = (right - left) * (bottom - top)
        if phase == 60:
            assert hud_matches == hud_pixels, (
                f"bonus {label} HUD differs from MAME: "
                f"{hud_matches}/{hud_pixels} pixels")
    print(f"round {round_number} bonus lower-ground strip: "
          f"{agreement:.2%} MAME agreement "
          f"after DAC normalization; BONUS ROUND label {label_matches}/704; "
          + ("canonical score/lives HUD exact at +60; " if phase == 60 else
             "HUD can be occluded by chicks at this phase; ")
          + "moving Chick and dynamic HUD parity is audited separately")


def compare_bonus_actor_phase(snapshot: Path, static: Path, phased: Path,
                              round_number: int, phase: int) -> None:
    """Require source-driven actors to improve the lower-stage VDP region."""
    from PIL import Image

    dac = {0: 0, 36: 52, 72: 87, 108: 116,
           144: 144, 180: 172, 216: 206, 252: 255}
    with (Image.open(snapshot) as mame_image,
          Image.open(static) as static_image,
          Image.open(phased) as phased_image):
        mame = mame_image.convert("RGB")
        baseline = static_image.convert("RGB")
        actors = phased_image.convert("RGB")
        assert mame.size == baseline.size == actors.size == (256, 224)
        positions = ((x, y) for y in range(168, 224)
                     for x in range(32, 224))
        static_matches = 0
        actor_matches = 0
        changed = 0
        for point in positions:
            expected = mame.getpixel(point)
            before = tuple(dac[value] for value in baseline.getpixel(point))
            after = tuple(dac[value] for value in actors.getpixel(point))
            static_matches += before == expected
            actor_matches += after == expected
            changed += before != after
        full_matches = sum(
            tuple(dac[value] for value in actors.getpixel((x, y)))
            == mame.getpixel((x, y))
            for y in range(224) for x in range(256))
        player_matches = sum(
            tuple(dac[value] for value in actors.getpixel((x, y)))
            == mame.getpixel((x, y))
            for y in range(128, 192) for x in range(96, 152))
    full_agreement = full_matches / (256 * 224)
    print(f"round {round_number} bonus phase {phase}: "
          f"lower-stage VDP matches {static_matches}→{actor_matches}; "
          f"{changed} native actor pixels changed; "
          f"full frame {full_matches}/57344 ({full_agreement:.2%}) agreement; "
          f"player/held ROI {player_matches}/3584")
    assert changed > 0 and actor_matches > static_matches, (
        "bonus actor phase did not improve MAME lower-stage pixels")
    if phase in (18, 120, 240, 298, 300, 302, 303, 304, 480, 600) \
            and round_number == 3:
        assert full_matches == 256 * 224, (
            f"bonus phase +{phase} full frame differs: {full_matches}/57344")
    if phase == 60:
        assert full_matches == 256 * 224, (
            f"bonus phase +60 full frame differs: {full_matches}/57344")
    if phase == 180:
        assert full_matches == 256 * 224, (
            f"bonus phase +180 full frame differs: {full_matches}/57344")
    if phase == 67 and round_number == 3:
        assert full_matches == 256 * 224, (
            f"bonus scenery/slot-0 Chick VDP cadence regressed at +67: "
            f"{full_matches}/57344")
    if phase in (374, 375, 376) and round_number == 7:
        assert full_matches == 256 * 224, (
            f"bonus catch/VDP boundary regressed at +{phase}: "
            f"{full_matches}/57344")
    if (phase == 18 and round_number == 3) or phase == 60:
        assert player_matches == 3584, (
            f"bonus player/held Chick ROI differs from MAME: {player_matches}/3584")


def render_native(executable: Path, source: Path, round_number: int,
                  output: Path) -> None:
    root = source / "content" / "workspace"
    subprocess.run([
        str(executable.resolve()), "--render-image", str(source),
        str(root / "level" / "levels.json"),
        str(root / "graphics" / "graphics.json"),
        str(root / "graphics" / "semantics.json"),
        str(root / "graphics" / "sequences.json"),
        str(round_number), str(output),
    ], check=True)


def render_native_bonus_phase(executable: Path, source: Path, round_number: int,
                              phase: int, output: Path) -> None:
    root = source / "content" / "workspace"
    subprocess.run([
        str(executable.resolve()), "--render-bonus-phase", str(source),
        str(root / "level" / "levels.json"),
        str(root / "graphics" / "graphics.json"),
        str(root / "graphics" / "semantics.json"),
        str(root / "graphics" / "sequences.json"),
        str(round_number), str(phase), str(output),
    ], check=True)


def render_source_runtime(preview: object, layouts: dict, round_number: int,
                          output: Path) -> None:
    from PIL import Image
    from authoring.level_preview import md_color

    layout = layouts["layouts"][layouts["round_layouts"][round_number - 1]]
    frame = preview.render(layout, round_number)
    rgb = bytes.fromhex("".join(
        md_color(word)[1:] for row in frame for word in row))
    Image.frombytes("RGB", (256, 224), rgb).save(output)


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
    parser.add_argument("--source-runtime-preview", action="store_true",
                        help="compare MAME with source preview corrected for Plane A scroll")
    parser.add_argument("--all-rounds-audit", action="store_true",
                        help="Playtest all 48 rounds and compare normal-round VDP scenes")
    parser.add_argument("--bonus-actor-phase-audit", action="store_true",
                        help="also compare native seesaw/Tiger phase with MAME lower-stage pixels")
    parser.add_argument("--all-rounds-playtest", action="store_true",
                        help="verify normal/bonus entry for all 48 rounds")
    parser.add_argument("--audit-rounds", nargs="+", type=int, metavar="ROUND",
                        help="compare selected rounds; round 1 is included for baseline checks")
    parser.add_argument("--native-level-editor", type=Path,
                        help="move the round-1 player via its Qt Levels widget")
    parser.add_argument("--snapshot-output", type=Path,
                        help="copy the selected round VDP PNG for local visual inspection")
    parser.add_argument("--bonus-actor-trace-output", type=Path,
                        help="copy player, Tiger and seesaw RAM frames through the selected bonus snapshot")
    parser.add_argument("--snapshot-round", type=int, default=1,
                        help="round copied by --snapshot-output (default: 1)")
    parser.add_argument("--diagnostics-dir", type=Path,
                        help="retain zero/edited ROM images for failed-gate diagnosis")
    parser.add_argument("--snapshot-delay", type=int, default=60)
    args = parser.parse_args()
    if not 1 <= args.snapshot_round <= 48:
        parser.error("--snapshot-round must be in 1..48")
    if args.bonus_actor_trace_output and args.snapshot_round % 4 != 3:
        parser.error("--bonus-actor-trace-output requires a bonus --snapshot-round")
    if not 0 <= args.snapshot_delay <= 600:
        parser.error("--snapshot-delay must be in 0..600")
    if args.native_level_editor and args.snapshot_delay != 60:
        parser.error("--native-level-editor requires the deterministic 60-frame snapshot")
    if args.native_renderer and args.source_runtime_preview:
        parser.error("choose --native-renderer or --source-runtime-preview")
    if args.all_rounds_audit and not (args.native_renderer or args.source_runtime_preview):
        parser.error("--all-rounds-audit requires a scene preview")
    if args.bonus_actor_phase_audit and (not args.native_renderer
                                        or args.snapshot_delay < 1):
        parser.error("--bonus-actor-phase-audit requires --native-renderer and phase >=1")
    if args.audit_rounds and not (args.native_renderer or args.source_runtime_preview
                                  or args.snapshot_output):
        parser.error("--audit-rounds requires a scene preview or snapshot output")
    if args.audit_rounds and args.all_rounds_audit:
        parser.error("choose --audit-rounds or --all-rounds-audit")
    if args.all_rounds_playtest and (args.all_rounds_audit or args.audit_rounds):
        parser.error("--all-rounds-playtest cannot be combined with VDP audits")
    if args.all_rounds_playtest and (args.native_renderer or args.source_runtime_preview):
        parser.error("--all-rounds-playtest is a boot-only gate; omit scene preview")
    if args.audit_rounds and any(not 1 <= round_number <= 48
                                 for round_number in args.audit_rounds):
        parser.error("--audit-rounds values must be in 1..48")
    os.environ["RETROLARIUM_FLICKY_LEVEL_WRITER"] = str(args.native_writer.resolve())
    scratch = Path(__file__).resolve().parents[3] / "build"
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
        source_preview = None
        source_layouts = None
        if args.source_runtime_preview:
            sys.path.insert(0, str(source / "scripts"))
            from authoring import level_preview
            level_preview.PLANE_A_SCROLL_X = -1
            workspace = source / "content" / "workspace"
            source_preview = level_preview.LevelPreview.load(
                source, workspace / "graphics" / "graphics.json",
                workspace / "graphics" / "semantics.json",
                workspace / "graphics" / "sequences.json")
            source_layouts = json.loads((workspace / "level" / "levels.json")
                                        .read_text(encoding="utf-8"))
        if args.all_rounds_audit or args.all_rounds_playtest:
            rounds = range(1, 49)
        elif args.audit_rounds:
            rounds = tuple(dict.fromkeys((1, *args.audit_rounds)))
        else:
            rounds = (1, 3, 26, 48)
        vdp_agreements = {}
        for round_number in rounds:
            plan = studio_flicky_round_playtest_plan(
                project, mame=args.mame, round=round_number)
            assert plan["operation"] == "retrolarium.studio.flicky-round-playtest"
            snapshot = root / f"round-{round_number}-vdp.png"
            actor_trace = (root / f"round-{round_number}-bonus-actors.csv"
                           if args.bonus_actor_trace_output is not None
                           and round_number == args.snapshot_round else None)
            launch(plan, round_number, root,
                   snapshot if (round_number == 1 or
                                (args.snapshot_output is not None
                                 and round_number == args.snapshot_round) or
                                actor_trace is not None or
                                args.native_renderer
                                or args.source_runtime_preview) else None,
                   snapshot_delay=args.snapshot_delay,
                   extra_environment={"FLICKY_PLAYTEST_BONUS_ACTORS_TRACE":
                                      str(actor_trace)} if actor_trace else None)
            if actor_trace is not None:
                assert actor_trace.is_file(), "MAME bonus actor RAM trace is missing"
                if round_number == 7 and args.snapshot_delay >= 376:
                    with actor_trace.open(newline="", encoding="utf-8") as stream:
                        rows = {(int(row["phase"]), row["name"]): row
                                for row in csv.DictReader(stream)}
                    before = rows[(374, "chick03")]
                    caught = rows[(375, "chick03")]
                    held = rows[(375, "held")]
                    assert (before["object"] == "8038"
                            and before["collision_type"] == "03"
                            and before["caught_count"] == "00"
                            and before["chicks_remaining"] == "14"
                            and caught["object"] == "0000"
                            and caught["caught_count"] == "01"
                            and caught["chicks_remaining"] == "13"
                            and held["collision_type"] == "11"), (
                        "round-7 Chick slot 3 catch does not match source AABB event")
                destination = args.bonus_actor_trace_output.resolve()
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(actor_trace, destination)
            if (args.native_renderer or args.source_runtime_preview) \
                    and round_number % 4 != 3:
                native_snapshot = root / f"native-round-{round_number}.png"
                if args.native_renderer:
                    render_native(args.native_renderer, source, round_number,
                                  native_snapshot)
                else:
                    render_source_runtime(source_preview, source_layouts,
                                          round_number, native_snapshot)
                vdp_agreements[round_number] = compare_vdp_scene(
                    snapshot, native_snapshot, round_number,
                    "native" if args.native_renderer else "source-runtime")
                if (args.diagnostics_dir is not None
                        and vdp_agreements[round_number] < 0.93):
                    shutil.copyfile(snapshot, args.diagnostics_dir
                                    / f"round-{round_number}-mame.png")
                    shutil.copyfile(native_snapshot, args.diagnostics_dir
                                    / f"round-{round_number}-native.png")
            elif args.native_renderer and round_number % 4 == 3:
                native_snapshot = root / f"native-round-{round_number}.png"
                render_native(args.native_renderer, source, round_number,
                              native_snapshot)
                if args.diagnostics_dir is not None:
                    shutil.copyfile(snapshot, args.diagnostics_dir
                                    / f"round-{round_number}-bonus-mame.png")
                    shutil.copyfile(native_snapshot, args.diagnostics_dir
                                    / f"round-{round_number}-bonus-native.png")
                compare_bonus_static_ground(snapshot, native_snapshot,
                                            round_number, args.snapshot_delay)
                if args.bonus_actor_phase_audit:
                    actor_snapshot = root / f"native-round-{round_number}-phase-{args.snapshot_delay}.png"
                    render_native_bonus_phase(args.native_renderer, source,
                                              round_number, args.snapshot_delay,
                                              actor_snapshot)
                    if args.diagnostics_dir is not None:
                        shutil.copyfile(actor_snapshot, args.diagnostics_dir
                                        / f"round-{round_number}-bonus-actors.png")
                    compare_bonus_actor_phase(snapshot, native_snapshot,
                                              actor_snapshot, round_number,
                                              args.snapshot_delay)
            if round_number == args.snapshot_round and args.snapshot_output is not None:
                destination = args.snapshot_output.resolve()
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(snapshot, destination)
        weak_rounds = {round_number: agreement
                       for round_number, agreement in vdp_agreements.items()
                       if agreement < 0.93}
        if vdp_agreements:
            weakest = min(vdp_agreements, key=vdp_agreements.get)
            print(f"VDP audit: {len(vdp_agreements)} normal rounds, weakest "
                  f"round {weakest}={vdp_agreements[weakest]:.2%}")
        if args.all_rounds_audit:
            assert len(vdp_agreements) == 36
        if args.all_rounds_audit or args.all_rounds_playtest:
            print("Playtest audit: all 48 rounds reached their expected "
                  "normal/bonus mode"
                  + ("; 12 bonus VDP scenes lack native parity"
                     if args.all_rounds_audit else ""))
        assert not weak_rounds, (
            "Flicky native/MAME VDP below 93%: "
            + ", ".join(f"{round_number}={agreement:.2%}"
                        for round_number, agreement in weak_rounds.items())
        )
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
