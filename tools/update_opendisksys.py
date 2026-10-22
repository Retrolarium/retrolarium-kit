#!/usr/bin/env python3
"""Build OpenDiskSysROM from a checkout and bring it into the kit.

    python tools/update_opendisksys.py path/to/OpenDiskSysROM [--asm6f asm6f]

The kit plays Famicom Disk System games on OpenDiskSysROM when the user has
not installed Nintendo's BIOS. This assembles the checkout's freedisksys.asm
with asm6f, copies the image to core/opendisksys/fds/rp2c33a-01a.bin (the
name MAME's fds set gives the BIOS) with the project's license, and records
the source commit and the image's digest in core/opendisksys/manifest.json.
The checkout must be clean, so the commit names exactly what was built.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

FIRMWARE = Path(__file__).resolve().parents[1] / "src" / "retrolarium_kit" / "core" / "opendisksys"
REPOSITORY = "https://github.com/RetrolariumLab/OpenDiskSysROM"
IMAGE_SIZE = 0x2000


def git(checkout: Path, *arguments: str) -> str:
    return subprocess.run(["git", *arguments], cwd=checkout, capture_output=True, text=True, check=True).stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("checkout", type=Path)
    parser.add_argument("--asm6f", default=os.environ.get("ASM6F", "asm6f"))
    arguments = parser.parse_args()
    checkout = arguments.checkout.resolve()
    if git(checkout, "status", "--porcelain", "--untracked-files=no"):
        sys.exit(f"{checkout} has uncommitted changes")
    commit = git(checkout, "rev-parse", "HEAD")
    with tempfile.TemporaryDirectory(prefix="opendisksys-") as temp:
        image_path = Path(temp) / "opendisksys.bin"
        result = subprocess.run(
            [arguments.asm6f, "freedisksys.asm", str(image_path)], cwd=checkout, capture_output=True, text=True
        )
        if result.returncode != 0 or not image_path.is_file():
            sys.exit("asm6f failed:\n" + result.stdout + result.stderr)
        image = image_path.read_bytes()
    if len(image) != IMAGE_SIZE:
        sys.exit(f"the BIOS is {len(image)} bytes, not {IMAGE_SIZE}")
    (FIRMWARE / "fds").mkdir(parents=True, exist_ok=True)
    (FIRMWARE / "fds" / "rp2c33a-01a.bin").write_bytes(image)
    # The checkout may have CRLF line ends; the kit keeps LF.
    license_text = (checkout / "LICENSE").read_text(encoding="utf-8")
    (FIRMWARE / "LICENSE-LGPL-3.0.txt").write_text(license_text, encoding="utf-8", newline="\n")
    manifest = {
        "schema": "retrolarium.firmware",
        "schema_version": 1,
        "name": "OpenDiskSysROM",
        "license": "LGPL-3.0",
        "license_file": "LICENSE-LGPL-3.0.txt",
        "source": REPOSITORY,
        "commit": commit,
        "image": "fds/rp2c33a-01a.bin",
        "size": len(image),
        "sha256": hashlib.sha256(image).hexdigest(),
    }
    (FIRMWARE / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"OpenDiskSysROM {commit[:12]} -> {FIRMWARE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
