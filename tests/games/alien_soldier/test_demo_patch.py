from __future__ import annotations

import copy
import unittest
from retrolarium_kit.games.alien_soldier import demos


class DemoPatchTests(unittest.TestCase):
    def setUp(self):
        self.steps = [
            {"held": 0, "pressed": 0, "frames": 1},
            {"held": 1, "pressed": 1, "frames": demos.DEMO_FRAMES - 1},
        ]
        self.stream = demos.encode(self.steps, 10)
        self.image = bytes.fromhex("00020000") + self.stream + b"untouched"
        self.layout = {
            "labels": ["stream"],
            "slots": {
                demos.STAGES: {"offset": 0, "size": 2, "file": "stage.s"},
                "stream": {"offset": 4, "size": 10, "file": "stream.s"},
            },
        }
        self.doc = {
            "schema": demos.SCHEMA,
            "schema_version": 1,
            "demos": [{"label": "stream", "stage": 2, "steps": copy.deepcopy(self.steps)}],
        }

    def test_unchanged_preserves_every_byte_including_trailing_data(self):
        self.assertEqual(demos.patch(self.image, self.doc, self.layout), (self.image, []))

    def test_updates_buttons_and_stage_only_inside_their_slots(self):
        self.doc["demos"][0]["steps"][1]["held"] = 2
        self.doc["demos"][0]["stage"] = 4
        result, files = demos.patch(self.image, self.doc, self.layout)
        self.assertEqual(result[:4], bytes.fromhex("00040000"))
        self.assertEqual(result[4:14], demos.encode(self.doc["demos"][0]["steps"], 10))
        self.assertEqual(result[14:], b"untouched")
        self.assertEqual(files, ["stage.s", "stream.s"])

    def test_repeated_edits_always_start_from_pristine_stream(self):
        self.doc["demos"][0]["steps"][1]["held"] = 2
        demos.patch(self.image, self.doc, self.layout)
        self.doc["demos"][0]["steps"] = copy.deepcopy(self.steps)
        self.assertEqual(demos.patch(self.image, self.doc, self.layout)[0], self.image)

    def test_invalid_input_is_rejected(self):
        for key, value in [("held", 256), ("pressed", True), ("frames", 0)]:
            document = copy.deepcopy(self.doc)
            document["demos"][0]["steps"][1][key] = value
            with self.assertRaises(demos.DemoError):
                demos.patch(self.image, document, self.layout)

    def test_overflow_is_rejected(self):
        self.doc["demos"][0]["steps"] += [self.steps[1]] * 4
        with self.assertRaises(demos.DemoError):
            demos.patch(self.image, self.doc, self.layout)

    def test_invalid_slot_boundaries_are_rejected(self):
        self.layout["slots"]["stream"]["offset"] = len(self.image)
        with self.assertRaises(demos.DemoError):
            demos.patch(self.image, self.doc, self.layout)

    def test_overlapping_slots_are_rejected(self):
        self.layout["slots"][demos.STAGES]["offset"] = 5
        self.doc["demos"][0]["steps"][1]["held"] = 2
        with self.assertRaises(demos.DemoError):
            demos.patch(self.image, self.doc, self.layout)
