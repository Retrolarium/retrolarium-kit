#!/usr/bin/env python3
"""Exercise Flicky Sound preview and edited-note audio in an isolated snapshot."""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import tempfile

from retrolarium_kit.flicky_content import WORKSPACE_FILES, import_flicky_source
from retrolarium_kit.studio_project import (
    StudioProjectError,
    create_flicky_studio_project,
    studio_flicky_sound_preview,
)


def check_audio(report: dict, root: Path) -> None:
    assert report["status"] == "rendered"
    for kind, magic in (("vgm", b"Vgm "), ("wav", b"RIFF")):
        path = Path(report[kind]["path"])
        assert path.is_relative_to(root / "build/sound-preview")
        assert path.read_bytes().startswith(magic)
        assert path.stat().st_size == report[kind]["size"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--rom", type=Path, required=True)
    parser.add_argument("--native-sound-editor", type=Path, required=True)
    args = parser.parse_args()
    scratch = Path(__file__).resolve().parents[2] / "build"
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="flicky-audio-gate-", dir=scratch) as name:
        root = Path(name)
        import_flicky_source(args.source_root, args.rom, root)
        inspection = create_flicky_studio_project(root)
        sound = next(tab for tab in inspection["workspace"]["tabs"]
                     if tab["id"] == "sound")
        assert "preview" in sound["capabilities"]
        project = root / "flicky-ue.rlj"
        music = studio_flicky_sound_preview(
            project, header="zMusic81Header", seconds=2)
        check_audio(music, root)
        muted = studio_flicky_sound_preview(
            project, header="zMusic81Header", seconds=2,
            enabled_channels=(0,))
        check_audio(muted, root)
        assert muted["vgm"]["sha256"] != music["vgm"]["sha256"]
        assert muted["wav"]["sha256"] != music["wav"]["sha256"]
        original_sfx = studio_flicky_sound_preview(
            project, header="zSFX90Header", seconds=1)
        check_audio(original_sfx, root)
        source = root / "flicky-source"
        sound_path = source / WORKSPACE_FILES["z80_sound_banks"]
        subprocess.run([
            str(args.native_sound_editor.resolve()), "--edit-note",
            str(sound_path), "zSFX90Sequence", "0", "2",
        ], check=True)
        edited_sfx = studio_flicky_sound_preview(
            project, header="zSFX90Header", seconds=1)
        check_audio(edited_sfx, root)
        assert edited_sfx["document_sha256"] != original_sfx["document_sha256"]
        assert edited_sfx["vgm"]["sha256"] != original_sfx["vgm"]["sha256"]
        assert edited_sfx["wav"]["sha256"] != original_sfx["wav"]["sha256"]
        for kwargs in (
            {"header": "missing", "seconds": 1},
            {"header": "zMusic81Header", "seconds": 0},
            {"header": "zMusic81Header", "seconds": 1,
             "enabled_channels": (99,)},
            {"header": "zMusic81Header", "seconds": 1,
             "enabled_channels": ()},
        ):
            try:
                studio_flicky_sound_preview(project, **kwargs)
            except StudioProjectError:
                pass
            else:
                raise AssertionError(f"invalid preview accepted: {kwargs}")
        pinned_script = source / "scripts/authoring/sound_vgm.py"
        pinned_script.write_bytes(pinned_script.read_bytes() + b"\n# stale source probe\n")
        try:
            studio_flicky_sound_preview(
                project, header="zSFX90Header", seconds=1)
        except StudioProjectError as error:
            assert "snapshot changed" in str(error)
        else:
            raise AssertionError("changed Flicky sound renderer source was accepted")
        print("Flicky Sound preview: music, mute, SFX and native GUI note edit verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
