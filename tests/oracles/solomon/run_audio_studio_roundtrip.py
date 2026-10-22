"""Opt-in Solomon sound/presentation plus levels/graphics Studio build gate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from retrolarium_kit.games.catalog import extract_game, inspect_game
from retrolarium_kit.studio.project import (
    build_studio_project,
    validate_studio_project,
    verify_studio_project,
)
from retrolarium_kit.studio.create import create_studio_project


def check(rom: Path, studio: Path, revision: str, app: Path | None) -> None:
    inspection = inspect_game(rom)
    if (inspection["game"], inspection["revision"]) != ("solomons-key", revision):
        raise AssertionError(f"{revision}: unexpected source image")
    with tempfile.TemporaryDirectory(prefix=f"retrolarium-solomon-audio-studio-{revision}-") as temporary:
        workspace = Path(temporary) / "studio"
        shutil.copytree(studio, workspace)
        audio_path = workspace / "documents" / "audio.json"
        presentation_path = workspace / "documents" / "presentation.json"
        extract_game(rom, audio_path, resource="audio")
        extract_game(rom, presentation_path, resource="presentation")
        documents = [
            workspace / "documents" / name
            for name in (
                "levels.json",
                "room-art.json",
                "sprite-frames.json",
                "audio.json",
                "presentation.json",
                "graphics.json",
            )
        ]
        project = workspace / f"solomons-key-{revision}-audio.rlj"
        created = create_studio_project(
            workspace / "manifest.json",
            project,
            project_id=f"solomons-key-{revision}-audio",
            name=f"Solomon's Key {revision} with audio",
            profile="solomons-key.v1",
            source_mode="binary-profile",
            resource_documents=documents,
        )
        tabs = created["workspace"]["tabs"]
        if not {"audio", "screens", "demo"}.issubset({tab["id"] for tab in tabs}):
            raise AssertionError(f"{revision}: Studio presentation tabs missing")
        validate_studio_project(project)
        build_studio_project(project)
        verified = verify_studio_project(project)
        if verified["status"] != "current":
            raise AssertionError(f"{revision}: Studio build is not current")
        original_rom = rom.read_bytes()
        original_output = Path(verified["artifact"]["absolute_path"]).read_bytes()
        if original_output != original_rom:
            raise AssertionError(f"{revision}: zero-edit Studio output differs")
        if app is not None:
            for tab in ("audio", "screens", "demo"):
                smoke = subprocess.run(
                    [str(app), "--studio-smoke", str(project), "--require-tab", tab, "--smoke-test"],
                    capture_output=True,
                    text=True,
                    timeout=90,
                )
                if smoke.returncode:
                    raise AssertionError(f"{revision}: GUI {tab} tab failed: {smoke.stderr}")
        edited = json.loads(audio_path.read_text(encoding="utf-8"))
        note_index, note = next(
            (index, item) for index, item in enumerate(edited["commands"]) if item["kind"] == "note"
        )
        note["value"] ^= 1
        audio_path.write_text(json.dumps(edited, indent=2) + "\n", encoding="utf-8")
        presentation = json.loads(presentation_path.read_text(encoding="utf-8"))
        title_tokens = presentation["streams"][0]["tokens"]
        literal_index, literal = next(
            (index, token) for index, token in enumerate(title_tokens) if token["kind"] == "literal"
        )
        literal["tiles"][0] ^= 1
        presentation["demo_steps"][1]["duration"] ^= 1
        buttons = presentation["demo_steps"][1]["buttons"]
        if "RIGHT" in buttons:
            buttons.remove("RIGHT")
        else:
            buttons.append("RIGHT")
        presentation_path.write_text(json.dumps(presentation, indent=2) + "\n", encoding="utf-8")
        build_studio_project(project)
        verified = verify_studio_project(project)
        if verified["status"] != "current":
            raise AssertionError(f"{revision}: edited Studio build is not current")
        output = Path(verified["artifact"]["absolute_path"]).read_bytes()
        changed = [index for index, (before, after) in enumerate(zip(original_rom, output)) if before != after]
        preceding = edited["commands"][:note_index]
        command_offset = sum(
            3
            if item["kind"] in ("jump", "call")
            else 2
            if item["kind"] in ("set_sequence", "set_control", "begin_loop", "set_sweep", "set_volume")
            else 1
            for item in preceding
        )
        stream_cpu = 0xF592 if revision == "usa" else 0xF53A
        audio_offset = 16 + stream_cpu - 0x8000 + command_offset
        title_cpu = 0xCD5F if revision == "usa" else 0xCCEF
        duration_cpu = 0xCEF1 if revision == "usa" else 0xCE81
        input_cpu = 0xCF13 if revision == "usa" else 0xCEA3
        title_offset = (
            16
            + title_cpu
            - 0x8000
            + sum(len(token["tiles"]) if token["kind"] == "literal" else 1 for token in title_tokens[:literal_index])
        )
        expected = sorted(
            (
                audio_offset,
                title_offset,
                16 + duration_cpu - 0x8000 + 1,
                16 + input_cpu - 0x8000 + 1,
            )
        )
        if changed != expected or output[audio_offset] != note["value"]:
            raise AssertionError(f"{revision}: Studio audio edit changed {changed}, expected {expected}")
    print(f"Solomon {revision}: audio/presentation/levels/graphics Studio current")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--usa", type=Path, required=True)
    parser.add_argument("--europe", type=Path, required=True)
    parser.add_argument("--usa-studio", type=Path, required=True)
    parser.add_argument("--europe-studio", type=Path, required=True)
    parser.add_argument("--app", type=Path)
    args = parser.parse_args()
    app = args.app.resolve(strict=True) if args.app else None
    check(args.usa.resolve(strict=True), args.usa_studio.resolve(strict=True), "usa", app)
    check(args.europe.resolve(strict=True), args.europe_studio.resolve(strict=True), "europe", app)


if __name__ == "__main__":
    main()
