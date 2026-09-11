from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from retrolarium_kit.studio_project import (
    StudioProjectError,
    create_studio_project,
    load_studio_project,
    validate_studio_project,
)


SHA256 = "a" * 64


def manifest(platform: str = "nes") -> dict[str, object]:
    return {
        "schema": f"retrolarium.{platform}.disassembly",
        "schema_version": 1,
        "input": {"sha256": SHA256, "size": 16},
    }


def write_project(root: Path, tabs: str, *, startup: str = "levels") -> Path:
    (root / "disassembly").mkdir()
    (root / "documents").mkdir()
    (root / "disassembly/manifest.json").write_text("{}", encoding="utf-8")
    (root / "documents/levels.toml").write_text(
        'schema = "test.levels"\nschema_version = 1\n', encoding="utf-8"
    )
    (root / "documents/graphics.toml").write_text(
        'schema = "test.graphics"\nschema_version = 1\n', encoding="utf-8"
    )
    project = root / "fixture.retrolarium"
    project.write_text(
        f'''schema = "retrolarium.project"
schema_version = 1

[project]
id = "fixture"
name = "Fixture"
platform = "nes"
profile = "fixture.v1"
source_mode = "source-project"

[source]
disassembly = "disassembly/manifest.json"
sha256 = "{SHA256}"

[workspace]
layout = "auto"
startup_tab = "{startup}"

{tabs}

[operations]
validate = "project.validate"
''',
        encoding="utf-8",
    )
    return project


LEVELS_TAB = '''[[workspace.tabs]]
id = "levels"
title = "Levels"
kind = "level.tile-grid"
group = "content"
document = "documents/levels.toml"
codec = "pacman.maze-rle@1"
capabilities = ["paint", "undo"]'''

GRAPHICS_TAB = '''[[workspace.tabs]]
id = "graphics"
title = "Graphics"
kind = "graphics.tile-set"
group = "assets"
document = "documents/graphics.toml"
codec = "nes.chr-2bpp.v1"
capabilities = ["pixel-paint"]'''


