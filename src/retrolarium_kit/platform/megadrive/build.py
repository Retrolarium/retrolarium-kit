"""Building a Mega Drive game from its AS source with the kit's assembler.

A game's profile describes its build as steps: each assembles one entry
file with the kit's `asl` (from the repository root, so `include` and
`binclude` paths resolve there), converts it with `p2bin` and its fill
byte, and writes the image where a later step includes it (a Z80 sound
program before the 68000 program, say). A revision adds its defines, such as
a region switch.

`rltool profile build` runs the steps the same way for a repository's own
`make build`.

A revision may also name the range its boot code sums for the header
checksum (`checksum`: first and last-plus-one file offsets); an edited image
gets that sum, so it boots. An unedited image already has it.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import shutil
from pathlib import Path, PurePosixPath
import subprocess
import tempfile
from typing import Any, Mapping

from retrolarium_kit.core.build_toolchains import ResolvedToolchain, materialize_build_toolchain


class MegaDriveBuildError(ValueError):
    """A build description or an assembly that fails."""


@dataclass(frozen=True)
class Step:
    entry: str
    output: str
    fill: int
    size: int | None = None

    @classmethod
    def parse(cls, value: Mapping[str, Any]) -> "Step":
        try:
            entry, output = str(value["entry"]), str(value["output"])
            fill = int(str(value.get("fill", "0xFF")), 0)
            size = None if value.get("size") is None else int(str(value["size"]), 0)
        except (KeyError, TypeError, ValueError) as error:
            raise MegaDriveBuildError(f"a build step needs entry, output and a numeric fill: {error}") from None
        for path in (entry, output):
            parts = PurePosixPath(path).parts
            if PurePosixPath(path).is_absolute() or ".." in parts or "\\" in path:
                raise MegaDriveBuildError(f"a build step path must stay inside the repository: {path}")
        return cls(entry, output, fill & 0xFF, size)


def toolchain() -> ResolvedToolchain:
    return materialize_build_toolchain("megadrive")


def assemble(root: Path, step: Step, defines: Mapping[str, int], tools: ResolvedToolchain | None = None) -> Path:
    """One step: the image it writes, replaced only when the step succeeds."""
    tools = tools or toolchain()
    asl, p2bin = tools.command("asl"), tools.command("p2bin")
    environment = tools.process_environment()
    environment["AS_MSGPATH"] = str(Path(asl).parent)
    output = root / step.output
    output.parent.mkdir(parents=True, exist_ok=True)
    arguments = [part for name, value in defines.items() for part in ("-D", f"{name}={value}")]
    with tempfile.TemporaryDirectory(prefix=".assemble-", dir=output.parent) as directory:
        obj, image = Path(directory) / "image.p", Path(directory) / "image.bin"
        result = subprocess.run(
            [str(asl), "-i", ".", "-q", "-maxerrors", "2", "-o", str(obj), *arguments, step.entry],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
        )
        if result.returncode or not obj.is_file():
            raise MegaDriveBuildError(f"{step.entry} does not assemble:\n{(result.stdout + result.stderr)[-3000:]}")
        result = subprocess.run(
            [str(p2bin), str(obj), str(image), f"-p={step.fill:02X}"],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
        )
        if result.returncode or not image.is_file():
            raise MegaDriveBuildError(f"{step.entry} does not convert:\n{(result.stdout + result.stderr)[-3000:]}")
        if step.size is not None and image.stat().st_size != step.size:
            raise MegaDriveBuildError(f"{step.entry} built {image.stat().st_size} bytes, not {step.size}")
        os.replace(image, output)
    return output


def build(root: Path, steps: list[Step], defines: Mapping[str, int]) -> Path:
    """Every step in order; the last one's image is the game."""
    if not steps:
        raise MegaDriveBuildError("the build has no steps")
    tools = toolchain()
    for step in steps:
        image = assemble(root, step, defines, tools)
    return image


PROFILE = Path("config/game_profile.json")


def build_revision(root: Path, revision: str, output: Path | None = None, entry: str | None = None) -> Path:
    """Build a revision as the repository's game profile describes it (its
    `source` section: the steps, and the revision's defines); the image, also
    copied to `output` when given. `entry` assembles another copy of the
    68000 source (a moved one, say) in the last step's place, into `output`
    only."""
    try:
        section = json.loads((Path(root) / PROFILE).read_text(encoding="utf-8"))["source"]
        settings = section["revisions"][revision]
        steps = [Step.parse(step) for step in section["steps"]]
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise MegaDriveBuildError(f"{PROFILE.as_posix()} does not describe a build of {revision!r}: {error}") from None
    if entry is not None:
        if output is None:
            raise MegaDriveBuildError("a build of another entry needs an output")
        target = Path(output).resolve()
        try:
            relative = target.relative_to(Path(root).resolve()).as_posix()
        except ValueError:
            raise MegaDriveBuildError(f"the output must stay inside the repository: {output}") from None
        last = steps[-1]
        steps[-1] = Step.parse({"entry": entry, "output": relative, "fill": hex(last.fill), "size": last.size})
        return build(Path(root), steps, settings.get("defines", {}))
    image = build(Path(root), steps, settings.get("defines", {}))
    if output is not None and Path(output).resolve() != image.resolve():
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(image, output)
        return Path(output)
    return image


def boot_checksum(data: bytes, first: int, end: int) -> int:
    """The big-endian word sum the boot code compares with the header."""
    total = 0
    for offset in range(first, min(end, len(data)), 2):
        total += int.from_bytes(data[offset : offset + 2].ljust(2, b"\0"), "big")
    return total & 0xFFFF


def stamp(data: bytes, first: int, end: int) -> bytes:
    """`data` with the header checksum (at $18E) its boot code expects."""
    stamped = bytearray(data)
    stamped[0x18E:0x190] = boot_checksum(data, first, end).to_bytes(2, "big")
    return bytes(stamped)
