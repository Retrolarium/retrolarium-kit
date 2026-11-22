from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from .fixtures import corpus_tool
from helpers import nrom16_image


class SmbCorpusTests(unittest.TestCase):
    def test_public_documents_are_deterministic_and_use_explicit_synthetic_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            first = corpus_tool.synthetic_documents()
            corpus_tool.write_documents(output, first)
            before = {name: (output / name).read_bytes() for name in first}
            corpus_tool.write_documents(output, corpus_tool.synthetic_documents())
            for name in first:
                self.assertEqual(before[name], (output / name).read_bytes())
                self.assertEqual(first[name], json.loads(before[name]))
                self.assertEqual("synthetic", first[name]["provenance"]["kind"])
                self.assertNotIn("rom_sha256", first[name]["provenance"])

    def test_rom_outputs_must_be_inside_an_ignored_build_subdirectory(self) -> None:
        for path in (
            corpus_tool.ROOT,
            corpus_tool.ROOT / "tests/fixtures",
            corpus_tool.ROOT / "build",
            corpus_tool.ROOT / "build/../tests/fixtures",
        ):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "ignored build"):
                corpus_tool.rom_output(path)
        output = corpus_tool.ROOT / "build/smb-rom-corpus"
        self.assertEqual(output.resolve(), corpus_tool.rom_output(output))

    def test_a_foreign_image_is_rejected_before_any_export(self) -> None:
        from retrolarium_kit.core.authoring import AuthoringError
        from retrolarium_kit.core.game_profiles import load_game_profile

        profile = load_game_profile(Path(__file__).resolve().parents[2] / "fixtures/game_honkeypong.json")
        with self.assertRaises(AuthoringError):
            corpus_tool.rom_documents(b"not a ROM", profile)
        # A valid homebrew cartridge is still not an SMB1 JU source.
        with self.assertRaises(AuthoringError):
            corpus_tool.rom_documents(nrom16_image(), profile)

    def test_publication_candidates_exclude_rom_data_even_after_a_force_add(self) -> None:
        self.assertEqual([], corpus_tool.private_publication_files(corpus_tool.ROOT))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            git = corpus_tool.subprocess
            git.run(["git", "init", "--quiet", str(root)], check=True)
            (root / ".gitignore").write_text("build/\n")
            (root / "build").mkdir()
            data = root / "build/renamed.json"
            data.write_text('{"provenance":{"kind":"rom-derived"}}')
            self.assertEqual([], corpus_tool.private_publication_files(root))
            git.run(["git", "-C", str(root), "add", "-f", "build/renamed.json"], check=True)
            self.assertEqual(["build/renamed.json"], corpus_tool.private_publication_files(root))

    def test_rom_output_refuses_a_force_tracked_destination(self) -> None:
        with patch.object(corpus_tool.subprocess, "check_output", return_value=b"build/corpus/levels.json\n"):
            with self.assertRaisesRegex(ValueError, "no tracked files"):
                corpus_tool.rom_output(corpus_tool.ROOT / "build/corpus")


if __name__ == "__main__":
    unittest.main()
