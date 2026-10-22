"""movie: convert recordings to Retrolarium movies and check them in MAME.

A game keeps a movie check (``retrolarium.movie-check`` v1, JSON) next to each
recording: the movie, the RAM cells to read, the frames to read them after and
the expected values. ``golden`` plays the movie on the original ROM and writes
the values; ``check`` plays it on a build and compares; ``play`` is
``check`` in a window. ``mame`` remembers which MAME to use, so neither
--mame nor RETROLARIUM_MAME is needed after it.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys


def _mame(args: argparse.Namespace) -> Path:
    from retrolarium_kit.core import user_settings
    from retrolarium_kit.core.movies import MovieError

    named = args.mame or os.environ.get("RETROLARIUM_MAME") or user_settings.get("mame")
    if not named:
        raise MovieError(
            "which MAME? remember one with `rltool movie mame <path>` (or name it with --mame or RETROLARIUM_MAME)"
        )
    path = Path(named).resolve()
    if not path.is_file():
        raise MovieError(f"MAME executable does not exist: {path}")
    return path


def _play(args: argparse.Namespace, check_path: Path, *, watch: bool = False) -> tuple[Path, dict, dict, object]:
    from retrolarium_kit.core import movies

    spec_path = check_path.resolve()
    spec, check = movies.load_check(spec_path)
    movie = movies.read_movie(spec_path.parent / spec["movie"])
    # The movie's own offset (the one the Studio plays it with) and the
    # check's on top of it.
    movie.offset += int(spec.get("offset", 0))
    stop_at = getattr(args, "stop_at", None)
    if stop_at:
        # Only the start of the movie: the checkpoints up to that frame, and
        # the run ends there.
        kept = tuple(frame for frame in check.frames if frame != "end" and int(frame) < stop_at)
        check = movies.Check(check.cells, kept + (stop_at,))
        spec = {
            **spec,
            "expected": {
                label: values
                for label, values in spec.get("expected", {}).items()
                if label != "end" and int(label) <= stop_at
            },
        }
    stay = None if not watch else not (stop_at or getattr(args, "close", False))
    snapshot = movies.run_in_mame(
        movie, args.rom.resolve(), check, mame=_mame(args), machine=spec.get("machine"), watch=watch, stay=stay
    )
    return spec_path, spec, snapshot, check


def command_golden(args: argparse.Namespace) -> None:
    for check_path in args.checks:
        spec_path, spec, snapshot, _check = _play(args, check_path)
        spec["expected"] = {label: snapshot[label] for label in sorted(snapshot, key=_frame_order)}
        spec_path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8", newline="\n")
        print(f"[OK] {spec_path.name}: expected values for {len(snapshot)} frame(s) written")


def command_check(args: argparse.Namespace, *, watch: bool = False) -> None:
    """Play each check in turn and stop at the first that differs: a TAS
    first, then a longplay only once the TAS has passed."""
    from retrolarium_kit.core import movies

    stop_at = getattr(args, "stop_at", None)
    for check_path in args.checks:
        if watch:
            ends = (
                f"it closes at frame {stop_at}"
                if stop_at
                else "it closes when the movie ends"
                if getattr(args, "close", False)
                else "close it after the movie ends"
            )
            print(f"{check_path.name}: playing in a MAME window; {ends}")
        spec_path, spec, snapshot, check = _play(args, check_path, watch=watch)
        expected = spec.get("expected")
        if not expected and stop_at:
            print(f"[OK] {spec_path.name}: played to frame {stop_at}, before its first checkpoint")
            continue
        if not expected:
            raise movies.MovieError(f"{spec_path.name} has no expected values; run rltool movie golden first")
        problems = movies.compare(expected, snapshot, check.cells)
        for problem in problems:
            print(problem, file=sys.stderr)
        if problems:
            raise movies.MovieError(f"{spec_path.name}: {len(problems)} cell(s) differ from the original")
        print(
            f"[OK] {spec_path.name}: {sum(len(cells) for cells in expected.values())} cell(s) "
            f"match the original after {len(expected)} frame(s)"
        )


def command_play(args: argparse.Namespace) -> None:
    """``check`` in a MAME window at the game's speed, to watch the movie."""
    command_check(args, watch=True)


