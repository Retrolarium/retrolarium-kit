"""studio: create, upgrade, build and verify .rlj Studio projects, open game
repositories, and plan the MAME runs the Studio starts.

Each command imports what it runs, so the CLI starts without loading every
codec."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from retrolarium_kit.cli.common import print_json


def command_studio_create(args: argparse.Namespace) -> None:
    from retrolarium_kit.studio.create import create_studio_project

    print_json(
        create_studio_project(
            args.manifest,
            args.output,
            project_id=args.project_id,
            name=args.name,
            profile=args.profile,
            source_mode=args.source_mode,
            resource_document=args.resource_document,
        )
    )


def command_studio_flicky_create(args: argparse.Namespace) -> None:
    from retrolarium_kit.games.flicky.content import FlickyContentError, import_flicky_source
    from retrolarium_kit.studio.create import create_flicky_studio_project
    from retrolarium_kit.studio.project import StudioProjectError

    try:
        import_flicky_source(args.source_root, args.rom, args.project_root)
    except FlickyContentError as exc:
        raise StudioProjectError(str(exc)) from exc
    print_json(create_flicky_studio_project(args.project_root))


def command_studio_flicky_upgrade(args: argparse.Namespace) -> None:
    from retrolarium_kit.studio.create import upgrade_flicky_studio_project

    print_json(upgrade_flicky_studio_project(args.project, output=args.output))


def command_studio_solomon_upgrade(args: argparse.Namespace) -> None:
    from retrolarium_kit.studio.create import upgrade_solomon_studio_project

    print_json(upgrade_solomon_studio_project(args.project))


def command_studio_inspect(args: argparse.Namespace) -> None:
    from retrolarium_kit.studio.project import load_studio_project

    print_json(load_studio_project(args.project))


def command_studio_migrate(args: argparse.Namespace) -> None:
    from retrolarium_kit.studio.project import migrate_studio_project

    print_json(migrate_studio_project(
        args.project,
        output=args.output,
        **({} if args.target_version is None
           else {"target_version": args.target_version}),
    ))


def command_studio_validate(args: argparse.Namespace) -> None:
    from retrolarium_kit.studio.project import validate_studio_project

    print_json(validate_studio_project(args.project))


def command_studio_build(args: argparse.Namespace) -> None:
    from retrolarium_kit.studio.project import build_studio_project

    options = {"container": args.container, "toolchain_cache": args.toolchain_cache}
    if args.flicky_level_writer is not None:
        options["flicky_level_writer"] = args.flicky_level_writer
    result = build_studio_project(args.project, **options)
    if args.plan_json is not None:
        result = {**result, **_plan_after_build(args.plan_json)}
    print_json(result)


def _plan_after_build(plan_json: str) -> dict[str, object]:
    """Run a plan command (its argv as a JSON array) in this process.

    Play is a build followed by a plan; one process for both spares the
    Studio a second interpreter start and the imports. The plan's document
    comes back as "plan", or its error message as "plan_error".
    """
    import contextlib
    import io

    from retrolarium_kit.cli import _expected_errors, parser

    argv = json.loads(plan_json)
    if not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
        return {"plan_error": "--plan-json must be a JSON array of strings"}
    usage = io.StringIO()
    try:
        with contextlib.redirect_stderr(usage):
            plan_args = parser().parse_args(argv)
    except SystemExit:
        message = usage.getvalue().strip().splitlines()
        return {"plan_error": message[-1] if message else f"not a kit command: {' '.join(argv)}"}
    output = io.StringIO()
    try:
        with contextlib.redirect_stdout(output):
            plan_args.handler(plan_args)
    except Exception as exc:
        if not isinstance(exc, _expected_errors()):
            raise
        return {"plan_error": str(exc)}
    return {"plan": json.loads(output.getvalue())}


def command_studio_revisions(args: argparse.Namespace) -> None:
    from retrolarium_kit.studio.repository import load_descriptor, revision_statuses

    print_json(revision_statuses(load_descriptor(args.descriptor)))


def command_studio_prepare(args: argparse.Namespace) -> None:
    from retrolarium_kit.studio.repository import load_descriptor, prepare_revision

    print_json(prepare_revision(load_descriptor(args.descriptor), args.revision, rom=args.rom))


def command_studio_reset(args: argparse.Namespace) -> None:
    from retrolarium_kit.studio.repository import load_descriptor, reset_revision

    print_json(reset_revision(load_descriptor(args.descriptor), args.revision))


def command_studio_check(args: argparse.Namespace) -> None:
    from retrolarium_kit.studio.repository import check_repository, load_descriptor

    report = check_repository(load_descriptor(args.descriptor), args.revision or None,
                              flicky_level_writer=args.flicky_level_writer,
                              release=args.release)
    print_json(report)
    if report["status"] != "ok":
        raise SystemExit(1)


def command_studio_open_rom(args: argparse.Namespace) -> None:
    from retrolarium_kit.studio.repository import open_rom

    print_json(open_rom(args.rom, args.games, known=args.known or [], source=args.source))


def command_studio_identify(args: argparse.Namespace) -> None:
    from retrolarium_kit.studio.repository import identify_rom

    print_json({"schema": "retrolarium.rom-identity", "rom": str(args.rom), "matches": identify_rom(args.rom)})


def command_studio_fds_bios(args: argparse.Namespace) -> None:
    from retrolarium_kit.core.project import fds_bios, install_fds_bios

    if args.install is not None:
        print_json(install_fds_bios(args.install, args.mame))
        return
    found = fds_bios(args.mame)
    print_json({"schema": "retrolarium.fds-bios", "schema_version": 1,
                "path": None if found is None else str(found)})


def command_studio_patch(args: argparse.Namespace) -> None:
    from retrolarium_kit.studio.create import _project_source
    from retrolarium_kit.studio.project import build_studio_project, load_studio_project, rom_as_is
    from retrolarium_kit.studio.repository import ips_patch

    as_is = rom_as_is(args.project)
    if as_is is not None:
        # A ROM the Studio opened as it is: edited in place, nothing to build.
        original, edited = (path.read_bytes() for path in as_is)
    else:
        report = build_studio_project(args.project)
        root = Path(load_studio_project(args.project)["project_root"])
        original = _project_source(load_studio_project(args.project)).read_bytes()
        edited = (root / report["artifact"]["path"]).read_bytes()
    payload = ips_patch(original, edited)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(payload)
    print_json({"schema": "retrolarium.patch", "format": "ips", "output": str(args.output.resolve()),
                "bytes": len(payload), "changed_bytes": sum(a != b for a, b in zip(original, edited))
                + max(0, len(edited) - len(original))})


def command_studio_verify(args: argparse.Namespace) -> None:
    from retrolarium_kit.studio.project import StudioProjectError, verify_studio_project

    result = verify_studio_project(args.project)
    print_json(result)
    if result["status"] != "current":
        raise StudioProjectError("Studio artifact does not match current documents")


def command_studio_launch_plan(args: argparse.Namespace) -> None:
    from retrolarium_kit.studio.playtest import studio_launch_plan

    print_json(
        studio_launch_plan(
            args.project,
            mame=args.mame,
            machine=args.machine,
            attach_window=args.attach_window,
        )
    )


def command_studio_smb_point_playtest_plan(args: argparse.Namespace) -> None:
    from retrolarium_kit.studio.playtest import studio_smb_point_playtest_plan

    print_json(studio_smb_point_playtest_plan(
        args.project,
        mame=args.mame,
        course_bank=args.course_bank,
        area=args.area,
        world=args.world,
        course=args.course,
        column=args.column,
        row=args.row,
        theme=args.theme,
        infinite_time=not args.no_infinite_time,
        invincible=not args.no_invincible,
        wrap_pits=not args.no_wrap_pits,
        sfx_channel=args.sfx_channel,
        sfx_value=args.sfx_value,
    ))


def command_studio_flicky_round_playtest_plan(args: argparse.Namespace) -> None:
    from retrolarium_kit.studio.playtest import studio_flicky_round_playtest_plan

    print_json(studio_flicky_round_playtest_plan(
        args.project, mame=args.mame, round=args.round,
    ))


def command_studio_flicky_demo_plan(args: argparse.Namespace) -> None:
    from retrolarium_kit.studio.demo import studio_flicky_demo_plan

    print_json(studio_flicky_demo_plan(
        args.project, mame=args.mame, demo=args.demo, record=args.record,
    ))


def command_studio_flicky_sound_preview(args: argparse.Namespace) -> None:
    from retrolarium_kit.studio.playtest import studio_flicky_sound_preview

    print_json(studio_flicky_sound_preview(
        args.project, header=args.header, seconds=args.seconds,
        enabled_channels=tuple(args.channel) if args.channel else None,
        document_override=args.document_override,
    ))


def command_studio_doraemon_world2_view(args: argparse.Namespace) -> None:
    from retrolarium_kit.games.doraemon.world2.routes import route_view

    document = json.loads(args.document.read_text(encoding="utf-8"))
    resources = {
        item["id"]: {key: value for key, value in item.items() if key not in ("id", "kind", "codec")}
        for item in document["resources"]
    }
    view = route_view(
        resources["world2-screens"], resources["world2-stage"],
        resources["world2-branches"], resources["world2-chapters"],
    )
    args.output.write_text(json.dumps(view, separators=(",", ":")), encoding="utf-8")
    print_json({"status": "written", "output": str(args.output.resolve()),
                "routes": [route["name"] for route in view["routes"]]})


def command_studio_pacman_demo_plan(args: argparse.Namespace) -> None:
    from retrolarium_kit.studio.demo import studio_pacman_demo_plan

    print_json(studio_pacman_demo_plan(args.project, mame=args.mame, record=args.record))


def command_studio_doraemon_demo_plan(args: argparse.Namespace) -> None:
    from retrolarium_kit.studio.demo import studio_doraemon_demo_plan

    print_json(studio_doraemon_demo_plan(args.project, mame=args.mame, record=args.record))


def command_studio_doraemon_playtest_plan(args: argparse.Namespace) -> None:
    from retrolarium_kit.studio.playtest import studio_doraemon_playtest_plan

    print_json(studio_doraemon_playtest_plan(
        args.project, mame=args.mame, world=args.world,
        block=tuple(args.block) if args.block else None, stage=args.stage, room=args.room,
        underground=tuple(args.underground) if args.underground else None))


def command_studio_doraemon_upgrade(args: argparse.Namespace) -> None:
    from retrolarium_kit.studio.create import upgrade_doraemon_studio_project

    print_json(upgrade_doraemon_studio_project(args.project))


def command_studio_pacman_upgrade(args: argparse.Namespace) -> None:
    from retrolarium_kit.studio.create import upgrade_pacman_studio_project

    print_json(upgrade_pacman_studio_project(args.project))


def command_studio_pacman_round_playtest_plan(args: argparse.Namespace) -> None:
    from retrolarium_kit.studio.playtest import studio_pacman_round_playtest_plan

    print_json(studio_pacman_round_playtest_plan(args.project, mame=args.mame, round=args.round))


def command_studio_castle_excellent_point_playtest_plan(args: argparse.Namespace) -> None:
    from retrolarium_kit.studio.playtest import studio_castle_excellent_point_playtest_plan

    print_json(studio_castle_excellent_point_playtest_plan(
        args.project, mame=args.mame, room=args.room, x=args.x, y=args.y,
    ))


def command_studio_solomon_point_playtest_plan(args: argparse.Namespace) -> None:
    from retrolarium_kit.studio.playtest import studio_solomon_point_playtest_plan

    print_json(studio_solomon_point_playtest_plan(
        args.project, mame=args.mame, room=args.room,
    ))


def command_studio_smb_upgrade(args: argparse.Namespace) -> None:
    from retrolarium_kit.studio.create import upgrade_smb_studio_project

    print_json(upgrade_smb_studio_project(args.project))


def command_studio_castle_excellent_upgrade(args: argparse.Namespace) -> None:
    from retrolarium_kit.studio.create import upgrade_castle_excellent_studio_project

    print_json(upgrade_castle_excellent_studio_project(args.project))


def command_studio_castle_excellent_demo_plan(args: argparse.Namespace) -> None:
    from retrolarium_kit.studio.demo import studio_castle_excellent_demo_plan

    print_json(studio_castle_excellent_demo_plan(args.project, mame=args.mame, record=args.record))


def command_studio_smb_demo_plan(args: argparse.Namespace) -> None:
    from retrolarium_kit.studio.demo import studio_smb_demo_plan

    print_json(studio_smb_demo_plan(args.project, mame=args.mame, record=args.record))


def command_studio_solomon_demo_plan(args: argparse.Namespace) -> None:
    from retrolarium_kit.studio.demo import studio_solomon_demo_plan

    print_json(studio_solomon_demo_plan(args.project, mame=args.mame, record=args.record))


def command_studio_launch(args: argparse.Namespace) -> None:
    from retrolarium_kit.studio.playtest import launch_studio_smoke
    from retrolarium_kit.studio.project import StudioProjectError

    result = launch_studio_smoke(
        args.project,
        mame=args.mame,
        output=args.output,
        machine=args.machine,
        seconds=args.seconds,
        timeout=args.timeout,
    )
    print_json(result)
    if result["status"] != "completed":
        raise StudioProjectError(f"MAME smoke launch ended with {result['status']}")


def add_parsers(commands: argparse._SubParsersAction) -> None:
    studio = commands.add_parser(
        "studio", help="create and inspect human-editable .rlj projects"
    )
    studio_commands = studio.add_subparsers(dest="studio_command", required=True)
    studio_create = studio_commands.add_parser(
        "create", help="create a minimal Studio workspace for a disassembly"
    )
    studio_create.add_argument("manifest", type=Path)
    studio_create.add_argument("--output", type=Path, required=True)
    studio_create.add_argument("--id", dest="project_id", required=True)
    studio_create.add_argument("--name", required=True)
    studio_create.add_argument("--profile", required=True)
    studio_create.add_argument(
        "--resource-document",
        type=Path,
        help="add typed editor tabs declared by a NES resource bundle",
    )
    studio_create.add_argument(
        "--source-mode",
        choices=("binary-profile", "source-project"),
        default="source-project",
    )
    studio_create.set_defaults(handler=command_studio_create)
    studio_flicky = studio_commands.add_parser(
        "flicky-create", help="import canonical Flicky content as a portable Studio project"
    )
    studio_flicky.add_argument("rom", type=Path)
    studio_flicky.add_argument("--source-root", type=Path, required=True)
    studio_flicky.add_argument("--project-root", type=Path, required=True)
    studio_flicky.set_defaults(handler=command_studio_flicky_create)
    studio_flicky_upgrade = studio_commands.add_parser(
        "flicky-upgrade",
        help="upgrade Flicky GUI capabilities and pinned bonus positions without reimport",
    )
    studio_flicky_upgrade.add_argument("project", type=Path)
    studio_flicky_upgrade.add_argument(
        "--output", type=Path,
        help="write a sibling .rlj; it still shares the migrated source documents",
    )
    studio_flicky_upgrade.set_defaults(handler=command_studio_flicky_upgrade)
    studio_solomon_upgrade = studio_commands.add_parser(
        "solomon-upgrade",
        help="add the documents newer kits edit (screen text) to a Solomon's Key project",
    )
    studio_solomon_upgrade.add_argument("project", type=Path)
    studio_solomon_upgrade.set_defaults(handler=command_studio_solomon_upgrade)
    studio_inspect = studio_commands.add_parser(
        "inspect", help="emit the normalized GUI projection as JSON"
    )
    studio_inspect.add_argument("project", type=Path)
    studio_inspect.set_defaults(handler=command_studio_inspect)
    studio_migrate = studio_commands.add_parser(
        "migrate", help="upgrade a Studio project without discarding TOML comments"
    )
    studio_migrate.add_argument("project", type=Path)
    studio_migrate.add_argument("--output", type=Path)
    studio_migrate.add_argument(
        "--target-version", type=int,
        help="the schema version to reach (default: the current one)"
    )
    studio_migrate.set_defaults(handler=command_studio_migrate)
    studio_validate = studio_commands.add_parser(
        "validate", help="validate a Studio project and all referenced inputs"
    )
    studio_validate.add_argument("project", type=Path)
    studio_validate.set_defaults(handler=command_studio_validate)
    studio_build = studio_commands.add_parser(
        "build", help="build the source project and apply edited Studio documents"
    )
    studio_build.add_argument("project", type=Path)
    studio_build.add_argument("--container")
    studio_build.add_argument("--toolchain-cache", type=Path)
    studio_build.add_argument("--flicky-level-writer", type=Path,
                              help="native Flicky level writer executable")
    studio_build.add_argument(
        "--plan-json",
        help="a launch or playtest plan command (argv as a JSON array) to run "
             "after the build; its document is returned as \"plan\"")
    studio_build.set_defaults(handler=command_studio_build)
    studio_revisions = studio_commands.add_parser(
        "revisions", help="list a game repository's revisions with their ROM and split state"
    )
    studio_revisions.add_argument("descriptor", type=Path, help="the .rlj at the repository root")
    studio_revisions.set_defaults(handler=command_studio_revisions)
    studio_prepare = studio_commands.add_parser(
        "prepare", help="ready one revision of a game repository and write its studio project"
    )
    studio_prepare.add_argument("descriptor", type=Path, help="the .rlj at the repository root")
    studio_prepare.add_argument("--revision", required=True)
    studio_prepare.add_argument("--rom", type=Path, help="a ROM to copy into the repository first")
    studio_prepare.set_defaults(handler=command_studio_prepare)
    studio_reset = studio_commands.add_parser(
        "reset", help="discard a revision's edits: put back the documents taken from its ROM"
    )
    studio_reset.add_argument("descriptor", type=Path, help="the .rlj at the repository root")
    studio_reset.add_argument("--revision", required=True)
    studio_reset.set_defaults(handler=command_studio_reset)
    studio_check = studio_commands.add_parser(
        "check", help="prove every revision opens and builds back to its ROM, in a throwaway copy"
    )
    studio_check.add_argument("descriptor", type=Path, help="the .rlj at the repository root")
    studio_check.add_argument("--revision", action="append",
                              help="check only this revision (repeatable); default: all of them")
    studio_check.add_argument("--flicky-level-writer", type=Path,
                              help="the studio's retrolarium-flicky-level-writer, for Flicky")
    studio_check.add_argument("--release", type=Path,
                              help="also check the Retrolarium part of this 3.0/4.0 release manifest")
    studio_check.set_defaults(handler=command_studio_check)
    studio_open_rom = studio_commands.add_parser(
        "open-rom", help="identify a ROM, find or download its game's repository and ready its version"
    )
    studio_open_rom.add_argument("rom", type=Path)
    studio_open_rom.add_argument("--games", type=Path, required=True, help="where downloaded repositories go")
    studio_open_rom.add_argument("--known", type=Path, action="append", help="a descriptor already on disk")
    studio_open_rom.add_argument("--source", help="archive URL or path template ({organization}, {repository}, {branch})")
    studio_open_rom.set_defaults(handler=command_studio_open_rom)
    studio_identify = studio_commands.add_parser(
        "identify", help="name the game and revision a ROM file is"
    )
    studio_identify.add_argument("rom", type=Path)
    studio_identify.set_defaults(handler=command_studio_identify)
    studio_fds_bios = studio_commands.add_parser(
        "fds-bios", help="find the Famicom Disk System BIOS MAME uses, or install one"
    )
    studio_fds_bios.add_argument("--mame", type=Path, required=True)
    studio_fds_bios.add_argument("--install", type=Path, help="a BIOS dump (disksys.rom) to copy for MAME")
    studio_fds_bios.set_defaults(handler=command_studio_fds_bios)
    studio_patch = studio_commands.add_parser(
        "patch", help="build a Studio project and write an IPS patch from its original ROM"
    )
    studio_patch.add_argument("project", type=Path)
    studio_patch.add_argument("--output", type=Path, required=True)
    studio_patch.set_defaults(handler=command_studio_patch)
    studio_verify = studio_commands.add_parser(
        "verify", help="verify that the Studio artifact matches current documents"
    )
    studio_verify.add_argument("project", type=Path)
    studio_verify.set_defaults(handler=command_studio_verify)
    studio_launch_plan_parser = studio_commands.add_parser(
        "launch-plan", help="emit an allowlisted MAME process specification"
    )
    studio_launch_plan_parser.add_argument("project", type=Path)
    studio_launch_plan_parser.add_argument("--mame", type=Path, required=True)
    studio_launch_plan_parser.add_argument("--machine")
    studio_launch_plan_parser.add_argument("--attach-window")
    studio_launch_plan_parser.set_defaults(handler=command_studio_launch_plan)
    studio_point_playtest = studio_commands.add_parser(
        "smb-point-playtest-plan",
        help="emit a validated MAME point-playtest process specification",
    )
    studio_point_playtest.add_argument("project", type=Path)
    studio_point_playtest.add_argument("--mame", type=Path, required=True)
    studio_point_playtest.add_argument("--course-bank", required=True)
    studio_point_playtest.add_argument("--area", required=True)
    studio_point_playtest.add_argument("--world", type=int, required=True)
    studio_point_playtest.add_argument("--course", type=int, required=True)
    studio_point_playtest.add_argument("--column", type=int, required=True)
    studio_point_playtest.add_argument("--row", type=int, required=True)
    studio_point_playtest.add_argument(
        "--theme", choices=("day", "night"), required=True
    )
    studio_point_playtest.add_argument("--no-infinite-time", action="store_true")
    studio_point_playtest.add_argument("--no-invincible", action="store_true")
    studio_point_playtest.add_argument("--no-wrap-pits", action="store_true")
    studio_point_playtest.add_argument(
        "--sfx-channel", choices=("none", "square1", "square2", "noise"),
        default="none",
    )
    studio_point_playtest.add_argument("--sfx-value", type=int, default=0)
    studio_point_playtest.set_defaults(
        handler=command_studio_smb_point_playtest_plan
    )
    flicky_round_playtest = studio_commands.add_parser(
        "flicky-round-playtest-plan",
        help="emit the canonical Flicky round MAME/Lua process specification",
    )
    flicky_round_playtest.add_argument("project", type=Path)
    flicky_round_playtest.add_argument("--mame", type=Path, required=True)
    flicky_round_playtest.add_argument("--round", type=int, required=True)
    flicky_round_playtest.set_defaults(
        handler=command_studio_flicky_round_playtest_plan
    )
    flicky_demo = studio_commands.add_parser(
        "flicky-demo-plan",
        help="emit the MAME process that loops a Flicky attract demo or records a new one",
    )
    flicky_demo.add_argument("project", type=Path)
    flicky_demo.add_argument("--mame", type=Path, required=True)
    flicky_demo.add_argument("--demo", type=int, required=True, help="the attract demo, 1-4")
    flicky_demo.add_argument("--record", type=Path,
                             help="play the demo with the controller and write its steps here")
    flicky_demo.set_defaults(handler=command_studio_flicky_demo_plan)
    flicky_sound_preview = studio_commands.add_parser(
        "flicky-sound-preview",
        help="render a Flicky Sound header to VGM and YMFM WAV in project build/",
    )
    flicky_sound_preview.add_argument("project", type=Path)
    flicky_sound_preview.add_argument("--header", required=True)
    flicky_sound_preview.add_argument("--seconds", type=int, default=30)
    flicky_sound_preview.add_argument("--channel", type=int, action="append")
    flicky_sound_preview.add_argument("--document-override", type=Path)
    flicky_sound_preview.set_defaults(handler=command_studio_flicky_sound_preview)
    doraemon_world2_view = studio_commands.add_parser(
        "doraemon-world2-view",
        help="write the World 2 routes of a Doraemon resource bundle as editable panels",
    )
    doraemon_world2_view.add_argument("document", type=Path)
    doraemon_world2_view.add_argument("--output", type=Path, required=True)
    doraemon_world2_view.set_defaults(handler=command_studio_doraemon_world2_view)
    pacman_demo = studio_commands.add_parser(
        "pacman-demo-plan",
        help="emit a MAME process specification that loops or records the Pac-Man attract demo",
    )
    pacman_demo.add_argument("project", type=Path)
    pacman_demo.add_argument("--mame", type=Path, required=True)
    pacman_demo.add_argument("--record", type=Path, help="steer the demo with the controller and write its runs here")
    pacman_demo.set_defaults(handler=command_studio_pacman_demo_plan)
    doraemon_demo = studio_commands.add_parser(
        "doraemon-demo-plan",
        help="emit a MAME process specification that loops or records Doraemon's World 1 attract demo",
    )
    doraemon_demo.add_argument("project", type=Path)
    doraemon_demo.add_argument("--mame", type=Path, required=True)
    doraemon_demo.add_argument("--record", type=Path, help="steer the demo with the controller and write its runs here")
    doraemon_demo.set_defaults(handler=command_studio_doraemon_demo_plan)
    doraemon_playtest = studio_commands.add_parser(
        "doraemon-playtest-plan",
        help="emit a MAME process specification that starts Doraemon in one world past the title",
    )
    doraemon_playtest.add_argument("project", type=Path)
    doraemon_playtest.add_argument("--mame", type=Path, required=True)
    doraemon_playtest.add_argument("--world", type=int, choices=(1, 2, 3), required=True)
    doraemon_playtest.add_argument("--block", type=int, nargs=2, metavar=("X", "Y"),
                                   help="World 1: start at this city block (0-63 each)")
    doraemon_playtest.add_argument("--underground", type=int, nargs=2, metavar=("X", "Y"),
                                   help="World 1: start at this underground block (0-63, 0-24)")
    doraemon_playtest.add_argument("--stage", type=int, help="World 2: start at stage 0-2")
    doraemon_playtest.add_argument("--room", type=int, help="World 3: start in room 1-63")
    doraemon_playtest.set_defaults(handler=command_studio_doraemon_playtest_plan)
    doraemon_upgrade = studio_commands.add_parser(
        "doraemon-upgrade", help="add the documents newer kits edit to a Doraemon project",
    )
    doraemon_upgrade.add_argument("project", type=Path)
    doraemon_upgrade.set_defaults(handler=command_studio_doraemon_upgrade)
    pacman_upgrade = studio_commands.add_parser(
        "pacman-upgrade", help="add the attract demo document to a Pac-Man project",
    )
    pacman_upgrade.add_argument("project", type=Path)
    pacman_upgrade.set_defaults(handler=command_studio_pacman_upgrade)
    pacman_round_playtest = studio_commands.add_parser(
        "pacman-round-playtest-plan",
        help="emit a MAME process specification that starts Pac-Man at one round",
    )
    pacman_round_playtest.add_argument("project", type=Path)
    pacman_round_playtest.add_argument("--mame", type=Path, required=True)
    pacman_round_playtest.add_argument("--round", type=int, required=True)
    pacman_round_playtest.set_defaults(handler=command_studio_pacman_round_playtest_plan)
    castle_excellent_point_playtest = studio_commands.add_parser(
        "castle-excellent-point-playtest-plan",
        help="prepare a Castle Excellent room preview and emit its MAME process specification",
    )
    castle_excellent_point_playtest.add_argument("project", type=Path)
    castle_excellent_point_playtest.add_argument("--mame", type=Path, required=True)
    castle_excellent_point_playtest.add_argument("--room", type=int, required=True)
    castle_excellent_point_playtest.add_argument("--x", type=int, required=True)
    castle_excellent_point_playtest.add_argument("--y", type=int, required=True)
    castle_excellent_point_playtest.set_defaults(
        handler=command_studio_castle_excellent_point_playtest_plan
    )
    solomon_point_playtest = studio_commands.add_parser(
        "solomon-point-playtest-plan",
        help="emit the USA/Europe Solomon MAME room-playtest process specification",
    )
    solomon_point_playtest.add_argument("project", type=Path)
    solomon_point_playtest.add_argument("--mame", type=Path, required=True)
    solomon_point_playtest.add_argument("--room", type=int, required=True)
    solomon_point_playtest.set_defaults(
        handler=command_studio_solomon_point_playtest_plan
    )
    solomon_demo = studio_commands.add_parser(
        "solomon-demo-plan",
        help="emit the MAME process that loops the Solomon attract demo or records a new one",
    )
    solomon_demo.add_argument("project", type=Path)
    solomon_demo.add_argument("--mame", type=Path, required=True)
    solomon_demo.add_argument(
        "--record", type=Path,
        help="play the demo room with the controller and write its buttons here",
    )
    solomon_demo.set_defaults(handler=command_studio_solomon_demo_plan)
    smb_demo = studio_commands.add_parser(
        "smb-demo-plan",
        help="emit the MAME process that loops the SMB title demo or records a new one",
    )
    smb_demo.add_argument("project", type=Path)
    smb_demo.add_argument("--mame", type=Path, required=True)
    smb_demo.add_argument("--record", type=Path, help="play the demo with the controller and write its steps here")
    smb_demo.set_defaults(handler=command_studio_smb_demo_plan)
    smb_upgrade = studio_commands.add_parser(
        "smb-upgrade", help="add the documents newer kits edit (title demo) to an SMB project",
    )
    smb_upgrade.add_argument("project", type=Path)
    smb_upgrade.set_defaults(handler=command_studio_smb_upgrade)
    castle_excellent_demo = studio_commands.add_parser(
        "castle-excellent-demo-plan",
        help="emit the MAME process that loops the Castle Excellent title demo or records a new one",
    )
    castle_excellent_demo.add_argument("project", type=Path)
    castle_excellent_demo.add_argument("--mame", type=Path, required=True)
    castle_excellent_demo.add_argument("--record", type=Path, help="play the demo with the controller and write its commands here")
    castle_excellent_demo.set_defaults(handler=command_studio_castle_excellent_demo_plan)
    castle_excellent_upgrade = studio_commands.add_parser(
        "castle-excellent-upgrade",
        help="add the documents newer kits edit (title demo, screens) to a Castle Excellent project",
    )
    castle_excellent_upgrade.add_argument("project", type=Path)
    castle_excellent_upgrade.set_defaults(handler=command_studio_castle_excellent_upgrade)
    studio_launch = studio_commands.add_parser(
        "launch", help="run a bounded headless MAME smoke test of the edited artifact"
    )
    studio_launch.add_argument("project", type=Path)
    studio_launch.add_argument("--mame", type=Path, required=True)
    studio_launch.add_argument("--output", type=Path)
    studio_launch.add_argument("--machine")
    studio_launch.add_argument("--seconds", type=int, default=2)
    studio_launch.add_argument("--timeout", type=int, default=60)
    studio_launch.set_defaults(handler=command_studio_launch)
