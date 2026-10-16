"""Input movies: FCEUX .fm2 and Gens .gmv recordings, one neutral format, and
runs in MAME that snapshot chosen RAM cells.

A movie is a list of frames; each frame holds the pressed buttons of pads one
and two, as MAME's controller masks, and an optional command (soft reset or
power cycle) that comes before that frame's input. The neutral file
(``.rlmovie``) is text: a header, then runs of identical frames,

    # retrolarium movie 1
    platform nes
    source fm2 <sha1 of the recording>
    frames 103652
    offset 0
    ---
    <count> <pad one hex> <pad two hex> [reset|power]

Frames are numbered as the recording emulator counts them: frame n is the
state after n emulated frames. A run in MAME (mame_movie.lua) feeds the frames
to the emulated pads, reads the requested RAM cells after the frames a check
names, and exits after the last. MAME runs a frame before the script starts
and takes a pad's input a frame later than FCEUX does; ALIGNMENT says how many
leading frames MAME skips and how its frame count trails the recording's, as
measured against FCEUX playing the same movie (equal RAM at every frame
checked). A movie's ``offset`` adds to the skip.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

# MAME's controller masks: NES joypad (:ctrlN:joypad:JOYPAD) and the Mega
# Drive 3-button pad (:ctrlN:mdpad:PAD).
NES_MASKS = {"A": 0x01, "B": 0x02, "S": 0x04, "T": 0x08, "U": 0x10, "D": 0x20, "L": 0x40, "R": 0x80}
MD_MASKS = {"U": 0x01, "D": 0x02, "L": 0x04, "R": 0x08, "B": 0x10, "C": 0x20, "A": 0x40, "S": 0x80}
PLATFORMS = ("nes", "megadrive")
# (leading frames MAME skips, frames MAME's count trails the recording's)
ALIGNMENT = {"nes": (2, 1), "megadrive": (2, 1)}
COMMANDS = ("reset", "power")
LUA = Path(__file__).with_name("mame_movie.lua")


class MovieError(ValueError):
    pass


@dataclass(frozen=True)
class Frame:
    pad1: int = 0
    pad2: int = 0
    command: str = ""


@dataclass
class Movie:
    platform: str
    frames: list[Frame]
    source: str = ""
    offset: int = 0
    notes: dict[str, str] = field(default_factory=dict)

    def runs(self) -> list[tuple[int, Frame]]:
        result: list[tuple[int, Frame]] = []
        for frame in self.frames:
            if result and result[-1][1] == frame and not frame.command:
                result[-1] = (result[-1][0] + 1, frame)
            else:
                result.append((1, frame))
        return result


def _sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def read_fm2(path: Path) -> Movie:
    """An FCEUX movie: one ``|commands|port0|port1|port2|`` line a frame,
    gamepads as ``RLDUTSBA`` with ``.`` or a space for a released button."""
    data = path.read_bytes()
    frames = []
    notes = {}
    order = "RLDUTSBA"
    for number, line in enumerate(data.decode("utf-8", "replace").splitlines(), 1):
        if not line.startswith("|"):
            key, _, value = line.partition(" ")
            if key:
                notes[key] = value
            continue
        fields = line.split("|")
        if len(fields) < 4:
            raise MovieError(f"{path}:{number}: not an fm2 frame")
        commands = int(fields[1] or "0")
        if commands & ~0x03:
            raise MovieError(f"{path}:{number}: FDS or VS commands are not supported")
        pads = []
        for text in fields[2:4]:
            mask = 0
            for index, char in enumerate(text[:8].ljust(8, ".")):
                if char not in ". ":
                    mask |= NES_MASKS[order[index]]
            pads.append(mask)
        command = "power" if commands & 0x02 else "reset" if commands & 0x01 else ""
        frames.append(Frame(pads[0], pads[1], command))
    if notes.get("palFlag", "0") != "0":
        raise MovieError(f"{path}: PAL movies are not supported")
    if notes.get("fourscore", "0") != "0":
        raise MovieError(f"{path}: Four Score movies are not supported")
    return Movie("nes", frames, f"fm2 {_sha1(data)}", notes=notes)


# Gens .gmv: a 64-byte header, then three bytes a frame (pad one, pad two,
# the extra 6-button keys); a cleared bit is a pressed button, bits 0-7 being
# Up, Down, Left, Right, A, B, C, Start.
_GMV_BITS = ("U", "D", "L", "R", "A", "B", "C", "S")


def read_gmv(path: Path) -> Movie:
    data = path.read_bytes()
    if not data.startswith(b"Gens Movie TEST"):
        raise MovieError(f"{path}: not a Gens movie")
    # 0x14 and 0x15: pad one's and pad two's kind, "3" or "6" buttons.
    if data[0x14:0x15] == b"6":
        raise MovieError(f"{path}: a 6-button pad one is not supported")
    body = data[64:]
    if len(body) % 3:
        raise MovieError(f"{path}: the frame data is not a whole number of frames")
    frames = []
    for index in range(0, len(body), 3):
        pads = []
        for value in body[index:index + 2]:
            mask = 0
            for bit, name in enumerate(_GMV_BITS):
                if not value & (1 << bit):
                    mask |= MD_MASKS[name]
            pads.append(mask)
        frames.append(Frame(pads[0], pads[1]))
    description = data[0x18:0x40].split(b"\0", 1)[0].decode("latin-1", "replace")
    return Movie("megadrive", frames, f"gmv {_sha1(data)}", notes={"description": description})


def read_movie(path: Path) -> Movie:
    suffix = path.suffix.lower()
    if suffix == ".fm2":
        return read_fm2(path)
    if suffix == ".gmv":
        return read_gmv(path)
    if suffix == ".rlmovie":
        return read_rlmovie(path)
    raise MovieError(f"{path}: unknown movie format")


def write_rlmovie(movie: Movie, path: Path) -> None:
    lines = ["# retrolarium movie 1", f"platform {movie.platform}"]
    if movie.source:
        lines.append(f"source {movie.source}")
    lines += [f"frames {len(movie.frames)}", f"offset {movie.offset}", "---"]
    for count, frame in movie.runs():
        line = f"{count} {frame.pad1:02x} {frame.pad2:02x}"
        lines.append(f"{line} {frame.command}" if frame.command else line)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def read_rlmovie(path: Path) -> Movie:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "# retrolarium movie 1":
        raise MovieError(f"{path}: not a Retrolarium movie")
    header: dict[str, str] = {}
    position = 1
    while position < len(lines) and lines[position] != "---":
        key, _, value = lines[position].partition(" ")
        header[key] = value
        position += 1
    platform = header.get("platform", "")
    if platform not in PLATFORMS:
        raise MovieError(f"{path}: unknown platform {platform!r}")
    frames: list[Frame] = []
    for number, line in enumerate(lines[position + 1:], position + 2):
        parts = line.split()
        if len(parts) not in (3, 4) or (len(parts) == 4 and parts[3] not in COMMANDS):
            raise MovieError(f"{path}:{number}: not a run of frames")
        count, pad1, pad2 = int(parts[0]), int(parts[1], 16), int(parts[2], 16)
        command = parts[3] if len(parts) == 4 else ""
        frames.append(Frame(pad1, pad2, command))
        frames.extend([Frame(pad1, pad2)] * (count - 1))
    if "frames" in header and int(header["frames"]) != len(frames):
        raise MovieError(f"{path}: {len(frames)} frames, the header says {header['frames']}")
    return Movie(platform, frames, header.get("source", ""), int(header.get("offset", "0")))


@dataclass(frozen=True)
class Cell:
    """A run of RAM bytes. A cell with a tolerance is a number (``endian``
    big or little) that may differ from the reference by that much: on the
    NES a moved routine can cross a page boundary and cost the odd lag
    frame, so a position may trail by a few pixels."""
    name: str
    address: int
    length: int = 1
    space: str = "program"
    tolerance: int = 0
    endian: str = "big"

    def value(self, text: str) -> int:
        return int.from_bytes(bytes.fromhex(text), "little" if self.endian == "little" else "big")


@dataclass(frozen=True)
class Check:
    """What a run reads: named RAM cells after each listed frame (``end`` is
    the movie's last frame)."""
    cells: tuple[Cell, ...]
    frames: tuple[int | str, ...]


def machine_for(platform: str) -> str:
    return {"nes": "nes", "megadrive": "genesis"}[platform]


def _resolve_frames(check: Check, total: int) -> list[int]:
    result = []
    for frame in check.frames:
        number = total if frame == "end" else int(frame)
        if not 1 <= number <= total:
            raise MovieError(f"check frame {frame} is outside the movie (1..{total})")
        result.append(number)
    return sorted(set(result))


def run_in_mame(movie: Movie, rom: Path, check: Check, *, mame: Path, machine: str | None = None,
                timeout: float = 3600.0, watch: bool = False) -> dict[str, dict[str, str]]:
    """Play the movie on rom in MAME (no window, no sound, unthrottled) and
    return {frame label: {cell: hex bytes}}; a frame label is the number, or
    ``end`` for the movie's last frame. ``watch`` shows it instead: a window
    with sound at the game's speed, left open at the end until it is closed."""
    total = len(movie.frames)
    frames = _resolve_frames(check, total)
    skip, trail = ALIGNMENT[movie.platform]
    skip += movie.offset
    played = movie.frames[skip:] if skip >= 0 else [Frame()] * -skip + movie.frames
    last = frames[-1] - trail
    played = played[:last] + [Frame()] * max(0, last - len(played))
    with tempfile.TemporaryDirectory(prefix="rlmovie-") as directory:
        folder = Path(directory)
        inputs = folder / "input.txt"
        inputs.write_text(
            "".join(f"{count} {frame.pad1} {frame.pad2} {COMMANDS.index(frame.command) + 1 if frame.command else 0}\n"
                    for count, frame in Movie(movie.platform, played).runs()), encoding="utf-8", newline="\n")
        reads = folder / "reads.txt"
        reads.write_text("".join(f"{frame - trail} {cell.space} {cell.address} {cell.length} {cell.name}\n"
                                 for frame in frames for cell in check.cells),
                         encoding="utf-8", newline="\n")
        output = folder / "out.txt"
        environment = dict(os.environ, RL_MOVIE_INPUT=str(inputs), RL_MOVIE_READS=str(reads),
                           RL_MOVIE_OUTPUT=str(output), RL_MOVIE_OFFSET="0", RL_MOVIE_FRAMES=str(len(played)),
                           RL_MOVIE_STAY="1" if watch else "0")
        headless = [] if watch else ["-video", "none", "-sound", "none", "-nothrottle"]
        command = [str(mame), machine or machine_for(movie.platform), "-cart", str(rom), *headless,
                   "-skip_gameinfo", "-nomouse", "-window", "-autoboot_delay", "0",
                   "-autoboot_script", str(LUA)]
        result = subprocess.run(command, cwd=mame.parent, env=environment, capture_output=True, text=True,
                                errors="replace", timeout=None if watch else timeout)
        error = Path(str(output) + ".error")
        if error.is_file():
            raise MovieError(f"the MAME movie script failed: {error.read_text(encoding='utf-8').strip()}")
        if not output.is_file():
            raise MovieError(f"MAME stopped before the movie ended (exit {result.returncode}): "
                             f"{(result.stdout + result.stderr).strip()[-600:]}")
        snapshot: dict[str, dict[str, str]] = {}
        for line in output.read_text(encoding="utf-8").splitlines():
            frame, name, value = line.split(" ", 2)
            number = int(frame) + trail
            label = "end" if number == total and "end" in check.frames else str(number)
            snapshot.setdefault(label, {})[name] = value
        return snapshot


def load_check(path: Path) -> tuple[dict, Check]:
    """A game's movie check (``retrolarium.movie-check`` v1): the movie, the
    RAM cells, the frames to read them after, and the committed expected
    values."""
    spec = json.loads(path.read_text(encoding="utf-8"))
    if spec.get("schema") != "retrolarium.movie-check" or spec.get("schema_version") != 1:
        raise MovieError(f"{path}: not a retrolarium.movie-check v1 file")
    cells = tuple(Cell(name, int(str(item["address"]), 0), int(item.get("length", 1)), item.get("space", "program"),
                       int(item.get("tolerance", 0)), item.get("endian", "big"))
                  for name, item in spec["cells"].items())
    return spec, Check(cells, tuple(spec["frames"]))


def compare(expected: dict[str, dict[str, str]], actual: dict[str, dict[str, str]],
            cells: tuple[Cell, ...] = ()) -> list[str]:
    """The differences between two snapshots, one line each; a cell with a
    tolerance only counts when it is further off than that."""
    by_name = {cell.name: cell for cell in cells}
    problems = []
    for label, values in expected.items():
        for name, value in values.items():
            got = actual.get(label, {}).get(name)
            if got == value:
                continue
            cell = by_name.get(name)
            if (got is not None and cell is not None and cell.tolerance
                    and abs(cell.value(got) - cell.value(value)) <= cell.tolerance):
                continue
            problems.append(f"frame {label}: {name} is {got}, expected {value}")
    return problems
