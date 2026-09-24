"""Flicky .rlj loading and ownership checks without a private ROM."""

from pathlib import Path
from contextlib import redirect_stdout
from io import StringIO
import shutil
import tempfile
import unittest
from unittest.mock import patch

from retrolarium_kit.cli import main as cli_main
from retrolarium_kit.studio_project import (
    StudioProjectError, load_studio_project, upgrade_flicky_studio_project,
)


FIXTURE = (Path(__file__).resolve().parents[2] / "retrolarium_core" / "tests"
           / "fixtures" / "studio_projects")


class FlickyStudioContractTests(unittest.TestCase):
    def test_cli_uses_explicit_source_and_project_root(self) -> None:
        output = StringIO()
        with patch("retrolarium_kit.cli.import_flicky_source") as importing, \
                patch("retrolarium_kit.cli.create_flicky_studio_project",
                      return_value={"schema": "inspection"}) as creating, \
                redirect_stdout(output):
            status = cli_main(["studio", "flicky-create", "original.bin",
                               "--source-root", "source", "--project-root", "project"])
        self.assertEqual(0, status)
        importing.assert_called_once_with(Path("source"), Path("original.bin"),
                                          Path("project"))
        creating.assert_called_once_with(Path("project"))
        self.assertIn("inspection", output.getvalue())

    def test_imported_five_document_projection(self) -> None:
        inspection = load_studio_project(FIXTURE / "accept-flicky-content.rlj")
        self.assertEqual("flicky-content", inspection["project"]["source_mode"])
        self.assertEqual("flicky.content-studios", inspection["source"]["schema"])
        self.assertEqual(5, len(inspection["workspace"]["tabs"]))
        self.assertEqual("levels", inspection["workspace"]["startup_tab"])
        levels = next(tab for tab in inspection["workspace"]["tabs"]
                      if tab["id"] == "levels")
        self.assertEqual(["graphics", "semantics", "animations"],
                         levels["depends_on"])
        self.assertTrue({"paint", "edit", "preview", "playtest"}.issubset(
            levels["capabilities"]))
        sound = next(tab for tab in inspection["workspace"]["tabs"]
                     if tab["id"] == "sound")
        self.assertIn("preview", sound["capabilities"])

    def test_round_playtest_cli_forwards_one_based_round(self) -> None:
        output = StringIO()
        with patch("retrolarium_kit.cli.studio_flicky_round_playtest_plan",
                   return_value={"operation": "flicky-round"}) as planning, \
                redirect_stdout(output):
            status = cli_main([
                "studio", "flicky-round-playtest-plan", "flicky-ue.rlj",
                "--mame", "consoles.exe", "--round", "26",
            ])
        self.assertEqual(0, status)
        planning.assert_called_once_with(
            Path("flicky-ue.rlj"), mame=Path("consoles.exe"), round=26,
        )
        self.assertIn("flicky-round", output.getvalue())

    def test_sound_preview_cli_forwards_header_and_mixer(self) -> None:
        output = StringIO()
        with patch("retrolarium_kit.cli.studio_flicky_sound_preview",
                   return_value={"status": "rendered"}) as rendering, \
                redirect_stdout(output):
            status = cli_main([
                "studio", "flicky-sound-preview", "flicky-ue.rlj",
                "--header", "zMusic81Header", "--seconds", "2",
                "--channel", "1", "--channel", "3",
            ])
        self.assertEqual(0, status)
        rendering.assert_called_once_with(
            Path("flicky-ue.rlj"), header="zMusic81Header", seconds=2,
            enabled_channels=(1, 3),
        )
        self.assertIn("rendered", output.getvalue())

    def test_older_flicky_project_upgrades_capabilities_without_losing_comments(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(FIXTURE / "flicky-source", root / "flicky-source")
            project = root / "flicky-ue.rlj"
            payload = (FIXTURE / "accept-flicky-content.rlj").read_text(
                encoding="utf-8"
            )
            payload = payload.replace('"preview", "playtest",', '"preview",', 1)
            sound_start = payload.index('id = "sound"')
            payload = (payload[:sound_start]
                       + payload[sound_start:].replace('"edit", "preview",',
                                                        '"edit",', 1))
            project.write_bytes((payload + "# user's Flicky note\n")
                                .replace("\n", "\r\n").encode("utf-8"))
            output = StringIO()
            with redirect_stdout(output):
                status = cli_main(["studio", "flicky-upgrade", str(project)])
            self.assertEqual(0, status)
            self.assertIn('"status": "upgraded"', output.getvalue())
            result = upgrade_flicky_studio_project(project)
            self.assertEqual("current", result["status"])
            self.assertIn("# user's Flicky note", project.read_text(encoding="utf-8"))
            tabs = {tab["id"]: tab for tab in result["inspection"]["workspace"]["tabs"]}
            self.assertIn("playtest", tabs["levels"]["capabilities"])
            self.assertIn("preview", tabs["sound"]["capabilities"])
            upgraded = project.read_bytes()
            self.assertIn(b"\r\n", upgraded)
            self.assertEqual(upgraded.count(b"\n"), upgraded.count(b"\r\n"))
            self.assertEqual("current", upgrade_flicky_studio_project(project)["status"])
            self.assertEqual(upgraded, project.read_bytes())

    def test_flicky_upgrade_cli_forwards_project(self) -> None:
        output = StringIO()
        with patch("retrolarium_kit.cli.upgrade_flicky_studio_project",
                   return_value={"status": "upgraded"}) as upgrading, \
                redirect_stdout(output):
            status = cli_main(["studio", "flicky-upgrade", "flicky-ue.rlj"])
        self.assertEqual(0, status)
        upgrading.assert_called_once_with(Path("flicky-ue.rlj"), output=None)
        self.assertIn("upgraded", output.getvalue())

    def test_wrong_document_owner_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(FIXTURE / "flicky-source", root / "flicky-source")
            project = root / "flicky-ue.rlj"
            original = (FIXTURE / "accept-flicky-content.rlj").read_text(
                encoding="utf-8"
            )
            project.write_text(original.replace(
                'document = "flicky-source/content/workspace/level/levels.json"',
                'document = "flicky-source/content/workspace/graphics/graphics.json"',
            ), encoding="utf-8")
            with self.assertRaisesRegex(StudioProjectError, "Flicky tab"):
                load_studio_project(project)


if __name__ == "__main__":
    unittest.main()
