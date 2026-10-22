#!/usr/bin/env python3
"""Probe a Flicky sprite-mapping offset through ROM and emulated VDP."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile

from retrolarium_kit.games.flicky.content import (
    CANONICAL_SHA256,
    WORKSPACE_FILES,
    import_flicky_source,
)
from retrolarium_core.megadrive import cartridge_checksum
from retrolarium_kit.studio.project import (
    StudioProjectError,
    build_studio_project,
    verify_studio_project,
)
from retrolarium_kit.studio.create import create_flicky_studio_project
from retrolarium_kit.studio.playtest import studio_flicky_round_playtest_plan
from run_level_tiles_mame_source_oracle import changed_pixels
from run_palette_mame_source_oracle import render_native
from run_round_playtest_source_oracle import launch

MAPPING_ID = "Throwable_ThrownAnim0Data0"


def mapping_bytes(mapping: dict) -> bytes:
    output = bytearray((len(mapping["pieces"]) - 1, mapping["render_flags"]))
    for piece in mapping["pieces"]:
        size = ((piece["width"] - 1) << 2) | (piece["height"] - 1)
        attributes = piece["tile"] | (piece["palette"] << 13)
        attributes |= 0x8000 if piece["priority"] else 0
        attributes |= 0x0800 if piece["hflip"] else 0
        attributes |= 0x1000 if piece["vflip"] else 0
        output.extend((piece["y"] & 0xFF, size))
        output.extend(attributes.to_bytes(2, "big"))
        output.extend((piece["x"] & 0xFF, piece["flipped_x"] & 0xFF))
    return bytes(output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--rom", type=Path, required=True)
    parser.add_argument("--native-writer", type=Path, required=True)
    parser.add_argument(
        "--native-sequences-editor", type=Path, help="exercise the Qt Animations widget; omit for document probe"
    )
    parser.add_argument("--native-renderer", type=Path, required=True)
    parser.add_argument("--mame", type=Path, required=True)
    args = parser.parse_args()
    os.environ["RETROLARIUM_FLICKY_LEVEL_WRITER"] = str(args.native_writer.resolve())
    scratch = Path(__file__).resolve().parents[3] / "build"
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="flicky-mapping-mame-", dir=scratch) as name:
        root = Path(name)
        import_flicky_source(args.source_root, args.rom, root)
        create_flicky_studio_project(root)
        project = root / "flicky-ue.rlj"
        build_studio_project(project)
        baseline_rom = (root / "build" / "flicky-edited.bin").read_bytes()
        assert verify_studio_project(project)["status"] == "current"
        baseline_png = root / "baseline-vdp.png"
        launch(studio_flicky_round_playtest_plan(project, mame=args.mame, round=1), 1, root, baseline_png)
        source = root / "flicky-source"
        render_native(args.native_renderer, source, root / "native-baseline.png")

        sequences = source / WORKSPACE_FILES["graphics_sequences"]
        if args.native_sequences_editor:
            environment = os.environ.copy()
            # The Windows Qt offscreen plugin stalls this source-backed
            # preview probe; use the native backend on Windows.
            environment.setdefault("QT_QPA_PLATFORM", "windows" if os.name == "nt" else "offscreen")
            preview_result = subprocess.run(
                [
                    str(args.native_sequences_editor.resolve()),
                    "--preview-source",
                    str(source),
                    str(source / WORKSPACE_FILES["graphics_assets"]),
                    str(source / WORKSPACE_FILES["graphics_semantics"]),
                    str(sequences),
                ],
                env=environment,
                capture_output=True,
                text=True,
                timeout=60,
            )
            assert preview_result.returncode == 0, (
                f"Qt mapping preview probe failed ({preview_result.returncode}): "
                f"{preview_result.stdout} {preview_result.stderr}"
            )
        original = sequences.read_bytes()
        document = json.loads(original)
        mapping = next(item for item in document["mappings"] if item["id"] == MAPPING_ID)
        assert len(mapping["pieces"]) == 1
        original_bytes = mapping_bytes(mapping)
        address = baseline_rom.find(original_bytes)
        assert address >= 0 and baseline_rom.find(original_bytes, address + 1) < 0, (
            "sprite mapping has no unique canonical ROM address"
        )
        old_x = mapping["pieces"][0]["x"]
        new_x = old_x + 4
        assert new_x <= 127
        if args.native_sequences_editor:
            subprocess.run(
                [
                    str(args.native_sequences_editor.resolve()),
                    "--edit-mapping-x",
                    str(sequences),
                    MAPPING_ID,
                    "0",
                    str(new_x),
                ],
                check=True,
            )
        else:
            print("Flicky mapping probe uses document edit; Qt click is unverified")
            mapping["pieces"][0]["x"] = new_x
            sequences.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        saved = json.loads(sequences.read_text(encoding="utf-8"))
        edited_mapping = next(item for item in saved["mappings"] if item["id"] == MAPPING_ID)
        assert edited_mapping["pieces"][0]["x"] == new_x
        try:
            studio_flicky_round_playtest_plan(project, mame=args.mame, round=1)
        except StudioProjectError as error:
            assert "stale" in str(error)
        else:
            raise AssertionError("MAME accepted stale Flicky mapping build")
        build_studio_project(project)
        assert verify_studio_project(project)["status"] == "current"
        edited_rom = (root / "build" / "flicky-edited.bin").read_bytes()
        offset = address + 6
        assert edited_rom[offset] == new_x & 0xFF
        assert edited_rom[0x18E:0x190] == cartridge_checksum(edited_rom).to_bytes(2, "big")
        expected_changes = {offset} | {index for index in (0x18E, 0x18F) if edited_rom[index] != baseline_rom[index]}
        actual_changes = {index for index, pair in enumerate(zip(baseline_rom, edited_rom)) if pair[0] != pair[1]}
        assert actual_changes == expected_changes, (
            f"mapping edit changed unrelated ROM bytes: {sorted(actual_changes)[:16]}"
        )

        edited_png = root / "edited-vdp.png"
        launch(studio_flicky_round_playtest_plan(project, mame=args.mame, round=1), 1, root, edited_png)
        render_native(args.native_renderer, source, root / "native-edited.png")
        mame_delta = changed_pixels(baseline_png, edited_png)
        native_delta = changed_pixels(root / "native-baseline.png", root / "native-edited.png")
        print(
            f"Flicky {MAPPING_ID} VDP delta: MAME={len(mame_delta)} "
            f"native={len(native_delta)} overlap={len(mame_delta & native_delta)}"
        )
        assert mame_delta == native_delta and len(mame_delta) == 288, (
            "mapping edit changed unexpected MAME/native VDP pixels"
        )
        sequences.write_bytes(original)
        assert build_studio_project(project)["artifact"]["sha256"] == CANONICAL_SHA256
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
