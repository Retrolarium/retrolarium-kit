"""Download and validate every source archive in the Open ROM catalog."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from retrolarium_kit.studio.repository import (
    GameRepositoryError,
    fetch_repository,
    load_descriptor,
    repository_catalog,
)


def main() -> int:
    failed = False
    with tempfile.TemporaryDirectory(prefix="retrolarium-catalog-") as temporary:
        for game_id, entry in repository_catalog()["games"].items():
            try:
                descriptor = load_descriptor(fetch_repository(game_id, Path(temporary)))
                actual = {revision.id: revision.sha256 for revision in descriptor.revisions}
                expected = {revision["id"]: revision["sha256"] for revision in entry["revisions"]}
                if descriptor.id != game_id or actual != expected:
                    raise GameRepositoryError("downloaded descriptor does not match the catalog's game and ROM hashes")
            except (GameRepositoryError, OSError, ValueError) as exc:
                print(f"FAIL {game_id}: {exc}", file=sys.stderr)
                failed = True
            else:
                print(f"OK {game_id}: {entry['ref']}")
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
