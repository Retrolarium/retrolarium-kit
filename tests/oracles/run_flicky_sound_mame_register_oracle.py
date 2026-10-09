#!/usr/bin/env python3
"""Compare an edited Flicky music note in MAME and Studio VGM preview."""

from __future__ import annotations

import argparse
from collections import Counter
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from retrolarium_kit.games.flicky.content import (
    CANONICAL_SHA256, WORKSPACE_FILES, import_flicky_source,
)
from retrolarium_kit.studio.project import build_studio_project, verify_studio_project
from retrolarium_kit.studio.create import create_flicky_studio_project
from retrolarium_kit.studio.playtest import (
    studio_flicky_round_playtest_plan,
    studio_flicky_sound_preview,
)
from run_flicky_content_build_source_oracle import (
    assert_rom_delta, sound_source_model,
)
from run_flicky_round_playtest_source_oracle import launch


def register_writes(raw: list[tuple[int, int, int, int]]
                    ) -> list[tuple[int, int, int, int]]:
    selected: dict[int, int] = {}
    result = []
    for frame, address, data, mask in raw:
        assert mask == 0xFF and 0x4000 <= address <= 0x4003
        port, command = divmod(address - 0x4000, 2)
        if command == 0:
            selected[port] = data
        else:
            assert port in selected, "YM data write has no register address"
            result.append((frame, port, selected[port], data))
    return result


def vgm_register_writes(vgm: bytes) -> list[tuple[int, int, int]]:
    return [event[1:] for event in vgm_register_events(vgm)]


def vgm_register_events(vgm: bytes) -> list[tuple[int, int, int, int]]:
    assert vgm.startswith(b"Vgm ")
    cursor = 0x34 + int.from_bytes(vgm[0x34:0x38], "little")
    sample = 0
    result = []
    while cursor < len(vgm):
        command = vgm[cursor]
        cursor += 1
        if command in (0x52, 0x53):
            result.append((sample, command - 0x52,
                           vgm[cursor], vgm[cursor + 1]))
            cursor += 2
        elif command == 0x50:
            cursor += 1
        elif command == 0x61:
            sample += int.from_bytes(vgm[cursor:cursor + 2], "little")
            cursor += 2
        elif 0x70 <= command <= 0x7F:
            sample += (command & 15) + 1
        elif command == 0x66:
            break
        else:
            raise AssertionError(f"unsupported VGM command 0x{command:02X}")
    return result


def matching_grams(needle: list[tuple[int, int, int]],
                   haystack: list[tuple[int, int, int]], size: int) -> int:
    corpus = {tuple(haystack[index:index + size])
              for index in range(len(haystack) - size + 1)}
    return sum(tuple(needle[index:index + size]) in corpus
               for index in range(len(needle) - size + 1))


