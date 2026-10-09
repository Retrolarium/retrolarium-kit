#!/usr/bin/env python3
"""Verify Flicky audio preview consumes a transient document without Save All."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from retrolarium_kit.games.flicky.content import WORKSPACE_FILES, import_flicky_source
from retrolarium_kit.studio.project import StudioProjectError
from retrolarium_kit.studio.create import create_flicky_studio_project
from retrolarium_kit.studio.playtest import studio_flicky_sound_preview


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--rom", type=Path, required=True)
    args = parser.parse_args()
    scratch = Path(__file__).resolve().parents[2] / "build"
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="flicky-unsaved-audio-", dir=scratch) as name:
        root = Path(name)
        import_flicky_source(args.source_root, args.rom, root)
        create_flicky_studio_project(root)
        project = root / "flicky-ue.rlj"
        source = root / "flicky-source" / WORKSPACE_FILES["z80_sound_banks"]
        original = source.read_bytes()
        marker = b"$D2,$0C,$CF"
        assert original.count(marker) == 1
        edited = original.replace(marker, b"$D3,$0C,$CF", 1)
        preview_dir = root / "build/sound-preview"
        preview_dir.mkdir(parents=True, exist_ok=True)
        transient = preview_dir / ".flicky-unsaved-test.asm"
        transient.write_bytes(edited)

        saved = studio_flicky_sound_preview(
            project, header="zSFX90Header", seconds=1)
        unsaved = studio_flicky_sound_preview(
            project, header="zSFX90Header", seconds=1,
            document_override=transient)
        assert saved["document_origin"] == "saved"
        assert unsaved["document_origin"] == "unsaved"
        assert saved["document_sha256"] == sha256(original).hexdigest()
        assert unsaved["document_sha256"] == sha256(edited).hexdigest()
        assert saved["wav"]["sha256"] != unsaved["wav"]["sha256"]
        assert saved["vgm"]["sha256"] != unsaved["vgm"]["sha256"]
        assert source.read_bytes() == original, "preview saved unsaved source changes"
        command = subprocess.run([
            sys.executable,
            str(Path(__file__).resolve().parents[2] / "rltool.py"),
            "studio", "flicky-sound-preview", str(project),
            "--header", "zSFX90Header", "--seconds", "1",
            "--document-override", str(transient),
        ], check=True, capture_output=True, text=True, timeout=120)
        cli_report = json.loads(command.stdout)
        assert cli_report["document_origin"] == "unsaved"
        assert cli_report["document_sha256"] == unsaved["document_sha256"]
        assert cli_report["wav"]["sha256"] == unsaved["wav"]["sha256"]
        again = studio_flicky_sound_preview(
            project, header="zSFX90Header", seconds=1)
        assert again["wav"]["sha256"] == saved["wav"]["sha256"]
        assert again["document_origin"] == "saved"

        for candidate in (
            source,
            root / "outside.asm",
            preview_dir / "wrong-prefix.asm",
        ):
            if candidate != source:
                candidate.write_bytes(edited)
            try:
                studio_flicky_sound_preview(
                    project, header="zSFX90Header", seconds=1,
                    document_override=candidate)
            except StudioProjectError as error:
                assert "transient Flicky Sound document" in str(error)
            else:
                raise AssertionError(f"unsafe preview override accepted: {candidate}")
        assert source.read_bytes() == original
        print("Flicky unsaved audio preview: changed WAV/VGM through API and CLI, "
              "unchanged source, saved preview restored, unsafe overrides rejected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
