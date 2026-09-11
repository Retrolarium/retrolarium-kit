from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from retrolarium_kit.studio_project import (
    StudioProjectError,
    build_studio_project,
    studio_smb_point_playtest_plan,
    validate_studio_project,
    verify_studio_project,
)


SHA256 = "a" * 64


def ines_image() -> bytes:
    return b"NES\x1a" + bytes([1, 1]) + bytes(10) + bytes(16_384 + 8_192)


def inspection(root: Path) -> dict[str, object]:
    return {
        "schema": "retrolarium.project-inspection",
        "schema_version": 1,
        "project_file": str((root / "fixture.retrolarium").resolve()),
        "project_root": str(root.resolve()),
        "project": {
            "id": "fixture",
            "name": "Fixture",
            "platform": "nes",
            "profile": "honkeypong.v1",
            "source_mode": "source-project",
        },
        "source": {
            "disassembly": "disassembly/manifest.json",
            "disassembly_path": str((root / "disassembly/manifest.json").resolve()),
            "sha256": SHA256,
            "schema": "retrolarium.nes.disassembly",
        },
        "workspace": {
            "layout": "auto",
            "startup_tab": "graphics",
            "tabs": [
                {
                    "id": "graphics",
                    "title": "Graphics",
                    "kind": "graphics.tile-set",
                    "group": "assets",
                    "document": "documents/resources.json",
                    "document_path": str((root / "documents/resources.json").resolve()),
                    "codec": "nes.chr-2bpp.v1",
                    "capabilities": ["pixel-paint"],
                    "depends_on": [],
                    "enabled": True,
                }
            ],
        },
        "operations": {
            "validate": "project.validate",
            "build": "project.build",
            "verify": "project.verify",
            "launch": "project.launch",
        },
    }


