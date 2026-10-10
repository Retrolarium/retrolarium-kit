"""Manifest-driven build and verification operations for Retrolarium projects."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any

from retrolarium_kit.core.build_toolchains import ResolvedToolchain, materialize_build_toolchain
from retrolarium_core.contracts import artifact_path, load_disassembly_manifest
from retrolarium_core.fds import FdsError, parse_fds_side
from retrolarium_core.ines import InesError, load_ines, parse_ines_prefix
from retrolarium_core.megadrive import load_megadrive
from retrolarium_core.snes import load_snes


class ProjectError(RuntimeError):
    """A declared project operation could not be completed."""


# Vs. System boards MAME runs, by machine name: the ROM chips the arcade
# driver loads, as file names in the order PRG and CHR fill them (8 KiB each),
# and the palette ROM of the board's RGB PPU.
VS_MACHINES: dict[str, dict[str, Any]] = {
    "suprmrio": {
        "name": "Vs. Super Mario Bros. (set SM4-4 E)",
        "prg": ("mds-sm4-4__1dor6d_e.1d or 6d", "mds-sm4-4__1cor6c_e.1c or 6c",
                "mds-sm4-4__1bor6b_e.1b or 6b", "mds-sm4-4__1aor6a_e.1a or 6a"),
        "chr": ("mds-sm4-4__2bor8b_e.2b or 8b", "mds-sm4-4__2aor8a_e.2a or 8a"),
        "palette": "rp2c04-0004",
        "revision": ("smb1", "vs_smb"),
    },
}
VS_CHIP_SIZE = 0x2000


def vs_machine(game: str, revision: str) -> str | None:
    """The MAME Vs. System machine a game revision runs on, if it is one."""
    return next((name for name, board in VS_MACHINES.items()
                 if board["revision"] == (game, revision)), None)
# The RGB PPUs' palette ROMs: 64 colours of three 3-bit DAC levels (red,
# green, blue), as the NESdev wiki's "PPU palettes" page tabulates them; MAME
# loads each as a 192-byte file, one level per byte. The kit builds the file
# from the table (SHA-1 below, the checksum MAME expects).
VS_PALETTES: dict[str, tuple[str, str]] = {
    "rp2c04-0004": (
        "430,326,044,660,000,755,014,630,555,310,070,003,764,770,040,572,"
        "737,200,027,747,000,222,510,740,653,053,447,140,403,000,473,357,"
        "503,031,420,006,407,507,333,704,022,666,036,020,111,773,444,707,"
        "757,777,320,700,760,276,777,467,000,750,637,567,360,657,077,120",
        "0f9090225eb1f08ae5072d40af3e95547cbce05f",
    ),
}
MAME_VS_START = Path(__file__).with_name("mame_vs_start.lua")

MAME_NES_MACHINES = {"nes", "nespal", "famicom", "fds", *VS_MACHINES}
MAME_MEGADRIVE_MACHINES = {"genesis", "megadriv"}
MAME_SNES_MACHINES = {"snes", "snespal"}
MAME_MACHINES = {
    "nes": MAME_NES_MACHINES,
    "megadrive": MAME_MEGADRIVE_MACHINES,
    "snes": MAME_SNES_MACHINES,
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_document(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def process_text(value: str | bytes | None) -> str:
    """Normalize subprocess output, including TimeoutExpired byte payloads."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def classify_mame_failure(stderr: str) -> tuple[str, dict[str, Any] | None]:
    """Turn MAME's stable missing-ROM diagnostic into structured evidence."""
    if "Required files are missing" not in stderr:
        return "failed", None
    missing = sorted(
        {
            match.group(1)
            for line in stderr.splitlines()
            if (match := re.match(r"^([^\s]+) NOT FOUND(?:\s|$)", line))
        }
    )
    if not missing:
        return "failed", None
    return (
        "missing-dependency",
        {
            "kind": "mame-required-roms",
            "message": "MAME cannot start because required device ROMs are missing",
            "files": missing,
        },
    )


