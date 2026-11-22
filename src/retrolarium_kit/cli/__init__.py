"""Command-line interface for Retrolarium Kit (rltool).

Commands are grouped by what they work on: project and build-tools
(cli/project.py), game (cli/game.py), studio (cli/studio.py), audio
(cli/audio.py), lint and hooks (cli/lint.py), movie (cli/movie.py), assets
(cli/assets.py), profile (cli/profile.py), fds (cli/fds.py) and assembly
analysis (cli/asm.py). Each
command imports the modules it runs, so `rltool --help` and the Studio's
frequent short calls do not pay for loading every game's codecs.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from retrolarium_kit import __version__
from retrolarium_kit.cli import asm, assets, audio, fds, game, lint, movie, profile, project, studio


# The errors a command reports as a one-line message, by the module that
# defines them: OSError, every KitError (all the kit's own refusals derive
# from it) and the core's errors. A module that was never imported cannot
# have raised, and the CLI starts without importing any of them.
EXPECTED_ERRORS = {
    "retrolarium_kit.core.errors": "KitError",
    "retrolarium_core.contracts": "ContractError",
    "retrolarium_core.fds": "FdsError",
    "retrolarium_core.ines": "InesError",
    "retrolarium_core.megadrive": "MegaDriveError",
    "retrolarium_core.snes": "SnesError",
}


# Consent to run game repositories' scripts (core/repository_trust.py): the
# flag, accepted anywhere on the command line, stands for the variable's `all`.
ALLOW_SCRIPTS_FLAG = "--allow-repository-scripts"
CONSENT_VARIABLE = "RETROLARIUM_ALLOW_REPOSITORY_SCRIPTS"
CONSENT_EXIT_CODE = 3


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="rltool", description=__doc__.split("\n", 1)[0])
    root.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    # Taken out of the command line by main() wherever it stands; listed here for --help.
    root.add_argument(
        ALLOW_SCRIPTS_FLAG,
        action="store_true",
        help=f"run game repositories' scripts without asking (as {CONSENT_VARIABLE}=all)",
    )
    commands = root.add_subparsers(dest="command", required=True)
    for group in (project, game, studio, audio, lint, movie, assets, profile, fds, asm):
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


def _consent_needed(exc: BaseException) -> dict[str, object] | None:
    """The consent-needed answer when `exc` asks for the user's consent."""
    trust = sys.modules.get("retrolarium_kit.core.repository_trust")
    needed = getattr(trust, "ConsentNeeded", None) if trust is not None else None
    return exc.document if isinstance(needed, type) and isinstance(exc, needed) else None


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    allow_scripts = ALLOW_SCRIPTS_FLAG in argv
    args = parser().parse_args([item for item in argv if item != ALLOW_SCRIPTS_FLAG])
    previous = os.environ.get(CONSENT_VARIABLE)
    if allow_scripts:
        os.environ[CONSENT_VARIABLE] = "all"
    try:
        # A command on a game's documents or ROM uses the profile its game
        # repository keeps, as the commands that open the repository do.
        from retrolarium_kit.core.game_profiles import discover_profiles

        discover_profiles(*_places(args))
        args.handler(args)
    except Exception as exc:
        document = _consent_needed(exc)
        if document is not None:
            # The only output on stdout: the Studio asks the user and runs
            # the command again with RETROLARIUM_ALLOW_REPOSITORY_SCRIPTS.
            print(json.dumps(document, indent=2, sort_keys=True))
            print(f"error: {exc}", file=sys.stderr)
            return CONSENT_EXIT_CODE
        if not isinstance(exc, _expected_errors()):
            raise
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        if allow_scripts:
            if previous is None:
                os.environ.pop(CONSENT_VARIABLE, None)
            else:
                os.environ[CONSENT_VARIABLE] = previous
    return 0