class StudioProjectTests(unittest.TestCase):
    def load(self, path: Path) -> dict[str, object]:
        with patch(
            "retrolarium_kit.studio_project.load_disassembly_manifest",
            return_value=manifest(),
        ):
            return load_studio_project(path)

    def test_project_owns_visible_tab_order(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = write_project(
                Path(temporary), f"{GRAPHICS_TAB}\n\n{LEVELS_TAB}"
            )
            # The startup tab can name either enabled tab; file order remains canonical.
            document = self.load(project)
            tabs = document["workspace"]["tabs"]  # type: ignore[index]
            self.assertEqual(["graphics", "levels"], [tab["id"] for tab in tabs])
            self.assertEqual(["assets", "content"], [tab["group"] for tab in tabs])

    def test_disabled_tab_is_retained_but_not_a_startup_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            disabled = GRAPHICS_TAB + "\nenabled = false"
            project = write_project(Path(temporary), f"{LEVELS_TAB}\n\n{disabled}")
            document = self.load(project)
            tabs = document["workspace"]["tabs"]  # type: ignore[index]
            self.assertEqual([True, False], [tab["enabled"] for tab in tabs])
            project.write_text(
                project.read_text(encoding="utf-8").replace(
                    'startup_tab = "levels"', 'startup_tab = "graphics"'
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(StudioProjectError, "must name an enabled tab"):
                self.load(project)

    def test_rejects_unknown_kind_and_path_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = write_project(root, LEVELS_TAB)
            project.write_text(
                project.read_text(encoding="utf-8").replace(
                    'kind = "level.tile-grid"', 'kind = "game.magic-editor"'
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(StudioProjectError, "unsupported value"):
                self.load(project)
            project.write_text(
                project.read_text(encoding="utf-8")
                .replace('kind = "game.magic-editor"', 'kind = "level.tile-grid"')
                .replace('document = "documents/levels.toml"', 'document = "../outside"'),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(StudioProjectError, "without '..'"):
                self.load(project)

    def test_rejects_missing_dependency_and_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            missing = LEVELS_TAB + '\ndepends_on = ["palettes"]'
            project = write_project(root, missing)
            with self.assertRaisesRegex(StudioProjectError, "unknown tab 'palettes'"):
                self.load(project)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            levels = LEVELS_TAB + '\ndepends_on = ["graphics"]'
            graphics = GRAPHICS_TAB + '\ndepends_on = ["levels"]'
            project = write_project(root, f"{levels}\n\n{graphics}")
            with self.assertRaisesRegex(StudioProjectError, "dependency cycle"):
                self.load(project)

    def test_reports_toml_line_and_column(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "broken.retrolarium"
            project.write_text('schema = "unterminated', encoding="utf-8")
            with self.assertRaisesRegex(
                StudioProjectError, r"broken\.retrolarium:\d+:\d+:"
            ):
                load_studio_project(project)

    def test_validate_returns_only_enabled_tabs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            disabled = GRAPHICS_TAB + "\nenabled = false"
            project = write_project(Path(temporary), f"{LEVELS_TAB}\n\n{disabled}")
            with patch(
                "retrolarium_kit.studio_project.load_disassembly_manifest",
                return_value=manifest(),
            ):
                result = validate_studio_project(project)
            self.assertEqual("valid", result["status"])
            self.assertEqual(["levels"], result["enabled_tabs"])

    def test_create_emits_a_minimal_code_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "disassembly/manifest.json"
            source.parent.mkdir()
            source.write_text(json.dumps(manifest()), encoding="utf-8")
            output = root / "fixture.retrolarium"
            with patch(
                "retrolarium_kit.studio_project.load_disassembly_manifest",
                return_value=manifest(),
            ):
                result = create_studio_project(
                    source,
                    output,
                    project_id="fixture",
                    name="Fixture",
                    profile="fixture.v1",
                )
            self.assertTrue(output.is_file())
            self.assertIn('kind = "code.analysis"', output.read_text(encoding="utf-8"))
            self.assertEqual("code", result["workspace"]["startup_tab"])

    def test_create_materializes_nes_resource_tabs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "disassembly/manifest.json"
            source.parent.mkdir()
            source.write_text(json.dumps(manifest()), encoding="utf-8")
            resources = root / "documents/resources.json"
            resources.parent.mkdir()
            resources.write_text(
                json.dumps(
                    {
                        "schema": "retrolarium.nes-resource-bundle",
                        "schema_version": 1,
                        "game": "fixture",
                        "revision": "fixture-v1",
                        "source_sha256": SHA256,
                        "resources": [
                            {"id": "maze", "kind": "level", "codec": "pacman.maze-rle.v1"},
                            {"id": "screen", "kind": "screen", "codec": "nes.nametable.v1"},
                            {"id": "text", "kind": "text", "codec": "nes.fixed-text-fields.v1"},
                            {"id": "chr", "kind": "graphics", "codec": "nes.chr-2bpp.v1"},
                            {"id": "palette", "kind": "palette", "codec": "nes.palette-table.v1"},
                            {"id": "actors", "kind": "animation", "codec": "pacman.actor-mapping.v1"},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            output = root / "fixture.retrolarium"
            with patch(
                "retrolarium_kit.studio_project.load_disassembly_manifest",
                return_value=manifest(),
            ):
                result = create_studio_project(
                    source,
                    output,
                    project_id="fixture",
                    name="Fixture",
                    profile="fixture.v1",
                    resource_document=resources,
                )
            tabs = result["workspace"]["tabs"]
            self.assertEqual(
                ["maze", "screens", "text", "graphics", "palettes", "animations", "code"],
                [tab["id"] for tab in tabs],
            )
            self.assertEqual(
                [["graphics"], ["graphics", "palettes"], [], ["palettes"], [], ["graphics"], []],
                [tab["depends_on"] for tab in tabs],
            )
            self.assertEqual("maze", result["workspace"]["startup_tab"])

    def test_create_selects_profile_palette_codec(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "disassembly/manifest.json"
            source.parent.mkdir()
            source.write_text(json.dumps(manifest()), encoding="utf-8")
            resources = root / "documents/resources.json"
            resources.parent.mkdir()
            resources.write_text(
                json.dumps(
                    {
                        "schema": "retrolarium.nes-resource-bundle",
                        "schema_version": 1,
                        "game": "fixture",
                        "revision": "fixture-v1",
                        "source_sha256": SHA256,
                        "resources": [
                            {
                                "id": "palette",
                                "kind": "palette",
                                "codec": "pacman.palette-table.v1",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            output = root / "fixture.retrolarium"
            with patch(
                "retrolarium_kit.studio_project.load_disassembly_manifest",
                return_value=manifest(),
            ):
                result = create_studio_project(
                    source,
                    output,
                    project_id="fixture",
                    name="Fixture",
                    profile="fixture.v1",
                    resource_document=resources,
                )

            palettes = result["workspace"]["tabs"][0]
            self.assertEqual("palettes", palettes["id"])
            self.assertEqual("pacman.palette-table.v1", palettes["codec"])


if __name__ == "__main__":
    unittest.main()
