"""Command-line interface for Retrolarium Kit (rltool).

Commands are grouped by what they work on: project and build-tools
(cli/project.py), game (cli/game.py), studio (cli/studio.py), audio
(cli/audio.py), lint and hooks (cli/lint.py) and movie (cli/movie.py). Each
command imports the modules it runs, so `rltool --help` and the Studio's
frequent short calls do not pay for loading every game's codecs.
"""

from __future__ import annotations

import argparse
import sys

from retrolarium_kit import __version__
from retrolarium_kit.cli import audio, game, lint, movie, project, studio


# The errors a command reports as a one-line message, by the module that
# defines them. A module that was never imported cannot have raised.
EXPECTED_ERRORS = {
    "retrolarium_core.contracts": "ContractError",
    "retrolarium_kit.core.authoring": "AuthoringError",
    "retrolarium_kit.core.build_toolchains": "BuildToolchainError",
    "retrolarium_kit.core.lint.issues": "LintError",
    "retrolarium_kit.core.movies": "MovieError",
    "retrolarium_kit.core.project": "ProjectError",
    "retrolarium_kit.studio.project": "StudioProjectError",
}


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="rltool", description=__doc__.split("\n", 1)[0])
    root.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = root.add_subparsers(dest="command", required=True)
    for group in (project, game, studio, audio, lint, movie):
        group.add_parsers(commands)
    return root


def _expected_errors() -> tuple[type[BaseException], ...]:
    errors: list[type[BaseException]] = [OSError]
    for module, name in EXPECTED_ERRORS.items():
        loaded = sys.modules.get(module)
        error = getattr(loaded, name, None) if loaded is not None else None
        if isinstance(error, type):
            errors.append(error)
    return tuple(errors)


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        args.handler(args)
    except Exception as exc:
        if not isinstance(exc, _expected_errors()):
            raise
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0
