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
import subprocess
import tempfile

from retrolarium_kit.flicky_content import WORKSPACE_FILES, import_flicky_source
from retrolarium_kit.studio_project import (
    StudioProjectError,
    build_studio_project,
    create_flicky_studio_project,
    studio_flicky_round_playtest_plan,
)


def launch(plan: dict, round_number: int, root: Path) -> None:
    result = root / f"round-{round_number}.txt"
    environment = os.environ.copy()
    environment.update(plan["environment"])
    environment.update({
        "FLICKY_PLAYTEST_EXIT": "1",
        "FLICKY_PLAYTEST_RESULT": str(result),
    })
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
    assert fields["status"] == "ready", fields
    assert int(fields["round"]) == round_number, fields
    assert int(fields["round_word"], 16) == expected_word, fields
    assert int(fields["mode"], 16) == 0x24, fields
    assert int(fields["lives"]) == 3, fields
    print(f"round {round_number}: {result.read_text(encoding='utf-8').strip()}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--rom", type=Path, required=True)
    parser.add_argument("--mame", type=Path, required=True)
    args = parser.parse_args()
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
        for round_number in (1, 26, 48):
            plan = studio_flicky_round_playtest_plan(
                project, mame=args.mame, round=round_number)
            assert plan["operation"] == "retrolarium.studio.flicky-round-playtest"
            launch(plan, round_number, root)
        for invalid in (0, 49, True):
            try:
                studio_flicky_round_playtest_plan(
                    project, mame=args.mame, round=invalid)
            except StudioProjectError:
                pass
            else:
                raise AssertionError(f"invalid round was accepted: {invalid!r}")
        level_path = root / "flicky-source" / WORKSPACE_FILES["level_layouts"]
        document = json.loads(level_path.read_text(encoding="utf-8"))
        document["layouts"][0]["collision"][4][0] ^= 1
        level_path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        try:
            studio_flicky_round_playtest_plan(project, mame=args.mame, round=26)
        except StudioProjectError as error:
            assert "stale" in str(error)
        else:
            raise AssertionError("stale Flicky ROM was accepted")
        build_studio_project(project)
        launch(studio_flicky_round_playtest_plan(
            project, mame=args.mame, round=26), 26, root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
