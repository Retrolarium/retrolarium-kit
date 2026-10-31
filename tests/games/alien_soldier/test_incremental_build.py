from __future__ import annotations

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from retrolarium_kit.core import source_snapshot
from retrolarium_kit.games.alien_soldier import content, workspace


class IncrementalBuildTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.project = Path(self.directory.name)
        self.snapshot = self.project / content.SOURCE_DIRECTORY
        self.snapshot.mkdir()
        self.modules = tuple(SimpleNamespace(DOCUMENT=f"{name}.json") for name in ("first", "second"))
        self.baseline = {module.DOCUMENT: {"value": 1} for module in self.modules}
        workspace.write(self.baseline, self.snapshot / content.CONTENT)
        source = self.snapshot / "source.s"
        source.write_text("immutable source", encoding="utf-8")
        rom = self.snapshot / "original.bin"
        rom.write_bytes(b"original ROM")
        self.record = {
            "schema": source_snapshot.SCHEMA,
            "schema_version": 1,
            "revision": "test",
            "rom": rom.name,
            "rom_sha256": source_snapshot.sha256_file(rom),
            "files": {p.name: source_snapshot.sha256_file(p) for p in (source, rom)},
        }
        (self.snapshot / source_snapshot.RECORD).write_text(json.dumps(self.record), encoding="utf-8")
        self.addCleanup(patch.stopall)
        patch.object(workspace, "DOCUMENTS", self.modules).start()
        self.export = patch.object(workspace, "export", return_value=self.baseline).start()
        patch.object(workspace, "source_profile", return_value={"revisions": {"test": {}}}).start()
        patch.object(workspace, "tree").start()
        patch.object(workspace.demos, "pointer_table", return_value=[]).start()
        patch.object(workspace.demos, "layout", return_value={}).start()

        def assemble(root, revision, documents, output, **kwargs):
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"assembled source ROM")
            return {"size": output.stat().st_size, "changed": [], "symbols": {}}

        self.assemble = patch.object(workspace, "build", side_effect=assemble).start()
        self.validate = patch.object(workspace, "validate").start()

    def edit(self, name="first", value=2):
        path = self.snapshot / content.CONTENT / f"{name}.json"
        path.write_text(json.dumps({"value": value}), encoding="utf-8")

    def test_unchanged_build_reuses_verified_source_image(self):
        result = content.build(self.project)
        self.assertEqual(Path(result["artifact"]).read_bytes(), b"assembled source ROM")
        self.assertEqual(self.assemble.call_count, 1)
        self.assertEqual(self.assemble.call_args.kwargs["modules"], [])
        content.build(self.project)
        self.assertEqual(self.assemble.call_count, 1)
        self.validate.assert_not_called()
        self.assertEqual(self.export.call_count, 1)
        content.validate(self.project)
        self.assertEqual(self.export.call_count, 1)

    def test_validation_only_checks_changed_document_and_caches_success(self):
        content.validate(self.project)
        self.edit()
        content.validate(self.project)
        self.assertEqual(self.validate.call_args.args[3], [self.modules[0]])
        content.validate(self.project)
        self.assertEqual(self.validate.call_count, 1)

    def test_invalid_edits_are_never_cached(self):
        content.validate(self.project)
        self.edit(value=-1)
        self.validate.side_effect = workspace.WorkspaceError("invalid edit")
        for _ in range(2):
            with self.assertRaisesRegex(content.AlienSoldierContentError, "invalid edit"):
                content.validate(self.project)
        self.assertEqual(self.validate.call_count, 2)

    def test_source_tampering_is_rejected_even_with_warm_cache(self):
        content.validate(self.project)
        (self.snapshot / "source.s").write_text("modified", encoding="utf-8")
        with self.assertRaisesRegex(content.AlienSoldierContentError, "source changed"):
            content.build(self.project)

    def test_codec_change_reexports_baseline(self):
        content.validate(self.project)
        with patch.object(content, "_cache_key", return_value="new codec"):
            content.validate(self.project)
        self.assertEqual(self.export.call_count, 2)

    def test_formatting_does_not_force_rebuild(self):
        content.build(self.project)
        (self.snapshot / content.CONTENT / "first.json").write_text('{ "value" : 1 }', encoding="utf-8")
        with patch.object(workspace, "build") as assemble:
            content.build(self.project)
        assemble.assert_not_called()

    def test_changed_build_applies_only_edited_module_and_checks_again_after_failure(
        self,
    ):
        content.validate(self.project)
        self.edit()
        with patch.object(workspace, "build", side_effect=workspace.WorkspaceError("invalid build")) as assemble:
            with self.assertRaisesRegex(content.AlienSoldierContentError, "invalid build"):
                content.build(self.project)
        self.assertEqual(assemble.call_args.kwargs["modules"], [self.modules[0]])
        content.validate(self.project)
        self.assertEqual(self.validate.call_count, 1)