def _operation_argv(operation: dict[str, Any]) -> list[str]:
    argv = operation.get("argv", ["make", "all"])
    if not isinstance(argv, list) or not argv or any(
        not isinstance(value, str) or not value for value in argv
    ):
        raise ProjectError("build operation argv must be a non-empty string array")
    return argv


def _run_build_process(
    command: list[str],
    *,
    project_directory: Path,
    environment: dict[str, str] | None = None,
) -> None:
    result = subprocess.run(
        command,
        cwd=project_directory,
        env=environment,
        text=True,
    )
    if result.returncode:
        raise ProjectError(f"project build failed with exit code {result.returncode}")


def _docker_build(
    operation: dict[str, Any], project_directory: Path, selected_container: str
) -> dict[str, str]:
    if operation.get("z80_prebuild"):
        raise ProjectError("container build cannot assemble declared Z80 sources")
    command = [
        "docker",
        "run",
        "--rm",
        "--mount",
        f"type=bind,source={project_directory},target=/work",
        "--workdir",
        "/work",
        selected_container,
        *_operation_argv(operation),
    ]
    _run_build_process(command, project_directory=project_directory)
    return {"kind": "docker", "container": selected_container}


def _project_relative_path(
    project_directory: Path, value: object, name: str
) -> tuple[Path, str]:
    try:
        path = artifact_path(project_directory, value)
    except (OSError, ValueError) as exc:
        raise ProjectError(f"invalid {name}: {exc}") from exc
    relative = path.relative_to(project_directory).as_posix()
    return path, relative


def _ca65_build(
    operation: dict[str, Any],
    project_directory: Path,
    output: Path,
    suite: ResolvedToolchain,
) -> None:
    source, source_relative = _project_relative_path(
        project_directory, operation.get("source"), "ca65 source"
    )
    linker, linker_relative = _project_relative_path(
        project_directory, operation.get("linker_config"), "linker config"
    )
    object_file, object_relative = _project_relative_path(
        project_directory, operation.get("object"), "object output"
    )
    map_file, map_relative = _project_relative_path(
        project_directory, operation.get("map"), "map output"
    )
    if not source.is_file() or not linker.is_file():
        raise ProjectError("ca65 recipe input is missing")
    object_file.parent.mkdir(parents=True, exist_ok=True)
    map_file.parent.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    object_file.unlink(missing_ok=True)
    output.unlink(missing_ok=True)
    _run_build_process(
        [str(suite.command("ca65")), "-g", "-o", object_relative, source_relative],
        project_directory=project_directory,
        environment=suite.process_environment(),
    )
    _run_build_process(
        [
            str(suite.command("ld65")),
            "-C",
            linker_relative,
            "-m",
            map_relative,
            "-o",
            output.relative_to(project_directory).as_posix(),
            object_relative,
        ],
        project_directory=project_directory,
        environment=suite.process_environment(),
    )


