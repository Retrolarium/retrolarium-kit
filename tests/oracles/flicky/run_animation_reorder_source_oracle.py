"""Verify a Qt animation-frame reorder through the canonical UE ROM.

Uses an isolated source snapshot; the neighboring source and ROM are read-only.
No emulator or Playtest is launched.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile

from retrolarium_kit.games.flicky.content import (
    CANONICAL_SHA256,
    SOURCE_DIRECTORY,
    WORKSPACE_FILES,
    import_flicky_source,
)
from retrolarium_kit.studio.project import build_studio_project, verify_studio_project
from retrolarium_kit.studio.create import create_flicky_studio_project
from run_content_build_source_oracle import (
    assert_rom_delta,
    timer_animation_address,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--rom", type=Path, required=True)
    parser.add_argument("--native-writer", type=Path, required=True)
    parser.add_argument("--native-sequences-editor", type=Path, required=True)
    args = parser.parse_args()
    os.environ["RETROLARIUM_FLICKY_LEVEL_WRITER"] = str(args.native_writer.resolve())

    original_rom = args.rom.read_bytes()
    scratch = Path(__file__).resolve().parents[3] / "build"
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="flicky-frame-reorder-", dir=scratch) as name:
        project = Path(name)
        import_flicky_source(args.source_root, args.rom, project)
        create_flicky_studio_project(project)
        project_file = project / "flicky-ue.rlj"
        zero = build_studio_project(project_file)
        assert zero["artifact"]["sha256"] == CANONICAL_SHA256, "zero-edit build must match the canonical ROM"

        path = project / SOURCE_DIRECTORY / WORKSPACE_FILES["graphics_sequences"]
        original_document = path.read_bytes()
        animations = json.loads(original_document)["animations"]
        timer = next(record for record in animations if record["id"] == "UI_TimerAnim")
        before = list(timer["frames"])
        assert before == ["UI_TimerData0", "UI_TimerData1", "UI_TimerData2", "UI_TimerData0"]
        address, frame_addresses = timer_animation_address(original_rom, timer["delay"])

        subprocess.run(
            [
                str(args.native_sequences_editor.resolve()),
                "--move-animation-frame",
                str(path),
                timer["id"],
                "1",
                "-1",
            ],
            check=True,
        )
        after = next(
            record
            for record in json.loads(path.read_text(encoding="utf-8"))["animations"]
            if record["id"] == timer["id"]
        )
        assert after["frames"] == [before[1], before[0], *before[2:]], (
            "Qt frame-strip buttons did not save the expected order"
        )
        assert verify_studio_project(project_file)["status"] == "mismatch"

        built = build_studio_project(project_file)
        assert verify_studio_project(project_file)["status"] == "current"
        edited_rom = (project / built["artifact"]["path"]).read_bytes()
        first_pointer = address + 2
        second_pointer = address + 6
        expected_offsets = []
        for pointer, old, new in (
            (first_pointer, frame_addresses[0], frame_addresses[1]),
            (second_pointer, frame_addresses[1], frame_addresses[0]),
        ):
            old_bytes = old.to_bytes(4, "big")
            new_bytes = new.to_bytes(4, "big")
            assert edited_rom[pointer : pointer + 4] == new_bytes, (
                "reordered animation pointer did not read back from ROM"
            )
            expected_offsets.extend(
                pointer + index for index, pair in enumerate(zip(old_bytes, new_bytes)) if pair[0] != pair[1]
            )
        assert_rom_delta(original_rom, edited_rom, sorted(expected_offsets), "Qt animation frame reorder")

        path.write_bytes(original_document)
        restored = build_studio_project(project_file)
        assert restored["artifact"]["sha256"] == CANONICAL_SHA256, (
            "restoring the animation document must reproduce the exact ROM"
        )
    print("Flicky Qt frame reorder: Save/Build/Verify, two independent ROM pointers, isolated delta, exact restore")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
