"""The shared linter for game repositories (rltool lint).

Every game disassembly keeps the same house style: ca65 sources for the NES
games, AS sources for the Mega Drive ones, and one text-format contract for
the rest of the tree. The rules live here once; a game keeps only its
config/lint.json, which names its sources, its dialect and which trees the
text contract skips. Checks that only make sense for one game's source stay
in that game's repository.
"""

from retrolarium_kit.core.lint.issues import Issue, LintError
from retrolarium_kit.core.lint.runner import LintConfig, load_config, run

__all__ = ["Issue", "LintConfig", "LintError", "load_config", "run"]
