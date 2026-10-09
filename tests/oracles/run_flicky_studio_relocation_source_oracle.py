#!/usr/bin/env python3
"""Move an edited Flicky Studio workspace and continue Build/Verify/Preview/Playtest."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

from retrolarium_kit.games.flicky.content import (
    CANONICAL_SHA256, WORKSPACE_FILES, import_flicky_source,
)
from retrolarium_kit.studio.project import (
    build_studio_project, create_flicky_studio_project,
    load_studio_project, studio_flicky_round_playtest_plan,
    studio_flicky_sound_preview, verify_studio_project,
)
from run_flicky_round_playtest_source_oracle import launch


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--rom", type=Path, required=True)
    parser.add_argument("--native-writer", type=Path, required=True)
    parser.add_argument("--mame", type=Path, required=True)
    parser.add_argument("--gui", type=Path,
                        help="smoke the relocated .rlj through the Qt application")
    args = parser.parse_args()
    os.environ["RETROLARIUM_FLICKY_LEVEL_WRITER"] = str(
        args.native_writer.resolve())
    scratch = Path(__file__).resolve().parents[2] / "build"
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="flicky-relocation-", dir=scratch) as name:
        envelope = Path(name)
        before = envelope / "original-location"
        after = envelope / "relocated-workspace"
        import_flicky_source(args.source_root, args.rom, before)
        create_flicky_studio_project(before)
        project = before / "flicky-ue.rlj"
        sequences = before / "flicky-source" / WORKSPACE_FILES["graphics_sequences"]
        baseline = sequences.read_bytes()
        document = json.loads(baseline)
        animation = next(item for item in document["animations"]
                         if item["id"] == "UI_TimerAnim")
        assert animation["delay"] == 15
        animation["delay"] = 1
        sequences.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        edited_build = build_studio_project(project)
        assert edited_build["artifact"]["sha256"] != CANONICAL_SHA256
        assert verify_studio_project(project)["status"] == "current"
        assert str(before.resolve()) not in project.read_text(encoding="utf-8")
        assert str(before.resolve()) not in (before / "build/studio-build-result.json") \
            .read_text(encoding="utf-8")

        assert before.parent == after.parent == envelope and not after.exists()
        before.rename(after)
        project = after / "flicky-ue.rlj"
        sequences = after / "flicky-source" / WORKSPACE_FILES["graphics_sequences"]
        inspection = load_studio_project(project)
        assert Path(inspection["project_root"]) == after.resolve()
        assert verify_studio_project(project)["status"] == "current"
        if args.gui:
            environment = os.environ.copy()
            # The Windows Qt build enters the event loop but never handles
            # the queued smoke-test quit with the offscreen platform plugin.
            # Use the native backend there; keep headless Linux behavior.
            environment.setdefault("QT_QPA_PLATFORM",
                                   "windows" if os.name == "nt" else "offscreen")
            command = [str(args.gui.resolve()), "--studio-smoke", str(project)]
            for tab in ("levels", "graphics", "semantics", "animations", "sound"):
                command.extend(("--require-tab", tab))
            try:
                subprocess.run(command, cwd=after, env=environment,
                               capture_output=True, text=True, check=True,
                               timeout=30)
            except subprocess.TimeoutExpired as exc:
                raise AssertionError(
                    "Qt relocation smoke did not exit within 30 seconds; "
                    "rebuild the GUI and inspect its startup before claiming "
                    "relocated-project support") from exc
            except subprocess.CalledProcessError as exc:
                raise AssertionError(
                    f"Qt relocation smoke exited {exc.returncode}: "
                    f"{(exc.stderr or '')[-2000:]}") from exc
        moved_build = build_studio_project(project)
        assert moved_build["artifact"]["sha256"] == edited_build["artifact"]["sha256"]
        assert verify_studio_project(project)["status"] == "current"
        audio = studio_flicky_sound_preview(
            project, header="zSFX90Header", seconds=1)
        assert audio["status"] == "rendered"
        assert Path(audio["wav"]["path"]).is_relative_to(after / "build")
        plan = studio_flicky_round_playtest_plan(project, mame=args.mame, round=26)
        assert Path(plan["project_file"]) == project.resolve()
        launch(plan, 26, after, after / "relocated-round-26.png")

        sequences.write_bytes(baseline)
        assert verify_studio_project(project)["status"] == "mismatch"
        restored = build_studio_project(project)
        assert restored["artifact"]["sha256"] == CANONICAL_SHA256
        assert verify_studio_project(project)["status"] == "current"
        print("Flicky relocation: edited ROM retained, Verify/Build/Preview/"
              "round-26 Playtest passed after move; "
              f"GUI tabs={bool(args.gui)}; restore rebuilt exact ROM")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
