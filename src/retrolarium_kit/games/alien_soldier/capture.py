"""A stage's boss fights recorded from MAME as the game draws them.

Every boss, however its code builds it, ends up as what the VDP shows:
the patterns, colours, scroll and sprite table in its memory, and the
object records the game keeps for it. The capture script
(studio/runtime/alien_soldier/capture.lua) starts the stage through the
playtest script, walks the player to the boss and records each frame of
each fight (the next boss or form follows once one is put down); this
module runs it and turns its lines into a document the studio replays,
layer by layer:

    {"schema": "alien-soldier.capture", "schema_version": 1,
     "stage": 1, "rom": "...", "frames_per_fight": 600, "end": "done",
     "fights": [{"type": 340, "frames": [
         {"vram_all": "<base64>", "vram": {"<chunk>": "<base64>"},
          "cram": [...], "vsram": [...], "regs": [...],
          "objects": [{"slot": 0, "record": "<base64>"}]}, ...]}],
     "mappings": {"<address, hex>": "<label>"}}

A fight's first frame holds the whole VRAM (`vram_all`); every other frame
the 32-byte chunks that changed (`vram`), and the colours, vertical scroll
and registers only when they changed. `objects` are the live object
records' first $60 bytes; `mappings` names the static mappings their
mapping pointers (field 8) point at, read from the ROM against the source.

A capture is the game's, not the source's: the studio keeps it beside the
project (`captures/stage-NN.json`), and making it again replaces it.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any

from retrolarium_kit.games.alien_soldier import mappings, playtest, pose_code, workspace
from retrolarium_kit.platform.megadrive.asl import AslSourceError, SourceTree


SCHEMA = "alien-soldier.capture"
FOLDER = "captures"
SCRIPT = Path(__file__).resolve().parents[2] / "studio" / "runtime" / "alien_soldier" / "capture.lua"
POOL = "Entity_ObjectPool"
# The routine that walks the object records ends at the address it compares
# with (`cmpa.w #$DCA0,a5`).
POOL_WALKER = "Sys_ProcessVisibleObjects"
RAM = ("BossHealth", "GameModeIndex", "StageTableIndex")
MAPPING_FIELD = 8


class CaptureError(ValueError):
    """A capture that cannot be made or read."""


def path_for(project_root: Path, stage: int) -> Path:
    return Path(project_root) / FOLDER / f"stage-{stage + 1:02d}.json"


def _pool(tree: SourceTree) -> tuple[int, int]:
    """The object records' first address and the address past the last."""
    start = tree.equates[POOL] & 0xFFFFFF
    block = tree.block(POOL_WALKER)
    for line in pose_code.routine_lines(block.file.lines[block.line :]):
        if match := re.match(r"^\s*cmpa\.w\s+#\$([0-9A-Fa-f]+),a5\s*$", line):
            return start, 0xFF0000 | int(match[1], 16)
    raise CaptureError(f"{POOL_WALKER} names no end of the object records")


def environment(snapshot: Path, revision: str, stage: int, frames: int, fights: int) -> dict[str, str]:
    """The capture script's settings (the playtest script's among them)."""
    tree = workspace.tree(snapshot, workspace.source_profile(snapshot), revision)
    start, end = _pool(tree)
    return {
        **playtest.environment(snapshot, revision, stage),
        **playtest.ram_environment(tree, RAM),
        "ALIEN_PLAYTEST_EXIT": "0",
        "ALIEN_CAPTURE_FRAMES": str(frames),
        "ALIEN_CAPTURE_FIGHTS": str(fights),
        "ALIEN_CAPTURE_POOL": f"{start:06X}",
        "ALIEN_CAPTURE_POOL_END": f"{end:06X}",
        "ALIEN_CAPTURE_BOSSES": ",".join(f"{kind:X}" for kind in boss_types(tree)),
    }


def boss_types(tree: SourceTree) -> list[int]:
    """The entity types the bosses' asset sets install in the first object
    record (Boss_LoadAssetSet), the lettered ones too."""
    from retrolarium_kit.games.alien_soldier.objects import boss_contexts

    return sorted({item["type"] for item in boss_contexts(tree, artless=True).values()})


