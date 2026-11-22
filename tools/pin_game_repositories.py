"""Pin every game of the Open ROM catalog to a commit and its content digest.

For each game of src/retrolarium_kit/core/profiles/repositories.json the
release tag or branch (`ref`) is resolved to its commit through the GitHub
API (a ref that is a commit already is kept), the archive of that commit is
downloaded and unpacked as the kit unpacks it, and its tree_digest is
recorded as `content_sha256` beside `commit`. Review the diff and commit
the catalog: the kit downloads exactly those commits and refuses any other
content.

    python tools/pin_game_repositories.py              # pin every game
    python tools/pin_game_repositories.py --check      # report, change nothing
    python tools/pin_game_repositories.py --game flicky
    python tools/pin_game_repositories.py --source path/{repository}.zip  # archives at hand
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
import urllib.parse
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from retrolarium_kit.core.repository_trust import tree_digest  # noqa: E402
from retrolarium_kit.studio.repository import GameRepositoryError, download_repository  # noqa: E402

CATALOG = Path(__file__).resolve().parents[1] / "src" / "retrolarium_kit" / "core" / "profiles" / "repositories.json"
API = "https://api.github.com"


def resolve_commit(organization: str, repository: str, ref: str) -> str:
    """The commit a tag (tags/<name>) or branch (heads/<name>) names now."""
    if re.fullmatch(r"[0-9a-f]{40}", ref):
        return ref
    owner = urllib.parse.urlparse(organization).path.strip("/")
    name = urllib.parse.quote(f"refs/{ref}", safe="/")
    request = urllib.request.Request(
        f"{API}/repos/{owner}/{repository}/commits/{name}",
        headers={"Accept": "application/vnd.github.sha", "User-Agent": "retrolarium-kit-pin"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        commit = response.read().decode("ascii").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise GameRepositoryError(f"{repository} {ref} did not resolve to a commit: {commit[:80]!r}")
    return commit


def content_digest(organization: str, repository: str, commit: str, source: str | None) -> str:
    """The tree_digest of the archive of `commit`, unpacked as the kit does."""
    template = source or "{organization}/{repository}/archive/{ref}.zip"
    url = template.format(organization=organization, repository=repository, ref=commit)
    staging = Path(tempfile.mkdtemp(prefix="retrolarium-pin-"))
    try:
        return tree_digest(download_repository(url, staging))
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def pin(catalog: dict, games: list[str] | None = None, *, source: str | None = None) -> list[str]:
    """Pin the chosen games of `catalog` in place; what changed, one line each."""
    changes = []
    organization = catalog["organization"]
    for game_id, entry in catalog["games"].items():
        if games and game_id not in games:
            continue
        commit = resolve_commit(organization, entry["repository"], entry["ref"])
        digest = content_digest(organization, entry["repository"], commit, source)
        if entry.get("commit") != commit or entry.get("content_sha256") != digest:
            changes.append(f"{game_id}: {entry['ref']} -> {commit} content {digest}")
        # The pins sit after ref, before the revisions.
        pinned = {}
        for key, value in entry.items():
            if key in ("commit", "content_sha256"):
                continue
            pinned[key] = value
            if key == "ref":
                pinned["commit"], pinned["content_sha256"] = commit, digest
        entry.clear()
        entry.update(pinned)
    return changes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--catalog", type=Path, default=CATALOG)
    parser.add_argument("--game", action="append", help="pin only this game (repeatable)")
    parser.add_argument("--source", help="archive template with {organization}, {repository} and {ref}")
    parser.add_argument("--check", action="store_true", help="fail when a pin is missing or differs")
    args = parser.parse_args(argv)
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    try:
        changes = pin(catalog, args.game, source=args.source)
    except (GameRepositoryError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for line in changes:
        print(line)
    if args.check:
        return 1 if changes else 0
    args.catalog.write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
