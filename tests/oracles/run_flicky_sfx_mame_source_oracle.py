#!/usr/bin/env python3
"""Locate a deterministic Flicky SFX in the round-1 MAME YM trace."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import tempfile

from retrolarium_kit.games.flicky.content import (
    CANONICAL_SHA256, WORKSPACE_FILES, import_flicky_source,
)
from retrolarium_kit.studio.project import build_studio_project, verify_studio_project
from retrolarium_kit.studio.create import create_flicky_studio_project
from retrolarium_kit.studio.playtest import studio_flicky_sound_preview
from run_flicky_content_build_source_oracle import (
    assert_rom_delta, sound_source_model,
)
from run_flicky_sound_mame_register_oracle import (
    capture_mame, matching_grams, vgm_register_writes,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--rom", type=Path, required=True)
    parser.add_argument("--native-writer", type=Path, required=True)
    parser.add_argument("--native-sound-editor", type=Path,
                        help="edit the SFX note through the Qt Sound widget")
    parser.add_argument("--mame", type=Path, required=True)
    args = parser.parse_args()
    os.environ["RETROLARIUM_FLICKY_LEVEL_WRITER"] = str(args.native_writer.resolve())
    scratch = Path(__file__).resolve().parents[2] / "build"
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="flicky-sfx-mame-", dir=scratch) as name:
        root = Path(name)
        import_flicky_source(args.source_root, args.rom, root)
        create_flicky_studio_project(root)
        project = root / "flicky-ue.rlj"
        build_studio_project(project)
        baseline_rom = (root / "build" / "flicky-edited.bin").read_bytes()
        _, registers = capture_mame(root, project, args.mame, "baseline")
        _, sfx_registers = capture_mame(
            root, project, args.mame, "sfx90", sfx=0x90)
        _, repeat_registers = capture_mame(
            root, project, args.mame, "sfx90-repeat", sfx=0x90)
        assert repeat_registers == sfx_registers, \
            "injected SFX90 MAME YM trace is not deterministic"
        _, sfx95_registers = capture_mame(
            root, project, args.mame, "sfx95", sfx=0x95)
        _, sfx96_registers = capture_mame(
            root, project, args.mame, "sfx96", sfx=0x96)
        assert sfx95_registers == sfx96_registers, \
            "SFX95 and SFX96 should alias the same sound effect"
        injected_registers = {"90": sfx_registers, "95And96": sfx95_registers}
        for suffix in ("91", "92", "93", "94", "97", "98"):
            _, injected_registers[suffix] = capture_mame(
                root, project, args.mame, f"sfx{suffix}", sfx=int(suffix, 16))
        mame_values = [item[1:] for item in registers]
        sfx_values = [item[1:] for item in sfx_registers]
        print(f"round-1 MAME YM register writes={len(mame_values)}")
        print(f"injected SFX90 MAME YM register writes={len(sfx_values)}")
        print(f"injected SFX95/96 MAME YM register writes="
              f"{len(sfx95_registers)}/{len(sfx96_registers)}; identical=True")
        previews = {}
        preview_reports = {}
        for suffix in ("90", "91", "92", "93", "94", "95And96", "97", "98"):
            header = f"zSFX{suffix}Header"
            preview = studio_flicky_sound_preview(
                project, header=header, seconds=2)
            preview_reports[suffix] = preview
            reference = vgm_register_writes(
                Path(preview["vgm"]["path"]).read_bytes())
            previews[suffix] = reference
            baseline_motifs = matching_grams(reference, mame_values, 8)
            own_motifs = matching_grams(
                reference, [item[1:] for item in injected_registers[suffix]], 8)
            assert baseline_motifs == 0 and own_motifs >= 20, \
                f"SFX{suffix} lacks an injection-specific MAME YM motif"
            assert injected_registers[suffix] != registers, \
                f"SFX{suffix} injection did not affect MAME YM writes"
            print(f"{header}: writes={len(reference)} "
                  f"baseline grams8={baseline_motifs} "
                  f"SFX90 grams8={matching_grams(reference, sfx_values, 8)} "
                  f"own injected grams8={own_motifs}")
        assert matching_grams(previews["90"], mame_values, 8) == 0
        assert matching_grams(previews["90"], sfx_values, 8) >= 27
        assert all(matching_grams(previews[suffix], sfx_values, 8) == 0
                   for suffix in ("91", "92", "93", "94", "95And96", "97", "98"))

        source = root / "flicky-source"
        sound_path = source / WORKSPACE_FILES["z80_sound_banks"]
        original_sound = sound_path.read_bytes()
        model = sound_source_model(source)
        document = model.export_document(sound_path)
        sequence = next(item for item in document["sequences"]
                        if item["id"] == "zSFX90Sequence")
        old_sequence = bytes(sequence["bytes"])
        assert baseline_rom.count(old_sequence) == 1
        address = baseline_rom.find(old_sequence)
        note = model.piano_roll_notes(sequence["bytes"])[0]
        new_note = note["note"] + 1
        if args.native_sound_editor:
            environment = os.environ.copy()
            environment.setdefault("QT_QPA_PLATFORM",
                                   "windows" if os.name == "nt" else "offscreen")
            subprocess.run([
                str(args.native_sound_editor.resolve()), "--edit-note",
                str(sound_path), sequence["id"], "0", str(new_note),
            ], check=True, env=environment, timeout=60)
        else:
            print("Flicky SFX90 note uses document edit; Qt click unverified")
            sequence["bytes"][note["offset"]] = 0x81 + new_note
            model.apply_document(document, source / "src/sound/z80/data.asm",
                                 sound_path)
        saved_sequence = next(item for item in model.export_document(
            sound_path)["sequences"] if item["id"] == sequence["id"])
        assert saved_sequence["bytes"][note["offset"]] == 0x81 + new_note
        assert verify_studio_project(project)["status"] == "mismatch"
        edited_preview = studio_flicky_sound_preview(
            project, header="zSFX90Header", seconds=2)
        assert edited_preview["wav"]["sha256"] \
            != preview_reports["90"]["wav"]["sha256"], \
            "SFX90 note edit did not change audible WAV preview"
        edited_reference = vgm_register_writes(
            Path(edited_preview["vgm"]["path"]).read_bytes())
        build_studio_project(project)
        assert verify_studio_project(project)["status"] == "current"
        edited_rom = (root / "build" / "flicky-edited.bin").read_bytes()
        note_address = address + note["offset"]
        assert edited_rom[note_address] == 0x81 + new_note
        assert_rom_delta(baseline_rom, edited_rom, [note_address], "SFX90 note")
        _, edited_registers = capture_mame(
            root, project, args.mame, "sfx90-edited", sfx=0x90)
        assert len(edited_registers) == len(sfx_registers)
        assert all(before[:3] == after[:3] for before, after in zip(
            sfx_registers, edited_registers)), \
            "SFX90 edit shifted or reordered emulated YM writes"
        mame_delta = [(before[1], before[2], before[3], after[3])
                      for before, after in zip(sfx_registers, edited_registers)
                      if before != after]
        assert len(edited_reference) == len(previews["90"])
        assert all(before[:2] == after[:2] for before, after in zip(
            previews["90"], edited_reference)), \
            "SFX90 edit shifted or reordered preview YM writes"
        preview_delta = [(before[0], before[1], before[2], after[2])
                         for before, after in zip(previews["90"], edited_reference)
                         if before != after]
        assert len(mame_delta) == 15 and mame_delta == preview_delta, \
            "SFX90 note register deltas differ between MAME and preview"
        assert all(register in (0xA1, 0xA5)
                   for _, register, _, _ in mame_delta), \
            "SFX90 note edit changed non-frequency YM registers"
        print(f"SFX90 note {note['note']}->{new_note} at ROM ${note_address:X}: "
              f"MAME writes {len(sfx_registers)}->{len(edited_registers)} "
              f"deltas={len(mame_delta)}, preview writes "
              f"{len(previews['90'])}->{len(edited_reference)} "
              f"deltas={len(preview_delta)}; exact-delta=True")
        sound_path.write_bytes(original_sound)
        assert build_studio_project(project)["artifact"]["sha256"] == CANONICAL_SHA256
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
