"""game: inspect, extract, validate and build resources through game profiles.

Each command imports what it runs, so the CLI starts without loading every
codec."""

from __future__ import annotations

import argparse
from pathlib import Path

from retrolarium_kit.cli.common import print_json


def command_game_profiles(_args: argparse.Namespace) -> None:
    from retrolarium_kit.games.catalog import profile_catalog

    print_json(profile_catalog())


def command_game_inspect(args: argparse.Namespace) -> None:
    from retrolarium_kit.games.catalog import inspect_game

    print_json(inspect_game(args.rom, custom_profile=args.profile))


def command_game_extract(args: argparse.Namespace) -> None:
    from retrolarium_kit.games.catalog import extract_game

    print_json(
        extract_game(
            args.rom,
            args.output,
            custom_profile=args.profile,
            resource=args.resource,
        )
    )


def command_game_validate(args: argparse.Namespace) -> None:
    from retrolarium_kit.games.catalog import validate_game

    print_json(
        validate_game(args.rom, args.document, custom_profile=args.profile)
    )


def command_game_build(args: argparse.Namespace) -> None:
    from retrolarium_kit.games.catalog import build_game

    print_json(
        build_game(
            args.rom,
            args.document,
            args.output,
            custom_profile=args.profile,
            report_path=args.report,
        )
    )


def command_game_audit_smb_migration(args: argparse.Namespace) -> None:
    from retrolarium_kit.games.smb.migration import audit_smb_migration

    print_json(audit_smb_migration(
        args.legacy_source, args.revision, profile_path=args.profile))


def command_game_launch_plan(args: argparse.Namespace) -> None:
    from retrolarium_kit.games.catalog import game_launch_plan

    print_json(
        game_launch_plan(
            args.rom,
            args.artifact,
            mame=args.mame,
            machine=args.machine,
            attach_window=args.attach_window,
            custom_profile=args.profile,
        )
    )


def command_game_launch(args: argparse.Namespace) -> None:
    from retrolarium_kit.core.project import ProjectError
    from retrolarium_kit.games.catalog import launch_game_smoke

    result = launch_game_smoke(
        args.rom,
        args.artifact,
        mame=args.mame,
        output=args.output,
        machine=args.machine,
        seconds=args.seconds,
        timeout=args.timeout,
        custom_profile=args.profile,
    )
    print_json(result)
    if result["status"] != "completed":
        raise ProjectError(f"MAME smoke launch ended with {result['status']}")


def add_parsers(commands: argparse._SubParsersAction) -> None:
    game = commands.add_parser(
        "game", help="inspect and edit resources through declarative game profiles"
    )
    game_commands = game.add_subparsers(dest="game_command", required=True)
    game_profiles = game_commands.add_parser(
        "profiles", help="list game profiles carried by the package"
    )
    game_profiles.set_defaults(handler=command_game_profiles)
    game_inspect = game_commands.add_parser(
        "inspect", help="recognize a revision and validate its resource layout"
    )
    game_inspect.add_argument("rom", type=Path)
    game_inspect.add_argument("--profile", type=Path)
    game_inspect.set_defaults(handler=command_game_inspect)
    game_extract = game_commands.add_parser(
        "extract", help="extract editable resources to a versioned JSON document"
    )
    game_extract.add_argument("rom", type=Path)
    game_extract.add_argument("--output", type=Path, required=True)
    game_extract.add_argument("--profile", type=Path)
    game_extract.add_argument(
        "--resource",
        help="authoring resource to extract when the profile exposes several",
    )
    game_extract.set_defaults(handler=command_game_extract)
    game_validate = game_commands.add_parser(
        "validate", help="validate an edited document and its complete build plan"
    )
    game_validate.add_argument("rom", type=Path)
    game_validate.add_argument("document", type=Path)
    game_validate.add_argument("--profile", type=Path)
    game_validate.set_defaults(handler=command_game_validate)
    game_build = game_commands.add_parser(
        "build", help="transactionally compose an edited image"
    )
    game_build.add_argument("rom", type=Path)
    game_build.add_argument("document", type=Path)
    game_build.add_argument("--output", type=Path, required=True)
    game_build.add_argument("--report", type=Path)
    game_build.add_argument("--profile", type=Path)
    game_build.set_defaults(handler=command_game_build)
    game_audit_smb = game_commands.add_parser(
        "audit-smb-migration",
        help="audit Retrolarium resource coverage of the retired SMB Studios",
    )
    game_audit_smb.add_argument("revision")
    game_audit_smb.add_argument("--legacy-source", type=Path, required=True)
    game_audit_smb.add_argument("--profile", type=Path)
    game_audit_smb.set_defaults(handler=command_game_audit_smb_migration)
    game_launch_plan_parser = game_commands.add_parser(
        "launch-plan", help="create a validated MAME process-spec for an edited ROM"
    )
    game_launch_plan_parser.add_argument("rom", type=Path)
    game_launch_plan_parser.add_argument("artifact", type=Path)
    game_launch_plan_parser.add_argument("--mame", type=Path, required=True)
    game_launch_plan_parser.add_argument("--machine")
    game_launch_plan_parser.add_argument("--attach-window")
    game_launch_plan_parser.add_argument("--profile", type=Path)
    game_launch_plan_parser.set_defaults(handler=command_game_launch_plan)
    game_launch = game_commands.add_parser(
        "launch", help="run a bounded headless MAME smoke on an edited ROM"
    )
    game_launch.add_argument("rom", type=Path)
    game_launch.add_argument("artifact", type=Path)
    game_launch.add_argument("--mame", type=Path, required=True)
    game_launch.add_argument("--output", type=Path, required=True)
    game_launch.add_argument("--machine")
    game_launch.add_argument("--seconds", type=int, default=2)
    game_launch.add_argument("--timeout", type=int, default=60)
    game_launch.add_argument("--profile", type=Path)
    game_launch.set_defaults(handler=command_game_launch)
