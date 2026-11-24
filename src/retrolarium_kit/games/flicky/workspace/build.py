"""Build a Flicky ROM from the content documents in a source snapshot.

The snapshot's own build scripts assemble the Z80 driver, the edited sound
banks and the ROM (scripts/run.py build.build_z80_driver and
build.build_rom); this module stages the edited documents into a copy of the
source between those steps. The two level source sections come from the
studio's native level writer, and the attract demos, bonus motion and title
layout the kit keeps beside the documents are staged after it.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from retrolarium_kit.core.errors import BUILD_TIMEOUT
from retrolarium_kit.core.self_command import child_environment
from retrolarium_kit.games.flicky.bonus_motion import stage_bonus_motion
from retrolarium_kit.games.flicky.demo import stage_attract_demos
from retrolarium_kit.games.flicky.title_layout import stage_title_layout
from retrolarium_kit.games.flicky.workspace import graphics, semantics, sequences
from retrolarium_kit.games.flicky.workspace.content import (
    ARTIFACTS,
    OUTPUT,
    FlickyWorkspaceError,
    artifact,
    stage_sources,
    validate_workspace,
    workspace_path,
)


def _process(command: list[str], root: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """A bounded child process, its output (stderr with it) read as UTF-8
    whatever it prints."""
    try:
        return subprocess.run(
            command,
            cwd=root,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=BUILD_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        name = " ".join(command[2:4]) if len(command) > 2 else Path(command[0]).name
        raise FlickyWorkspaceError(f"{name} did not finish in {BUILD_TIMEOUT / 60:g} minutes") from None


def _run(command: list[str], root: Path, env: dict[str, str]) -> None:
    result = _process(command, root, env)
    if result.returncode:
        raise FlickyWorkspaceError(
            f"Flicky source command failed ({result.returncode}): "
            + " ".join(command[2:4])
            + "\n"
            + result.stdout[-8000:]
        )


def build_content(root: Path, *, as_bin: Path, p2bin: Path, level_writer: Path, as_args: str = "-maxerrors 2") -> Path:
    """Build the snapshot's documents into build/content/flicky.bin."""
    root = root.resolve()
    validate_workspace(root)
    env = child_environment()
    env["AS_MSGPATH"] = str(as_bin.parent)
    sound = artifact("z80_sound_banks")
    build_dir = root / "build" / "content"
    build_dir.mkdir(parents=True, exist_ok=True)
    driver_output = build_dir / "z80_driver.bin"
    sound_output = build_dir / "z80_sound_data.bin"
    common = ["--as-bin", str(as_bin.resolve()), "--p2bin", str(p2bin.resolve()), "--as-args", as_args]
    _run(
        [
            sys.executable,
            "scripts/run.py",
            "build.build_z80_driver",
            "--source",
            "src/sound/z80/driver.asm",
            "--obj",
            str(build_dir / "z80_driver.p"),
            "--output",
            str(driver_output),
            "--reference",
            "data/sound/data_z80_part1.bin",
            "--expected-size",
            "4070",
            *common,
        ],
        root,
        env,
    )
    _run(
        [
            sys.executable,
            "scripts/run.py",
            "build.build_z80_driver",
            "--source",
            str(workspace_path(root, sound)),
            "--obj",
            str(build_dir / "z80_sound_data.p"),
            "--output",
            str(sound_output),
            "--reference",
            "data/sound/data_z80_part2.bin",
            "--reference-offset",
            "12",
            "--description",
            "editable Z80 sound-data banks",
            "--expected-size",
            str(sound["capacity"]),
            "--split-label",
            "zMusicBank",
            "--allow-different",
            *common,
        ],
        root,
        env,
    )
    staged_main = stage_sources(
        root, build_dir / "src", driver_output.relative_to(root), sound_output.relative_to(root)
    )
    staged = staged_main.parent

    level_path = workspace_path(root, artifact("level_layouts"))
    expected_stage = root / "build/content/src"
    if staged.resolve() != expected_stage.resolve():
        raise FlickyWorkspaceError("Flicky level staging is not the disposable build output")
    written = _process([str(level_writer), str(level_path), str(staged)], root)
    if written.returncode:
        raise FlickyWorkspaceError(
            f"the Flicky level writer failed ({written.returncode}):\n" + (written.stdout or "")[-8000:]
        )
    levels = json.loads(level_path.read_text(encoding="utf-8"))
    stage_attract_demos(levels, root, staged)

    graphics_document = graphics.load_document(workspace_path(root, artifact("graphics_assets")))
    graphics.redirect_includes(staged, graphics.build_assets(graphics_document, root, build_dir))
    semantics_document = semantics.load_document(workspace_path(root, artifact("graphics_semantics")))
    semantics.apply_document(semantics_document, root, staged)
    stage_title_layout(semantics_document, root, staged)
    sequences_document = sequences.load_document(workspace_path(root, artifact("graphics_sequences")), root)
    sequences.apply_document(sequences_document, root, staged)
    stage_bonus_motion(sequences_document, root, staged)

    rom_output = root / OUTPUT
    _run(
        [
            sys.executable,
            "scripts/run.py",
            "build.build_rom",
            "--source",
            str(staged_main),
            "--obj",
            str(build_dir / "main.p"),
            "--output",
            str(rom_output),
            *common,
        ],
        root,
        env,
    )
    return rom_output


__all__ = ["ARTIFACTS", "build_content"]
