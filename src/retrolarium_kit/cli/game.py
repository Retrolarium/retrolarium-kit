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

    print_json(validate_game(args.rom, args.document, custom_profile=args.profile))


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


def command_game_doraemon_world_generate(args: argparse.Namespace) -> None:
    import json
    import os
    import tempfile

    from retrolarium_kit.core.authoring import AuthoringError
    from retrolarium_kit.games.catalog import validate_game
    from retrolarium_kit.games.doraemon.world3.adjacency import generate_adjacency_world, audit_adjacency_world
    from retrolarium_kit.games.doraemon.world3.document import generation_constraints, hierarchy_of
    from retrolarium_kit.games.doraemon.world3.geometry import room_number
    from retrolarium_kit.games.doraemon.world3.preview import room_png
    from retrolarium_kit.games.doraemon.world3.primitives import audit_definitions
    from retrolarium_kit.games.doraemon.world3.world import audit_world

    output = args.output.resolve()
    if os.path.lexists(output):
        raise AuthoringError("candidate output already exists; choose a new directory")
    if not 1 <= args.count <= 8:
        raise AuthoringError("candidate count must be 1..8")

    def read(path):
        return json.loads(path.read_text(encoding="utf-8"))

    try:
        document, catalogue, semantics = (read(p) for p in (args.document, args.catalogue, args.semantics))
        if any(not isinstance(value, dict) for value in (document, catalogue, semantics)):
            raise AuthoringError("resource, catalogue and semantics inputs must be JSON objects")
        constraints = generation_constraints(read(args.constraints))
        controls = read(args.controls) if args.controls else None
    except (ValueError, TypeError) as error:
        raise AuthoringError(f"invalid graph generation input: {error}") from error
    fixed = set(constraints["fixed_rooms"])
    if args.room is not None:
        selected = {room_number(i) for i in args.room}
        if selected & fixed:
            raise AuthoringError("a selected room is fixed by the event constraints")
        fixed |= set(range(64)) - selected
    if len(fixed) == 64:
        raise AuthoringError("no ordinary rooms selected for generation")
    validate_game(args.rom, args.document, custom_profile=args.profile)
    before = audit_world(document, start=constraints["start"])
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".graph-candidates-", dir=output.parent) as temporary:
        stage = Path(temporary) / "candidates"
        stage.mkdir()
        records = []
        for index in range(args.count):
            candidate, generation = generate_adjacency_world(
                document,
                catalogue=catalogue,
                semantics=semantics,
                fixed_rooms=sorted(fixed),
                seed=args.seed + index,
                attempts_per_room=args.attempts,
                island_probability=args.island_probability,
                room_controls=controls,
                extra_passable=constraints["extra_passable"],
            )
            audit_definitions(hierarchy_of(document), hierarchy_of(candidate))
            materials = audit_adjacency_world(
                document, candidate, catalogue, semantics, generation, extra_passable=constraints["extra_passable"]
            )
            normal = audit_world(candidate, start=constraints["start"])
            if not set(before["reachable_rooms"]) <= set(normal["reachable_rooms"]):
                raise AuthoringError("generation lost an original ordinary-terrain route")
            conditional = audit_world(
                candidate,
                start=constraints["start"],
                extra_passable=constraints["extra_passable"],
                event_destinations=constraints["event_destinations"],
                excluded_rooms=constraints["excluded_rooms"],
            )
            if not conditional["all_required_rooms_reachable"]:
                raise AuthoringError(f"unreachable conditional rooms: {conditional['unreachable_rooms']}")
            filename = f"candidate-{index + 1:02d}.json"
            path = stage / filename
            path.write_text(json.dumps(candidate, indent=2) + "\n", encoding="utf-8")
            validate_game(args.rom, path, custom_profile=args.profile)
            previews = []
            if args.previews:
                for item in generation["rooms"]:
                    if item["status"] == "generated":
                        name = f"candidate-{index + 1:02d}-room-{item['room']:02x}.png"
                        (stage / name).write_bytes(room_png(candidate, item["room"]))
                        previews.append(name)
            records.append(
                {
                    "document": filename,
                    "previews": previews,
                    "generation": generation,
                    "materials": materials,
                    "normal_routes": normal,
                    "conditional_routes": conditional,
                }
            )
        report = {
            "schema": "doraemon.graph-candidates.v1",
            "seed": args.seed,
            "source_sha256": document["source_sha256"],
            "candidates": records,
        }
        (stage / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        if os.path.lexists(output):
            raise AuthoringError("candidate output appeared during generation")
        stage.rename(output)
    print_json(
        {"status": "generated", "output": str(output), "count": args.count, "reports": [r["document"] for r in records]}
    )


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
    game = commands.add_parser("game", help="inspect and edit resources through declarative game profiles")
    game_commands = game.add_subparsers(dest="game_command", required=True)
    game_profiles = game_commands.add_parser(
        "profiles",
        help="list the game profiles found: those of the game repositories the current folder or the files named lie in",
    )
    game_profiles.add_argument(
        "places", type=Path, nargs="*", help="files or folders whose game repositories to look in"
    )
    game_profiles.set_defaults(handler=command_game_profiles)
    game_inspect = game_commands.add_parser("inspect", help="recognize a revision and validate its resource layout")
    game_inspect.add_argument("rom", type=Path)
    game_inspect.add_argument("--profile", type=Path)
    game_inspect.set_defaults(handler=command_game_inspect)
    game_extract = game_commands.add_parser("extract", help="extract editable resources to a versioned JSON document")
    game_extract.add_argument("rom", type=Path)
    game_extract.add_argument("--output", type=Path, required=True)
    game_extract.add_argument("--profile", type=Path)
    game_extract.add_argument(
        "--resource",
        help="authoring resource to extract when the profile exposes several",
    )
    game_extract.set_defaults(handler=command_game_extract)
    game_validate = game_commands.add_parser("validate", help="validate an edited document and its complete build plan")
    game_validate.add_argument("rom", type=Path)
    game_validate.add_argument("document", type=Path)
    game_validate.add_argument("--profile", type=Path)
    game_validate.set_defaults(handler=command_game_validate)
    game_build = game_commands.add_parser("build", help="transactionally compose an edited image")
    game_build.add_argument("rom", type=Path)
    game_build.add_argument("document", type=Path)
    game_build.add_argument("--output", type=Path, required=True)
    game_build.add_argument("--report", type=Path)
    game_build.add_argument("--profile", type=Path)
    game_build.set_defaults(handler=command_game_build)
    generate = game_commands.add_parser(
        "doraemon-world-generate", help="generate graph-based World 3 layouts with preserved ports and checked routes"
    )
    generate.add_argument("rom", type=Path)
    generate.add_argument("document", type=Path, help="an extracted NES resource bundle")
    generate.add_argument("--profile", type=Path)
    generate.add_argument("--catalogue", type=Path, required=True, help="game-owned primitive material definitions")
    generate.add_argument("--semantics", type=Path, required=True, help="reviewed primitive placement policies")
    generate.add_argument("--constraints", type=Path, required=True, help="fixed events and conditional route rules")
    generate.add_argument("--controls", type=Path, help="optional source-bound room ports and author anchors")
    generate.add_argument(
        "--room", type=lambda v: int(v, 0), action="append", help="limit generation to these ordinary rooms"
    )
    generate.add_argument("--count", type=int, default=1)
    generate.add_argument("--seed", type=int, default=0)
    generate.add_argument("--attempts", type=int, default=64)
    generate.add_argument("--island-probability", type=float, default=0.2)
    generate.add_argument("--previews", action="store_true", help="also render source-ROM PNG previews")
    generate.add_argument("--output", type=Path, required=True, help="a new directory for candidate JSON and reports")
    generate.set_defaults(handler=command_game_doraemon_world_generate)
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
    game_launch = game_commands.add_parser("launch", help="run a bounded headless MAME smoke on an edited ROM")
    game_launch.add_argument("rom", type=Path)
    game_launch.add_argument("artifact", type=Path)
    game_launch.add_argument("--mame", type=Path, required=True)
    game_launch.add_argument("--output", type=Path, required=True)
    game_launch.add_argument("--machine")
    game_launch.add_argument("--seconds", type=int, default=2)
    game_launch.add_argument("--timeout", type=int, default=60)
    game_launch.add_argument("--profile", type=Path)
    game_launch.set_defaults(handler=command_game_launch)
