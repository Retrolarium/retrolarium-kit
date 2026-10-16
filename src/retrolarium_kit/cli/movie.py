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
        raise MovieError("which MAME? remember one with `rltool movie mame <path>` "
                         "(or name it with --mame or RETROLARIUM_MAME)")
    path = Path(named).resolve()
    if not path.is_file():
        raise MovieError(f"MAME executable does not exist: {path}")
    return path


def _play(args: argparse.Namespace, check_path: Path, *, watch: bool = False) -> tuple[Path, dict, dict, object]:
    from retrolarium_kit.core import movies

    spec_path = check_path.resolve()
    spec, check = movies.load_check(spec_path)
    movie = movies.read_movie(spec_path.parent / spec["movie"])
    movie.offset = int(spec.get("offset", 0))
    snapshot = movies.run_in_mame(movie, args.rom.resolve(), check, mame=_mame(args),
                                  machine=spec.get("machine"), watch=watch)
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

    for check_path in args.checks:
        if watch:
            print(f"{check_path.name}: playing in a MAME window; close it after the movie ends")
        spec_path, spec, snapshot, check = _play(args, check_path, watch=watch)
        expected = spec.get("expected")
        if not expected:
            raise movies.MovieError(f"{spec_path.name} has no expected values; run rltool movie golden first")
        problems = movies.compare(expected, snapshot, check.cells)
        for problem in problems:
            print(problem, file=sys.stderr)
        if problems:
            raise movies.MovieError(f"{spec_path.name}: {len(problems)} cell(s) differ from the original")
        print(f"[OK] {spec_path.name}: {sum(len(cells) for cells in expected.values())} cell(s) "
              f"match the original after {len(expected)} frame(s)")


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
        action.add_argument("checks", type=Path, nargs="+", metavar="check",
                            help="movie checks (JSON), played in this order")
        action.add_argument("--rom", type=Path, required=True, help="the ROM to play")
        action.add_argument("--mame", help="MAME executable (default: RETROLARIUM_MAME, "
                            "else the one `rltool movie mame` remembers)")
        action.set_defaults(handler=handler)
    remember = actions.add_parser("mame", help="remember the MAME movies play with (no path: show it)")
    remember.add_argument("path", type=Path, nargs="?")
    remember.set_defaults(handler=command_mame)
    convert = actions.add_parser("convert", help="convert an .fm2 or .gmv recording to .rlmovie")
    convert.add_argument("input", type=Path)
    convert.add_argument("output", type=Path)
    convert.set_defaults(handler=command_convert)
