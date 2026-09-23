"""Read-only World 2 selector-stream oracle against Doraemon source data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import zlib


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from retrolarium_kit.doraemon_world2_streams import (
    decode_screens, encode_screens, PAYLOAD_SIZE,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True,
                        help="exact path to doraemon_src")
    args = parser.parse_args()
    root = args.source_root.resolve()
    if root.name != "doraemon_src":
        parser.error("--source-root must point directly to doraemon_src")
    sys.path.insert(0, str(root))
    from scripts.validation.world2 import world2_streaming

    source = json.loads((root / "data/world2/compressed_screens.json")
                        .read_text(encoding="utf-8"))
    payload = world2_streaming.encode_authoring(source)
    checksum = f"{zlib.crc32(payload) & 0xffffffff:08x}"
    if len(payload) != PAYLOAD_SIZE or checksum != "030e0045":
        raise AssertionError(f"World 2 source differs: {len(payload)} bytes, {checksum}")
    decoded = decode_screens(payload)
    if encode_screens(decoded) != payload:
        raise AssertionError("World 2 zero-edit payload did not round-trip")
    if len(decoded["screens"]) != 119 or any(
        len(screen) != 16 for screen in decoded["screens"]
    ):
        raise AssertionError("World 2 selector geometry differs")
    from scripts.authoring.world2_level_model import World2ScreenDocument

    reference = World2ScreenDocument.load(
        root / "data/world2/compressed_screens.json"
    )
    for selector in (0, 50):
        decoded["screens"][selector][0][0] = 1
    repacked = encode_screens(decoded)
    reference.paint(0, 0, 0, 1)
    if repacked != reference.encode():
        raise AssertionError("World 2 edited repack differs from source model")
    print(f"World 2: {PAYLOAD_SIZE} bytes, CRC32 {checksum}, 119 exact selectors")
    print("World 2: aliased cell edit, exact fixed-capacity source repack")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
