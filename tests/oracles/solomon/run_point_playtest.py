"""Opt-in Solomon Studio edit/build/MAME room-playtest journey for USA/Europe."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from retrolarium_kit.games.catalog import extract_game, inspect_game
from retrolarium_kit.studio.project import build_studio_project, verify_studio_project
from retrolarium_kit.studio.create import create_studio_project
from retrolarium_kit.studio.playtest import studio_solomon_point_playtest_plan


def check(
    rom: Path, studio: Path, revision: str, mame: Path,
    app: Path | None,
) -> None:
    source = rom.read_bytes()
    inspection = inspect_game(rom)
    if (inspection["game"], inspection["revision"]) != ("solomons-key", revision):
        raise AssertionError(f"{revision}: unexpected source image")
    with tempfile.TemporaryDirectory(
        prefix=f"retrolarium-solomon-playtest-{revision}-"
    ) as temporary:
        workspace = Path(temporary) / "studio"
        shutil.copytree(studio, workspace)
        documents = workspace / "documents"
        extract_game(rom, documents / "audio.json", resource="audio")
        extract_game(rom, documents / "presentation.json", resource="presentation")
        project = workspace / f"solomons-key-{revision}-playtest.rlj"
        created = create_studio_project(
            workspace / "manifest.json", project,
            project_id=f"solomons-key-{revision}-playtest",
            name=f"Solomon's Key {revision} playtest",
            profile="solomons-key.v1", source_mode="binary-profile",
            resource_documents=[
                documents / name for name in (
                    "levels.json", "room-art.json", "sprite-frames.json",
                    "audio.json", "presentation.json", "graphics.json",
                )
            ],
        )
        room_tabs = [
            tab for tab in created["workspace"]["tabs"]
            if tab["id"] == "rooms"
        ]
        if len(room_tabs) != 1 or "playtest" not in room_tabs[0]["capabilities"]:
            raise AssertionError(
                f"{revision}: generated Rooms tab is not Playtest-capable: "
                f"{room_tabs!r}"
            )
        if app is not None:
            smoke = subprocess.run(
                [str(app), "--studio-smoke", str(project),
                 "--require-tab", "playtest", "--smoke-test"],
                capture_output=True, text=True, timeout=90,
            )
            if smoke.returncode:
                raise AssertionError(
                    f"{revision}: GUI Playtest tab failed: {smoke.stderr}"
                )
        build_studio_project(project)
        original = verify_studio_project(project)
        if original["status"] != "current" or Path(
            original["artifact"]["absolute_path"]
        ).read_bytes() != source:
            raise AssertionError(f"{revision}: zero-edit Studio ROM differs")
        level_path = documents / "levels.json"
        levels = json.loads(level_path.read_text(encoding="utf-8"))
        room = 29
        block = levels["rooms"][room]["brown"][2]
        block[2] ^= 1
        level_path.write_text(
            json.dumps(levels, indent=2) + "\n", encoding="utf-8"
        )
        build_studio_project(project)
        verified = verify_studio_project(project)
        if verified["status"] != "current":
            raise AssertionError(f"{revision}: edited Studio build is stale")
        artifact = Path(verified["artifact"]["absolute_path"])
        output = artifact.read_bytes()
        if sum(a != b for a, b in zip(source, output)) != 1:
            raise AssertionError(f"{revision}: room-cell edit was not one byte")
        plan = studio_solomon_point_playtest_plan(
            project, mame=mame, room=room,
        )
        if Path(plan["arguments"][2]) != artifact.resolve():
            raise AssertionError(f"{revision}: Playtest did not use edited ROM")
        result_path = Path(temporary) / "result.txt"
        environment = os.environ.copy()
        environment.update(plan["environment"])
        environment["SOLOMON_PLAYTEST_EXIT"] = "1"
        environment["SOLOMON_PLAYTEST_RESULT"] = str(result_path)
        completed = subprocess.run(
            [plan["program"], *plan["arguments"],
             "-video", "none", "-sound", "none", "-nothrottle",
             "-seconds_to_run", "30"],
            cwd=plan["working_directory"], env=environment,
            capture_output=True, text=True, timeout=60,
        )
        result = result_path.read_text(encoding="utf-8").strip() \
            if result_path.is_file() else ""
        if completed.returncode != 0 or not result.startswith(
            "status=ready requested_room=1d current_room=1d "
        ) or "room_loads=1 " not in result:
            raise AssertionError(
                f"{revision}: MAME point playtest failed: "
                f"{result!r}, exit={completed.returncode}, "
                f"stdout={completed.stdout[-1000:]!r}, "
                f"stderr={completed.stderr[-1000:]!r}"
            )
        if app is not None:
            ui_report = Path(temporary) / "ui-playtest.json"
            ui = subprocess.run(
                [str(app), "--solomon-ui-playtest", str(project), str(ui_report)],
                capture_output=True, text=True, timeout=120,
            )
            if ui.returncode or not ui_report.is_file() or json.loads(
                ui_report.read_text(encoding="utf-8")
            ).get("status") != "ready":
                ui_error = Path(str(ui_report) + ".error.txt")
                raise AssertionError(
                    f"{revision}: GUI-to-MAME journey failed: "
                    f"exit={ui.returncode}, stdout={ui.stdout[-1000:]!r}, "
                    f"stderr={ui.stderr[-1000:]!r}, "
                    f"error={ui_error.read_text(encoding='utf-8') if ui_error.is_file() else ''!r}"
                )
    print(f"Solomon {revision}: edited Studio ROM entered room 30 in MAME")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--usa", type=Path, required=True)
    parser.add_argument("--europe", type=Path, required=True)
    parser.add_argument("--usa-studio", type=Path, required=True)
    parser.add_argument("--europe-studio", type=Path, required=True)
    parser.add_argument("--mame", type=Path, required=True)
    parser.add_argument("--app", type=Path)
    parser.add_argument("--only", choices=("usa", "europe"))
    args = parser.parse_args()
    mame = args.mame.resolve(strict=True)
    app = args.app.resolve(strict=True) if args.app else None
    if args.only in (None, "usa"):
        check(args.usa.resolve(strict=True),
              args.usa_studio.resolve(strict=True), "usa", mame, app)
    if args.only in (None, "europe"):
        check(args.europe.resolve(strict=True),
              args.europe_studio.resolve(strict=True), "europe", mame, app)


if __name__ == "__main__":
    main()
