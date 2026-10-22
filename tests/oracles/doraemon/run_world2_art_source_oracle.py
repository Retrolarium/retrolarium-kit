"""Read-only World 2 metatile/palette oracle against Doraemon source data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import zlib


sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from retrolarium_kit.games.doraemon.world2.metatiles import (
    decode_metatiles,
    encode_metatiles,
)


def check(label: str, payload: bytes, size: int, crc: str) -> None:
    actual_crc = f"{zlib.crc32(payload) & 0xFFFFFFFF:08x}"
    if len(payload) != size or actual_crc != crc:
        raise AssertionError(f"{label} differs: size={len(payload)}, crc32={actual_crc}")
    print(f"{label}: {size} bytes, CRC32 {crc}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True, help="exact path to doraemon_src")
    args = parser.parse_args()
    root = args.source_root.resolve()
    if root.name != "doraemon_src":
        parser.error("--source-root must point directly to doraemon_src")

    palettes = json.loads((root / "data/world2/palettes.json").read_text(encoding="utf-8"))
    records = sorted(palettes["palettes"], key=lambda item: item["id"])
    if [record["id"] for record in records] != list(range(9)):
        raise AssertionError("World 2 palette IDs differ")
    palette_payload = bytes(int(color, 16) for record in records for color in record["colors"])
    check("World 2 palette sets", palette_payload, 144, "89d67e1f")

    metatiles = json.loads((root / "data/world2/metatiles.json").read_text(encoding="utf-8"))
    records = sorted(metatiles["records"], key=lambda item: item["id"])
    if [record["id"] for record in records] != list(range(208)):
        raise AssertionError("World 2 metatile IDs differ")
    value = {
        "palettes": [record["palette"] for record in records],
        "tiles": [
            [int(record[key], 16) for key in ("top_left", "top_right", "bottom_left", "bottom_right")]
            for record in records
        ],
        "solid": [record["solid"] for record in records],
    }
    visual, collision = encode_metatiles(value)
    check("World 2 palette selectors", visual[:208], 208, "2e118ad3")
    check("World 2 tile quads", visual[208:], 832, "a0a46c1f")
    check("World 2 collision bits", collision, 26, "38c00148")
    if decode_metatiles(visual, collision) != value:
        raise AssertionError("World 2 metatiles did not round-trip")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
