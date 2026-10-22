"""Command-line interface for Retrolarium Kit (rltool).

Commands are grouped by what they work on: project and build-tools
(cli/project.py), game (cli/game.py), studio (cli/studio.py), audio
(cli/audio.py), lint and hooks (cli/lint.py), movie (cli/movie.py), assets
(cli/assets.py) and profile (cli/profile.py). Each
command imports the modules it runs, so `rltool --help` and the Studio's
frequent short calls do not pay for loading every game's codecs.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from retrolarium_kit import __version__
from retrolarium_kit.cli import assets, audio, game, lint, movie, profile, project, studio


# The errors a command reports as a one-line message, by the module that
# defines them. A module that was never imported cannot have raised.
EXPECTED_ERRORS = {
    "retrolarium_core.contracts": "ContractError",
    "retrolarium_core.fds": "FdsError",
    "retrolarium_core.ines": "InesError",
    "retrolarium_core.megadrive": "MegaDriveError",
    "retrolarium_core.snes": "SnesError",
    "retrolarium_kit.core.assets": "AssetsError",
    "retrolarium_kit.core.authoring": "AuthoringError",
    "retrolarium_kit.core.build_toolchains": "BuildToolchainError",
    "retrolarium_kit.core.game_names": "GameNamesError",
    "retrolarium_kit.core.lint.issues": "LintError",
    "retrolarium_kit.core.movies": "MovieError",
    "retrolarium_kit.core.project": "ProjectError",
    "retrolarium_kit.platform.nes.profile_tables": "ProfileTablesError",
    "retrolarium_kit.games.doraemon.sound_synth": "SynthError",
    "retrolarium_kit.games.flicky.content": "FlickyContentError",
    "retrolarium_kit.games.flicky.workspace.content": "FlickyWorkspaceError",
    "retrolarium_kit.studio.project": "StudioProjectError",
}


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="rltool", description=__doc__.split("\n", 1)[0])
    root.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = root.add_subparsers(dest="command", required=True)
    for group in (project, game, studio, audio, lint, movie, assets, profile):
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


def _places(args: argparse.Namespace) -> list[Path]:
    """The files and folders a command was given, and the current folder."""
    places = [Path.cwd()]
    for value in vars(args).values():
        items = value if isinstance(value, (list, tuple)) else [value]
        places.extend(item for item in items if isinstance(item, Path))
    return places


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        # A command on a game's documents or ROM uses the profile its game
        # repository keeps, as the commands that open the repository do.
        from retrolarium_kit.core.game_profiles import discover_profiles

        discover_profiles(*_places(args))
        args.handler(args)
    except Exception as exc:
        if not isinstance(exc, _expected_errors()):
            raise
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0
