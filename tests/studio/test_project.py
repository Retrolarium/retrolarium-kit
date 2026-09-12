from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from retrolarium_kit.game_profiles import packaged_game_profiles
from retrolarium_kit.studio_project import (
    StudioProjectError,
    create_studio_project,
    load_studio_project,
    migrate_studio_project,
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

    def test_project_relocates_without_rewriting_internal_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            original = base / "original"
            original.mkdir()
            project = write_project(original, LEVELS_TAB)
            source_text = project.read_text(encoding="utf-8")
            relocated = base / "relocated"
            shutil.copytree(original, relocated)
            relocated_project = relocated / project.name
            with patch(
                "retrolarium_kit.studio_project.load_disassembly_manifest",
                return_value=manifest(),
            ):
                inspection = load_studio_project(relocated_project)
            self.assertEqual(source_text, relocated_project.read_text(encoding="utf-8"))
            self.assertEqual(str(relocated.resolve()), inspection["project_root"])
            document_path = inspection["workspace"]["tabs"][0]["document_path"]
            self.assertTrue(Path(document_path).is_relative_to(relocated.resolve()))

    def test_reports_toml_line_and_column(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "broken.retrolarium"
            project.write_text('schema = "unterminated', encoding="utf-8")
            with self.assertRaisesRegex(
                StudioProjectError, r"broken\.retrolarium:\d+:\d+:"
            ):
                load_studio_project(project)

    def test_migration_preserves_comments_and_line_endings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = write_project(Path(temporary), LEVELS_TAB)
            legacy = project.read_text(encoding="utf-8").replace(
                'schema = "retrolarium.project"\n',
                '# project identity\nschema = "retrolarium.project" # keep schema\n',
            ).replace(
                "schema_version = 1",
                "schema_version = 0 # legacy version",
            ).replace("\n", "\r\n")
            project.write_bytes(legacy.encode("utf-8"))
            with patch(
                "retrolarium_kit.studio_project.load_disassembly_manifest",
                return_value=manifest(),
            ):
                result = migrate_studio_project(project)
            expected = legacy.replace(
                "schema_version = 0", "schema_version = 1", 1
            )
            self.assertEqual(expected.encode("utf-8"), project.read_bytes())
            self.assertEqual("migrated", result["status"])
            self.assertEqual(0, result["from_version"])
            self.assertEqual(1, result["to_version"])

    def test_migration_inserts_missing_legacy_version_without_reformatting(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = write_project(Path(temporary), LEVELS_TAB)
            legacy = project.read_text(encoding="utf-8").replace(
                "schema_version = 1\n", "# version was implicit in v0\n", 1
            )
            project.write_text(legacy, encoding="utf-8")
            with patch(
                "retrolarium_kit.studio_project.load_disassembly_manifest",
                return_value=manifest(),
            ):
                migrate_studio_project(project)
            migrated = project.read_text(encoding="utf-8")
            self.assertIn(
                'schema = "retrolarium.project"\nschema_version = 1\n', migrated
            )
            self.assertIn("# version was implicit in v0", migrated)

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
                            {"id": "world", "kind": "world", "codec": "smb.world.v1"},
                            {"id": "maze", "kind": "level", "codec": "pacman.maze-rle.v1"},
                            {"id": "screen", "kind": "screen", "codec": "nes.nametable.v1"},
                            {"id": "text", "kind": "text", "codec": "nes.fixed-text-fields.v1"},
                            {"id": "chr", "kind": "graphics", "codec": "nes.chr-2bpp.v1"},
                            {"id": "metatiles", "kind": "metatile", "codec": "nes.metatile-2x2.v1"},
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
                ["world", "maze", "screens", "text", "graphics", "metatiles", "palettes", "animations", "code"],
                [tab["id"] for tab in tabs],
            )
            self.assertEqual(
                [[], ["graphics"], ["graphics", "palettes"], [], ["palettes"],
                 ["graphics", "palettes"], [], ["graphics"], []],
                [tab["depends_on"] for tab in tabs],
            )
            self.assertEqual("world", result["workspace"]["startup_tab"])

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

    def test_create_maps_smb_levels_to_procedural_editor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "manifest.json"
            source.write_text(json.dumps(manifest()), encoding="utf-8")
            resources = root / "resources.json"
            resources.write_text(json.dumps({
                "schema": "retrolarium.nes-resource-bundle",
                "schema_version": 1,
                "game": "smb1",
                "revision": "ju",
                "source_sha256": SHA256,
                "resources": [
                    {"id": "levels", "kind": "level", "codec": "smb.levels.v1"},
                    {"id": "levels-extended", "kind": "level", "codec": "smb.levels.v1"},
                    {"id": "metatiles", "kind": "metatile", "codec": "nes.metatile-2x2.v1"},
                    {"id": "palettes", "kind": "palette", "codec": "nes.ppu-palette-packets.v1"},
                    {"id": "player-palettes", "kind": "palette", "codec": "nes.palette-table.v1"},
                    {"id": "ui-text", "kind": "text", "codec": "nes.fixed-text-fields.v1"},
                    {"id": "title-screen", "kind": "screen",
                     "codec": "nes.ppu-screen-packets.v1",
                     "text_fields": [{"key": "title"}]},
                    {"id": "chr", "kind": "graphics", "codec": "nes.chr-2bpp.v1"},
                    {"id": "player-frames", "kind": "animation", "codec": "smb.player-frames.v1"},
                    {"id": "music", "kind": "audio", "codec": "smb.apu-music.v1"},
                    {"id": "swim-stomp-envelope", "kind": "audio",
                     "codec": "smb.apu-envelope.v1"},
                ],
            }), encoding="utf-8")
            with patch(
                "retrolarium_kit.studio_project.load_disassembly_manifest",
                return_value=manifest(),
            ):
                result = create_studio_project(
                    source, root / "smb.retrolarium", project_id="smb",
                    name="SMB", profile="smb1.v1", resource_document=resources,
                )
            levels = result["workspace"]["tabs"][0]
            self.assertEqual("levels", levels["id"])
            self.assertEqual("level.procedural", levels["kind"])
            self.assertEqual("smb.levels.v1", levels["codec"])
            self.assertEqual(
                1,
                sum(tab["id"] == "levels" for tab in result["workspace"]["tabs"]),
            )
            self.assertIn("playtest", levels["capabilities"])
            self.assertEqual(["graphics", "palettes"], levels["depends_on"])
            metatiles = next(
                tab for tab in result["workspace"]["tabs"] if tab["id"] == "metatiles"
            )
            self.assertEqual("metatile.composer", metatiles["kind"])
            self.assertEqual(["graphics", "palettes"], metatiles["depends_on"])
            player_frames = next(
                tab for tab in result["workspace"]["tabs"]
                if tab["id"] == "player-frames"
            )
            self.assertEqual("animation.mapping", player_frames["kind"])
            self.assertEqual("smb.player-frames.v1", player_frames["codec"])
            self.assertEqual(
                ["graphics", "palettes"], player_frames["depends_on"]
            )
            palettes = next(
                tab for tab in result["workspace"]["tabs"] if tab["id"] == "palettes"
            )
            self.assertEqual("nes.palette-table.v1", palettes["codec"])
            text = next(
                tab for tab in result["workspace"]["tabs"] if tab["id"] == "text"
            )
            self.assertEqual("text.fixed", text["kind"])
            self.assertEqual("nes.fixed-text-fields.v1", text["codec"])
            screens = next(
                tab for tab in result["workspace"]["tabs"] if tab["id"] == "screens"
            )
            self.assertEqual("screen.tilemap", screens["kind"])
            self.assertEqual("nes.ppu-screen-packets.v1", screens["codec"])
            self.assertEqual(["graphics", "palettes"], screens["depends_on"])
            music = next(
                tab for tab in result["workspace"]["tabs"]
                if tab["id"] == "music-sounds"
            )
            self.assertEqual("audio.sequencer", music["kind"])
            self.assertEqual("smb.apu-music.v1", music["codec"])
            self.assertEqual(
                ["edit", "midi-import", "restore", "undo", "wav-preview"],
                music["capabilities"],
            )

            resource_value = json.loads(resources.read_text(encoding="utf-8"))
            resource_value["revision"] = "pal"
            resources.write_text(json.dumps(resource_value), encoding="utf-8")
            with patch(
                "retrolarium_kit.studio_project.load_disassembly_manifest",
                return_value=manifest(),
            ):
                pal_result = create_studio_project(
                    source, root / "smb-pal.retrolarium", project_id="smb-pal",
                    name="SMB PAL", profile="smb1.v1", resource_document=resources,
                )
            pal_levels = pal_result["workspace"]["tabs"][0]
            self.assertNotIn("playtest", pal_levels["capabilities"])

    def test_create_maps_complete_fds_music_to_typed_editor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "manifest.json"
            source.write_text(json.dumps(manifest()), encoding="utf-8")
            resources = root / "resources.json"
            resources.write_text(json.dumps({
                "schema": "retrolarium.nes-resource-bundle",
                "schema_version": 1,
                "game": "smb1",
                "revision": "ann_fds",
                "source_sha256": SHA256,
                "resources": [
                    {
                        "id": "swim-stomp-envelope",
                        "kind": "audio",
                        "codec": "smb.apu-envelope.v1",
                    },
                    {
                        "id": "fds-music",
                        "kind": "audio",
                        "codec": "fds.music.v1",
                    },
                ],
            }), encoding="utf-8")
            with patch(
                "retrolarium_kit.studio_project.load_disassembly_manifest",
                return_value=manifest(),
            ):
                result = create_studio_project(
                    source, root / "ann.retrolarium", project_id="ann",
                    name="ANN", profile="smb1.v1", resource_document=resources,
                )
            synthesis = result["workspace"]["tabs"][0]
            self.assertEqual("synthesis", synthesis["id"])
            self.assertEqual("FDS Synthesis", synthesis["title"])
            self.assertEqual("audio.synthesis", synthesis["kind"])
            self.assertEqual("fds.music.v1", synthesis["codec"])
            self.assertEqual(
                ["edit", "restore", "undo"], synthesis["capabilities"]
            )

    def test_create_maps_combined_smb_audio_to_two_editors(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "manifest.json"
            source.write_text(json.dumps(manifest()), encoding="utf-8")
            resources = root / "resources.json"
            resources.write_text(json.dumps({
                "schema": "retrolarium.nes-resource-bundle",
                "schema_version": 1,
                "game": "smb1",
                "revision": "ann_fds",
                "source_sha256": SHA256,
                "resources": [
                    {"id": "music", "kind": "audio",
                     "codec": "smb.apu-music.v1"},
                    {"id": "swim-stomp-envelope", "kind": "audio",
                     "codec": "smb.apu-envelope.v1"},
                    {"id": "fds-synthesis", "kind": "audio",
                     "codec": "fds.synthesis.v1"},
                ],
            }), encoding="utf-8")
            with patch(
                "retrolarium_kit.studio_project.load_disassembly_manifest",
                return_value=manifest(),
            ):
                result = create_studio_project(
                    source, root / "ann.retrolarium", project_id="ann",
                    name="ANN", profile="smb1.v1",
                    resource_document=resources,
                )
            by_id = {tab["id"]: tab for tab in result["workspace"]["tabs"]}
            self.assertEqual("smb.apu-music.v1", by_id["music-sounds"]["codec"])
            self.assertEqual("audio.sequencer", by_id["music-sounds"]["kind"])
            self.assertEqual("fds.synthesis.v1", by_id["synthesis"]["codec"])
            self.assertEqual("audio.synthesis", by_id["synthesis"]["kind"])
            self.assertEqual(
                by_id["music-sounds"]["document"],
                by_id["synthesis"]["document"],
            )

    def test_smb_revision_matrix_creates_only_supported_editor_tabs(self) -> None:
        profile = packaged_game_profiles().get("smb1")
        expected_common = {
            "world", "levels", "graphics", "metatiles", "palettes",
            "player-frames", "code",
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "manifest.json"
            source.write_text(json.dumps(manifest()), encoding="utf-8")
            for revision in profile.revisions:
                revision_id = revision.revision_id
                resources = []
                for resource in profile.resources_for(revision):
                    item = {
                        "id": resource.resource_id,
                        "kind": resource.kind,
                        "codec": resource.codec,
                    }
                    if (
                        resource.kind == "screen"
                        and resource.parameters.get("text_fields")
                    ):
                        item["text_fields"] = [{"key": "profile-text"}]
                    resources.append(item)
                resource_path = root / f"{revision_id}.json"
                resource_path.write_text(json.dumps({
                    "schema": "retrolarium.nes-resource-bundle",
                    "schema_version": 1,
                    "game": "smb1",
                    "revision": revision_id,
                    "source_sha256": SHA256,
                    "resources": resources,
                }), encoding="utf-8")
                with self.subTest(revision=revision_id), patch(
                    "retrolarium_kit.studio_project.load_disassembly_manifest",
                    return_value=manifest(),
                ):
                    result = create_studio_project(
                        source, root / f"{revision_id}.retrolarium",
                        project_id=revision_id, name=revision.name,
                        profile="smb1.v1", resource_document=resource_path,
                    )
                    tabs = result["workspace"]["tabs"]
                    by_id = {tab["id"]: tab for tab in tabs}
                    self.assertTrue(expected_common.issubset(by_id), set(by_id))
                    if revision_id in {"ann_fds", "smb2_jp_fds"}:
                        self.assertEqual("fds.music.v1",
                            by_id["synthesis"]["codec"])
                        self.assertEqual("audio.synthesis",
                            by_id["synthesis"]["kind"])
                        self.assertEqual("smb.apu-music.v1",
                            by_id["music-sounds"]["codec"])
                        self.assertEqual("audio.sequencer",
                            by_id["music-sounds"]["kind"])
                        self.assertIn("screens", by_id)
                        self.assertIn("text", by_id)
                    else:
                        self.assertEqual("smb.apu-music.v1",
                            by_id["music-sounds"]["codec"])
                        self.assertEqual("audio.sequencer",
                            by_id["music-sounds"]["kind"])
                        self.assertNotIn("synthesis", by_id)

    def test_create_maps_packet_screen_text_to_text_editor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "manifest.json"
            source.write_text(json.dumps(manifest()), encoding="utf-8")
            resources = root / "resources.json"
            resources.write_text(json.dumps({
                "schema": "retrolarium.nes-resource-bundle",
                "schema_version": 1,
                "game": "screen-text-probe",
                "revision": "probe-v1",
                "source_sha256": SHA256,
                "resources": [{
                    "id": "title-screen", "kind": "screen",
                    "codec": "nes.ppu-screen-packets.v1",
                    "text_fields": [{"key": "title"}],
                }],
            }), encoding="utf-8")
            with patch(
                "retrolarium_kit.studio_project.load_disassembly_manifest",
                return_value=manifest(),
            ):
                result = create_studio_project(
                    source, root / "screen-text.retrolarium",
                    project_id="screen-text", name="Screen text",
                    profile="screen-text.v1", resource_document=resources,
                )

            text = next(
                tab for tab in result["workspace"]["tabs"] if tab["id"] == "text"
            )
            self.assertEqual("text.fixed", text["kind"])
            self.assertEqual("nes.ppu-screen-packets.v1", text["codec"])


if __name__ == "__main__":
    unittest.main()
