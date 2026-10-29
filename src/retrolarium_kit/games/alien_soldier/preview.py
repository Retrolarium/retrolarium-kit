"""Hear an Alien Soldier song or sound effect without an emulator: the
sequencer plays it from the project's snapshot into a VGM, and the studio's
YMFM renderer turns that into a WAV. A voice or drum sample is written
straight to a WAV, its levels at the Z80's pace."""

from __future__ import annotations

import json
from pathlib import Path
import wave
from typing import Any

from retrolarium_kit.core import source_snapshot
from retrolarium_kit.games.alien_soldier import envelopes, pcm, sound, workspace
from retrolarium_kit.games.alien_soldier.content import CONTENT, SOURCE_DIRECTORY, machine
from retrolarium_kit.games.alien_soldier.sequencer import Sequencer, dac_rate
from retrolarium_kit.platform.megadrive.sound import render_wav, write_vgm


# The European console runs at 50 frames a second, and so does its driver.
FRAME_RATES = {"megadriv": 50}


def _document(snapshot: Path, relative: str) -> dict[str, Any]:
    return json.loads((snapshot / CONTENT / relative).read_text(encoding="utf-8"))


def render_sample(project_root: Path, kind: str, number: int, wav: Path) -> dict[str, Any]:
    """Write sample `number` of `kind` ("voice" for requests $10-$3E,
    "music" for the drums $81 on) to a WAV; what was written."""
    snapshot = Path(project_root) / SOURCE_DIRECTORY
    record = source_snapshot.load_record(snapshot)
    tree = workspace.tree(snapshot, workspace.source_profile(snapshot), record["revision"])
    samples = _document(snapshot, pcm.DOCUMENT)
    item = next((d for d in samples["descriptors"] if (d["kind"], d["id"]) == (kind, number)), None)
    if item is None:
        raise ValueError(f"no {kind} sample ${number:02X}")
    banks = {bank["label"]: bank["samples"] for bank in samples["banks"]}
    deltas = samples["deltas"]
    levels = pcm.decode(bytes.fromhex(banks[item["bank"]][item["sample"]]), deltas["high"], deltas["low"])
    rate = dac_rate(pcm.delays(tree)[(kind, number)])
    wav.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(wav), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(round(rate))
        output.writeframes(b"".join(((level - 0x80) * 256).to_bytes(2, "little", signed=True) for level in levels))
    return {"kind": kind, "id": number, "rate": rate, "levels": len(levels)}


def render(
    project_root: Path,
    label: str,
    vgm: Path,
    wav: Path | None,
    *,
    seconds: float = 30.0,
    enabled: set[int] | None = None,
    sound_document: dict[str, Any] | None = None,
    renderer: Path | None = None,
) -> dict[str, Any]:
    """Render `label` (the project's saved sound document, or
    `sound_document` for unsaved edits); what was rendered."""
    snapshot = Path(project_root) / SOURCE_DIRECTORY
    record = source_snapshot.load_record(snapshot)
    tree = workspace.tree(snapshot, workspace.source_profile(snapshot), record["revision"])
    rom = (snapshot / record["rom"]).read_bytes()
    frame_rate = FRAME_RATES.get(machine(project_root) or "", 60)
    sequencer = Sequencer(
        tree,
        sound_document or _document(snapshot, sound.DOCUMENT),
        _document(snapshot, envelopes.DOCUMENT),
        _document(snapshot, pcm.DOCUMENT),
        frame_rate=frame_rate,
        rom=rom,
    )
    writes, total = sequencer.render(label, seconds=seconds, enabled=enabled)
    write_vgm(vgm, writes, total, frame_rate)
    if wav is not None:
        render_wav(vgm, wav, renderer, cwd=project_root)
    return {"label": label, "frame_rate": frame_rate, "samples": total, "writes": len(writes)}