def capture_mame(root: Path, project: Path, mame: Path,
                 label: str, sfx: int | None = None,
                 ) -> tuple[Path, list[tuple[int, int, int, int]]]:
    trace = root / f"ym2612-{label}.csv"
    environment = {"FLICKY_PLAYTEST_YM_TRACE": str(trace)}
    if sfx is not None:
        environment["FLICKY_PLAYTEST_SFX"] = str(sfx)
    launch(studio_flicky_round_playtest_plan(project, mame=mame, round=1),
           1, root, root / f"round-1-{label}.png", snapshot_delay=300,
           extra_environment=environment)
    lines = trace.read_text(encoding="ascii").splitlines()
    assert lines, "MAME did not capture any YM2612 writes"
    writes = [tuple(int(part, 16) if index else int(part)
                    for index, part in enumerate(line.split(",")))
              for line in lines]
    return trace, register_writes(writes)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--rom", type=Path, required=True)
    parser.add_argument("--native-writer", type=Path, required=True)
    parser.add_argument("--native-sound-editor", type=Path,
                        help="edit the music note through the Qt Sound widget")
    parser.add_argument("--mame", type=Path, required=True)
    parser.add_argument("--diagnostics-dir", type=Path)
    args = parser.parse_args()
    os.environ["RETROLARIUM_FLICKY_LEVEL_WRITER"] = str(args.native_writer.resolve())
    scratch = Path(__file__).resolve().parents[1] / "build"
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="flicky-ym-mame-", dir=scratch) as name:
        root = Path(name)
        import_flicky_source(args.source_root, args.rom, root)
        create_flicky_studio_project(root)
        project = root / "flicky-ue.rlj"
        build_studio_project(project)
        baseline_rom = (root / "build" / "flicky-edited.bin").read_bytes()
        trace, registers = capture_mame(root, project, args.mame, "baseline")
        _, repeated_registers = capture_mame(root, project, args.mame, "repeat")
        assert repeated_registers == registers, \
            "canonical MAME YM2612 register trace is not deterministic"
        frames = Counter(write[0] for write in registers)
        print(f"MAME YM2612 register writes: {len(registers)}; "
              f"frames={min(frames)}..{max(frames)}")
        mame_values = [item[1:] for item in registers]
        preview_writes = {}
        preview_vgm = {}
        preview_reports = {}
        motif_counts = {}
        for header in ("zMusic81Header", "zMusic83Header", "zSFX90Header"):
            preview = studio_flicky_sound_preview(project, header=header, seconds=5)
            preview_reports[header] = preview
            vgm = Path(preview["vgm"]["path"]).read_bytes()
            preview_vgm[header] = vgm
            reference = vgm_register_writes(vgm)
            preview_writes[header] = reference
            motif_counts[header] = matching_grams(reference, mame_values, 8)
            print(f"{header} preview YM writes={len(reference)}; "
                  f"grams8={motif_counts[header]}")
        assert motif_counts["zMusic83Header"] >= 1800
        assert motif_counts["zMusic83Header"] > 20 * motif_counts["zMusic81Header"]
        assert motif_counts["zSFX90Header"] < 10

        source = root / "flicky-source"
        sound_path = source / WORKSPACE_FILES["z80_sound_banks"]
        original_sound = sound_path.read_bytes()
        model = sound_source_model(source)
        document = model.export_document(sound_path)
        sequence = next(item for item in document["sequences"]
                        if item["id"] == "zMusic83FM1")
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
            print("Flicky music note uses document edit; Qt click unverified")
            sequence["bytes"][note["offset"]] = 0x81 + new_note
            model.apply_document(document, source / "src/sound/z80/data.asm", sound_path)
        saved_document = model.export_document(sound_path)
        saved_sequence = next(item for item in saved_document["sequences"]
                              if item["id"] == sequence["id"])
        assert saved_sequence["bytes"][note["offset"]] == 0x81 + new_note
        assert verify_studio_project(project)["status"] == "mismatch"
        edited_preview = studio_flicky_sound_preview(
            project, header="zMusic83Header", seconds=5)
        edited_reference = vgm_register_writes(
            Path(edited_preview["vgm"]["path"]).read_bytes())
        build_studio_project(project)
        assert verify_studio_project(project)["status"] == "current"
        edited_rom = (root / "build" / "flicky-edited.bin").read_bytes()
        note_address = address + note["offset"]
        assert edited_rom[note_address] == 0x81 + new_note
        assert_rom_delta(baseline_rom, edited_rom, [note_address], "music note")
        edited_trace, edited_registers = capture_mame(
            root, project, args.mame, "edited")
        assert len(edited_registers) == len(registers)
        assert all(before[:3] == after[:3] for before, after in zip(
            registers, edited_registers)), \
            "music note edit shifted or reordered emulated YM writes"
        changed_registers = [(before[1], before[2], before[3], after[3])
                             for before, after in zip(registers, edited_registers)
                             if before != after]
        assert len(edited_reference) == len(preview_writes["zMusic83Header"])
        assert all(before[:2] == after[:2] for before, after in zip(
            preview_writes["zMusic83Header"], edited_reference)), \
            "music note edit shifted or reordered preview YM writes"
        changed_preview = [(before[0], before[1], before[2], after[2])
                           for before, after in zip(
                               preview_writes["zMusic83Header"], edited_reference)
                           if before != after]
        assert len(changed_registers) == 25
        assert changed_registers == changed_preview, \
            "integrated preview and MAME disagree on edited music register deltas"
        assert all(0xA0 <= register <= 0xA6
                   for _, register, _, _ in changed_registers), \
            "music note edit changed non-frequency YM registers"
        print(f"zMusic83FM1 note {note['note']}->{new_note} at ROM ${note_address:X}: "
              f"MAME registers={len(registers)}->{len(edited_registers)}, "
              f"differences={len(changed_registers)}")
        print(f"preview registers={len(preview_writes['zMusic83Header'])}"
              f"->{len(edited_reference)}; differences={len(changed_preview)}; "
              f"exact-delta={changed_registers == changed_preview}")
        sound_path.write_bytes(original_sound)
        assert build_studio_project(project)["artifact"]["sha256"] == CANONICAL_SHA256

        voice_document = model.export_document(sound_path)
        voice_bank = next(item for item in voice_document["voice_banks"]
                          if item["id"] == "zMusic83Voices")
        bank_bytes = bytes(sum(voice_bank["voices"], []) + voice_bank["tail"])
        assert baseline_rom.count(bank_bytes) == 1, \
            "music voice bank has no unique ROM address"
        voice_address = baseline_rom.find(bank_bytes)
        old_algorithm = voice_bank["voices"][0][0] & 7
        new_algorithm = old_algorithm + 1
        assert new_algorithm <= 7
        if args.native_sound_editor:
            subprocess.run([
                str(args.native_sound_editor.resolve()), "--edit-voice-algorithm",
                str(sound_path), voice_bank["id"], "0", str(new_algorithm),
            ], check=True, env=environment, timeout=60)
        else:
            print("Flicky FM voice uses document edit; Qt click unverified")
            voice_bank["voices"][0][0] = (
                voice_bank["voices"][0][0] & 0xF8) | new_algorithm
            model.apply_document(voice_document,
                                 source / "src/sound/z80/data.asm", sound_path)
        assert verify_studio_project(project)["status"] == "mismatch"
        voice_preview = studio_flicky_sound_preview(
            project, header="zMusic83Header", seconds=5)
        voice_reference = vgm_register_writes(
            Path(voice_preview["vgm"]["path"]).read_bytes())
        build_studio_project(project)
        assert verify_studio_project(project)["status"] == "current"
        voice_rom = (root / "build" / "flicky-edited.bin").read_bytes()
        assert voice_rom[voice_address] == (bank_bytes[0] & 0xF8) | new_algorithm
        assert_rom_delta(baseline_rom, voice_rom, [voice_address], "FM algorithm")
        voice_trace, voice_registers = capture_mame(
            root, project, args.mame, "voice")
        voice_mame_delta = [(before[1], before[2], before[3], after[3])
                            for before, after in zip(registers, voice_registers)
                            if before != after]
        voice_preview_delta = [(before[0], before[1], before[2], after[2])
                               for before, after in zip(
                                   preview_writes["zMusic83Header"], voice_reference)
                               if before != after]
        assert len(voice_registers) == len(registers)
        assert all(before[:3] == after[:3] for before, after in zip(
            registers, voice_registers)), \
            "FM voice edit shifted or reordered emulated YM writes"
        assert len(voice_reference) == len(preview_writes["zMusic83Header"])
        assert all(before[:2] == after[:2] for before, after in zip(
            preview_writes["zMusic83Header"], voice_reference)), \
            "FM voice edit shifted or reordered preview YM writes"
        assert voice_mame_delta == voice_preview_delta == [
            (0, 0xB0, 0x34, 0x35), (1, 0xB2, 0x34, 0x35),
        ], "FM algorithm register deltas differ between MAME and preview"
        print(f"zMusic83Voices algorithm {old_algorithm}->{new_algorithm} "
              f"at ROM ${voice_address:X}: MAME writes={len(voice_registers)} "
              f"deltas={len(voice_mame_delta)}, preview writes={len(voice_reference)} "
              f"deltas={len(voice_preview_delta)}, exact-delta=True")
        sound_path.write_bytes(original_sound)
        assert build_studio_project(project)["artifact"]["sha256"] == CANONICAL_SHA256

        tempo_document = model.export_document(sound_path)
        music = next(item for item in tempo_document["headers"]
                     if item["id"] == "zMusic83Header")
        music_voices = next(item for item in tempo_document["voice_banks"]
                            if item["id"] == music["voices"])
        music_voice_bytes = bytes(sum(music_voices["voices"], [])
                                  + music_voices["tail"])
        assert baseline_rom.count(music_voice_bytes) == 1
        music_voice_address = baseline_rom.find(music_voice_bytes)
        music_rom_base = 0x101E0 + 0x1C8
        music_voice_z80 = 0x1200 + music_voice_address - music_rom_base
        fm_count = sum(track["type"] == "fm" for track in music["tracks"])
        psg_count = sum(track["type"] == "psg" for track in music["tracks"])
        music_header = music_voice_z80.to_bytes(2, "little") + bytes((
            fm_count, psg_count, music["scale"], music["tempo"]))
        assert baseline_rom.count(music_header) == 1
        tempo_address = baseline_rom.find(music_header) + 5
        old_tempo = music["tempo"]
        new_tempo = old_tempo + 1
        if args.native_sound_editor:
            subprocess.run([
                str(args.native_sound_editor.resolve()), "--edit-tempo",
                str(sound_path), music["id"], str(new_tempo),
            ], check=True, env=environment, timeout=60)
        else:
            print("Flicky music tempo uses document edit; Qt click unverified")
            music["tempo"] = new_tempo
            model.apply_document(tempo_document,
                                 source / "src/sound/z80/data.asm", sound_path)
        assert verify_studio_project(project)["status"] == "mismatch"
        tempo_preview = studio_flicky_sound_preview(
            project, header="zMusic83Header", seconds=5)
        tempo_vgm = Path(tempo_preview["vgm"]["path"]).read_bytes()
        build_studio_project(project)
        assert verify_studio_project(project)["status"] == "current"
        tempo_rom = (root / "build" / "flicky-edited.bin").read_bytes()
        assert tempo_rom[tempo_address] == new_tempo
        assert_rom_delta(baseline_rom, tempo_rom, [tempo_address], "music tempo")
        tempo_trace, tempo_registers = capture_mame(
            root, project, args.mame, "tempo")
        baseline_vgm_events = vgm_register_events(preview_vgm["zMusic83Header"])
        tempo_vgm_events = vgm_register_events(tempo_vgm)
        frequency_registers = set(range(0xA0, 0xA7))
        baseline_mame_pitch = {
            item[1:] for item in registers
            if item[0] >= 234 and item[2] in frequency_registers
        }
        tempo_mame_pitch = {
            item[1:] for item in tempo_registers
            if item[0] >= 234 and item[2] in frequency_registers
        }
        baseline_preview_pitch = {
            item[1:] for item in baseline_vgm_events
            if item[2] in frequency_registers
        }
        tempo_preview_pitch = {
            item[1:] for item in tempo_vgm_events
            if item[2] in frequency_registers
        }
        removed_mame_pitch = baseline_mame_pitch - tempo_mame_pitch
        removed_preview_pitch = baseline_preview_pitch - tempo_preview_pitch
        assert tempo_vgm != preview_vgm["zMusic83Header"], \
            "music tempo edit did not change the VGM preview"
        assert tempo_preview["wav"]["sha256"] \
            != preview_reports["zMusic83Header"]["wav"]["sha256"], \
            "music tempo edit did not change the audible WAV preview"
        assert len(registers) == 10487 and len(tempo_registers) == 7844, \
            "music tempo edit changed an unexpected number of MAME YM writes"
        assert len(baseline_vgm_events) == 5294 and len(tempo_vgm_events) == 2452, \
            "music tempo edit changed an unexpected number of preview YM writes"
        assert len(removed_mame_pitch) == 99
        assert removed_mame_pitch <= removed_preview_pitch, \
            "preview retained pitches removed by the edited MAME music tempo"
        print(f"zMusic83Header tempo {old_tempo}->{new_tempo} at ROM "
              f"${tempo_address:X}: MAME writes {len(registers)}"
              f"->{len(tempo_registers)}, preview writes "
              f"{len(baseline_vgm_events)}->{len(tempo_vgm_events)}")
        print(f"pitch values removed by tempo: MAME={len(removed_mame_pitch)}, "
              f"preview={len(removed_preview_pitch)}, "
              f"shared={len(removed_mame_pitch & removed_preview_pitch)}")
        sound_path.write_bytes(original_sound)
        assert build_studio_project(project)["artifact"]["sha256"] == CANONICAL_SHA256
        if args.diagnostics_dir:
            args.diagnostics_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(trace, args.diagnostics_dir / "ym2612-baseline.csv")
            shutil.copyfile(edited_trace, args.diagnostics_dir / "ym2612-edited.csv")
            shutil.copyfile(voice_trace, args.diagnostics_dir / "ym2612-voice.csv")
            shutil.copyfile(tempo_trace, args.diagnostics_dir / "ym2612-tempo.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
