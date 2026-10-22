"""Compare native Mega Drive tile pixels with Flicky's tracked source assets."""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--native-model-exe", type=Path, required=True)
    args = parser.parse_args()
    root = args.source_root.resolve()
    sys.path.insert(0, str(root / "scripts"))
    sys.path.insert(0, str(root / "scripts" / "authoring"))
    from graphics_studio_model import (  # type: ignore[import-not-found]
        export_document,
        tiles_to_font,
        tiles_to_packed_4bpp,
    )

    document = export_document(root)
    counts = {}
    for kind, mode, packer in (
        ("tiles_4bpp", "--decode-4bpp", tiles_to_packed_4bpp),
        ("tiles_1bpp", "--decode-1bpp", tiles_to_font),
    ):
        tiles = [tile for asset in document["assets"] if asset["kind"] == kind for tile in asset["tiles"]]
        stride = 32 if kind == "tiles_4bpp" else 8
        plain = packer(tiles)
        request = "".join(plain[offset : offset + stride].hex() + "\n" for offset in range(0, len(plain), stride))
        result = subprocess.run(
            [str(args.native_model_exe.resolve()), mode],
            input=request,
            text=True,
            capture_output=True,
            check=True,
        )
        native = result.stdout.splitlines()
        if len(native) != len(tiles):
            raise AssertionError(f"{kind}: native tile count differs")
        for index, (expected_tile, pixels) in enumerate(zip(tiles, native)):
            expected = "".join(format(value, "x") for row in expected_tile for value in row)
            if pixels != expected:
                raise AssertionError(f"{kind} tile {index} differs from source")
        counts[kind] = len(tiles)
    print(f"Flicky native pixels match source: {counts['tiles_4bpp']} 4bpp tiles and {counts['tiles_1bpp']} font tiles")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
