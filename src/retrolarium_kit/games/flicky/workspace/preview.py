"""Render a Flicky song or sound effect to VGM and WAV without an emulator:
the sequencer writes the YM2612 and PSG registers to a VGM, and the
studio's YMFM renderer turns it into a WAV."""

from __future__ import annotations

from pathlib import Path

from retrolarium_kit.games.flicky.workspace.sequencer import TempoAwareSoundSequencer
from retrolarium_kit.games.flicky.workspace.sound_source import baseline_path
from retrolarium_kit.platform.megadrive.sound import render_wav, write_vgm


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
    baseline = baseline_path(project)
    sequencer = TempoAwareSoundSequencer.from_source(workspace, baseline)
    result = sequencer.render(
        header_id,
        seconds=seconds,
        enabled_channels=enabled_channels,
    )
    write_vgm(vgm_path, result.writes, result.total_samples)
    if wav_path is not None:
        render_wav(vgm_path, wav_path, renderer, cwd=project)
    return vgm_path, wav_path
