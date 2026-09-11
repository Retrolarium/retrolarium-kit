from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from retrolarium_kit.project import (
    ProjectError,
    build_project,
    launch_image_plan,
    launch_image_smoke,
    launch_plan,
    launch_project_smoke,
    verify_project,
)
from retrolarium_kit.snes_pipeline import disassemble_snes
from test_snes_pointers import probe_image


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def write_project(root: Path, rom: bytes | None = None) -> Path:
    if rom is None:
        rom = b"NES\x1a" + bytes((1, 0, 0, 0)) + bytes(8) + bytes(0x4000)
    files = {
        "analysis/instructions.json": b"facts",
        "analysis/pointers.json": b"pointers",
        "analysis/banks/bank00/analysis-input.nes": b"input",
        "analysis/banks/bank00/instructions.tsv": b"tsv",
        "generated/symbols.json": b"symbols",
        "generated/assets/header.bin": rom[:4],
        "generated/src/prg.asm": b'.segment "PRG"\n',
        "generated/build/rebuilt.nes": rom,
    }
    for relative, payload in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    manifest = {
        "schema": "retrolarium.nes.disassembly",
        "schema_version": 1,
        "task": "nes.disassemble",
        "input": {"sha256": digest(rom), "size": len(rom)},
        "backend": {
            "analysis_root": "analysis",
            "banks": [
                {
                    "analysis_input": "banks/bank00/analysis-input.nes",
                    "analysis_input_sha256": digest(b"input"),
                    "facts": "banks/bank00/instructions.tsv",
                    "facts_sha256": digest(b"tsv"),
                }
            ],
        },
        "facts": {
            "path": "analysis/instructions.json",
            "sha256": digest(b"facts"),
        },
        "symbols": {
            "path": "generated/symbols.json",
            "sha256": digest(b"symbols"),
        },
        "pointers": {
            "path": "analysis/pointers.json",
            "sha256": digest(b"pointers"),
        },
        "source": {"units": ["generated/src/prg.asm"]},
        "assets": {
            "items": [
                {
                    "id": "nes.container.header",
                    "path": "generated/assets/header.bin",
                    "asset_file_offset": 0,
                    "size": 4,
                    "sha256": digest(rom[:4]),
                }
            ]
        },
        "operations": {
            "build": {
                "id": "retrolarium.project.build",
                "project_directory": "generated",
                "output": "generated/build/rebuilt.nes",
                "default_container": "test-ca65:1",
            },
            "verify": {
                "id": "retrolarium.project.verify",
                "artifact": "generated/build/rebuilt.nes",
                "expected_sha256": digest(rom),
                "expected_size": len(rom),
            },
            "launch": {
                "id": "retrolarium.project.launch",
                "artifact": "generated/build/rebuilt.nes",
                "platform": "nes",
                "default_machine": "nes",
                "supported_machines": ["famicom", "nes", "nespal"],
                "supported_modes": ["interactive-plan", "smoke"],
            },
        },
        "verification": {"status": "not-run"},
    }
    path = root / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


