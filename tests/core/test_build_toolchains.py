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


# The assemblers are built by tools/build_toolchains.ps1 and not tracked; the
# tests that run them skip in a checkout that has not built them.
PAYLOADS = (Path(__file__).resolve().parents[2] / "src" / "retrolarium_kit" / "core"
            / "toolchains" / "payloads")
BUILT = (PAYLOADS / "windows-x86_64").is_dir() and (PAYLOADS / "linux-x86_64").is_dir()
needs_payloads = unittest.skipUnless(BUILT, "the assemblers are not built (tools/build_toolchains.ps1)")


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
        # Both hosts' AS builds come from the one source archive the kit
        # ships, and every component names the script that builds it.
        sources = manifest["components"]["asl"]["corresponding_sources"]
        self.assertEqual({source["revision"] for source in sources.values()},
                         {"721a327a803585e2d1f59acac93cbd0768213d06"})
        self.assertEqual(set(sources), {"windows-x86_64", "linux-x86_64"})
        for component in manifest["components"].values():
            self.assertEqual(component["build"], "tools/toolchains/build.sh")
            self.assertEqual(set(component["hosts"]), {"windows-x86_64", "linux-x86_64"})

    @needs_payloads
    def test_built_payloads_pass_every_hash_on_both_hosts(self) -> None:
        for host in ("windows-x86_64", "linux-x86_64"):
            status = packaged_toolchain_status(host)
            self.assertTrue(status["suites"]["nes"]["available"], host)
            self.assertTrue(status["suites"]["megadrive"]["available"], host)

    @needs_payloads
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

    @needs_payloads
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

    @needs_payloads
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

    @needs_payloads
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

    @needs_payloads
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
