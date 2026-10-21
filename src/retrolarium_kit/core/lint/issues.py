"""One finding of the linter, and the error a failed run raises."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import stat
import tempfile


class LintError(RuntimeError):
    """The tree breaks the house style, or the lint config is unusable."""


@dataclass(frozen=True)
class Issue:
    path: Path
    line: int
    code: str
    message: str

    def render(self, root: Path | None = None) -> str:
        path = self.path
        if root is not None:
            try:
                path = self.path.relative_to(root)
            except ValueError:
                pass
        where = f"{path.as_posix()}:{self.line}" if self.line else path.as_posix()
        return f"{where}: [{self.code}] {self.message}"


def write_text(path: Path, text: str) -> None:
    """Replace a file's text with LF endings, atomically, keeping its mode
    (an executable script stays executable)."""
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
        if path.exists():
            os.chmod(temporary, stat.S_IMODE(path.stat().st_mode))
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