def _asl_build(
    operation: dict[str, Any],
    project_directory: Path,
    output: Path,
    suite: ResolvedToolchain,
) -> None:
    source, source_relative = _project_relative_path(
        project_directory, operation.get("source"), "AS source"
    )
    object_file, object_relative = _project_relative_path(
        project_directory, operation.get("object"), "AS object output"
    )
    if not source.is_file():
        raise ProjectError("AS recipe input is missing")
    z80_prebuild = operation.get("z80_prebuild", [])
    if not isinstance(z80_prebuild, list):
        raise ProjectError("Z80 prebuild must be an array")
    for index, item in enumerate(z80_prebuild):
        if not isinstance(item, dict):
            raise ProjectError(f"Z80 prebuild {index} must be an object")
        z80_source, z80_source_relative = _project_relative_path(
            project_directory, item.get("source"), "Z80 source"
        )
        z80_object, z80_object_relative = _project_relative_path(
            project_directory, item.get("object"), "Z80 object"
        )
        z80_output, z80_output_relative = _project_relative_path(
            project_directory, item.get("output"), "Z80 output"
        )
        if not z80_source.is_file() or z80_source.suffix.lower() != ".asm":
            raise ProjectError(f"Z80 prebuild source is missing or is not .asm: {z80_source}")
        z80_object.parent.mkdir(parents=True, exist_ok=True)
        z80_output.parent.mkdir(parents=True, exist_ok=True)
        z80_listing = z80_object.with_suffix(".lst")
        z80_listing_relative = z80_listing.relative_to(project_directory).as_posix()
        z80_object.unlink(missing_ok=True)
        z80_output.unlink(missing_ok=True)
        z80_listing.unlink(missing_ok=True)
        _run_build_process(
            [str(suite.command("asl")), "-xx", "-n", "-q", "-A", "-L", "-U",
             "-olist", z80_listing_relative,
             "-i", ".", "-o", z80_object_relative, z80_source_relative],
            project_directory=project_directory,
            environment=suite.process_environment(),
        )
        _run_build_process(
            [str(suite.command("p2bin")), z80_object_relative,
             z80_output_relative, "-p=FF"],
            project_directory=project_directory,
            environment=suite.process_environment(),
        )
    if '"' in source_relative or "\n" in source_relative or "\r" in source_relative:
        raise ProjectError("AS source path cannot be represented in an include")
    object_file.parent.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    wrapper = object_file.parent / "retrolarium-main.68k"
    wrapper_relative = wrapper.relative_to(project_directory).as_posix()
    wrapper.write_text(
        "                cpu 68000\n"
        f'                include "{source_relative}"\n'
        "                end\n",
        encoding="utf-8",
        newline="\n",
    )
    object_file.unlink(missing_ok=True)
    output.unlink(missing_ok=True)
    _run_build_process(
        [
            str(suite.command("asl")),
            "-xx",
            "-n",
            "-q",
            "-A",
            "-L",
            "-U",
            "-i",
            ".",
            "-o",
            object_relative,
            wrapper_relative,
        ],
        project_directory=project_directory,
        environment=suite.process_environment(),
    )
    _run_build_process(
        [
            str(suite.command("p2bin")),
            object_relative,
            output.relative_to(project_directory).as_posix(),
            "-p=FF",
        ],
        project_directory=project_directory,
        environment=suite.process_environment(),
    )


def _bundled_build(
    operation: dict[str, Any],
    project_directory: Path,
    output: Path,
    cache_root: Path | None,
) -> dict[str, str]:
    suite_id = operation.get("toolchain_suite")
    recipe = operation.get("recipe")
    if not isinstance(suite_id, str) or not isinstance(recipe, str):
        raise ProjectError("bundled build operation has no toolchain suite or recipe")
    suite = materialize_build_toolchain(suite_id, cache_root=cache_root)
    if recipe == "ca65-ld65-v1" and suite_id == "nes":
        _ca65_build(operation, project_directory, output, suite)
    elif recipe == "asl-p2bin-v1" and suite_id == "megadrive":
        _asl_build(operation, project_directory, output, suite)
    else:
        raise ProjectError(f"unsupported bundled build recipe: {suite_id}/{recipe}")
    return {
        "kind": "retrolarium-kit",
        "suite": suite.suite,
        "release": suite.release,
        "host": suite.host,
        "recipe": recipe,
    }


def build_project(
    manifest_path: Path,
    *,
    container: str | None = None,
    toolchain_cache: Path | None = None,
) -> dict[str, Any]:
    manifest_path = manifest_path.resolve()
    manifest = load_disassembly_manifest(manifest_path, verify_rebuilt=False)
    root = manifest_path.parent
    operation = manifest["operations"]["build"]
    project_directory = artifact_path(root, operation["project_directory"])
    output = artifact_path(root, operation["output"])
    try:
        output.relative_to(project_directory)
    except ValueError as exc:
        raise ProjectError("build output must be inside the project directory") from exc
    if container is not None:
        runner = _docker_build(operation, project_directory, container)
    elif operation.get("runner") == "retrolarium-kit":
        runner = _bundled_build(
            operation, project_directory, output, toolchain_cache
        )
    else:
        default_container = operation.get("default_container")
        if not isinstance(default_container, str) or not default_container:
            raise ProjectError(
                "build operation has neither a bundled recipe nor a container"
            )
        runner = _docker_build(operation, project_directory, default_container)
    if not output.is_file():
        raise ProjectError(f"build did not produce declared artifact: {output}")
    document = {
        "schema": "retrolarium.build-result",
        "schema_version": 1,
        "status": "built",
        "manifest_sha256": sha256_file(manifest_path),
        "operation": operation["id"],
        "runner": runner,
        "artifact": {
            "path": operation["output"],
            "size": output.stat().st_size,
            "sha256": sha256_file(output),
        },
    }
    write_document(root / "build-result.json", document)
    return document


