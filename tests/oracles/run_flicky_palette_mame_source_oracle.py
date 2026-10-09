#!/usr/bin/env python3
"""Prove a Flicky CRAM edit reaches the built ROM and emulated VDP.

Reads the neighboring canonical source/ROM; all edits and images stay in an
isolated project beneath this repository's ignored build directory. Pass the
native Semantics editor to include the Qt click/save path.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import subprocess
import tempfile

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
from run_flicky_round_playtest_source_oracle import launch


def compare_vdp(before_path: Path, after_path: Path) -> None:
    try:
        from PIL import Image
    except ImportError as error:
        raise RuntimeError("Pillow is required for Flicky VDP pixel comparison") from error
    with Image.open(before_path) as before_image, Image.open(after_path) as after_image:
        before = before_image.convert("RGB")
        after = after_image.convert("RGB")
        assert before.size == after.size == (256, 224)
        before_bytes = before.tobytes()
        after_bytes = after.tobytes()
        changes = Counter((before_bytes[index:index + 3],
                           after_bytes[index:index + 3])
                          for index in range(0, len(before_bytes), 3)
                          if before_bytes[index:index + 3]
                          != after_bytes[index:index + 3])
    assert changes == Counter({(b"\x34\xac\x00", b"\xff\x00\x00"): 9582}), (
        f"Flicky palette edit changed unexpected VDP colours/pixels: {changes}"
    )
    print(f"Flicky CRAM edit: {sum(changes.values())} changed MAME VDP pixels; "
          f"colour transitions={changes.most_common(10)}")


def render_native(executable: Path, source: Path, output: Path) -> None:
    workspace = source / "content" / "workspace"
    subprocess.run([
        str(executable.resolve()), "--render-image", str(source),
        str(workspace / "level" / "levels.json"),
        str(workspace / "graphics" / "graphics.json"),
        str(workspace / "graphics" / "semantics.json"),
        str(workspace / "graphics" / "sequences.json"),
        "1", str(output),
    ], check=True)


def compare_preview_delta(native_before: Path, native_after: Path,
                          mame_before: Path, mame_after: Path) -> None:
    from PIL import Image
    def changed_positions(before: Path, after: Path) -> set[int]:
        with Image.open(before) as before_image, Image.open(after) as after_image:
            old = before_image.convert("RGB").tobytes()
            new = after_image.convert("RGB").tobytes()
        return {index // 3 for index in range(0, len(old), 3)
                if old[index:index + 3] != new[index:index + 3]}
    native = changed_positions(native_before, native_after)
    mame = changed_positions(mame_before, mame_after)
    overlap = len(native & mame)
    assert overlap >= 0.98 * len(native) and overlap >= 0.98 * len(mame), (
        f"Flicky native/MAME palette delta masks diverge: "
        f"native={len(native)} MAME={len(mame)} overlap={overlap}"
    )
    print(f"Flicky palette delta: native={len(native)} MAME={len(mame)} "
          f"overlap={overlap}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--rom", type=Path, required=True)
    parser.add_argument("--native-writer", type=Path, required=True)
    parser.add_argument("--native-semantics-editor", type=Path,
                        help="exercise the Qt Semantics widget; omit for document probe")
    parser.add_argument("--mame", type=Path, required=True)
    parser.add_argument("--native-renderer", type=Path,
                        help="also compare palette delta in the C++ Level scene")
    args = parser.parse_args()
    os.environ["RETROLARIUM_FLICKY_LEVEL_WRITER"] = str(args.native_writer.resolve())
    scratch = Path(__file__).resolve().parents[1] / "build"
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="flicky-palette-mame-", dir=scratch) as name:
        root = Path(name)
        import_flicky_source(args.source_root, args.rom, root)
        create_flicky_studio_project(root)
        project = root / "flicky-ue.rlj"
        build_studio_project(project)
        baseline_rom = (root / "build" / "flicky-edited.bin").read_bytes()
        assert verify_studio_project(project)["status"] == "current"
        baseline_png = root / "baseline-vdp.png"
        launch(studio_flicky_round_playtest_plan(project, mame=args.mame, round=1),
               1, root, baseline_png)
        source = root / "flicky-source"
        if args.native_renderer:
            render_native(args.native_renderer, source, root / "native-baseline.png")

        semantics = root / "flicky-source" / WORKSPACE_FILES["graphics_semantics"]
        original = semantics.read_bytes()
        document = json.loads(original)
        palette = next(item for item in document["palettes"]
                       if item["id"] == "Level_Palette0")
        original_word = palette["colours"][8]
        assert original_word == 0x0A2
        payload = b"".join(word.to_bytes(2, "big") for word in palette["colours"])
        address = baseline_rom.find(payload)
        assert address >= 0 and baseline_rom.find(payload, address + 1) < 0, \
            "Level_Palette0 has no unique canonical ROM address"
        edited_word = 0x00E
        if args.native_semantics_editor:
            subprocess.run([
                str(args.native_semantics_editor.resolve()), "--edit-colour",
                str(semantics), "Level_Palette0", "8", str(edited_word),
            ], check=True)
        else:
            print("Flicky palette probe uses document edit; Qt click is unverified")
            palette["colours"][8] = edited_word
            semantics.write_text(json.dumps(document, indent=2) + "\n",
                                 encoding="utf-8")
        edited_document = json.loads(semantics.read_text(encoding="utf-8"))
        edited_palette = next(item for item in edited_document["palettes"]
                              if item["id"] == "Level_Palette0")
        assert edited_palette["colours"][8] == edited_word
        try:
            studio_flicky_round_playtest_plan(project, mame=args.mame, round=1)
        except StudioProjectError as error:
            assert "stale" in str(error)
        else:
            raise AssertionError("MAME playtest accepted a stale palette build")
        build_studio_project(project)
        assert verify_studio_project(project)["status"] == "current"
        edited_rom = (root / "build" / "flicky-edited.bin").read_bytes()
        colour_address = address + 8 * 2
        assert edited_rom[colour_address:colour_address + 2] \
            == edited_word.to_bytes(2, "big")
        assert edited_rom[0x18E:0x190] == cartridge_checksum(edited_rom).to_bytes(2, "big")
        expected_changes = {colour_address + 1} | {
            offset for offset in (0x18E, 0x18F)
            if baseline_rom[offset] != edited_rom[offset]
        }
        actual_changes = {offset for offset, pair in enumerate(
            zip(baseline_rom, edited_rom)) if pair[0] != pair[1]}
        assert actual_changes == expected_changes, (
            f"palette edit touched unexpected ROM bytes: {sorted(actual_changes)[:16]}"
        )
        edited_png = root / "edited-vdp.png"
        launch(studio_flicky_round_playtest_plan(project, mame=args.mame, round=1),
               1, root, edited_png)
        compare_vdp(baseline_png, edited_png)
        if args.native_renderer:
            render_native(args.native_renderer, source, root / "native-edited.png")
            compare_preview_delta(root / "native-baseline.png",
                                  root / "native-edited.png", baseline_png,
                                  edited_png)
        semantics.write_bytes(original)
        restored = build_studio_project(project)
        assert restored["artifact"]["sha256"] == CANONICAL_SHA256
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
