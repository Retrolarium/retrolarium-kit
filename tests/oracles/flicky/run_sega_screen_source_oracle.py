"""Verify canonical Flicky SEGA-screen GUI edits and ROM capacity handling.

Uses the neighboring source and ROM read-only; generated files stay in an
ignored temporary directory under this repository's build directory.
"""

from __future__ import annotations

import argparse
import importlib
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
from retrolarium_kit.studio.project import StudioProjectError, build_studio_project
from retrolarium_kit.studio.create import create_flicky_studio_project


def asset(document: dict, identifier: str) -> dict:
    return next(item for item in document["assets"] if item["id"] == identifier)


def capture_sega_screen(mame: Path, rom: Path, prefix: Path) -> Path:
    probe = Path(__file__).with_name("sega_screen_probe.lua")
    environment = os.environ.copy()
    environment["FLICKY_SEGA_SCREEN_PREFIX"] = str(prefix.resolve())
    result = subprocess.run(
        [
            str(mame.resolve()),
            "genesis",
            "-cart",
            str(rom.resolve()),
            "-skip_gameinfo",
            "-video",
            "none",
            "-sound",
            "none",
            "-nothrottle",
            "-noreadconfig",
            "-nowriteconfig",
            "-seconds_to_run",
            "30",
            "-autoboot_delay",
            "0",
            "-autoboot_script",
            str(probe.resolve()),
        ],
        cwd=prefix.parent,
        env=environment,
        text=True,
        capture_output=True,
        timeout=90,
    )
    frame = prefix.with_name(prefix.name + "-180.png")
    assert result.returncode == 0 and frame.is_file(), f"MAME SEGA screen failed: {result.stdout}\n{result.stderr}"
    return frame


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--rom", required=True, type=Path)
    parser.add_argument("--native-writer", required=True, type=Path)
    parser.add_argument("--native-graphics-editor", required=True, type=Path)
    parser.add_argument("--mame", type=Path, help="compare canonical and GUI-edited SEGA logo at boot")
    args = parser.parse_args()
    os.environ["RETROLARIUM_FLICKY_LEVEL_WRITER"] = str(args.native_writer.resolve())

    source_scripts = args.source_root.resolve() / "scripts"
    sys.path.insert(0, str(source_scripts))
    formats = importlib.import_module("authoring.data_formats")
    enigma = importlib.import_module("formats.enigma_dec")
    encoder = importlib.import_module("formats.enigma_enc")

    scratch = Path(__file__).resolve().parents[3] / "build"
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="flicky-sega-screen-gate-", dir=scratch) as name:
        project = Path(name)
        import_flicky_source(args.source_root, args.rom, project)
        create_flicky_studio_project(project)
        project_file = project / "flicky-ue.rlj"
        zero = build_studio_project(project_file)
        assert zero["artifact"]["sha256"] == CANONICAL_SHA256
        built_path = project / zero["artifact"]["path"]
        original_rom = args.rom.read_bytes()
        assert built_path.read_bytes() == original_rom
        baseline_screen = None
        if args.mame:
            baseline_screen = capture_sega_screen(args.mame, built_path, project / "sega-baseline")

        graphics_path = project / SOURCE_DIRECTORY / WORKSPACE_FILES["graphics_assets"]
        original_graphics = graphics_path.read_bytes()
        original_document = json.loads(original_graphics)
        original_palette = asset(original_document, "sega_palette")["entries"]
        original_words = asset(original_document, "sega_tilemap")["words"]
        assert original_words == list(range(48)), "canonical screen layout changed"

        screen_masks = {}
        for label, option, palette_changed, map_changed in (
            ("palette", "--edit-sega-palette", True, False),
            ("tilemap", "--edit-sega-tilemap", False, True),
            ("combined", "--edit-sega", True, True),
        ):
            graphics_path.write_bytes(original_graphics)
            subprocess.run([str(args.native_graphics_editor.resolve()), option, str(graphics_path)], check=True)
            edited_document = json.loads(graphics_path.read_text(encoding="utf-8"))
            entries = asset(edited_document, "sega_palette")["entries"]
            words = asset(edited_document, "sega_tilemap")["words"]
            assert entries[0]["colour"] == original_palette[0]["colour"] - (2 if palette_changed else 0)
            assert entries[1:] == original_palette[1:]
            assert words == (list(range(1, 49)) if map_changed else original_words)

            expected_palette = formats.encode_palette_compact(entries)
            expected_map = (
                encoder.encode(b"".join(word.to_bytes(2, "big") for word in words))
                if map_changed
                else original_rom[0x510:0x51A]
            )
            assert len(expected_palette) == 20 and len(expected_map) == 10
            edited = build_studio_project(project_file)
            edited_rom = (project / edited["artifact"]["path"]).read_bytes()
            assert edited_rom[0x4F6:0x50A] == expected_palette
            assert edited_rom[0x510:0x51A] == expected_map
            assert formats.decode_palette_compact(edited_rom[0x4F6:0x50A]) == entries
            decoded_map = enigma.decompress(edited_rom[0x510:0x51A])
            assert [
                int.from_bytes(decoded_map[index : index + 2], "big") for index in range(0, len(decoded_map), 2)
            ] == words
            assert edited_rom[0x18E:0x190] == cartridge_checksum(edited_rom).to_bytes(2, "big")
            changes = {index for index, pair in enumerate(zip(original_rom, edited_rom)) if pair[0] != pair[1]}
            allowed = (
                {0x18E, 0x18F}
                | (set(range(0x4F6, 0x50A)) if palette_changed else set())
                | (set(range(0x510, 0x51A)) if map_changed else set())
            )
            assert changes and changes <= allowed, (label, sorted(changes)[:20])
            assert any(0x4F6 <= offset < 0x50A for offset in changes) == palette_changed
            assert any(0x510 <= offset < 0x51A for offset in changes) == map_changed
            if args.mame:
                from PIL import Image

                edited_screen = capture_sega_screen(args.mame, built_path, project / f"sega-{label}")
                with Image.open(baseline_screen) as base_image, Image.open(edited_screen) as edited_image:
                    base = base_image.convert("RGB")
                    changed = edited_image.convert("RGB")
                    assert base.size == changed.size
                    mask = {
                        (x, y)
                        for y in range(80, 160)
                        for x in range(64, 224)
                        if base.getpixel((x, y)) != changed.getpixel((x, y))
                    }
                    assert mask, f"GUI SEGA-screen {label} edit had no visible MAME effect"
                    screen_masks[label] = mask
                    print(f"SEGA screen MAME boot ({label}): {len(mask)} changed logo-region pixels")
            graphics_path.write_bytes(original_graphics)
            restored = build_studio_project(project_file)
            assert restored["artifact"]["sha256"] == CANONICAL_SHA256
            assert built_path.read_bytes() == original_rom

        if args.mame:
            assert screen_masks["palette"] != screen_masks["tilemap"], (
                "palette and Enigma GUI edits produced indistinguishable MAME masks"
            )

        subprocess.run([str(args.native_graphics_editor.resolve()), "--edit-sega-cell", str(graphics_path)], check=True)
        assert (
            len(
                encoder.encode(
                    b"".join(
                        word.to_bytes(2, "big")
                        for word in asset(json.loads(graphics_path.read_text(encoding="utf-8")), "sega_tilemap")[
                            "words"
                        ]
                    )
                )
            )
            > 10
        )
        try:
            build_studio_project(project_file)
        except StudioProjectError as error:
            assert "sega_tilemap" in str(error) and "exceeds 10-byte slot" in str(error), error
        else:
            raise AssertionError("over-capacity GUI tilemap edit unexpectedly built")
        assert built_path.read_bytes() == original_rom, "failed build changed published ROM"

    print(
        "PASS: SEGA-screen Qt palette/map edits, exact ROM slots, restore, capacity guard"
        + (", and MAME boot" if args.mame else "")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