class ProjectOperationTests(unittest.TestCase):
    def test_direct_image_launch_plan_validates_container_and_machine(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rom = root / "edited.nes"
            rom.write_bytes(
                b"NES\x1a" + bytes((1, 0, 0, 0)) + bytes(8) + bytes(0x4000)
            )
            mame = root / "consoles.exe"
            mame.write_bytes(b"mame")
            plan = launch_image_plan(
                rom, platform="nes", mame=mame, machine="famicom"
            )
            self.assertEqual("retrolarium.game.launch", plan["operation"])
            self.assertEqual("famicom", plan["arguments"][0])
            self.assertEqual(str(rom), plan["arguments"][2])
            with self.assertRaises(ProjectError):
                launch_image_plan(
                    rom, platform="nes", mame=mame, machine="genesis"
                )

    def test_direct_image_smoke_retains_a_run_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rom = root / "edited.nes"
            rom.write_bytes(
                b"NES\x1a" + bytes((1, 0, 0, 0)) + bytes(8) + bytes(0x4000)
            )
            mame = root / "consoles.exe"
            mame.write_bytes(b"mame")
            completed = subprocess.CompletedProcess([], 0, "out", "err")
            with patch(
                "retrolarium_kit.project.subprocess.run", return_value=completed
            ) as run:
                result = launch_image_smoke(
                    rom,
                    platform="nes",
                    mame=mame,
                    output=root / "runs",
                    seconds=1,
                    timeout=5,
                )
            self.assertEqual("completed", result["status"])
            self.assertIn("-seconds_to_run", run.call_args.args[0])
            self.assertTrue(
                (Path(result["run_directory"]) / "run-result.json").is_file()
            )

    @unittest.skipUnless(os.name == "nt", "bundled PE smoke runs on Windows")
    def test_bundled_runner_builds_generated_snes_without_docker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rom = root / "probe.sfc"
            rom.write_bytes(probe_image("lorom").container_data)
            project = root / "project"
            disassemble_snes(rom, project)

            result = build_project(
                project / "retrolarium-project.json",
                toolchain_cache=root / "toolchains",
            )

            self.assertEqual("retrolarium-kit", result["runner"]["kind"])
            self.assertEqual("ca65-ld65-v1", result["runner"]["recipe"])
            rebuilt = project / "generated/build/rebuilt.sfc"
            self.assertEqual(rom.read_bytes(), rebuilt.read_bytes())
            self.assertEqual(
                "exact",
                verify_project(project / "retrolarium-project.json")["status"],
            )

    def test_snes_launch_plan_uses_declared_machine_and_explicit_argv(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rom = root / "probe.sfc"
            rom.write_bytes(probe_image("lorom").container_data)
            project = root / "project"
            disassemble_snes(rom, project)
            rebuilt = project / "generated/build/rebuilt.sfc"
            rebuilt.parent.mkdir(parents=True, exist_ok=True)
            rebuilt.write_bytes(rom.read_bytes())
            mame = root / "consoles.exe"
            mame.write_bytes(b"mame")
            plan = launch_plan(
                project / "retrolarium-project.json",
                mame=mame,
                machine="snespal",
            )
            self.assertEqual("snes", plan["platform"])
            self.assertEqual("snespal", plan["arguments"][0])
            self.assertEqual("-cart", plan["arguments"][1])
            self.assertEqual(str(rebuilt), plan["arguments"][2])

    def test_launch_plan_is_explicit_argv_and_restricts_machine(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = write_project(root)
            mame = root / "consoles.exe"
            mame.write_bytes(b"mame")

            plan = launch_plan(
                manifest, mame=mame, machine="nespal", attach_window="0x1234"
            )
            self.assertEqual("retrolarium.process-spec", plan["schema"])
            self.assertEqual("nespal", plan["arguments"][0])
            self.assertIn("-attach_window", plan["arguments"])
            self.assertNotIn("shell", plan)
            with self.assertRaises(ProjectError):
                launch_plan(manifest, mame=mame, machine="arbitrary-driver")

    def test_smoke_launch_writes_run_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = write_project(root)
            mame = root / "consoles.exe"
            mame.write_bytes(b"mame")
            completed = subprocess.CompletedProcess([], 0, "out", "err")
            with patch(
                "retrolarium_kit.project.subprocess.run", return_value=completed
            ) as run:
                result = launch_project_smoke(
                    manifest,
                    mame=mame,
                    seconds=1,
                    timeout=5,
                    output=root / "runs",
                )

            self.assertEqual("completed", result["status"])
            self.assertIn("-seconds_to_run", run.call_args.args[0])
            self.assertEqual(1, len(list((root / "runs").glob("*/run-result.json"))))

    def test_smoke_launch_records_timeout_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = write_project(root)
            mame = root / "consoles.exe"
            mame.write_bytes(b"mame")
            timeout = subprocess.TimeoutExpired(
                [str(mame)], 1, output=b"partial output", stderr=b"partial error"
            )
            with patch(
                "retrolarium_kit.project.subprocess.run", side_effect=timeout
            ):
                result = launch_project_smoke(
                    manifest,
                    mame=mame,
                    seconds=1,
                    timeout=1,
                    output=root / "runs",
                )

            self.assertEqual("timeout", result["status"])
            run_directory = next((root / "runs").iterdir())
            self.assertEqual(
                "partial output", (run_directory / "stdout.log").read_text()
            )
            self.assertEqual(
                "partial error", (run_directory / "stderr.log").read_text()
            )

    def test_smoke_launch_classifies_missing_mame_device_rom(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = write_project(root)
            mame = root / "consoles.exe"
            mame.write_bytes(b"mame")
            completed = subprocess.CompletedProcess(
                [],
                2,
                "",
                "spc700.rom NOT FOUND (tried in s_smp snes)\n"
                "Fatal error: Required files are missing, the machine cannot be run.\n",
            )
            with patch(
                "retrolarium_kit.project.subprocess.run", return_value=completed
            ):
                result = launch_project_smoke(
                    manifest,
                    mame=mame,
                    seconds=1,
                    timeout=5,
                    output=root / "runs",
                )

            self.assertEqual("missing-dependency", result["status"])
            self.assertEqual(
                {
                    "kind": "mame-required-roms",
                    "message": (
                        "MAME cannot start because required device ROMs are missing"
                    ),
                    "files": ["spc700.rom"],
                },
                result["failure"],
            )

    def test_build_uses_declared_directory_container_and_argv(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = write_project(Path(directory))
            completed = subprocess.CompletedProcess([], 0)
            with patch("retrolarium_kit.project.subprocess.run", return_value=completed) as run:
                result = build_project(manifest)

            command = run.call_args.args[0]
            self.assertEqual("docker", command[0])
            self.assertIn("test-ca65:1", command)
            self.assertEqual(["make", "all"], command[-2:])
            self.assertEqual("built", result["status"])
            self.assertTrue((Path(directory) / "build-result.json").is_file())

    def test_verify_reports_exact_and_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = write_project(root)
            self.assertEqual("exact", verify_project(manifest)["status"])

            (root / "generated/build/rebuilt.nes").write_bytes(b"changed")
            self.assertEqual("mismatch", verify_project(manifest)["status"])


if __name__ == "__main__":
    unittest.main()
