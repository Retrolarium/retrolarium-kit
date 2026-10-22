"""Compare native Flicky collision decode/encode with the tracked source model.

No ROM or private workspace is copied into this repository. The neighboring
source project exports its 36 original layouts in memory for this gate.
"""

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

    source_root = args.source_root.resolve()
    native_exe = args.native_model_exe.resolve()
    sys.path.insert(0, str(source_root / "scripts" / "authoring"))
    from level_studio_model import (  # type: ignore[import-not-found]
        decode_collision,
        encode_collision,
        export_document,
    )

    document = export_document(source_root)
    layouts = document["layouts"]
    if len(layouts) != 36 or len(document["round_layouts"]) != 48:
        raise AssertionError("Flicky source did not export 36 layouts/48 rounds")
    streams = [layout["baseline_collision_stream"] for layout in layouts]
    request = "".join(bytes(stream).hex() + "\n" for stream in streams)
    result = subprocess.run(
        [str(native_exe), "--oracle"],
        input=request,
        text=True,
        capture_output=True,
        check=True,
    )
    lines = result.stdout.splitlines()
    if len(lines) != 36:
        raise AssertionError(f"native model returned {len(lines)} layouts, not 36")
    for index, (stream, output) in enumerate(zip(streams, lines)):
        native_cells, native_encoded = output.split("\t")
        source_grid = decode_collision(stream)
        source_cells = "".join(str(cell) for row in source_grid for cell in row)
        if native_cells != source_cells:
            raise AssertionError(f"layout {index} native collision grid differs")
        if native_encoded != bytes(encode_collision(source_grid)).hex():
            raise AssertionError(f"layout {index} native canonical encoder differs")
    edits = []
    for layout in layouts:
        grid = [row[:] for row in layout["collision"]]
        grid[2][0] ^= 1
        edits.append(grid)
    edited_request = "".join("".join(str(cell) for row in grid for cell in row) + "\n" for grid in edits)
    edited = subprocess.run(
        [str(native_exe), "--encode-grid"],
        input=edited_request,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.splitlines()
    if len(edited) != 36:
        raise AssertionError("native model omitted an edited layout")
    for index, (grid, encoded) in enumerate(zip(edits, edited)):
        if encoded != bytes(encode_collision(grid)).hex():
            raise AssertionError(f"layout {index} edited native encoder differs")
        if decode_collision(list(bytes.fromhex(encoded))) != grid:
            raise AssertionError(f"layout {index} edited collision did not round-trip")
    print("Flicky: 36 source layouts and 36 one-cell edits match native codec")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
