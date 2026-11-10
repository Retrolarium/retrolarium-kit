"""asm: inspect assembly sources for similar instruction blocks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def command_similar(args: argparse.Namespace) -> None:
    from retrolarium_kit.core.assembly_similarity import scan

    report = scan(
        args.root,
        args.paths,
        platform=args.platform,
        minimum=args.min_instructions,
        maximum=args.max_instructions,
        threshold=args.threshold,
        operand_threshold=args.operand_threshold,
        limit=args.limit,
        left=args.left,
        right=args.right,
        include=args.include,
        exclude=args.exclude,
        max_candidates=args.max_candidates,
    )
    if args.json:
        destination = args.json.resolve()
        # Output cannot replace an assembly input.
        if destination.suffix.lower() in {".s", ".asm", ".inc", ".a", ".a65"}:
            from retrolarium_kit.core.assembly_similarity import AssemblySimilarityError

            raise AssemblySimilarityError("--json must name a report file, not an assembly source")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    counts = report["counts"]
    print(
        f"{report['cpu']}: {counts['files']} files, {counts['routines']} routine blocks, "
        f"{counts['matches']} matches ({counts['reported']} shown)"
    )
    print("Scores compare source instructions and operands. Confirm candidates against the assembled game.")
    for row in report["matches"]:
        left, right = row["left"], row["right"]
        print(
            f"{row['shape_score']:.1%}/{row['operand_score']:.1%} {row['kind']}\n"
            f"  {left['path']}:{left['first_line']} {left['name']} ({left['instructions']} instructions)\n"
            f"  {right['path']}:{right['first_line']} {right['name']} ({right['instructions']} instructions)"
        )


def add_parsers(commands: argparse._SubParsersAction) -> None:
    group = commands.add_parser("asm", help="inspect assembly sources")
    actions = group.add_subparsers(dest="asm_command", required=True)
    similar = actions.add_parser("similar", help="find similar 6502, 65816 and 68000 routine blocks")
    similar.add_argument("paths", nargs="*", help="source files or directories below --root (default: src)")
    similar.add_argument("--root", type=Path, default=Path.cwd(), help="source repository root")
    similar.add_argument("--platform", required=True, choices=("nes", "snes", "genesis", "megadrive"))
    similar.add_argument("--min-instructions", type=int, default=6)
    similar.add_argument("--max-instructions", type=int, default=256)
    similar.add_argument("--threshold", type=float, default=0.8, help="minimum instruction-shape similarity")
    similar.add_argument("--operand-threshold", type=float, default=0.5, help="minimum operand similarity")
    similar.add_argument("--limit", type=int, default=100, help="maximum matches to show")
    similar.add_argument("--max-candidates", type=int, default=250_000, help="fail above this candidate pair count")
    similar.add_argument(
        "--include", action="append", default=[], metavar="PATTERN", help="include source path wildcard"
    )
    similar.add_argument(
        "--exclude", action="append", default=[], metavar="PATTERN", help="exclude source path wildcard"
    )
    similar.add_argument("--left", help="require one side to match this source path wildcard")
    similar.add_argument("--right", help="require the other side to match this source path wildcard")
    similar.add_argument("--json", type=Path, help="write the structured report here")
    similar.set_defaults(handler=command_similar)
