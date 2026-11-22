"""How the kit runs a build in a container: one `docker run` command line.

The Retrolarium Disassembler keeps a function with the same contract (the
two packages do not depend on each other):

- the project folder is bind-mounted at `workdir` with `--mount`, whose value
  is a CSV list: the source field is quoted so a comma in the path stays in
  it, and a path with a double quote or a line break is refused;
- on POSIX the container runs as the calling user (`--user uid:gid`), so the
  files it writes into build/ are the user's, not root's; Docker Desktop on
  Windows maps ownership itself;
- the argv is the operation's own, without a shell.
"""

from __future__ import annotations

import os
from pathlib import Path

from retrolarium_kit.core.errors import KitError


class ContainerError(ValueError, KitError):
    """A container build that cannot be expressed safely."""


def docker_run_command(
    image: str, project: Path, argv: list[str], *, workdir: str = "/work", name: str | None = None
) -> list[str]:
    """The `docker run` argv that runs `argv` in `image` over `project`."""
    if not image or image.startswith("-"):
        raise ContainerError(f"not a container image: {image!r}")
    source = str(project)
    if any(character in source for character in '"\r\n'):
        raise ContainerError(f"Docker cannot mount a folder whose path holds a quote or a line break: {source!r}")
    command = ["docker", "run", "--rm"]
    if name is not None:
        command += ["--name", name]
    if os.name != "nt" and hasattr(os, "getuid"):
        command += ["--user", f"{os.getuid()}:{os.getgid()}"]
    command += [
        "--mount",
        f'type=bind,"source={source}",target={workdir}',
        "--workdir",
        workdir,
        image,
        *argv,
    ]
    return command