class StudioOperationTests(unittest.TestCase):
    def prepare(self, root: Path) -> dict[str, object]:
        (root / "documents").mkdir()
        (root / "disassembly/generated").mkdir(parents=True)
        (root / "fixture.retrolarium").write_text("fixture\n", encoding="utf-8")
        (root / "documents/resources.json").write_text(
            json.dumps(
                {
                    "schema": "retrolarium.nes-resource-bundle",
                    "schema_version": 1,
                    "source_sha256": SHA256,
                }
            ),
            encoding="utf-8",
        )
        (root / "disassembly/manifest.json").write_text("{}", encoding="utf-8")
        (root / "disassembly/generated/base.nes").write_bytes(ines_image())
        return inspection(root)

    def test_validate_routes_recognized_document_through_game_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = self.prepare(root)
            semantic = {
                "schema": "retrolarium.game-document-validation",
                "status": "valid",
            }
            with patch(
                "retrolarium_kit.studio_project.load_studio_project",
                return_value=state,
            ), patch(
                "retrolarium_kit.studio_project.validate_game_document_for_revision",
                return_value=semantic,
            ) as validate:
                result = validate_studio_project(root / "fixture.retrolarium")
            self.assertEqual([semantic], result["documents"])
            validate.assert_called_once_with(
                (root / "documents/resources.json").resolve(),
                profile_reference="honkeypong.v1",
                source_sha256=SHA256,
            )

    def test_build_and_verify_bind_artifact_to_current_documents(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = self.prepare(root)

            def authoring_build(source: Path, document: Path, output: Path):
                payload = bytearray(source.read_bytes())
                payload[-1] = 1
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(payload)
                return {"schema": "retrolarium.nes-resource-build", "status": "built"}

            patches = (
                patch(
                    "retrolarium_kit.studio_project.load_studio_project",
                    return_value=state,
                ),
                patch(
                    "retrolarium_kit.studio_project.validate_game_document_for_revision",
                    return_value={"status": "valid"},
                ),
                patch(
                    "retrolarium_kit.studio_project.build_project",
                    return_value={"artifact": {"path": "generated/base.nes"}},
                ),
                patch(
                    "retrolarium_kit.studio_project.verify_project",
                    return_value={"status": "exact"},
                ),
                patch(
                    "retrolarium_kit.studio_project.build_game",
                    side_effect=authoring_build,
                ),
                patch(
                    "retrolarium_kit.studio_project.load_disassembly_manifest",
                    return_value={"input": {"size": len(ines_image())}},
                ),
            )
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
                built = build_studio_project(root / "fixture.retrolarium")
                verified = verify_studio_project(root / "fixture.retrolarium")
                self.assertEqual("built", built["status"])
                self.assertEqual("current", verified["status"])
                (root / "documents/resources.json").write_text(
                    '{"schema":"retrolarium.nes-resource-bundle","changed":true}',
                    encoding="utf-8",
                )
                stale = verify_studio_project(root / "fixture.retrolarium")
            self.assertEqual("mismatch", stale["status"])
            self.assertIn("documents", stale["differences"])

    def test_build_rejects_document_without_registered_writer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = self.prepare(root)
            (root / "documents/resources.json").write_text(
                '{"schema":"unknown"}\n', encoding="utf-8"
            )
            with patch(
                "retrolarium_kit.studio_project.load_studio_project",
                return_value=state,
            ), patch(
                "retrolarium_kit.studio_project.validate_studio_project",
                return_value={"status": "valid"},
            ), patch(
                "retrolarium_kit.studio_project.build_project",
                return_value={"artifact": {"path": "generated/base.nes"}},
            ), patch(
                "retrolarium_kit.studio_project.verify_project",
                return_value={"status": "exact"},
            ):
                with self.assertRaisesRegex(StudioProjectError, "no registered writer"):
                    build_studio_project(root / "fixture.retrolarium")

    def test_smb_point_playtest_plan_is_fixed_and_validated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = self.prepare(root)
            state["project"]["profile"] = "smb1.v1"
            tab = state["workspace"]["tabs"][0]
            tab["codec"] = "smb.levels.v1"
            tab["capabilities"] = ["playtest"]
            document = {
                "schema": "retrolarium.nes-resource-bundle",
                "schema_version": 1,
                "game": "smb1",
                "revision": "ju",
                "source_sha256": SHA256,
                "resources": [
                    {
                        "id": "levels",
                        "kind": "level",
                        "codec": "smb.levels.v1",
                        "world_resource": "world",
                        "areas": [{"name": "ground_1"}],
                    },
                    {
                        "id": "unused-world",
                        "kind": "world",
                        "codec": "smb.world.v1",
                        "worlds": [],
                    },
                    {
                        "id": "world",
                        "kind": "world",
                        "codec": "smb.world.v1",
                        "worlds": [{"world": 1, "areas": [{
                            "area_type": 1,
                            "area_index": 0,
                            "alternate_bit": False,
                        }]}],
                    },
                    {
                        "id": "palettes",
                        "kind": "palette",
                        "codec": "nes.ppu-palette-packets.v1",
                        "day_background": 0x22,
                        "night_background": 0x0f,
                    },
                ],
            }
            (root / "documents/resources.json").write_text(
                json.dumps(document), encoding="utf-8"
            )
            fceux = root / "fceux64.exe"
            fceux.write_bytes(b"fixture")
            artifact = root / "disassembly/generated/base.nes"
            verification = {
                "status": "current",
                "artifact": {"absolute_path": str(artifact)},
            }
            with patch(
                "retrolarium_kit.studio_project.load_studio_project",
                return_value=state,
            ), patch(
                "retrolarium_kit.studio_project.verify_studio_project",
                return_value=verification,
            ):
                plan = studio_smb_point_playtest_plan(
                    root / "fixture.retrolarium",
                    fceux=fceux,
                    course_bank="levels",
                    area="ground_1",
                    world=1,
                    course=1,
                    column=19,
                    row=8,
                    theme="night",
                    invincible=False,
                )
                self.assertEqual("fceux-lua", plan["backend"])
                self.assertEqual(str(fceux.resolve()), plan["program"])
                self.assertEqual("-lua", plan["arguments"][0])
                self.assertEqual(str(artifact.resolve()), plan["arguments"][2])
                self.assertTrue(Path(plan["arguments"][1]).is_file())
                self.assertEqual("32", plan["environment"]["SMB1_PLAYTEST_AREA"])
                self.assertEqual("1", plan["environment"]["SMB1_PLAYTEST_PAGE"])
                self.assertEqual("48", plan["environment"]["SMB1_PLAYTEST_X"])
                self.assertEqual("144", plan["environment"]["SMB1_PLAYTEST_Y"])
                self.assertEqual("0", plan["environment"]["SMB1_PLAYTEST_INVINCIBLE"])
                self.assertTrue(plan["point"]["route_matches_area"])
                with self.assertRaisesRegex(StudioProjectError, "column must be"):
                    studio_smb_point_playtest_plan(
                        root / "fixture.retrolarium",
                        fceux=fceux,
                        course_bank="levels",
                        area="ground_1",
                        world=1,
                        course=1,
                        column=512,
                        row=8,
                        theme="day",
                    )


if __name__ == "__main__":
    unittest.main()
