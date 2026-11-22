"""What the kit trusts in a game repository it downloaded, before it runs any
of the repository's Python scripts.

The kit's repository catalog (core/profiles/repositories.json) pins every
game to a commit and to the digest of the archive's content (tree_digest).
A download whose content does not match is refused. A verified download
gets a record at its root (MARKER) naming the repository, the commit, the
content digest and the digest of every script file in it.

The user edits files of a downloaded repository (the documents, the ROM
copies, hack/ and .retrolarium/), so the whole tree is checked only when it
is downloaded. Before a script runs (a split, check or build command, a
Flicky snapshot's scripts/run.py) the kit checks the files Python could run:
every file under scripts/, every Python source or extension module and the
script itself must be one the record lists, unchanged.

A repository without a record (a checkout of the user's own, a download of
an older kit or a download from an unverified source) runs no script unless
RETROLARIUM_UNVERIFIED_GAMES=1 says the user accepts unverified game
repositories.

Even a verified repository's scripts run only with the user's consent, given
per content digest: RETROLARIUM_ALLOW_REPOSITORY_SCRIPTS holds the
content_sha256 values the user agreed to (separated by commas or white
space), or `all` (rltool --allow-repository-scripts). Without it the kit
raises ConsentNeeded, which rltool prints as one retrolarium.consent-needed
JSON object on stdout and exit code 3; the Studio asks the user and runs the
command again with the variable. An unverified repository has no digest to
agree to: its scripts need `all`.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
from typing import Any, Iterable, Mapping

from retrolarium_kit.core.errors import KitError


MARKER = ".retrolarium-verified"
MARKER_SCHEMA = "retrolarium.verified-repository"
UNVERIFIED_VARIABLE = "RETROLARIUM_UNVERIFIED_GAMES"
CONSENT_VARIABLE = "RETROLARIUM_ALLOW_REPOSITORY_SCRIPTS"
CONSENT_SCHEMA = "retrolarium.consent-needed"
# rltool's exit code when a command stops for the user's consent.
CONSENT_EXIT_CODE = 3
# The files Python may run or load besides those under scripts/.
SCRIPT_SUFFIXES = frozenset({".py", ".pyw", ".pyd", ".so", ".pth"})
# Folders at the top of a repository that the kit and its builds make, and
# caches anywhere: never part of the scripts.
_MADE_AT_TOP = frozenset({".retrolarium", "hack", "build"})
_MADE_ANYWHERE = frozenset({".git", "__pycache__"})


class RepositoryTrustError(ValueError, KitError):
    """A game repository whose content or scripts the kit cannot vouch for."""


class ConsentNeeded(Exception):
    """The user has not agreed to run the scripts of a verified repository.

    Deliberately not a KitError: it is a question for the user, not a
    refusal, so nothing that marks a failed step may swallow it; rltool turns
    it into its consent-needed answer (exit code 3)."""

    def __init__(self, marker: Mapping[str, Any]) -> None:
        self.document = {
            "schema": CONSENT_SCHEMA,
            "schema_version": 1,
            "repository": marker["repository"],
            "commit": marker["commit"],
            "content_sha256": marker["content_sha256"],
            "url": marker["url"],
        }
        super().__init__(
            f"running the scripts of {marker['repository']} needs consent: set {CONSENT_VARIABLE}="
            f"{marker['content_sha256']} or pass --allow-repository-scripts"
        )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _regular_files(root: Path, skip_made: bool) -> list[str]:
    """Every regular file under `root` as a POSIX relative path, sorted. A
    symbolic link or any other special file is refused."""
    found: list[str] = []
    pending = [root]
    while pending:
        folder = pending.pop()
        with os.scandir(folder) as entries:
            for entry in entries:
                path = Path(entry.path)
                relative = path.relative_to(root)
                if skip_made and (
                    entry.name in _MADE_ANYWHERE or (len(relative.parts) == 1 and entry.name in _MADE_AT_TOP)
                ):
                    continue
                mode = entry.stat(follow_symlinks=False).st_mode
                if stat.S_ISDIR(mode):
                    pending.append(path)
                elif stat.S_ISREG(mode):
                    found.append(relative.as_posix())
                else:
                    raise RepositoryTrustError(f"{relative.as_posix()} is a link or a special file")
    return sorted(found)


def tree_digest(root: Path) -> str:
    """The digest of a tree's content: sha256 over one line per regular file,
    sorted by POSIX relative path, `<path>\\0<sha256 of the file>\\n`. Empty
    folders do not count; links and special files are refused."""
    digest = hashlib.sha256()
    for name in _regular_files(root, skip_made=False):
        digest.update(f"{name}\0{_sha256(root / name)}\n".encode("utf-8"))
    return digest.hexdigest()


def _is_script(name: str) -> bool:
    path = PurePosixPath(name)
    return path.parts[0] == "scripts" or path.suffix.lower() in SCRIPT_SUFFIXES


def script_files(root: Path, executed: Iterable[str] = ()) -> dict[str, str]:
    """The files Python could run in `root`, by POSIX relative path, with
    their SHA-256: everything under scripts/, every Python source or
    extension module (not in .git, caches or what the kit makes at the top)
    and the scripts named in `executed`."""
    names = {name for name in _regular_files(root, skip_made=True) if _is_script(name)}
    for name in executed:
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts or "\\" in name:
            raise RepositoryTrustError(f"{name} is not a path inside the repository")
        path = root.joinpath(*relative.parts)
        if path.is_symlink() or not path.is_file():
            raise RepositoryTrustError(f"{name} is not a file of the repository")
        names.add(relative.as_posix())
    return {name: _sha256(root / name) for name in sorted(names)}


def scripts_digest(scripts: Mapping[str, str]) -> str:
    """One digest for a set of script files (script_files)."""
    digest = hashlib.sha256()
    for name in sorted(scripts):
        digest.update(f"{name}\0{scripts[name]}\n".encode("utf-8"))
    return digest.hexdigest()


def write_marker(root: Path, *, game: str, repository: str, commit: str, content_sha256: str, url: str) -> None:
    """Record a verified download at its root."""
    document = {
        "schema": MARKER_SCHEMA,
        "schema_version": 1,
        "game": game,
        "repository": repository,
        "commit": commit,
        "content_sha256": content_sha256,
        "url": url,
        "scripts": script_files(root),
    }
    (root / MARKER).write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def read_marker(root: Path) -> dict[str, Any] | None:
    """The verification record at a repository's root, or None."""
    path = root / MARKER
    if not path.is_file() or path.is_symlink():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RepositoryTrustError(f"cannot read {path}: {exc}") from exc
    if (
        not isinstance(document, dict)
        or document.get("schema") != MARKER_SCHEMA
        or document.get("schema_version") != 1
        or not isinstance(document.get("scripts"), dict)
        or not all(isinstance(document.get(key), str) for key in ("repository", "commit", "content_sha256", "url"))
    ):
        raise RepositoryTrustError(f"{path} is not a verification record this kit reads")
    return document


