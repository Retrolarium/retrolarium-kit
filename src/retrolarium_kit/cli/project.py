"""project: validate, build, verify and launch generated projects; build-tools: the
compiler suites bundled with the kit.

Each command imports what it runs, so the CLI starts without loading every
codec."""

from __future__ import annotations

import argparse
from pathlib import Path

from retrolarium_kit.cli.common import print_json


def command_project_validate(args: argparse.Namespace) -> None:
    from retrolarium_core.contracts import load_disassembly_manifest

    manifest = load_disassembly_manifest(args.manifest)
    print_json(
        {
            "status": "valid",
            "schema": manifest["schema"],
            "schema_version": manifest["schema_version"],
            "manifest": str(args.manifest.resolve()),
        }
    )


def command_project_build(args: argparse.Namespace) -> None:
    from retrolarium_kit.core.project import build_project

    print_json(
        build_project(
            args.manifest,
            container=args.container,
            toolchain_cache=args.toolchain_cache,
        )
    )


def command_project_verify(args: argparse.Namespace) -> None:
    from retrolarium_kit.core.project import ProjectError, verify_project

    result = verify_project(args.manifest)
    print_json(result)
    if result["status"] != "exact":
        raise ProjectError("project artifact does not match the manifest input")


def command_project_launch_plan(args: argparse.Namespace) -> None:
    from retrolarium_kit.core.project import launch_plan

    print_json(
        launch_plan(
            args.manifest,
            mame=args.mame,
            machine=args.machine,
            attach_window=args.attach_window,
        )
    )


def command_project_launch(args: argparse.Namespace) -> None:
    from retrolarium_kit.core.project import ProjectError, launch_project_smoke

    result = launch_project_smoke(
        args.manifest,
        mame=args.mame,
        machine=args.machine,
        seconds=args.seconds,
        timeout=args.timeout,
        output=args.output,
    )
    print_json(result)
    if result["status"] != "completed":
        raise ProjectError(f"MAME smoke launch ended with {result['status']}")


def command_build_tools_status(args: argparse.Namespace) -> None:
    from retrolarium_kit.core.build_toolchains import packaged_toolchain_status

    print_json(packaged_toolchain_status(args.host))


def command_build_tools_materialize(args: argparse.Namespace) -> None:
    from retrolarium_kit.core.build_toolchains import materialize_build_toolchain

    print_json(materialize_build_toolchain(args.suite, cache_root=args.cache, host=args.host).as_dict())


def command_build_tools_path(args: argparse.Namespace) -> None:
    from retrolarium_kit.core.build_toolchains import materialize_build_toolchain

    suite = materialize_build_toolchain(args.suite, cache_root=args.cache, host=args.host)
    print(suite.command(args.tool))


def add_parsers(commands: argparse._SubParsersAction) -> None:
    project = commands.add_parser("project", help="validate and operate on generated Retrolarium projects")
    project_commands = project.add_subparsers(dest="project_command", required=True)
    project_validate = project_commands.add_parser("validate")
    project_validate.add_argument("manifest", type=Path)
    project_validate.set_defaults(handler=command_project_validate)
    project_build = project_commands.add_parser("build")
    project_build.add_argument("manifest", type=Path)
    project_build.add_argument("--container")
    project_build.add_argument("--toolchain-cache", type=Path)
    project_build.set_defaults(handler=command_project_build)
    project_verify = project_commands.add_parser("verify")
    project_verify.add_argument("manifest", type=Path)
    project_verify.set_defaults(handler=command_project_verify)
    project_launch_plan = project_commands.add_parser("launch-plan")
    project_launch_plan.add_argument("manifest", type=Path)
    project_launch_plan.add_argument("--mame", type=Path, required=True)
    project_launch_plan.add_argument("--machine")
    project_launch_plan.add_argument("--attach-window")
    project_launch_plan.set_defaults(handler=command_project_launch_plan)
    project_launch = project_commands.add_parser("launch")
    project_launch.add_argument("manifest", type=Path)
    project_launch.add_argument("--mame", type=Path, required=True)
    project_launch.add_argument("--machine")
    project_launch.add_argument("--seconds", type=int, default=2)
    project_launch.add_argument("--timeout", type=int, default=60)
    project_launch.add_argument("--output", type=Path)
    project_launch.set_defaults(handler=command_project_launch)

    build_tools = commands.add_parser("build-tools", help="use compiler suites shipped inside the Python wheel")
    build_tool_commands = build_tools.add_subparsers(dest="build_tools_command", required=True)
    build_tool_status = build_tool_commands.add_parser(
        "status", help="verify bundled payloads and show host availability"
    )
    build_tool_status.add_argument("--host")
    build_tool_status.set_defaults(handler=command_build_tools_status)
    build_tool_materialize = build_tool_commands.add_parser(
        "materialize", help="copy a verified suite into the local tool cache"
    )
    build_tool_materialize.add_argument("suite", choices=("nes", "megadrive"))
    build_tool_materialize.add_argument("--host")
    build_tool_materialize.add_argument("--cache", type=Path)
    build_tool_materialize.set_defaults(handler=command_build_tools_materialize)
    build_tool_path = build_tool_commands.add_parser("path", help="print one verified compiler command path")
    build_tool_path.add_argument("suite", choices=("nes", "megadrive"))
    build_tool_path.add_argument("tool")
    build_tool_path.add_argument("--host")
    build_tool_path.add_argument("--cache", type=Path)
    build_tool_path.set_defaults(handler=command_build_tools_path)