def _encode(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def parse(text: str) -> tuple[list[dict[str, Any]], str]:
    """The script's lines as fights of frames, and how it ended."""
    fights: list[dict[str, Any]] = []
    frame: dict[str, Any] | None = None
    end = "cut short"
    for line in text.splitlines():
        kind, _, rest = line.partition(" ")
        if kind == "B":
            fights.append({"type": int(rest, 16), "frames": []})
            frame = None
        elif kind == "F":
            if not fights:
                raise CaptureError("a frame before any fight")
            frame = {"vram": {}, "objects": []}
            fights[-1]["frames"].append(frame)
        elif kind == "E":
            end = rest.strip()
        elif frame is None:
            raise CaptureError(f"{kind} outside a frame")
        elif kind == "V":
            chunk, data = rest.split(" ")
            frame["vram"][str(int(chunk, 16))] = bytes.fromhex(data)
        elif kind in ("C", "S"):
            frame["cram" if kind == "C" else "vsram"] = [int(rest[at : at + 4], 16) for at in range(0, len(rest), 4)]
        elif kind == "R":
            frame["regs"] = list(bytes.fromhex(rest))
        elif kind == "O":
            slot, data = rest.split(" ")
            frame["objects"].append({"slot": int(slot), "record": bytes.fromhex(data)})
    for fight in fights:
        for index, item in enumerate(fight["frames"]):
            if index == 0:
                whole = b"".join(item["vram"].get(str(chunk), bytes(32)) for chunk in range(0x800))
                item["vram_all"] = _encode(whole)
                item["vram"] = {}
            else:
                item["vram"] = {chunk: _encode(data) for chunk, data in item["vram"].items()}
            for record in item["objects"]:
                record["record"] = _encode(record["record"])
    return fights, end


def _mapping_names(tree: SourceTree, rom: bytes, fights: list[dict[str, Any]]) -> dict[str, str]:
    """The static mappings the records' mapping pointers point at: the
    source's blocks whose bytes the ROM holds there."""
    pointers = set()
    for fight in fights:
        for frame in fight["frames"]:
            for item in frame["objects"]:
                record = base64.b64decode(item["record"])
                pointer = int.from_bytes(record[MAPPING_FIELD : MAPPING_FIELD + 4], "big")
                if 0 < pointer < len(rom) - 6 and not pointer & 1:
                    pointers.add(pointer)
    if not pointers:
        return {}
    by_start: dict[bytes, list[tuple[str, bytes]]] = {}
    for label, (kind, _) in mappings.blocks(tree).items():
        if kind != "static":
            continue
        try:
            data = tree.data(label)
        except AslSourceError:
            continue
        if len(data) >= 6:
            by_start.setdefault(data[:6], []).append((label, data))
    found = {}
    for pointer in sorted(pointers):
        for label, data in by_start.get(rom[pointer : pointer + 6], []):
            if rom[pointer : pointer + len(data)] == data:
                found[f"{pointer:06X}"] = label
                break
    return found


def capture(
    project_root: Path, mame: Path, stage: int, *, frames: int = 600, fights: int = 8, timeout: int = 1800
) -> dict[str, Any]:
    """Record stage slot `stage` (0-based) of the project's ROM (the one the
    snapshot was made from: the game as it is) and write the document; its
    path and a summary."""
    from retrolarium_kit.core.source_snapshot import load_record
    from retrolarium_kit.games.alien_soldier import content
    from retrolarium_kit.games.alien_soldier.content import SOURCE_DIRECTORY

    snapshot = Path(project_root) / SOURCE_DIRECTORY
    record = load_record(snapshot)
    rom_path = snapshot / record["rom"]
    if not rom_path.is_file():
        raise CaptureError(f"the project's ROM is missing: {rom_path}")
    revision = record["revision"]
    machine = content.machine(project_root) or "megadriv"
    try:
        settings = environment(snapshot, revision, stage, frames, fights)
    except playtest.PlaytestError as exc:
        raise CaptureError(str(exc)) from exc
    with tempfile.TemporaryDirectory(prefix="alien-capture-") as work:
        work_path = Path(work)
        lines = work_path / "capture.txt"
        env = dict(os.environ, **settings, ALIEN_CAPTURE_OUT=str(lines))
        command = [
            str(mame),
            machine,
            "-cart",
            str(rom_path.resolve()),
            "-video",
            "none",
            "-sound",
            "none",
            "-nothrottle",
            "-skip_gameinfo",
            "-autoboot_delay",
            "0",
            "-autoboot_script",
            str(SCRIPT),
            "-cfg_directory",
            str(work_path / "cfg"),
            "-nvram_directory",
            str(work_path / "nvram"),
        ]
        try:
            result = subprocess.run(
                command, cwd=Path(mame).parent, env=env, timeout=timeout, capture_output=True, text=True
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CaptureError(f"MAME did not finish the capture: {exc}") from exc
        text = lines.read_text(encoding="utf-8") if lines.is_file() else ""
        if not text:
            raise CaptureError(f"MAME wrote no capture: {(result.stderr or '').strip()[-400:]}")
    found, end = parse(text)
    tree = workspace.tree(snapshot, workspace.source_profile(snapshot), revision)
    document = {
        "schema": SCHEMA,
        "schema_version": 1,
        "stage": stage,
        "rom": record["rom"],
        "frames_per_fight": frames,
        "end": end,
        "fights": found,
        "mappings": _mapping_names(tree, rom_path.read_bytes(), found),
    }
    out = path_for(project_root, stage)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(document, separators=(",", ":")) + "\n", encoding="utf-8", newline="\n")
    return {
        "path": str(out),
        "stage": stage,
        "end": end,
        "fights": [{"type": fight["type"], "frames": len(fight["frames"])} for fight in found],
    }
