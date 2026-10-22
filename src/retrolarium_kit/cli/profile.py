"""rltool profile: what a game's profile records about its built source."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from retrolarium_kit.cli.common import print_json


def command_profile_tables(args: argparse.Namespace) -> None:
    from retrolarium_kit.platform.nes import profile_tables

    root = Path(args.root) if args.root else Path.cwd()
    spec = profile_tables.load_spec(root, args.spec)
    if args.list:
        print("\n".join(profile_tables.listing(root, spec, args.list)))
        return
    revisions = set(args.revision) if args.revision else None
    if args.check:
        problems = profile_tables.check(root, spec, revisions)
        for problem in problems:
            print(problem, file=sys.stderr)
        if problems:
            raise SystemExit(1)
        print("[OK] config/game_profile.json has the tables the builds give")
        return
    print_json(profile_tables.resolve_all(root, spec, revisions))


def add_parsers(commands: argparse._SubParsersAction) -> None:
    group = commands.add_parser("profile", help="what a game's profile records about its built source")
    actions = group.add_subparsers(dest="profile_command", required=True)
    tables = actions.add_parser(
        "tables", help="where the tables of config/profile_tables.json lie in each revision's build"
    )
    tables.add_argument("--root", help="the game repository (default: the current folder)")
    tables.add_argument("--spec", type=Path, help="the spec (default: config/profile_tables.json)")
    tables.add_argument("--revision", action="append", help="only this revision (repeatable)")
    tables.add_argument(
        "--check", action="store_true", help="compare with config/game_profile.json and fail where they differ"
    )
    tables.add_argument(
        "--list",
        metavar="REVISION:LABEL",
        help="print the immediates a revision assembles after a label, to write the spec",
    )
    tables.set_defaults(handler=command_profile_tables)