def verify_project(manifest_path: Path) -> dict[str, Any]:
    manifest_path = manifest_path.resolve()
    manifest = load_disassembly_manifest(manifest_path, verify_rebuilt=False)
    root = manifest_path.parent
    operation = manifest["operations"]["verify"]
    artifact = artifact_path(root, operation["artifact"])
    if not artifact.is_file():
        raise ProjectError(f"declared build artifact does not exist: {artifact}")
    actual_size = artifact.stat().st_size
    actual_sha256 = sha256_file(artifact)
    exact = (
        actual_size == operation["expected_size"]
        and actual_sha256 == operation["expected_sha256"]
    )
    document = {
        "schema": "retrolarium.verification-result",
        "schema_version": 1,
        "status": "exact" if exact else "mismatch",
        "manifest_sha256": sha256_file(manifest_path),
        "operation": operation["id"],
        "artifact": {
            "path": operation["artifact"],
            "size": actual_size,
            "sha256": actual_sha256,
        },
        "expected": {
            "size": operation["expected_size"],
            "sha256": operation["expected_sha256"],
        },
    }
    write_document(root / "verification-result.json", document)
    return document


def launch_plan(
    manifest_path: Path,
    *,
    mame: Path,
    machine: str | None = None,
    attach_window: str | None = None,
) -> dict[str, Any]:
    manifest_path = manifest_path.resolve()
    manifest = load_disassembly_manifest(manifest_path, verify_rebuilt=False)
    root = manifest_path.parent
    operation = manifest["operations"]["launch"]
    selected_machine = machine or operation["default_machine"]
    platform = operation["platform"]
    allowed_machines = MAME_MACHINES.get(platform)
    declared_machines = operation.get("supported_machines")
    if (
        allowed_machines is None
        or selected_machine not in allowed_machines
        or not isinstance(declared_machines, list)
        or selected_machine not in declared_machines
    ):
        raise ProjectError(
            f"unsupported {platform} MAME machine: {selected_machine}"
        )
    mame = mame.resolve()
    if not mame.is_file():
        raise ProjectError(f"MAME executable does not exist: {mame}")
    rom = artifact_path(root, operation["artifact"])
    if not rom.is_file():
        raise ProjectError(f"launch artifact does not exist: {rom}")
    if platform == "nes":
        load_ines(rom)
    elif platform == "megadrive":
        load_megadrive(rom)
    elif platform == "snes":
        load_snes(rom)
    else:  # The manifest contract normally rejects this first.
        raise ProjectError(f"unsupported launch platform: {platform}")
    arguments = [
        selected_machine,
        "-cart",
        str(rom),
        "-skip_gameinfo",
        "-window",
        "-nomouse",
        "-nolightgun",
        "-nofilter",
        "-nounevenstretch",
        "-bgfx_screen_chains",
        "unfiltered",
    ]
    if attach_window is not None:
        if re.fullmatch(r"(?:0[xX][0-9a-fA-F]+|[0-9]+)", attach_window) is None:
            raise ProjectError("attach-window must be a decimal or hexadecimal handle")
        arguments.extend(["-attach_window", attach_window])
    return {
        "schema": "retrolarium.process-spec",
        "schema_version": 1,
        "operation": operation["id"],
        "program": str(mame),
        "arguments": arguments,
        "working_directory": str(mame.parent),
        "platform": platform,
        "machine": selected_machine,
        "rom": {
            "path": operation["artifact"],
            "size": rom.stat().st_size,
            "sha256": sha256_file(rom),
        },
    }


