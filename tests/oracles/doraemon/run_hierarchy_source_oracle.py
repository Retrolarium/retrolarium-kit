"""Read-only World 1/3 codec oracle against the neighboring Doraemon source."""

from __future__ import annotations

import argparse
from pathlib import Path
import json
import runpy
import sys
import zlib


sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from retrolarium_kit.games.doraemon.hierarchy import decode_hierarchy, encode_hierarchy


EXPECTED = {
    "world1": (8000, "fe76a8f2"),
    "world3": (6400, "779d61b5"),
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True,
                        help="exact path to doraemon_src (not its parent)")
    args = parser.parse_args()
    root = args.source_root.resolve()
    if root.name != "doraemon_src":
        parser.error("--source-root must point directly to doraemon_src")
    encoder_path = root / "scripts/validation/reconstruction/world_data.py"
    if not encoder_path.is_file():
        parser.error(f"missing source encoder: {encoder_path}")
    encode_source = runpy.run_path(str(encoder_path))["encode_authoring"]
    for world, (size, crc) in EXPECTED.items():
        document_path = root / "data" / world / "hierarchical_world.json"
        source_document = json.loads(document_path.read_text(encoding="utf-8"))
        payload = encode_source(source_document)
        actual_crc = f"{zlib.crc32(payload) & 0xffffffff:08x}"
        if len(payload) != size or actual_crc != crc:
            raise AssertionError(
                f"{world} source differs: size={len(payload)}, crc32={actual_crc}"
            )
        if encode_hierarchy(decode_hierarchy(payload, world), world) != payload:
            raise AssertionError(f"{world} Retrolarium round-trip differs")
        print(f"{world}: {size} bytes, CRC32 {crc}, exact round-trip")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
