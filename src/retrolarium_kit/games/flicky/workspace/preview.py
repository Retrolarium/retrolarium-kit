"""Render a Flicky song or sound effect to VGM and WAV without an emulator:
the sequencer writes the YM2612 and PSG registers to a VGM, and the
studio's YMFM renderer turns it into a WAV."""

from __future__ import annotations

import subprocess
from pathlib import Path

from retrolarium_kit.games.flicky.workspace import sound as sound_studio_model
from retrolarium_kit.games.flicky.workspace.sequencer import TempoAwareSoundSequencer
from retrolarium_kit.games.flicky.workspace.vgm import write_vgm


def render_preview(
    project: Path,
    workspace: Path,
    header_id: str,
    vgm_path: Path,
    wav_path: Path | None,
    *,
    seconds: float = 30.0,
    enabled_channels: set[int] | None = None,
    renderer: Path | None = None,
) -> tuple[Path, Path | None]:
    baseline = project / sound_studio_model.SOURCE
    sequencer = TempoAwareSoundSequencer.from_source(workspace, baseline)
    result = sequencer.render(
        header_id,
        seconds=seconds,
        enabled_channels=enabled_channels,
    )
    write_vgm(result, vgm_path)

    if wav_path is not None:
        if renderer is None or not renderer.is_file():
            raise FileNotFoundError(f"the YMFM renderer is missing: {renderer}")
        wav_path.parent.mkdir(parents=True, exist_ok=True)
        # The renderer reports on stdout; the kit's own output is JSON.
        subprocess.run(
            [str(renderer), str(vgm_path), str(wav_path)],
            cwd=project,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    return vgm_path, wav_path
