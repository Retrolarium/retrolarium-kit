"""What a wheel of the kit must carry: the assembler payloads the manifest
pins (tools/check_toolchain_payloads.py) and the Debian snapshot the
toolchain image was built from."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import re
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
TOOLCHAINS = ROOT / "src" / "retrolarium_kit" / "core" / "toolchains"


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PayloadCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.toolchains = Path(folder.name)
        self.payload = b"assembler"
        manifest = {
            "components": {
                "asl": {
                    "hosts": {
                        "linux-x86_64": {
                            "files": [
                                {
                                    "package_path": "payloads/linux-x86_64/asl/asl",
                                    "size": len(self.payload),
                                    "sha256": hashlib.sha256(self.payload).hexdigest(),
                                }
                            ]
                        }
                    }
                }
            }
        }
        (self.toolchains / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        self.check = load("check_toolchain_payloads")

    def test_missing_and_changed_payloads_are_named(self) -> None:
        self.assertEqual(
            ["asl (linux-x86_64): payloads/linux-x86_64/asl/asl is missing"], self.check.problems(self.toolchains)
        )
        path = self.toolchains / "payloads" / "linux-x86_64" / "asl" / "asl"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"other")
        self.assertIn("differs from the manifest", self.check.problems(self.toolchains)[0])
        path.write_bytes(self.payload)
        self.assertEqual([], self.check.problems(self.toolchains))

    def test_the_kits_manifest_is_checkable(self) -> None:
        # Every pinned file is named; a checkout without payloads lists them all.
        found = self.check.problems(TOOLCHAINS)
        if (TOOLCHAINS / "payloads").is_dir() and not found:
            return
        self.assertTrue(all(re.search(r"is missing|differs", line) for line in found))


class ReproducibleImageTests(unittest.TestCase):
    def test_the_image_installs_from_the_snapshot_the_manifest_records(self) -> None:
        dockerfile = (ROOT / "tools" / "toolchains" / "Dockerfile").read_text(encoding="utf-8")
        snapshot = re.search(r"^ARG SNAPSHOT=(\d{8}T\d{6}Z)$", dockerfile, re.M)
        self.assertIsNotNone(snapshot)
        self.assertIn("snapshot.debian.org/archive/debian/${SNAPSHOT}", dockerfile)
        manifest = json.loads((TOOLCHAINS / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(snapshot.group(1), manifest["build_environment"]["debian_snapshot"])
        image = re.search(r"^FROM (\S+)$", dockerfile, re.M).group(1)
        self.assertEqual(image, manifest["build_environment"]["image"])

    def test_both_hosts_have_a_build_script(self) -> None:
        for name in ("build_toolchains.ps1", "build_toolchains.sh"):
            self.assertTrue((ROOT / "tools" / name).is_file(), name)
        self.assertIn("docker", (ROOT / "tools" / "build_toolchains.sh").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
