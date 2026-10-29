"""The kit's Alien Soldier sequencer against the game's own sound driver.

MAME plays each song and sound effect in the cartridge with the game's
driver (alien_soldier_sound_capture.lua records every chip write it makes);
the kit's sequencer plays the same payload from the source. The 68000's FM
and PSG writes are compared frame by frame, the DAC's by the samples started
and the levels written. MAME is the measure only: nothing it records goes
into the kit or a project.

    python tools/alien_soldier_sound_check.py --mame consoles.exe \\
        --game ../alien_soldier_src --rom "Alien Soldier (J) [!].bin" [--request 81 ...] [--seconds 60]

Exits 1 when a frame differs.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import os
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from retrolarium_kit.games.alien_soldier import envelopes, pcm, sound, workspace  # noqa: E402
from retrolarium_kit.games.alien_soldier.sequencer import Sequencer  # noqa: E402

CAPTURE = Path(__file__).with_name("alien_soldier_sound_capture.lua")
FRAME_RATE = 60
SAMPLE_RATE = 44100
# FM6's pan is written by the 68000, or by the Z80 while a sample plays; the
# DAC registers are the Z80's. They are compared apart.
APART = {(1, 0xB6), (0, 0x2A), (0, 0x2B)}


def capture(mame: Path, rom: Path, plan: list[tuple[int, int]], log: Path) -> None:
    environment = dict(
        os.environ,
        CAPTURE_PLAN=",".join(f"{request:02X}:{updates}" for request, updates in plan),
        CAPTURE_OUT=str(log),
    )
    with tempfile.TemporaryDirectory() as scratch:
        subprocess.run(
            [
                str(mame),
                "megadrij",
                "-cart",
                str(rom),
                "-video",
                "none",
                "-sound",
                "none",
                "-nothrottle",
                "-skip_gameinfo",
                "-autoboot_script",
                str(CAPTURE),
                "-cfg_directory",
                scratch,
                "-nvram_directory",
                scratch,
            ],
            cwd=mame.parent,
            env=environment,
            check=True,
            capture_output=True,
        )


def read_log(path: Path) -> dict[int, dict]:
    runs: dict[int, dict] = {}
    current = None
    for line in path.read_text().splitlines():
        parts = line.split()
        if parts[0] == "S":
            current = int(parts[1], 16)
            runs[current] = {"start": int(parts[2]), "writes": defaultdict(list), "starts": 0, "levels": 0}
        elif parts[0] == "E":
            current = None
        elif current is None:
            continue
        elif parts[0] == "F":
            port, register = int(parts[2]), int(parts[3], 16)
            if (port, register) not in APART:
                runs[current]["writes"][int(parts[1])].append((port, register, int(parts[4], 16)))
        elif parts[0] == "P":
            runs[current]["writes"][int(parts[1])].append(("psg", 0, int(parts[2], 16)))
        elif parts[0] == "Z":
            runs[current]["starts"] += (int(parts[3], 16), int(parts[4], 16)) == (0x2B, 0x80)
        elif parts[0] == "D":
            runs[current]["levels"] += int(parts[2])
    return runs


def kit_run(sequencer: Sequencer, label: str, frames: int) -> dict:
    writes, _ = sequencer.render(label, seconds=frames / FRAME_RATE + 1)
    by_frame, starts, levels = defaultdict(list), 0, 0
    last = frames * SAMPLE_RATE // FRAME_RATE
    for write in writes:
        if write.sample >= last:
            continue
        if write.chip == "sn76489":
            by_frame[write.sample * FRAME_RATE // SAMPLE_RATE].append(("psg", 0, write.value))
        elif (write.port, write.address) == (0, 0x2A):
            levels += 1
        elif (write.port, write.address) == (0, 0x2B):
            starts += write.value == 0x80
        elif (write.port, write.address) not in APART:
            by_frame[write.sample * FRAME_RATE // SAMPLE_RATE].append((write.port, write.address, write.value))
    return {"writes": by_frame, "starts": starts, "levels": levels}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--mame", type=Path, required=True)
    parser.add_argument("--game", type=Path, required=True, help="the alien_soldier_src repository")
    parser.add_argument("--rom", type=Path, required=True, help="the Japanese cartridge")
    parser.add_argument("--request", action="append", help="requests to check (hex), all by default")
    parser.add_argument("--seconds", type=int, default=60, help="how long each song plays (effects 4 s)")
    args = parser.parse_args()

    root = args.game.resolve()
    documents = workspace.export(root, "jp", [sound, envelopes, pcm])
    tree = workspace.tree(root, workspace.source_profile(root), "jp")
    rom = args.rom.resolve().read_bytes()
    sequencer = Sequencer(
        tree,
        documents[sound.DOCUMENT],
        documents[envelopes.DOCUMENT],
        documents[pcm.DOCUMENT],
        frame_rate=FRAME_RATE,
        rom=rom,
    )
    payloads = documents[sound.DOCUMENT]["payloads"]
    wanted = {int(value, 16) for value in args.request} if args.request else None
    plan, labels = [], {}
    for item in payloads:
        request = item["requests"][0]
        if wanted is None or wanted & set(item["requests"]):
            frames = args.seconds * FRAME_RATE if item["kind"] == "bgm" else 4 * FRAME_RATE
            plan.append((request, frames))
            labels[request] = item
    with tempfile.TemporaryDirectory() as scratch:
        log = Path(scratch) / "capture.log"
        capture(args.mame.resolve(), args.rom.resolve(), plan, log)
        runs = read_log(log)

    failed = 0
    for request, frames in plan:
        item, run = labels[request], runs.get(request)
        if run is None:
            print(f"{item['label']}: not recorded")
            failed += 1
            continue
        # A song loads in one update and plays from the next; an effect
        # plays in the update that loads it. The load's own frame is the
        # driver's stop-all against the kit's fresh start: left out.
        offset = run["start"] + (2 if item["kind"] == "bgm" else 1)
        kit = kit_run(sequencer, item["label"], frames)
        span = range(1, frames - 2)
        differ = [f for f in span if run["writes"].get(offset + f, []) != kit["writes"].get(f, [])]
        failed += bool(differ)
        line = f"{item['label']} (${request:02X}): {len(span) - len(differ)}/{len(span)} frames match"
        if differ:
            line += f", first difference at frame {differ[0]}"
        if run["starts"] or kit["starts"]:
            line += (
                f"; samples started {kit['starts']} (game {run['starts']}),"
                f" levels {kit['levels']} (game {run['levels']})"
            )
        print(line)
    print(f"{len(plan) - failed} of {len(plan)} sounds match the game")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
