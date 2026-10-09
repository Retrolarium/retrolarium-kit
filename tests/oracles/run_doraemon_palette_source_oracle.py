"""Read-only palette and room-selector oracle against Doraemon source data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import zlib


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from retrolarium_kit.games.doraemon.palettes import decode_room_selectors, encode_room_selectors


def check_payload(label: str, payload: bytes, size: int, crc: str) -> None:
    actual_crc = f"{zlib.crc32(payload) & 0xffffffff:08x}"
    if len(payload) != size or actual_crc != crc:
        raise AssertionError(
            f"{label} source differs: size={len(payload)}, crc32={actual_crc}"
        )
    print(f"{label}: {size} bytes, CRC32 {crc}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True,
                        help="exact path to doraemon_src (not its parent)")
    args = parser.parse_args()
    root = args.source_root.resolve()
    if root.name != "doraemon_src":
        parser.error("--source-root must point directly to doraemon_src")

    world1 = json.loads((root / "data/world1/palettes.json").read_text(encoding="utf-8"))
    palettes1 = sorted(world1["palettes"], key=lambda item: item["id"])
    payload1 = bytes(
        int(color, 16)
        for palette in palettes1
        for color in palette["colors"].split()
    )
    check_payload("world1 palettes", payload1, 384, "f26c2516")

    world3 = json.loads((root / "data/world3/metasprites.json").read_text(encoding="utf-8"))
    palettes3 = sorted(world3["palettes"], key=lambda item: item["id"])
    payload3 = bytes(
        int(color, 16)
        for palette in palettes3
        for color in palette["colors"]
    )
    check_payload("world3 palettes", payload3, 352, "81e21c09")

    rooms = sorted(world3["room_palettes"], key=lambda item: item["room"])
    selectors = [item["palette"] for item in rooms]
    encoded = encode_room_selectors(selectors)
    check_payload("world3 room selectors", encoded, 64, "c0335e41")
    if decode_room_selectors(encoded) != selectors:
        raise AssertionError("world3 room selectors did not round-trip")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