def find_marker(base: Path) -> tuple[Path, dict[str, Any]] | None:
    """The verified repository `base` is in (its root and record): `base`
    itself, or a folder above it (a snapshot the kit made inside the
    repository's .retrolarium/)."""
    base = base.resolve()
    for folder in (base, *base.parents):
        marker = read_marker(folder)
        if marker is not None:
            return folder, marker
    return None


def unverified_allowed() -> bool:
    return os.environ.get(UNVERIFIED_VARIABLE) == "1"


def verify_scripts(base: Path, executed: Iterable[str] = ()) -> dict[str, Any] | None:
    """Check the scripts under `base` before one of them runs: each must be
    a file of the verified download, unchanged. Returns the download's
    record, or None for an unverified repository the user accepts with
    RETROLARIUM_UNVERIFIED_GAMES=1."""
    found = find_marker(base)
    if found is None:
        if not unverified_allowed():
            raise RepositoryTrustError(
                f"{base} was not downloaded and verified by the kit, so its scripts do not run; open the game's "
                f"ROM again to download it, or set {UNVERIFIED_VARIABLE}=1 to run an unverified repository"
            )
        print(f"warning: running scripts of the unverified repository {base}", file=sys.stderr)
        return None
    _root, marker = found
    listed = marker["scripts"]
    for name, digest in script_files(base, executed).items():
        if name not in listed:
            raise RepositoryTrustError(
                f"{name} is not part of {marker['repository']} as the kit downloaded it, so no script runs"
            )
        if listed[name] != digest:
            raise RepositoryTrustError(
                f"{name} changed since the kit downloaded {marker['repository']}, so no script runs"
            )
    return marker


def consented(content_sha256: str | None) -> bool:
    """Whether RETROLARIUM_ALLOW_REPOSITORY_SCRIPTS covers a content digest
    (None: an unverified repository, which only `all` covers)."""
    given = {item.lower() for item in re.split(r"[\s,]+", os.environ.get(CONSENT_VARIABLE, "")) if item}
    return "all" in given or (content_sha256 is not None and content_sha256.lower() in given)


def authorize_scripts(base: Path, executed: Iterable[str] = ()) -> dict[str, Any] | None:
    """Everything before a repository's script runs: its scripts are those of
    the verified download (verify_scripts) and the user agreed to run them.
    Raises ConsentNeeded for a verified repository without consent."""
    marker = verify_scripts(base, executed)
    if marker is None:
        if not consented(None):
            raise RepositoryTrustError(
                f"the scripts of the unverified repository {base} run only with {CONSENT_VARIABLE}=all "
                "or --allow-repository-scripts"
            )
        return None
    if not consented(marker["content_sha256"]):
        raise ConsentNeeded(marker)
    return marker
