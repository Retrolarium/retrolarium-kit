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

from .build_toolchains import ResolvedToolchain, materialize_build_toolchain
from .contracts import artifact_path, load_disassembly_manifest
from .ines import load_ines
from .megadrive import load_megadrive
from .snes import load_snes


class ProjectError(RuntimeError):
    """A declared project operation could not be completed."""


MAME_NES_MACHINES = {"nes", "nespal", "famicom"}
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