# The Famicom Disk System BIOS dumps the kit accepts, by SHA-1. MAME's fds
# machine loads one file, rp2c33a-01a.bin, and knows only the newer dump's
# checksum: the older dump (its other BIOS set, which the command line cannot
# select) goes in under that name too and runs like any BIOS MAME does not
# know.
FDS_BIOS_FILE = "rp2c33a-01a.bin"
FDS_BIOS_KNOWN = "57fe1bdee955bb48d357e463ccbf129496930b62"
FDS_BIOS = {
    FDS_BIOS_KNOWN: "newer dump (rp2c33a-01a)",
    "af5af53f66982e749643fdf8b2acbb7d4d3ed229": "older dump (rp2c33-01)",
}


def _fds_bios_directory(mame: Path) -> Path:
    return mame.resolve().parent / "roms" / "fds"


def fds_bios(mame: Path) -> Path | None:
    """The user's Disk System BIOS MAME will load from beside `mame`, if any."""
    directory = _fds_bios_directory(mame)
    if (directory / FDS_BIOS_FILE).is_file():
        return directory / FDS_BIOS_FILE
    for archive in ("fds.zip", "fds.7z"):
        if (directory.parent / archive).is_file():
            return directory.parent / archive
    return None


# OpenDiskSysROM, the free Disk System BIOS the kit carries, for players
# without Nintendo's: a ROM folder holding it as MAME names the BIOS, and the
# script that gets MAME past its warning about the unknown checksum.
# OpenDiskSys: the open FDS BIOS replacement, as a MAME ROM folder, and the
# Lua script that starts a disk with it.
OPEN_DISK_SYS_ROMS = Path(__file__).with_name("opendisksys")
OPEN_DISK_SYS_START = OPEN_DISK_SYS_ROMS / "mame_start.lua"


def _unknown_bios_arguments(*, interactive: bool) -> list[str]:
    """For a BIOS MAME does not know the checksum of, a run that shows MAME's
    window passes a time limit under five minutes, which keeps MAME's startup
    screens away, and a script that lifts the limit and restarts the machine
    once. A headless run never stops for those screens."""
    if not interactive:
        return []
    return [
        "-seconds_to_run", "299",
        "-autoboot_delay", "0",
        "-autoboot_script", str(OPEN_DISK_SYS_START),
    ]


def _disk_bios_arguments(mame: Path, *, interactive: bool) -> tuple[str, list[str]]:
    """Which BIOS a disk game runs on, and the MAME arguments for it: the
    user's dump when MAME has one, otherwise OpenDiskSysROM, from a ROM folder
    after MAME's own."""
    user = fds_bios(mame)
    if user is not None:
        known = user.suffix.lower() != ".bin" or hashlib.sha1(user.read_bytes()).hexdigest() == FDS_BIOS_KNOWN
        return "user", [] if known else _unknown_bios_arguments(interactive=interactive)
    if not (OPEN_DISK_SYS_ROMS / "fds" / FDS_BIOS_FILE).is_file():
        raise ProjectError(
            "MAME needs the Famicom Disk System BIOS to run disk games, and the "
            "kit's OpenDiskSysROM is missing. Choose your BIOS in Settings."
        )
    folders = [str(mame.parent / "roms"), str(OPEN_DISK_SYS_ROMS)]
    if any(";" in folder for folder in folders):
        raise ProjectError(
            "MAME cannot use a folder with ';' in its name for the Disk System "
            "BIOS. Move the studio to a folder without ';': " + " or ".join(folders)
        )
    return "opendisksys", ["-rompath", ";".join(folders), *_unknown_bios_arguments(interactive=interactive)]


def install_fds_bios(source: Path, mame: Path) -> dict[str, Any]:
    """Copy a Disk System BIOS dump to where MAME loads it from, under the one
    name MAME's fds machine loads."""
    try:
        data = source.read_bytes()
    except OSError as exc:
        raise ProjectError(f"cannot read {source}: {exc}") from exc
    digest = hashlib.sha1(data).hexdigest()
    if digest not in FDS_BIOS:
        raise ProjectError(f"{source.name} is not a Famicom Disk System BIOS dump the kit knows")
    directory = _fds_bios_directory(mame)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / FDS_BIOS_FILE
    target.write_bytes(data)
    return {"schema": "retrolarium.fds-bios", "schema_version": 1, "path": str(target), "sha1": digest}


