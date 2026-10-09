from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from retrolarium_kit.core.build_toolchains import (
    BuildToolchainError,
    load_build_toolchain_manifest,
    materialize_build_toolchain,
    normalize_host,
    packaged_toolchain_status,
)


class BuildToolchainTests(unittest.TestCase):
    def test_host_names_are_stable(self) -> None:
        self.assertEqual(normalize_host("Windows", "AMD64"), "windows-x86_64")
        self.assertEqual(normalize_host("Linux", "x86_64"), "linux-x86_64")
        self.assertEqual(normalize_host("Darwin", "arm64"), "macos-aarch64")

    def test_manifest_exposes_the_two_console_suites(self) -> None:
        manifest = load_build_toolchain_manifest()
        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(set(manifest["suites"]), {"nes", "megadrive"})
        self.assertEqual(manifest["suites"]["nes"]["components"], ["cc65"])
        self.assertEqual(
            manifest["suites"]["megadrive"]["components"], ["asl", "p2bin"]
        )
        sources = manifest["components"]["asl"]["corresponding_sources"]
        self.assertEqual(
            sources["windows-x86_64"]["revision"],
            "721a327a803585e2d1f59acac93cbd0768213d06",
        )
        self.assertEqual(
            sources["linux-x86_64"]["revision"],
            "3a5d200f84be0418b3a49cda62749956e01a33a0",
        )
        self.assertEqual(
            sources["windows-x86_64"]["binary_origin"]["commit"],
            "10e7792967a38ed22c7871e77749bb13c45e2ae4",
        )
        self.assertEqual(
            manifest["components"]["p2bin"]["hosts"]["linux-x86_64"]
            ["binary_origin"]["commit"],
            "1ce386fc63bcef58d53f87d51a2e8620ff24de9a",
        )

    def test_packaged_windows_payloads_pass_every_hash(self) -> None:
        status = packaged_toolchain_status("windows-x86_64")
        self.assertTrue(status["suites"]["nes"]["available"])
        self.assertTrue(status["suites"]["megadrive"]["available"])

    def test_linux_reports_the_honest_partial_boundary(self) -> None:
        status = packaged_toolchain_status("linux-x86_64")
        self.assertFalse(status["suites"]["nes"]["available"])
        self.assertTrue(status["suites"]["megadrive"]["available"])

    def test_materializes_verified_commands_and_as_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            nes = materialize_build_toolchain(
                "nes", cache_root=root, host="windows-x86_64"
            )
            sega = materialize_build_toolchain(
                "megadrive", cache_root=root, host="windows-x86_64"
            )
            self.assertTrue(nes.command("ca65").is_file())
            self.assertTrue(nes.command("ld65").is_file())
            self.assertTrue(sega.command("asl").is_file())
            self.assertTrue(sega.command("p2bin").is_file())
            self.assertEqual(Path(sega.environment["AS_MSGPATH"]), sega.root / "asl")
            self.assertEqual(
                sega.process_environment({"EXISTING": "yes"})["EXISTING"], "yes"
            )

    def test_unknown_suite_and_command_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(BuildToolchainError, "unknown"):
                materialize_build_toolchain(
                    "unknown", cache_root=Path(directory), host="windows-x86_64"
                )
            suite = materialize_build_toolchain(
                "nes", cache_root=Path(directory), host="windows-x86_64"
            )
            with self.assertRaisesRegex(BuildToolchainError, "no command"):
                suite.command("asl")

    @unittest.skipUnless(os.name == "nt", "bundled PE smoke runs on Windows")
    def test_windows_compilers_report_pinned_versions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            nes = materialize_build_toolchain(
                "nes", cache_root=root, host="windows-x86_64"
            )
            sega = materialize_build_toolchain(
                "megadrive", cache_root=root, host="windows-x86_64"
            )
            ca65 = subprocess.run(
                [str(nes.command("ca65")), "--version"],
                capture_output=True,
                text=True,
                errors="replace",
            )
            asl = subprocess.run(
                [str(sega.command("asl")), "-h"],
                capture_output=True,
                text=True,
                errors="replace",
                env=sega.process_environment(),
            )
            self.assertIn("ca65 V2.19 - Git 0fca835", ca65.stdout + ca65.stderr)
            self.assertIn("Macro Assembler 1.42 Beta [Bld 212]", asl.stdout + asl.stderr)

    @unittest.skipUnless(os.name == "nt", "bundled PE smoke runs on Windows")
    def test_windows_nes_suite_builds_a_binary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            suite = materialize_build_toolchain(
                "nes", cache_root=root / "cache", host="windows-x86_64"
            )
            source = root / "probe.asm"
            config = root / "probe.cfg"
            object_file = root / "probe.o"
            output = root / "probe.bin"
            source.write_text(
                '.segment "ROM"\n.byte $52, $4C\n', encoding="utf-8", newline="\n"
            )
            config.write_text(
                "MEMORY { ROM: start = $8000, size = $0002, file = %O; }\n"
                "SEGMENTS { ROM: load = ROM, type = ro; }\n",
                encoding="utf-8",
                newline="\n",
            )
            subprocess.run(
                [str(suite.command("ca65")), "-o", str(object_file), str(source)],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                [
                    str(suite.command("ld65")),
                    "-C",
                    str(config),
                    "-o",
                    str(output),
                    str(object_file),
                ],
                check=True,
                capture_output=True,
            )
            self.assertEqual(output.read_bytes(), b"RL")

    @unittest.skipUnless(os.name == "nt", "bundled PE smoke runs on Windows")
    def test_windows_megadrive_suite_builds_a_binary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            suite = materialize_build_toolchain(
                "megadrive", cache_root=root / "cache", host="windows-x86_64"
            )
            source = root / "probe.asm"
            object_file = root / "probe.p"
            output = root / "probe.bin"
            source.write_text(
                "                cpu 68000\n"
                "                org $000000\n"
                "                dc.b $52, $4C\n"
                "                end\n",
                encoding="ascii",
                newline="\n",
            )
            assembled = subprocess.run(
                [
                    str(suite.command("asl")),
                    "-q",
                    "-o",
                    str(object_file),
                    str(source),
                ],
                capture_output=True,
                text=True,
                errors="replace",
                env=suite.process_environment(),
            )
            self.assertEqual(
                assembled.returncode,
                0,
                msg=assembled.stdout + assembled.stderr,
            )
            subprocess.run(
                [
                    str(suite.command("p2bin")),
                    str(object_file),
                    str(output),
                    "-p=FF",
                ],
                check=True,
                capture_output=True,
            )
            self.assertEqual(output.read_bytes(), b"RL")


if __name__ == "__main__":
    unittest.main()
