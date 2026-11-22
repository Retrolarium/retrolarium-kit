"""Check the assembler payloads before the kit is packaged.

The payloads (core/toolchains/payloads, built by tools/build_toolchains.ps1
or tools/build_toolchains.sh) are not tracked, but a wheel must carry them:
without them the bundled builds fail at first use. This checks every file
core/toolchains/manifest.json pins, by size and SHA-256, and fails with the
command that builds them. Run it before `python -m build` or `uv build`.

    python tools/check_toolchain_payloads.py
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

TOOLCHAINS = Path(__file__).resolve().parents[1] / "src" / "retrolarium_kit" / "core" / "toolchains"


def problems(toolchains: Path = TOOLCHAINS) -> list[str]:
    """Every payload file the manifest pins that is missing or differs."""
    manifest = json.loads((toolchains / "manifest.json").read_text(encoding="utf-8"))
    found = []
    for component_id, component in manifest["components"].items():
        for host, entry in component["hosts"].items():
            for record in entry["files"]:
                path = toolchains / record["package_path"]
                if not path.is_file():
                    found.append(f"{component_id} ({host}): {record['package_path']} is missing")
                    continue
                data = path.read_bytes()
                if len(data) != record["size"] or hashlib.sha256(data).hexdigest() != record["sha256"]:
                    found.append(f"{component_id} ({host}): {record['package_path']} differs from the manifest")
    return found


def main() -> int:
    found = problems()
    for line in found:
        print(line, file=sys.stderr)
    if found:
        print(
            "error: the toolchain payloads are incomplete; build them with tools/build_toolchains.sh "
            "(or tools/build_toolchains.ps1 on Windows) before packaging the kit",
            file=sys.stderr,
        )
        return 1
    print(f"OK: every payload in {TOOLCHAINS / 'payloads'} matches the manifest")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
