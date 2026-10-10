#!/usr/bin/env python3
"""Write core/toolchains/manifest.json for the assemblers build.sh built.

    python tools/toolchains/write_manifest.py [--release 2026.10.11]

The manifest pins every payload by size and SHA-256, so the kit refuses a
changed or partial build. Run it after a deliberate rebuild (a new compiler
image or source commit); an unchanged rebuild reproduces the same files and
needs no new manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

TOOLCHAINS = Path(__file__).resolve().parents[2] / "src" / "retrolarium_kit" / "core" / "toolchains"
HOSTS = {"windows-x86_64": ".exe", "linux-x86_64": ""}
ASL_REVISION = "721a327a803585e2d1f59acac93cbd0768213d06"
BUILD = "tools/toolchains/build.sh"

COMPONENTS = {
    "cc65": {
        "name": "cc65 assembler and linker",
        "version": "2.19-git-0fca835",
        "license": "Zlib",
        "license_file": "licenses/cc65-Zlib.txt",
        "source": "https://github.com/cc65/cc65/tree/0fca83500b1a32fa1758bf2484562a00381f5642",
        "folder": "cc65",
        "commands": {"ca65": "ca65", "ld65": "ld65"},
        "extra_files": [],
        "environment": {},
    },
    "asl": {
        "name": "AS Macro Assembler",
        "version": "1.42-beta-build-212",
        "license": "GPL-2.0-or-later",
        "license_file": "licenses/AS-GPL-2.0.txt",
        "source": f"https://github.com/flamewing/asl-releases/tree/{ASL_REVISION}",
        "folder": "asl",
        "commands": {"asl": "asl"},
        "extra_files": ["as.msg", "cmdarg.msg", "ioerrs.msg"],
        "environment": {"AS_MSGPATH": "asl"},
    },
    "p2bin": {
        "name": "p2bin",
        "version": "e26d8aa8c43e285bac5e3b7df3be1adae515994f",
        "license": "ISC",
        "license_file": "licenses/p2bin-ISC.txt",
        "source": "https://github.com/Clownacy/p2bin/tree/e26d8aa8c43e285bac5e3b7df3be1adae515994f",
        "folder": "p2bin",
        "commands": {"p2bin": "p2bin"},
        "extra_files": [],
        "environment": {},
    },
}


def record(package_path: str, install_path: str | None = None, *, executable: bool = False) -> dict:
    data = (TOOLCHAINS / package_path).read_bytes()
    result = {"package_path": package_path}
    if install_path is not None:
        result["install_path"] = install_path
    result["size"] = len(data)
    result["sha256"] = hashlib.sha256(data).hexdigest()
    if executable:
        result["executable"] = True
    return result


def component(definition: dict) -> dict:
    folder = definition["folder"]
    license_file = record(definition["license_file"])
    result = {
        "name": definition["name"],
        "version": definition["version"],
        "license": definition["license"],
        "license_file": definition["license_file"],
        "license_size": license_file["size"],
        "license_sha256": license_file["sha256"],
        "source": definition["source"],
        "build": BUILD,
    }
    if definition["license"].startswith("GPL"):
        # The source the binaries are built from ships beside them.
        archive = f"sources/asl-flamewing-{ASL_REVISION}.tar.gz"
        source = {"revision": ASL_REVISION,
                  "source_url": f"https://github.com/flamewing/asl-releases/archive/{ASL_REVISION}.tar.gz",
                  **record(archive)}
        result["corresponding_sources"] = {host: source for host in HOSTS}
    hosts = {}
    for host, suffix in HOSTS.items():
        files = [record(f"payloads/{host}/{folder}/{name}{suffix}", f"{folder}/{name}{suffix}", executable=True)
                 for name in definition["commands"].values()]
        files += [record(f"payloads/{host}/{folder}/{name}", f"{folder}/{name}")
                  for name in definition["extra_files"]]
        hosts[host] = {
            "commands": {command: f"{folder}/{name}{suffix}" for command, name in definition["commands"].items()},
            **({"environment": definition["environment"]} if definition["environment"] else {}),
            "files": files,
        }
    result["hosts"] = hosts
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--release", default="2026.10.11")
    arguments = parser.parse_args()
    manifest = {
        "schema": "retrolarium.build-toolchains",
        "schema_version": 1,
        "release": arguments.release,
        "suites": {"nes": {"components": ["cc65"]}, "megadrive": {"components": ["asl", "p2bin"]}},
        "components": {name: component(definition) for name, definition in COMPONENTS.items()},
    }
    (TOOLCHAINS / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {TOOLCHAINS / 'manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
