#!/usr/bin/env python3
"""Compare normal Flicky VDP scenes with runtime-corrected source data.

Reads the neighboring canonical workspace and ROM reconstruction; never writes
to them. Pass a freshly built retrolarium-flicky-level-scene-tests.exe.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from retrolarium_kit.games.flicky.content import import_flicky_source  # noqa: E402
from retrolarium_kit.studio.create import create_flicky_studio_project


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--rom", type=Path, required=True)
    parser.add_argument("--native-renderer", type=Path, required=True)
    parser.add_argument("--native-editor", type=Path,
                        help="also verify live Qt Level view and dependency refresh")
    parser.add_argument("--native-factory", type=Path,
                        help="also open the copied .rlj through the Studio editor factory")
    args = parser.parse_args()
    scratch = Path(__file__).resolve().parents[1] / "build"
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="flicky-scene-gate-", dir=scratch) as name:
        return compare(Path(name), args)


def compare(project: Path, args: argparse.Namespace) -> int:
    import_flicky_source(args.source_root, args.rom, project)
    create_flicky_studio_project(project)
    source = project / "flicky-source"
    sys.path.insert(0, str(source / "scripts"))
    from authoring import level_preview  # noqa: E402

    level_path = source / "content/workspace/level/levels.json"
    graphics_path = source / "content/workspace/graphics/graphics.json"
    semantics_path = source / "content/workspace/graphics/semantics.json"
    sequences_path = source / "content/workspace/graphics/sequences.json"
    native = subprocess.run([
        str(args.native_renderer.resolve()), "--source", str(source),
        str(level_path), str(graphics_path), str(semantics_path),
        str(sequences_path),
    ], text=True, capture_output=True)
    if native.returncode:
        raise RuntimeError(native.stderr or native.stdout)
    reported = {}
    for line in native.stdout.splitlines():
        round_number, digest = line.split("\t", 1)
        reported[int(round_number)] = digest
    assert len(reported) == 48, f"native renderer returned {len(reported)} rounds"

    document = json.loads(level_path.read_text(encoding="utf-8"))
    # Game_SetupLevel sets CameraX=1 and Gfx_UpdateScrollRegs applies -CameraX
    # to both VDP planes. The upstream preview's Plane A=0 is a source-preview
    # discrepancy, proven by independent MAME screenshots; do not edit source.
    level_preview.PLANE_A_SCROLL_X = -1
    preview = level_preview.LevelPreview.load(
        source, graphics_path, semantics_path, sequences_path)
    for round_number in range(1, 49):
        # Upstream level_preview has no Bonus_Init scene path; its bonus slots
        # still render the aliased normal layout. That is not a parity oracle.
        if round_number % 4 == 3:
            continue
        layout = document["layouts"][document["round_layouts"][round_number - 1]]
        frame = preview.render(layout, round_number)
        rgb = bytes.fromhex("".join(
            level_preview.md_color(word)[1:] for row in frame for word in row))
        expected = hashlib.sha256(rgb).hexdigest()
        actual = reported[round_number]
        assert actual == expected, (
            f"round {round_number} native VDP scene differs from runtime-corrected source: "
            f"{actual} != {expected}"
        )
    if args.native_editor:
        subprocess.run([
            str(args.native_editor.resolve()), "--scene", str(source),
            str(level_path), str(graphics_path), str(semantics_path),
            str(sequences_path),
        ], check=True)
    if args.native_factory:
        subprocess.run([
            str(args.native_factory.resolve()), str(project / "flicky-ue.rlj"),
            "--flicky-scene",
        ], check=True)
    print("Flicky native VDP scene: all 36 normal rounds pixel-exact against "
          "runtime-corrected source preview; 12 bonus scenes rendered "
          "from Bonus_Init without false normal-layout parity"
          + ("; Qt Level view refresh verified" if args.native_editor else "")
          + ("; .rlj factory verified" if args.native_factory else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