def launch_image_plan(
    rom: Path,
    *,
    platform: str,
    mame: Path,
    machine: str | None = None,
    attach_window: str | None = None,
    interactive: bool = True,
) -> dict[str, Any]:
    """Create the same strict process-spec for a direct authoring artifact.

    A disk game runs on the user's Disk System BIOS when MAME has one, and
    on OpenDiskSysROM otherwise; `fds_bios` in the plan says which. With a
    BIOS MAME does not know the checksum of, an interactive plan carries an
    autoboot script of its own: the studio runs every -autoboot_script a plan
    gives, in order, where MAME alone would keep only the last."""
    default_machines = {"nes": "nes", "megadrive": "genesis", "snes": "snes"}
    allowed_machines = MAME_MACHINES.get(platform)
    disk = platform == "nes" and rom.suffix.lower() == ".fds"
    selected_machine = "fds" if disk else machine or default_machines.get(platform)
    if allowed_machines is None or selected_machine not in allowed_machines:
        raise ProjectError(
            f"unsupported {platform} MAME machine: {selected_machine}"
        )
    mame = mame.resolve()
    if not mame.is_file():
        raise ProjectError(f"MAME executable does not exist: {mame}")
    rom = rom.resolve()
    if not rom.is_file():
        raise ProjectError(f"launch artifact does not exist: {rom}")
    if disk:
        try:
            parse_fds_side(rom.read_bytes())
        except FdsError as exc:
            raise ProjectError(f"{rom.name}: {exc}") from exc
        bios, bios_arguments = _disk_bios_arguments(mame, interactive=interactive)
    elif platform == "nes":
        # An arcade image may carry more after its cartridge (PlayChoice-10).
        try:
            image, _rest = parse_ines_prefix(rom.read_bytes(), rom)
        except InesError as exc:
            raise ProjectError(f"{rom.name}: {exc}") from exc
        vs = image.mapper == 99
        if vs != (selected_machine in VS_MACHINES):
            raise ProjectError(
                f"{rom.name}: a Vs. System image runs on its arcade machine "
                f"({', '.join(sorted(VS_MACHINES))}), any other image on a console"
            )
        if vs:
            rom_folder = _vs_rom_set(image, rom, selected_machine)
    elif platform == "megadrive":
        load_megadrive(rom)
    elif platform == "snes":
        load_snes(rom)
    else:
        raise ProjectError(f"unsupported launch platform: {platform}")
    vs = platform == "nes" and not disk and selected_machine in VS_MACHINES
    medium = ["-rompath", str(rom_folder)] if vs else ["-flop" if disk else "-cart", str(rom)]
    arguments = [
        selected_machine,
        *medium,
        "-skip_gameinfo",
        "-window",
        "-nomouse",
        "-nolightgun",
        "-nofilter",
        "-nounevenstretch",
        "-bgfx_screen_chains",
        "unfiltered",
    ]
    if attach_window is not None:
        if re.fullmatch(r"(?:0[xX][0-9a-fA-F]+|[0-9]+)", attach_window) is None:
            raise ProjectError("attach-window must be a decimal or hexadecimal handle")
        arguments.extend(["-attach_window", attach_window])
    if disk:
        arguments.extend(bios_arguments)
    if vs and interactive:
        arguments.extend(["-seconds_to_run", "299", "-autoboot_delay", "0",
                          "-autoboot_script", str(MAME_VS_START)])
    plan = {
        "schema": "retrolarium.process-spec",
        "schema_version": 1,
        "operation": "retrolarium.game.launch",
        "program": str(mame),
        "arguments": arguments,
        "working_directory": str(mame.parent),
        "platform": platform,
        "machine": selected_machine,
        "rom": {
            "path": str(rom),
            "size": rom.stat().st_size,
            "sha256": sha256_file(rom),
        },
    }
    if disk:
        plan["fds_bios"] = bios
    if vs:
        plan["vs_rom_set"] = str(rom_folder / selected_machine)
    return plan


