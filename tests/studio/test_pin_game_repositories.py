"""tools/pin_game_repositories.py on local archives (no network)."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from retrolarium_kit.studio import repository

from studio.test_repository_trust import COMMIT, FILES, expected_digest, write_archive

TOOL = Path(__file__).resolve().parents[2] / "tools" / "pin_game_repositories.py"


def load_tool():
    spec = importlib.util.spec_from_file_location("pin_game_repositories", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PinToolTests(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.tool = load_tool()
        write_archive(self.root / f"probe_src-{COMMIT}.zip", FILES)
        self.source = str(self.root / "{repository}-{ref}.zip")
        self.catalog = self.root / "repositories.json"
        self.catalog.write_text(
            json.dumps(
                {
                    "schema": "retrolarium.game-repositories",
                    "schema_version": 1,
                    "organization": "https://github.com/ExampleLab",
                    "games": {
                        "probe": {
                            "repository": "probe_src",
                            "descriptor": "probe.rlj",
                            "ref": "tags/v1",
                            "revisions": [],
                        }
                    },
                }
            ),
            encoding="utf-8",
        )

    def run_tool(self, *arguments: str) -> tuple[int, str]:
        output = io.StringIO()
        resolved = contextlib.closing(io.BytesIO(COMMIT.encode("ascii")))
        with (
            mock.patch("urllib.request.urlopen", return_value=resolved) as opened,
            contextlib.redirect_stdout(output),
        ):
            status = self.tool.main(["--catalog", str(self.catalog), "--source", self.source, *arguments])
        self.assertEqual(
            "https://api.github.com/repos/ExampleLab/probe_src/commits/refs/tags/v1", opened.call_args.args[0].full_url
        )
        return status, output.getvalue()

    def test_a_tag_is_pinned_to_its_commit_and_the_content_digest(self) -> None:
        self.assertEqual(1, self.run_tool("--check")[0])
        status, output = self.run_tool()
        self.assertEqual(0, status)
        self.assertIn(f"probe: tags/v1 -> {COMMIT}", output)
        entry = json.loads(self.catalog.read_text(encoding="utf-8"))["games"]["probe"]
        self.assertEqual(["repository", "descriptor", "ref", "commit", "content_sha256", "revisions"], list(entry))
        self.assertEqual((COMMIT, expected_digest(FILES)), (entry["commit"], entry["content_sha256"]))
        # The pinned catalog is one the kit accepts, and pinning again changes nothing.
        repository.validate_catalog(json.loads(self.catalog.read_text(encoding="utf-8")))
        self.assertEqual((0, ""), self.run_tool("--check"))


if __name__ == "__main__":
    unittest.main()
