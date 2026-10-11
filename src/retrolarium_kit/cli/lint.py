"""lint: check (or with --fix, apply) a game repository's house style."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


def command_lint(args: argparse.Namespace) -> None:
    from retrolarium_kit.core.lint import LintError, load_config, run

    root = args.project.resolve()
    config = load_config(root, args.config)
    paths = tuple(str(path.resolve().relative_to(root) if path.is_absolute() else path)
                  for path in args.paths)
    issues = run(root, config, fix=args.fix, only=tuple(args.only or ()), paths=paths)
    if issues:
        limit = args.limit
        for issue in issues[:limit] if limit else issues:
            print(issue.render(root), file=sys.stderr)
        if limit and len(issues) > limit:
            print(f"... and {len(issues) - limit} more", file=sys.stderr)
        raise LintError(f"{len(issues)} style issue(s)")


def add_parsers(commands: argparse._SubParsersAction) -> None:
    lint = commands.add_parser(
        "lint", help="check a game repository's house style (config/lint.json)"
    )
    lint.add_argument("--project", type=Path, default=Path("."),
                      help="repository root (default: the current directory)")
    lint.add_argument("--config", type=Path, help="lint config (default: config/lint.json)")
    lint.add_argument("--fix", action="store_true", help="normalize the files before checking them")
    lint.add_argument("--only", action="append", choices=("assembly", "text"),
                      help="run one section (repeatable)")
    lint.add_argument("--limit", type=int, default=200, help="issues to print (0: all)")
    lint.add_argument("paths", nargs="*", type=Path,
                      help="assembly files or folders to check instead of the config's")
    lint.set_defaults(handler=command_lint)
