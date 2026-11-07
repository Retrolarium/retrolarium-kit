"""Graph CLI publication, explicit constraints and space-quality regression cases."""

import contextlib
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from games.doraemon.fixtures import world_fixture
from retrolarium_kit.cli import main
from retrolarium_kit.core.authoring import AuthoringError
from retrolarium_kit.games.doraemon.world3.document import generation_constraints
from retrolarium_kit.games.doraemon.world3.quality import check_quality, quality_contract


def constraints():
    return {"fixed_rooms": [], "extra_passable": {}, "event_destinations": [], "excluded_rooms": []}


class SpaceQualityTests(unittest.TestCase):
    def test_new_enclosed_void_is_rejected(self):
        before = [[0] * 32 for _ in range(32)]
        after = deepcopy(before)
        for y in range(10, 20):
            for x in range(10, 22):
                if y in (10, 19) or x in (10, 21):
                    after[y][x] = 0x40
        self.assertFalse(check_quality(after, quality_contract(before)))
        self.assertTrue(check_quality(after, quality_contract(after)))

    def test_new_24px_throat_between_broad_chambers_is_rejected(self):
        before = [[0] * 32 for _ in range(32)]
        after = deepcopy(before)
        for y in range(30):
            if y not in (13, 14, 15):
                for x in (15, 16):
                    after[y][x] = 0x40
        self.assertFalse(check_quality(after, quality_contract(before)))
        after[12][15:17] = [0, 0]
        self.assertTrue(check_quality(after, quality_contract(before)))

    def test_hidden_rows_do_not_create_or_remove_regions(self):
        tiles = [[0] * 32 for _ in range(32)]
        changed = deepcopy(tiles)
        changed[30:] = [[0x40] * 32 for _ in range(2)]
        self.assertEqual(quality_contract(tiles), quality_contract(changed))

    def test_conditional_barriers_are_checked_in_the_supplied_terrain_model(self):
        before = [[0] * 32 for _ in range(32)]
        after = deepcopy(before)
        for y in range(30):
            for x in (15, 16):
                after[y][x] = 0x40
        self.assertFalse(check_quality(after, quality_contract(before)))
        self.assertTrue(check_quality(after, quality_contract(before, [0x40]), [0x40]))


class GraphCliTests(unittest.TestCase):
    def inputs(self, root):
        document, catalogue, semantics = world_fixture()
        for name, value in (
            ("resources", document),
            ("catalogue", catalogue),
            ("semantics", semantics),
            ("constraints", constraints()),
        ):
            (root / f"{name}.json").write_text(json.dumps(value), encoding="utf-8")
        return [
            "game",
            "doraemon-world-generate",
            str(root / "source.nes"),
            str(root / "resources.json"),
            "--catalogue",
            str(root / "catalogue.json"),
            "--semantics",
            str(root / "semantics.json"),
            "--constraints",
            str(root / "constraints.json"),
            "--room",
            "10",
            "--attempts",
            "4",
            "--output",
            str(root / "output"),
        ]

    def test_cli_uses_graph_and_creates_json_and_optional_png_without_html(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            arguments = self.inputs(root)
            before = (root / "resources.json").read_bytes()
            with (
                patch("retrolarium_kit.games.catalog.validate_game") as validate,
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(0, main([*arguments, "--previews"]))
                self.assertEqual(2, validate.call_count)
            self.assertEqual(before, (root / "resources.json").read_bytes())
            self.assertEqual([], list((root / "output").glob("*.html")))
            self.assertTrue((root / "output/candidate-01-room-0a.png").is_file())
            report = json.loads((root / "output/report.json").read_text())
            self.assertEqual("doraemon.adjacency-world.v1", report["candidates"][0]["generation"]["schema"])
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(1, main(arguments))

    def test_build_validation_failure_does_not_publish_a_partial_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            arguments = self.inputs(root)
            with (
                patch(
                    "retrolarium_kit.games.catalog.validate_game", side_effect=[None, AuthoringError("invalid build")]
                ),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                self.assertEqual(1, main(arguments))
            self.assertFalse((root / "output").exists())
            self.assertFalse(list(root.glob(".graph-candidates-*")))

    def test_fixed_event_room_cannot_be_selected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            arguments = self.inputs(root)
            (root / "constraints.json").write_text(json.dumps({**constraints(), "fixed_rooms": [10]}))
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(1, main(arguments))
            self.assertFalse((root / "output").exists())

    def test_malformed_conditions_are_rejected_before_generation(self):
        for value in (
            {},
            {**constraints(), "fixed_rooms": [True]},
            {**constraints(), "extra_passable": {"01": [1]}},
            {**constraints(), "extra_passable": {"1": [256]}},
            {**constraints(), "event_destinations": [[0, -1, 20]]},
        ):
            with self.assertRaises(AuthoringError):
                generation_constraints(value)
