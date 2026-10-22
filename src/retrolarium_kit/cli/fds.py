"""rltool fds: Famicom Disk System sides a game's build writes."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from retrolarium_kit.cli.common import print_json


def command_fds_write(args: argparse.Namespace) -> None:
    from retrolarium_core.fds import FdsError, replace_fds_payload

    image = args.disk.read_bytes()
    named: set[int] = set()
    for argument in args.file:
        ids, separator, path = argument.partition("=")
        if not separator or not ids or not path:
            raise FdsError(f"--file takes IDS=PATH, not {argument!r}")
        try:
            file_ids = [int(part, 0) for part in ids.split(",")]
        except ValueError as exc:
            raise FdsError(f"--file {argument!r}: {exc}") from exc
        if named & set(file_ids):
            raise FdsError(f"file {sorted(named & set(file_ids))[0]} is named twice")
        named |= set(file_ids)
        image = replace_fds_payload(image, file_ids, Path(path).read_bytes())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(image)
    print_json(
        {"status": "written", "output": str(args.output), "size": len(image), "sha1": hashlib.sha1(image).hexdigest()}
    )


def add_parsers(commands: argparse._SubParsersAction) -> None:
    group = commands.add_parser("fds", help="Famicom Disk System sides a game's build writes")
    actions = group.add_subparsers(dest="fds_command", required=True)
    write = actions.add_parser("write", help="lay built program files over the data of a side's disk files")
    write.add_argument("--disk", required=True, type=Path, help="the side the files go into (raw, no header)")
    write.add_argument(
        "--file",
        action="append",
        default=[],
        metavar="IDS=PATH",
        help="disk file IDs (comma-separated, in order) and the built file that fills them",
    )
    write.add_argument("--output", required=True, type=Path)
    write.set_defaults(handler=command_fds_write)
