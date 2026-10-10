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


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from retrolarium_kit.core.project import (
    MAME_VS_START,
    OPEN_DISK_SYS_ROMS,
    OPEN_DISK_SYS_START,
    ProjectError,
    build_project,
    install_fds_bios,
    launch_image_plan,
    launch_image_smoke,
    launch_plan,
    launch_project_smoke,
    verify_project,
    vs_machine,
)
from helpers import fds_side as fds_fixture


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

    def test_disk_launch_plan_falls_back_to_open_disk_sys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            disk = root / "edited.fds"
            disk.write_bytes(fds_fixture())
            mame = root / "consoles.exe"
            mame.write_bytes(b"mame")

            plan = launch_image_plan(disk, platform="nes", mame=mame)
            arguments = plan["arguments"]
            self.assertEqual("opendisksys", plan["fds_bios"])
            self.assertEqual(["fds", "-flop", str(disk.resolve())], arguments[:3])
            rompath = arguments[arguments.index("-rompath") + 1].split(";")
            self.assertEqual([str(root / "roms"), str(OPEN_DISK_SYS_ROMS)], rompath)
            self.assertEqual("299", arguments[arguments.index("-seconds_to_run") + 1])
            self.assertEqual(str(OPEN_DISK_SYS_START), arguments[arguments.index("-autoboot_script") + 1])
            self.assertTrue(OPEN_DISK_SYS_START.is_file())

            headless = launch_image_plan(disk, platform="nes", mame=mame, interactive=False)
            self.assertIn("-rompath", headless["arguments"])
            self.assertNotIn("-seconds_to_run", headless["arguments"])
            self.assertNotIn("-autoboot_script", headless["arguments"])

            # The user's dump: one MAME knows runs as it is, any other (the
            # older dump) gets past MAME's warning like OpenDiskSysROM.
            dump = bytes(0x2000)
            (root / "roms" / "fds").mkdir(parents=True)
            (root / "roms" / "fds" / "rp2c33a-01a.bin").write_bytes(dump)
            own = launch_image_plan(disk, platform="nes", mame=mame)
            self.assertEqual("user", own["fds_bios"])
            self.assertNotIn("-rompath", own["arguments"])
            self.assertEqual(str(OPEN_DISK_SYS_START), own["arguments"][own["arguments"].index("-autoboot_script") + 1])
            with patch("retrolarium_kit.core.project.FDS_BIOS_KNOWN", hashlib.sha1(dump).hexdigest()):
                known = launch_image_plan(disk, platform="nes", mame=mame)
            self.assertEqual("user", known["fds_bios"])
            self.assertNotIn("-seconds_to_run", known["arguments"])

    def test_vs_launch_plan_writes_the_boards_rom_set(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            chips = bytes(range(256)) * (0xC000 // 256)
            # iNES with 2 x 16 KiB PRG, 2 x 8 KiB CHR, mapper 99 (Vs. System).
            rom = root / "edited.nes"
            rom.write_bytes(b"NES" + bytes((2, 2, 0x30, 0x60)) + bytes(8) + chips)
            mame = root / "consoles.exe"
            mame.write_bytes(b"mame")

            self.assertEqual("suprmrio", vs_machine("smb1", "vs_smb"))
            self.assertIsNone(vs_machine("smb1", "ju"))
            plan = launch_image_plan(rom, platform="nes", mame=mame, machine="suprmrio")
            arguments = plan["arguments"]
            self.assertEqual(["suprmrio", "-rompath", str(root / "mame-roms")], arguments[:3])
            self.assertNotIn("-cart", arguments)
            self.assertEqual("299", arguments[arguments.index("-seconds_to_run") + 1])
            self.assertEqual(str(MAME_VS_START), arguments[arguments.index("-autoboot_script") + 1])
            self.assertTrue(MAME_VS_START.is_file())
            folder = root / "mame-roms" / "suprmrio"
            self.assertEqual(chips[:0x2000], (folder / "mds-sm4-4__1dor6d_e.1d or 6d").read_bytes())
            self.assertEqual(chips[0x6000:0x8000], (folder / "mds-sm4-4__1aor6a_e.1a or 6a").read_bytes())
            self.assertEqual(chips[0xA000:], (folder / "mds-sm4-4__2aor8a_e.2a or 8a").read_bytes())
            # The palette ROM MAME checks against: CRC 0c2e8e4d.
            palette = (folder / "rp2c04-0004.pal").read_bytes()
            self.assertEqual("0f9090225eb1f08ae5072d40af3e95547cbce05f", hashlib.sha1(palette).hexdigest())

            headless = launch_image_plan(rom, platform="nes", mame=mame, machine="suprmrio",
                                         interactive=False)
            self.assertNotIn("-seconds_to_run", headless["arguments"])
            # A Vs. image needs its arcade machine; a console image cannot use one.
            with self.assertRaisesRegex(ProjectError, "Vs. System"):
                launch_image_plan(rom, platform="nes", mame=mame)
            console = root / "console.nes"
            console.write_bytes(b"NES" + bytes((2, 2, 0, 0)) + bytes(8) + chips)
            with self.assertRaisesRegex(ProjectError, "Vs. System"):
                launch_image_plan(console, platform="nes", mame=mame, machine="suprmrio")
            short = root / "short.nes"
            short.write_bytes(b"NES" + bytes((1, 1, 0x30, 0x60)) + bytes(8) + chips[:0x6000])
            with self.assertRaisesRegex(ProjectError, "do not fill the chips"):
                launch_image_plan(short, platform="nes", mame=mame, machine="suprmrio")

    def test_older_bios_dump_installs_under_the_name_mame_loads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mame = root / "consoles.exe"
            mame.write_bytes(b"mame")
            older = root / "disksys.rom"
            older.write_bytes(b"older dump")
            digest = hashlib.sha1(b"older dump").hexdigest()
            with patch.dict("retrolarium_kit.core.project.FDS_BIOS", {digest: "older dump"}):
                result = install_fds_bios(older, mame)
            self.assertEqual(str(root / "roms" / "fds" / "rp2c33a-01a.bin"), result["path"])
            self.assertEqual(b"older dump", (root / "roms" / "fds" / "rp2c33a-01a.bin").read_bytes())
            older.write_bytes(b"something else")
            with self.assertRaises(ProjectError):
                install_fds_bios(older, mame)

    def test_disk_launch_plan_refuses_a_semicolon_in_the_rom_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "a;b"
            root.mkdir()
            disk = root / "edited.fds"
            disk.write_bytes(fds_fixture())
            mame = root / "consoles.exe"
            mame.write_bytes(b"mame")
            with self.assertRaisesRegex(ProjectError, "';'"):
                launch_image_plan(disk, platform="nes", mame=mame)

    def test_open_disk_sys_image_matches_its_manifest(self) -> None:
        manifest = json.loads((OPEN_DISK_SYS_ROMS / "manifest.json").read_text(encoding="utf-8"))
        image = (OPEN_DISK_SYS_ROMS / manifest["image"]).read_bytes()
        self.assertEqual("fds/rp2c33a-01a.bin", manifest["image"])
        self.assertEqual(manifest["size"], len(image))
        self.assertEqual(manifest["sha256"], digest(image))
        self.assertTrue((OPEN_DISK_SYS_ROMS / manifest["license_file"]).is_file())

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
                "retrolarium_kit.core.project.subprocess.run", return_value=completed
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
                "retrolarium_kit.core.project.subprocess.run", return_value=completed
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
                "retrolarium_kit.core.project.subprocess.run", side_effect=timeout
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
                "retrolarium_kit.core.project.subprocess.run", return_value=completed
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
            with patch("retrolarium_kit.core.project.subprocess.run", return_value=completed) as run:
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