def _vs_rom_set(image: Any, rom: Path, machine: str) -> Path:
    """Write a Vs. System build as the ROM chips MAME's machine loads, into
    mame-roms/<machine>/ beside it, with the board's palette ROM; return the
    folder to pass as -rompath. An edited build's chips do not match MAME's
    checksums; the launch's start script gets past MAME's warning."""
    board = VS_MACHINES[machine]
    expected = (len(board["prg"]) + len(board["chr"])) * VS_CHIP_SIZE
    chips = image.prg + image.chr
    if len(image.prg) != len(board["prg"]) * VS_CHIP_SIZE or len(chips) != expected:
        raise ProjectError(
            f"{rom.name}: {len(image.prg)} bytes of PRG and {len(image.chr)} of CHR "
            f"do not fill the chips of {machine} ({board['name']})"
        )
    levels, digest = VS_PALETTES[board["palette"]]
    palette = bytes(int(level) for colour in levels.split(",") for level in colour)
    if hashlib.sha1(palette).hexdigest() != digest:
        raise ProjectError(f"the kit's {board['palette']} palette table is damaged")
    root = rom.parent / "mame-roms"
    if ";" in str(root):
        raise ProjectError(f"MAME cannot use a folder with ';' in its name: {root}")
    folder = root / machine
    try:
        folder.mkdir(parents=True, exist_ok=True)
        for index, name in enumerate((*board["prg"], *board["chr"])):
            (folder / name).write_bytes(chips[index * VS_CHIP_SIZE:(index + 1) * VS_CHIP_SIZE])
        (folder / f"{board['palette']}.pal").write_bytes(palette)
    except OSError as exc:
        raise ProjectError(f"cannot write the {machine} ROM set: {exc}") from exc
    return root


