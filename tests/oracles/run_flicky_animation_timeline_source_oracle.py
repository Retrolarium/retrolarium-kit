#!/usr/bin/env python3
"""Check every Flicky animation frame resolves to a source-backed preview."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import tempfile

from retrolarium_kit.games.flicky.content import WORKSPACE_FILES, import_flicky_source


def table_words(source: str, label: str) -> list[int] | None:
    match = re.search(
        rf"^{re.escape(label)}:\s*dc\.w\s+([^;\r\n]+)", source, re.MULTILINE)
    if match is None:
        return None
    words = []
    for token in match.group(1).split(","):
        token = token.strip()
        words.append(int(token[1:], 16) if token.startswith("$") else int(token))
    return words


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--rom", type=Path, required=True)
    args = parser.parse_args()
    scratch = Path(__file__).resolve().parents[2] / "build"
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="flicky-timeline-", dir=scratch) as name:
        root = Path(name)
        import_flicky_source(args.source_root, args.rom, root)
        source = root / "flicky-source"
        document = json.loads((source / WORKSPACE_FILES["graphics_sequences"])
                              .read_text(encoding="utf-8"))
        mappings = {item["id"] for item in document["mappings"]}
        assert len(mappings) == 224
        sprite = [item for item in document["animations"]
                  if item["kind"] == "sprite"]
        tilemap = [item for item in document["animations"]
                   if item["kind"] == "tilemap"]
        assert len(sprite) == 44 and len(tilemap) == 6
        for animation in sprite:
            assert animation["frames"] and 0 <= animation["delay"] <= 255
            assert set(animation["frames"]) <= mappings, animation["id"]
        tables = (source / "src/data/tables.s").read_text(encoding="utf-8")
        hud = (source / "src/rendering/hud.s").read_text(encoding="utf-8")
        frames = {frame for animation in tilemap for frame in animation["frames"]}
        assert len(frames) == 12
        for animation in tilemap:
            assert animation["frames"] and 0 <= animation["delay"] <= 255
        for frame in frames:
            words = table_words(tables, frame)
            if words is None:
                words = table_words(hud, frame)
            assert words is not None and len(words) in (3, 9), frame
            assert all(0 <= word <= 0xFFFF for word in words), frame
        print(f"Flicky animation preview inventory: {len(sprite)} sprite + "
              f"{len(tilemap)} tilemap animations, {len(frames)} distinct "
              "UI source frames (3 or 9 VDP words each)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
