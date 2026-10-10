"""How a program the kit starts runs this kit again.

A game repository's build asks the kit for its assemblers through the
command in RLTOOL (`$(RLTOOL) build-tools path nes ca65`). When the kit runs
that build itself (a revision built from another, a split), the user may
have no `rltool` on PATH, as in the studio's package, so the kit names
itself: the rltool.py launcher it was started from, or `python -m
retrolarium_kit` with the kit and core importable.
"""

from __future__ import annotations

import os
from pathlib import Path
import shlex
import subprocess
import sys
from typing import Mapping

import retrolarium_core
import retrolarium_kit


def self_command() -> list[str]:
    """The command line that runs this kit."""
    launcher = Path(sys.argv[0]) if sys.argv and sys.argv[0] else None
    if launcher is not None and launcher.name == "rltool.py" and launcher.is_file():
        return [sys.executable, str(launcher.resolve())]
    return [sys.executable, "-m", "retrolarium_kit"]


def child_environment(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """An environment whose RLTOOL runs this kit, for a game's build."""
    environment = dict(os.environ if base is None else base)
    command = self_command()
    environment["RLTOOL"] = subprocess.list2cmdline(command) if os.name == "nt" else shlex.join(command)
    if command[1:2] == ["-m"]:
        paths = [str(Path(module.__file__).resolve().parents[1]) for module in (retrolarium_kit, retrolarium_core)]
        if environment.get("PYTHONPATH"):
            paths.append(environment["PYTHONPATH"])
        environment["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(paths))
    return environment