def launch_image_smoke(
    rom: Path,
    *,
    platform: str,
    mame: Path,
    output: Path,
    machine: str | None = None,
    seconds: int = 2,
    timeout: int = 60,
) -> dict[str, Any]:
    """Run a direct authoring artifact headlessly and retain reproducible evidence."""
    if seconds <= 0 or timeout <= 0:
        raise ProjectError("seconds and timeout must be positive")
    plan = launch_image_plan(rom, platform=platform, mame=mame, machine=machine,
                             interactive=False)
    run_root = output.resolve()
    run_root.mkdir(parents=True, exist_ok=True)
    run_directory = Path(
        tempfile.mkdtemp(prefix=f"{plan['platform']}-authoring-smoke-", dir=run_root)
    )
    snapshot_directory = run_directory / "snapshots"
    snapshot_directory.mkdir()
    arguments = list(plan["arguments"])
    arguments.extend(
        [
            "-seconds_to_run",
            str(seconds),
            "-video",
            "none",
            "-sound",
            "none",
            "-nothrottle",
            "-noreadconfig",
            "-nowriteconfig",
            "-snapshot_directory",
            str(snapshot_directory),
            "-cfg_directory",
            str(run_directory / "cfg"),
            "-nvram_directory",
            str(run_directory / "nvram"),
            "-state_directory",
            str(run_directory / "state"),
        ]
    )
    status = "failed"
    exit_code: int | None = None
    stdout = ""
    stderr = ""
    failure: dict[str, Any] | None = None
    try:
        result = subprocess.run(
            [plan["program"], *arguments],
            cwd=plan["working_directory"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
        )
        exit_code = result.returncode
        stdout = result.stdout
        stderr = result.stderr
        if exit_code == 0:
            status = "completed"
        else:
            status, failure = classify_mame_failure(stderr)
    except subprocess.TimeoutExpired as exc:
        status = "timeout"
        stdout = process_text(exc.stdout)
        stderr = process_text(exc.stderr)
    (run_directory / "stdout.log").write_text(
        stdout, encoding="utf-8", newline="\n"
    )
    (run_directory / "stderr.log").write_text(
        stderr, encoding="utf-8", newline="\n"
    )
    snapshots = sorted(snapshot_directory.rglob("*.png"))
    document = {
        "schema": "retrolarium.run-result",
        "schema_version": 1,
        "status": status,
        "run_directory": str(run_directory),
        "process": {**plan, "arguments": arguments},
        "runner": {
            "id": "mame",
            "executable_sha256": sha256_file(Path(plan["program"])),
        },
        "limits": {"emulated_seconds": seconds, "timeout_seconds": timeout},
        "exit_code": exit_code,
        "stdout": "stdout.log",
        "stderr": "stderr.log",
        "snapshots": [
            {
                "path": path.relative_to(run_directory).as_posix(),
                "size": path.stat().st_size,
                "sha256": sha256_file(path),
            }
            for path in snapshots
        ],
    }
    if failure is not None:
        document["failure"] = failure
    write_document(run_directory / "run-result.json", document)
    return document


def launch_project_smoke(
    manifest_path: Path,
    *,
    mame: Path,
    machine: str | None = None,
    seconds: int = 2,
    timeout: int = 60,
    output: Path | None = None,
) -> dict[str, Any]:
    if seconds <= 0 or timeout <= 0:
        raise ProjectError("seconds and timeout must be positive")
    manifest_path = manifest_path.resolve()
    plan = launch_plan(manifest_path, mame=mame, machine=machine)
    root = manifest_path.parent
    run_root = (output or root / "run").resolve()
    run_root.mkdir(parents=True, exist_ok=True)
    run_directory = Path(
        tempfile.mkdtemp(prefix=f"{plan['platform']}-smoke-", dir=run_root)
    )
    snapshot_directory = run_directory / "snapshots"
    snapshot_directory.mkdir()
    arguments = list(plan["arguments"])
    arguments.extend(
        [
            "-seconds_to_run",
            str(seconds),
            "-video",
            "none",
            "-sound",
            "none",
            "-nothrottle",
            "-noreadconfig",
            "-nowriteconfig",
            "-snapshot_directory",
            str(snapshot_directory),
            "-cfg_directory",
            str(run_directory / "cfg"),
            "-nvram_directory",
            str(run_directory / "nvram"),
            "-state_directory",
            str(run_directory / "state"),
        ]
    )
    command = [plan["program"], *arguments]
    status = "failed"
    exit_code: int | None = None
    stdout = ""
    stderr = ""
    failure: dict[str, Any] | None = None
    try:
        result = subprocess.run(
            command,
            cwd=plan["working_directory"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
        )
        exit_code = result.returncode
        stdout = result.stdout
        stderr = result.stderr
        if exit_code == 0:
            status = "completed"
        else:
            status, failure = classify_mame_failure(stderr)
    except subprocess.TimeoutExpired as exc:
        status = "timeout"
        stdout = process_text(exc.stdout)
        stderr = process_text(exc.stderr)
    (run_directory / "stdout.log").write_text(
        stdout, encoding="utf-8", newline="\n"
    )
    (run_directory / "stderr.log").write_text(
        stderr, encoding="utf-8", newline="\n"
    )
    snapshots = sorted(snapshot_directory.rglob("*.png"))
    document = {
        "schema": "retrolarium.run-result",
        "schema_version": 1,
        "status": status,
        "manifest_sha256": sha256_file(manifest_path),
        "process": {**plan, "arguments": arguments},
        "runner": {
            "id": "mame",
            "executable_sha256": sha256_file(Path(plan["program"])),
        },
        "limits": {"emulated_seconds": seconds, "timeout_seconds": timeout},
        "exit_code": exit_code,
        "stdout": "stdout.log",
        "stderr": "stderr.log",
        "snapshots": [
            {
                "path": path.relative_to(run_directory).as_posix(),
                "size": path.stat().st_size,
                "sha256": sha256_file(path),
            }
            for path in snapshots
        ],
    }
    if failure is not None:
        document["failure"] = failure
    write_document(run_directory / "run-result.json", document)
    return document
