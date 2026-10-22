"""Verify a Qt piano-roll drag through the canonical UE sound ROM byte.

The imported source and ROM live in a disposable project. No emulator or
Playtest is launched.
"""

from __future__ import annotations

import argparse
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
    sound_source_model,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--rom", type=Path, required=True)
    parser.add_argument("--native-writer", type=Path, required=True)
    parser.add_argument("--native-sound-editor", type=Path, required=True)
    args = parser.parse_args()
    os.environ["RETROLARIUM_FLICKY_LEVEL_WRITER"] = str(args.native_writer.resolve())

    original_rom = args.rom.read_bytes()
    scratch = Path(__file__).resolve().parents[3] / "build"
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="flicky-piano-drag-", dir=scratch) as name:
        project = Path(name)
        import_flicky_source(args.source_root, args.rom, project)
        create_flicky_studio_project(project)
        project_file = project / "flicky-ue.rlj"
        zero = build_studio_project(project_file)
        assert zero["artifact"]["sha256"] == CANONICAL_SHA256, "zero-edit build must match the canonical ROM"

        source = project / SOURCE_DIRECTORY
        sound_path = source / WORKSPACE_FILES["z80_sound_banks"]
        original_sound = sound_path.read_bytes()
        sound_model = sound_source_model(source)
        sequence_id = "zSFX90Sequence"
        sequence = next(
            record for record in sound_model.export_document(sound_path)["sequences"] if record["id"] == sequence_id
        )
        original_stream = bytes(sequence["bytes"])
        assert original_rom.count(original_stream) == 1, "SFX90 stream has no unique ROM address"
        note = sound_model.piano_roll_notes(sequence["bytes"])[0]
        assert 0 <= note["note"] <= 92
        edited_pitch = note["note"] + 2
        note_address = original_rom.find(original_stream) + note["offset"]

        subprocess.run(
            [
                str(args.native_sound_editor.resolve()),
                "--drag-note",
                str(sound_path),
                sequence_id,
                "0",
                "2",
            ],
            check=True,
        )
        saved = next(
            record for record in sound_model.export_document(sound_path)["sequences"] if record["id"] == sequence_id
        )
        assert saved["bytes"][note["offset"]] == 0x81 + edited_pitch, (
            "Qt piano drag did not save the expected source note"
        )
        assert verify_studio_project(project_file)["status"] == "mismatch"

        built = build_studio_project(project_file)
        assert verify_studio_project(project_file)["status"] == "current"
        edited_rom = (project / built["artifact"]["path"]).read_bytes()
        assert edited_rom[note_address] == 0x81 + edited_pitch, (
            "Qt piano drag did not read back from the independent ROM address"
        )
        assert_rom_delta(original_rom, edited_rom, [note_address], "Qt piano-roll drag")

        sound_path.write_bytes(original_sound)
        restored = build_studio_project(project_file)
        assert restored["artifact"]["sha256"] == CANONICAL_SHA256, (
            "restoring the sound document must reproduce the exact ROM"
        )
    print("Flicky Qt piano drag: Save/Build/Verify, independent SFX ROM byte, isolated delta, exact restore")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
