"""Boss fights recorded from a tool-assisted run played in Gens.

MAME plays a Gens movie differently once the player moves through a stage,
so a recorded run of the whole game is played where it was made: the
Gens-automation build (a Gens-rerecording fork) writes the emulator's state
each frame of a range (`-dump-state-dir`, `.genstate` files: work RAM, VRAM,
CRAM, VSRAM and the VDP registers, see the game's
docs/gens_state_dump_spec.md). A run's scene list (`<first>-<last>, <scene>`
a line, Gens frames) says where its fights are; the movie is played once,
from the first such scene to the last, and every scene that names a boss,
a the multi-form encounter form, a mini-boss or one of the scrolling bosses is turned
into the capture document capture.py writes as its states come (the
others are deleted as they are written), one per
stage (the stage the game's StageTableIndex holds), each scene a fight
with its name and first frame. Scenes whose first object record never
holds a type a boss's code handles are left out.

Gens keeps work RAM and VRAM as host-endian words: their bytes are swapped
pairwise here. It does not stop itself at a frame without its screenshot
mode, so it is ended once the last frame's state is written.
"""

from __future__ import annotations

import base64
from collections.abc import Callable, Iterable
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import tempfile
import time
from typing import Any

from retrolarium_kit.games.alien_soldier import capture, workspace
from retrolarium_kit.platform.megadrive.asl import SourceTree


SECTIONS = {"ram": 0x01, "vram": 0x10, "cram": 0x11, "vsram": 0x12, "regs": 0x13}
# Scene names that hold a fight.
FIGHT = re.compile(r"boss|force|caterpillar|snake", re.IGNORECASE)
NOT_FIGHT = re.compile(r"credits|cutscene|intro|win\b", re.IGNORECASE)
CONFIG = "[General]\nIntro Style=0\n[Sound]\nState=0\n[Options]\nAuto Fix Checksum=0\n[CPU]\nCountry=-1\n"


def scenes(text: str) -> list[tuple[int, int, str]]:
    """A scene list's fight scenes: first frame, last frame, name."""
    found = []
    for line in text.splitlines():
        match = re.match(r"^\s*(\d+)\s*-\s*(\d+)\s*,\s*(.+?)\s*$", line)
        if match and FIGHT.search(match[3]) and not NOT_FIGHT.search(match[3]):
            found.append((int(match[1]), int(match[2]), match[3]))
    return found


def read_state(path: Path) -> dict[str, bytes]:
    """A .genstate's sections, words in the 68000's order."""
    data = path.read_bytes()
    if data[:8] != b"GENSTATE":
        raise capture.CaptureError(f"{path} is not a Gens state")
    by_id = {}
    at = 64
    while True:
        ident, offset, size, _ = struct.unpack_from("<4I", data, at)
        at += 16
        if ident == 0:
            break
        by_id[ident] = data[offset : offset + size]

    def swapped(raw: bytes) -> bytes:
        out = bytearray(len(raw))
        out[0::2], out[1::2] = raw[1::2], raw[0::2]
        return bytes(out)

    return {
        "ram": swapped(by_id[SECTIONS["ram"]]),
        "vram": swapped(by_id[SECTIONS["vram"]]),
        "cram": by_id[SECTIONS["cram"]],
        "vsram": by_id[SECTIONS["vsram"]],
        "regs": by_id[SECTIONS["regs"]],
    }