def command_mame(args: argparse.Namespace) -> None:
    """Remember the MAME movies play with, or say which one that is."""
    from retrolarium_kit.core import user_settings
    from retrolarium_kit.core.movies import MovieError

    if args.path is None:
        current = user_settings.get("mame")
        print(current or "no MAME remembered yet: rltool movie mame <path>")
        return
    path = args.path.expanduser().resolve()
    if not path.is_file():
        raise MovieError(f"MAME executable does not exist: {path}")
    print(f"[OK] movies play with {path} ({user_settings.put('mame', str(path))})")


def command_plan(args: argparse.Namespace) -> None:
    """How the Studio runs a ROM from power-on to record or play a movie: the
    launch, the image's identity, where its movies go and those already there.
    A game repository's revision (--descriptor and --revision) runs its own
    ROM on its own machine (PAL, Vs. System) and keeps its movies in the
    repository's movies/; any other ROM (--rom) keeps them where the Studio
    says (--movies)."""
    import hashlib

    from retrolarium_kit.core import movies
    from retrolarium_kit.core.project import launch_image_plan, vs_machine
    from retrolarium_kit.studio.playtest import PAL_REVISIONS
    from retrolarium_kit.studio.repository import load_descriptor

    machine = args.machine
    folder = args.movies
    if args.descriptor is not None:
        descriptor = load_descriptor(args.descriptor)
        revision = descriptor.revision(args.revision)
        rom = descriptor.root / revision.rom
        platform = descriptor.platform
        if machine is None:
            machine = (
                "nespal" if (descriptor.id, revision.id) in PAL_REVISIONS else vs_machine(descriptor.id, revision.id)
            )
        folder = descriptor.root / "movies"
    else:
        rom = args.rom
        platform = args.platform or _platform_of(rom)
    data = Path(rom).read_bytes()
    if args.movies_root is not None:
        folder = args.movies_root / hashlib.sha1(data).hexdigest()
    mame = _mame(args)
    plan = launch_image_plan(rom, platform=platform, mame=mame, machine=machine)
    prepared = None
    if args.movie is not None and args.prepared is None:
        raise movies.MovieError("--movie needs --prepared: where the movie as MAME plays it goes")
    if args.movie is not None:
        # The movie as MAME plays it from power-on: frame for frame, from the
        # first frame after the script starts, on the work RAM its recording
        # emulator starts with.
        movie = movies.read_movie(args.movie)
        skip, _trail = movies.alignment(movie)
        skip += movie.offset
        frames = movie.frames[skip:] if skip >= 0 else [movies.Frame()] * -skip + movie.frames
        movies.write_rlmovie(movies.Movie(movie.platform, frames, movie.source, 0, movie.notes), args.prepared)
        prepared = str(args.prepared.resolve())
        power_on = movies.power_on(movie)
        if power_on:
            plan["arguments"] += ["-pluginspath", f"{mame.parent / 'plugins'};{movies.PLUGINS}", "-plugin", "rlpoweron"]
            plan["environment"] = {
                **plan.get("environment", {}),
                "RL_MOVIE_RAM_FILL": power_on[0],
                "RL_MOVIE_RAM_SIZE": str(power_on[1]),
            }
    print(
        json.dumps(
            {
                "schema": "retrolarium.movie-plan",
                "schema_version": 1,
                "plan": plan,
                "platform": platform,
                "rom": {
                    "path": str(Path(rom).resolve()),
                    "name": Path(rom).name,
                    "sha1": hashlib.sha1(data).hexdigest(),
                    "size": len(data),
                },
                "movies_dir": str(folder.resolve()) if folder is not None else None,
                "movies": movies.describe_movies(folder) if folder is not None else [],
                "prepared_movie": prepared,
            },
            indent=2,
        )
    )


