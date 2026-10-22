"""Prove GUI collision-stroke growth and every resulting Flicky ROM relocation.

The canonical source and ROM are read-only. A disposable Studio project receives
one five-cell mouse stroke through either the main window or the Qt widget.
The main-window route also drags the player and selects/drags a spawner as
separate Undo steps;
no MAME or Playtest process is started.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from retrolarium_kit.games.flicky.content import (
    CANONICAL_SHA256,
    SOURCE_DIRECTORY,
    WORKSPACE_FILES,
    import_flicky_source,
)
from retrolarium_core.megadrive import cartridge_checksum
from retrolarium_kit.studio.project import build_studio_project, verify_studio_project
from retrolarium_kit.studio.create import create_flicky_studio_project


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--rom", type=Path, required=True)
    parser.add_argument("--native-writer", type=Path, required=True)
    editor = parser.add_mutually_exclusive_group(required=True)
    editor.add_argument("--native-level-editor", type=Path)
    editor.add_argument("--gui", type=Path)
    args = parser.parse_args()
    os.environ["RETROLARIUM_FLICKY_LEVEL_WRITER"] = str(args.native_writer.resolve())

    original_rom = args.rom.read_bytes()
    scratch = Path(__file__).resolve().parents[3] / "build"
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="flicky-collision-growth-", dir=scratch) as name:
        project = Path(name)
        import_flicky_source(args.source_root, args.rom, project)
        create_flicky_studio_project(project)
        project_file = project / "flicky-ue.rlj"
        zero = build_studio_project(project_file)
        assert zero["artifact"]["sha256"] == CANONICAL_SHA256

        source = project / SOURCE_DIRECTORY
        sys.path.insert(0, str(source / "scripts"))
        from authoring.level_studio_model import (  # noqa: E402
            decode_collision,
            encode_collision,
            encode_level,
        )

        levels_path = source / WORKSPACE_FILES["level_layouts"]
        original_levels = levels_path.read_bytes()
        document = json.loads(original_levels)
        layout = document["layouts"][0]
        before_grid = layout["collision"]
        assert before_grid[3][3:8] == [0] * 5, "canonical collision growth source cells differ"
        old_blocks = [bytes(item["baseline_bytes"]) for item in document["layouts"]]
        old_stream = bytes(layout["baseline_collision_stream"])
        assert decode_collision(list(old_stream)) == before_grid
        level_address = original_rom.find(old_blocks[0])
        assert level_address >= 96 and original_rom.find(old_blocks[0], level_address + 1) < 0, (
            "canonical layout 0 block is not unique in ROM"
        )
        old_section = b"".join(old_blocks)
        old_end = level_address + len(old_section)
        assert original_rom[level_address:old_end] == old_section, (
            "canonical ROM does not contain contiguous source level blocks"
        )

        if args.gui:
            report_path = project / "collision-growth-report.json"
            build_result = project / "build/studio-build-result.json"
            before_gui_build = (build_result.read_bytes(), build_result.stat().st_mtime_ns)
            completed = subprocess.run(
                [
                    str(args.gui.resolve()),
                    "--flicky-ui-journey-collision-growth",
                    str(project_file),
                    str(report_path),
                ],
                capture_output=True,
                text=True,
                timeout=90,
            )
            if completed.returncode:
                error_path = Path(str(report_path) + ".error.txt")
                detail = error_path.read_text(encoding="utf-8") if error_path.exists() else "<no GUI error report>"
                raise AssertionError(
                    "main-window collision growth failed: "
                    f"exit={completed.returncode}, report={detail!r}, "
                    f"stdout={completed.stdout!r}, stderr={completed.stderr!r}"
                )
            report = json.loads(report_path.read_text(encoding="utf-8"))
            assert report["schema"] == "retrolarium.test.flicky-ui-collision-growth"
            assert report["level_layout"] == 0
            assert report["collision_cells_before"] == [0] * 5
            assert report["collision_cells_after"] == [1] * 5
            assert report["object_field"] == "player"
            assert report["object_before"] == layout["player"] == [15, 23]
            assert report["object_after"] == [17, 23]
            assert report["spawner_index"] == 3
            assert report["spawner_before"] == layout["spawners"][3] == [19, 18]
            assert report["spawner_after"] == [21, 18]
            assert (build_result.read_bytes(), build_result.stat().st_mtime_ns) == before_gui_build, (
                "GUI collision stroke unexpectedly built ROM"
            )
        else:
            subprocess.run(
                [
                    str(args.native_level_editor.resolve()),
                    "--edit-stroke",
                    str(levels_path),
                    "0",
                    "3",
                    "3",
                    "7",
                    "1",
                ],
                capture_output=True,
                text=True,
                check=True,
            )
        saved = json.loads(levels_path.read_text(encoding="utf-8"))
        expected_grid = copy.deepcopy(before_grid)
        expected_grid[3][3:8] = [1] * 5
        expected_layout = copy.deepcopy(layout)
        expected_layout["collision"] = expected_grid
        if args.gui:
            expected_layout["player"] = [17, 23]
            expected_layout["spawners"][3] = [21, 18]
        assert saved["layouts"][0] == expected_layout, "GUI stroke changed unexpected layout 0 fields"
        assert saved["layouts"][1:] == document["layouts"][1:], "GUI stroke changed another level layout"
        new_stream = bytes(encode_collision(expected_grid))
        new_blocks = [bytes(encode_level(item)) for item in [expected_layout, *document["layouts"][1:]]]
        assert new_blocks[0].startswith(new_stream)
        growth = len(new_blocks[0]) - len(old_blocks[0])
        assert growth > 0 and len(new_stream) > len(old_stream), (
            "selected GUI stroke did not grow the encoded collision stream"
        )
        assert new_blocks[1:] == old_blocks[1:], "GUI stroke changed an unrelated encoded layout"
        assert verify_studio_project(project_file)["status"] == "mismatch"

        table_address = level_address - 48 * 2
        table = original_rom[table_address:level_address]
        assert len(table) == 96 and document["round_layouts"][0] == 0
        base_address = level_address - int.from_bytes(table[:2], "big")
        old_offsets = [0]
        new_offsets = [0]
        for old, new in zip(old_blocks[:-1], new_blocks[:-1]):
            old_offsets.append(old_offsets[-1] + len(old))
            new_offsets.append(new_offsets[-1] + len(new))
        expected_rom = bytearray(original_rom)
        moved_pointers = 0
        for round_index, layout_index in enumerate(document["round_layouts"]):
            pointer_address = table_address + round_index * 2
            canonical_pointer = level_address + old_offsets[layout_index] - base_address
            assert table[round_index * 2 : round_index * 2 + 2] == canonical_pointer.to_bytes(2, "big"), (
                f"canonical round {round_index + 1} pointer differs"
            )
            edited_pointer = level_address + new_offsets[layout_index] - base_address
            assert 0 <= edited_pointer <= 0xFFFF
            expected_rom[pointer_address : pointer_address + 2] = edited_pointer.to_bytes(2, "big")
            moved_pointers += edited_pointer != canonical_pointer
        assert moved_pointers > 0, "collision growth moved no round pointers"
        new_section = b"".join(new_blocks)
        new_end = level_address + len(new_section)
        assert new_end - old_end == growth
        assert original_rom[old_end:new_end] == b"\xff" * growth, (
            "growing level blocks would overwrite non-padding ROM data"
        )
        expected_rom[level_address:new_end] = new_section
        expected_rom[0x18E:0x190] = cartridge_checksum(expected_rom).to_bytes(2, "big")

        built = build_studio_project(project_file)
        assert verify_studio_project(project_file)["status"] == "current"
        actual_rom = (project / built["artifact"]["path"]).read_bytes()
        differences = [index for index, pair in enumerate(zip(actual_rom, expected_rom)) if pair[0] != pair[1]]
        assert len(actual_rom) == len(expected_rom) and not differences, (
            "GUI collision growth ROM differs from independently relocated "
            f"levels/pointers/checksum at {differences[:32]}"
        )
        assert actual_rom[level_address : level_address + len(new_stream)] == new_stream
        assert decode_collision(list(new_stream)) == expected_grid

        levels_path.write_bytes(original_levels)
        restored = build_studio_project(project_file)
        assert restored["artifact"]["sha256"] == CANONICAL_SHA256, (
            "restoring the GUI collision stroke did not restore canonical ROM"
        )

    route = "main window / shared Save All" if args.gui else "Qt widget"
    object_note = ", player/spawner drags with separate Undo" if args.gui else ""
    print(
        f"Flicky GUI collision growth ({route}): five-cell stroke, one Undo"
        f"{object_note}, "
        f"{growth}-byte level relocation, {moved_pointers} round pointers, "
        "full-ROM byte oracle and exact restore"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