def _play(
    gens: Path,
    rom: Path,
    movie: Path,
    first: int,
    last: int,
    out: Path,
    timeout: int,
    take: Callable[[int, Path], None],
) -> None:
    """Gens's states for frames `first`..`last` of the movie, each handed to
    `take` (frame, path) in order once it is whole, then deleted."""
    out.mkdir(parents=True, exist_ok=True)
    config = out / "gens.cfg"
    config.write_text(CONFIG, encoding="ascii")
    command = [
        str(gens),
        "-cfg",
        str(config),
        "-rom",
        str(rom),
        "-play",
        str(movie),
        "-dump-state-dir",
        str(out),
        "-dump-state-interval",
        "1",
        "-dump-state-start",
        str(first),
        "-dump-state-end",
        str(last),
        "-max-frames",
        str(last + 1),
        "-frameskip",
        "0",
        "-turbo",
        "-nosound",
    ]
    process = subprocess.Popen(command, cwd=out, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + timeout
    frame = first
    try:
        while frame <= last:
            if time.monotonic() > deadline:
                raise capture.CaptureError(f"Gens took too long: frame {frame} of {last}")
            path = out / f"{frame}.genstate"
            # A state is whole once the next one is begun (the last, once
            # Gens has moved on a moment).
            whole = (out / f"{frame + 1}.genstate").is_file()
            if frame == last and path.is_file():
                time.sleep(0.5)
                whole = True
            if not whole:
                if process.poll() is not None and not path.is_file():
                    raise capture.CaptureError(f"Gens wrote no state for frame {frame}")
                time.sleep(0.05)
                continue
            take(frame, path)
            os.remove(path)
            frame += 1
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


class _Fight:
    """States as a capture fight (see capture.py), one by one."""

    def __init__(self, types: set[int], pool: tuple[int, int]):
        self.types = types
        self.start, self.end = pool[0] & 0xFFFF, pool[1] & 0xFFFF
        self.frames: list[dict[str, Any]] = []
        self.previous: dict[str, Any] | None = None
        self.kind = 0

    def add(self, state: dict[str, bytes]) -> None:
        frame: dict[str, Any] = {"vram": {}, "objects": []}
        cram = list(struct.unpack("<64H", state["cram"]))
        vsram = list(struct.unpack("<40H", state["vsram"]))
        regs = list(state["regs"])
        previous = self.previous
        if previous is None:
            frame["vram_all"] = base64.b64encode(state["vram"]).decode("ascii")
            frame.update(cram=cram, vsram=vsram, regs=regs)
        else:
            for chunk in range(0x800):
                data = state["vram"][chunk * 32 : chunk * 32 + 32]
                if data != previous["vram"][chunk * 32 : chunk * 32 + 32]:
                    frame["vram"][str(chunk)] = base64.b64encode(data).decode("ascii")
            for key, value in (("cram", cram), ("vsram", vsram), ("regs", regs)):
                if value != previous[key]:
                    frame[key] = value
        ram = state["ram"]
        for slot, record in enumerate(range(self.start, self.end, 0x60)):
            if ram[record : record + 2] != b"\0\0":
                frame["objects"].append(
                    {"slot": slot, "record": base64.b64encode(ram[record : record + 0x60]).decode("ascii")}
                )
        head = int.from_bytes(ram[self.start : self.start + 2], "big")
        if not self.kind and head in self.types:
            self.kind = head
        self.previous = {"vram": state["vram"], "cram": cram, "vsram": vsram, "regs": regs}
        self.frames.append(frame)

    def result(self) -> dict[str, Any]:
        return {"type": self.kind, "frames": self.frames}


def _fight(states: Iterable[dict[str, bytes]], types: set[int], pool: tuple[int, int]) -> dict[str, Any]:
    """States as a capture fight (see capture.py)."""
    fight = _Fight(types, pool)
    for state in states:
        fight.add(state)
    return fight.result()


def capture_tas(
    project_root: Path,
    gens: Path,
    movie: Path,
    scene_list: Path,
    *,
    stage: int | None = None,
    timeout: int = 4 * 3600,
) -> dict[str, Any]:
    """Play the movie's fight scenes in Gens on the project's ROM and write a
    capture document for each stage they fight on (only `stage`'s when
    given); what was written."""
    from retrolarium_kit.core.source_snapshot import load_record
    from retrolarium_kit.games.alien_soldier.content import SOURCE_DIRECTORY
    from retrolarium_kit.games.alien_soldier.levels import handlers

    snapshot = Path(project_root) / SOURCE_DIRECTORY
    record = load_record(snapshot)
    rom = (snapshot / record["rom"]).resolve()
    tree = workspace.tree(snapshot, workspace.source_profile(snapshot), record["revision"])
    pool = capture._pool(tree)
    stage_ram = tree.equates["StageTableIndex"] & 0xFFFF
    # The types a boss's code handles (its asset set's, and the scrolling
    # bosses' that load none).
    types = set(capture.boss_types(tree)) | {
        int(kind[1:], 16)
        for kind, handler in handlers(tree).items()
        if handler in tree.blocks and tree.block(handler).file.path.startswith("src/bosses/")
    }
    by_stage: dict[int, list[dict[str, Any]]] = {}
    fights = scenes(Path(scene_list).read_text(encoding="utf-8"))
    if not fights:
        return {"captures": []}
    # The scene each frame belongs to, and its fight so far.
    owner = {frame: index for index, (first, last, _) in enumerate(fights) for frame in range(first, last + 1)}
    building: dict[int, tuple[int, _Fight]] = {}

    def take(frame: int, path: Path) -> None:
        index = owner.get(frame)
        if index is None:
            return
        first, last, name = fights[index]
        state = read_state(path)
        if index not in building:
            slot = int.from_bytes(state["ram"][stage_ram : stage_ram + 2], "big") // 2
            building[index] = (slot, _Fight(types, pool))
        slot, fight = building[index]
        fight.add(state)
        if frame == last:
            done = fight.result()
            del building[index]
            if done["type"] and (stage is None or slot == stage):
                done.update(scene=name, first_frame=first)
                by_stage.setdefault(slot, []).append(done)

    with tempfile.TemporaryDirectory(prefix="alien-tas-") as work:
        _play(
            Path(gens),
            rom,
            Path(movie).resolve(),
            min(first for first, _, _ in fights),
            max(last for _, last, _ in fights),
            Path(work),
            timeout,
            take,
        )
    written = []
    for slot, fights in sorted(by_stage.items()):
        document = {
            "schema": capture.SCHEMA,
            "schema_version": 1,
            "stage": slot,
            "rom": record["rom"],
            "movie": Path(movie).name,
            "end": "done",
            "fights": fights,
            "mappings": capture._mapping_names(tree, rom.read_bytes(), fights),
        }
        out = capture.path_for(project_root, slot)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(document, separators=(",", ":")) + "\n", encoding="utf-8", newline="\n")
        written.append(
            {
                "path": str(out),
                "stage": slot,
                "fights": [
                    {"scene": fight["scene"], "type": fight["type"], "frames": len(fight["frames"])} for fight in fights
                ],
            }
        )
    return {"captures": written}