def _platform_of(rom: Path) -> str:
    """NES for an iNES or Disk System image, else the Mega Drive."""
    head = Path(rom).read_bytes()[:0x110]
    if head[:4] in (b"NES\x1a", b"FDS\x1a") or head[:15] == b"\x01*NINTENDO-HVC*":
        return "nes"
    return "megadrive"


def command_list(args: argparse.Namespace) -> None:
    from retrolarium_kit.core import movies

    print(json.dumps(movies.describe_movies(args.folder), indent=2))


def command_convert(args: argparse.Namespace) -> None:
    from retrolarium_kit.core import movies

    movie = movies.read_movie(args.input)
    movies.write_rlmovie(movie, args.output)
    print(f"[OK] {args.output}: {len(movie.frames)} frames, {len(movie.runs())} runs")


def _frame_order(label: str) -> float:
    return float("inf") if label == "end" else int(label)


def add_parsers(commands: argparse._SubParsersAction) -> None:
    movie = commands.add_parser("movie", help="play recorded movies in MAME and check RAM cells")
    actions = movie.add_subparsers(dest="movie_command", required=True)
    for name, handler, summary in (
        ("golden", command_golden, "play the movie on the original ROM and record the expected cells"),
        ("check", command_check, "play the movies on a build in turn and compare the cells with the original"),
        ("play", command_play, "check, shown in a MAME window at the game's speed (close it at the end)"),
    ):
        action = actions.add_parser(name, help=summary)
        action.add_argument(
            "checks", type=Path, nargs="+", metavar="check", help="movie checks (JSON), played in this order"
        )
        action.add_argument("--rom", type=Path, required=True, help="the ROM to play")
        action.add_argument(
            "--mame", help="MAME executable (default: RETROLARIUM_MAME, else the one `rltool movie mame` remembers)"
        )
        if name == "play":
            action.add_argument(
                "--stop-at", type=int, metavar="frame", help="close MAME at this frame (only the start of the movie)"
            )
            action.add_argument(
                "--close", action="store_true", help="close MAME when the movie ends instead of leaving it to play on"
            )
        action.set_defaults(handler=handler)
    remember = actions.add_parser("mame", help="remember the MAME movies play with (no path: show it)")
    remember.add_argument("path", type=Path, nargs="?")
    remember.set_defaults(handler=command_mame)
    plan = actions.add_parser("plan", help="how the Studio runs a ROM from power-on for a movie (JSON)")
    source = plan.add_mutually_exclusive_group(required=True)
    source.add_argument("--rom", type=Path, help="any ROM or disk image")
    source.add_argument("--descriptor", type=Path, help="a game repository's .rlj (with --revision)")
    plan.add_argument("--revision", help="the repository revision")
    plan.add_argument("--platform", choices=("nes", "megadrive"), help="for --rom (default: from the image)")
    plan.add_argument("--machine", help="the MAME machine (default: the revision's or the platform's)")
    plan.add_argument("--movies", type=Path, help="for --rom: the folder its movies are kept in")
    plan.add_argument("--movies-root", type=Path, help="for --rom: keep its movies in <this>/<the image's SHA-1>")
    plan.add_argument("--movie", type=Path, help="a movie to play or resume (.rlmovie, .fm2, .gmv)")
    plan.add_argument("--prepared", type=Path, help="with --movie: where the movie as MAME plays it goes")
    plan.add_argument(
        "--mame", help="MAME executable (default: RETROLARIUM_MAME, else the one `rltool movie mame` remembers)"
    )
    plan.set_defaults(handler=command_plan)
    listing = actions.add_parser("list", help="the movies in a folder and their descriptions (JSON)")
    listing.add_argument("folder", type=Path)
    listing.set_defaults(handler=command_list)
    convert = actions.add_parser("convert", help="convert an .fm2 or .gmv recording to .rlmovie")
    convert.add_argument("input", type=Path)
    convert.add_argument("output", type=Path)
    convert.set_defaults(handler=command_convert)
