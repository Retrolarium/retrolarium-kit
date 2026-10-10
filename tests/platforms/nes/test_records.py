from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from retrolarium_kit.platform.nes.records import RecordTableError, decode_records, encode_records


IMAGE = bytes(range(64))
PARAMETERS = {
    "records": 3,
    "record_label": "Thing {n}",
    "tables": [
        # Rows: x and y of each record side by side at 10.
        {"file_offset": 10, "fields": [{"name": "x", "label": "X"}, {"name": "y", "label": "Y", "signed": True}]},
        # Columns: three speeds, then three flags; the last speed is the
        # first flag's byte.
        {"file_offset": 20, "columns": True, "fields": [{"name": "speed", "label": "Speed"}]},
        {"file_offset": 22, "fields": [{"name": "flag", "label": "Flag", "locked": [0]}]},
    ],
}


def runs_image(runs: list[tuple[int, bytes]]) -> bytes:
    image = bytearray(IMAGE)
    for offset, data in runs:
        image[offset:offset + len(data)] = data
    return bytes(image)


class RecordTableTests(unittest.TestCase):
    def test_decode_reads_rows_and_columns(self) -> None:
        decoded = decode_records(IMAGE, PARAMETERS, "things")
        self.assertEqual(["Thing 1", "Thing 2", "Thing 3"], decoded["record_labels"])
        self.assertEqual({"x": 10, "y": 11, "speed": 20, "flag": 22}, decoded["records"][0])
        self.assertEqual({"x": 14, "y": 15, "speed": 22, "flag": 24}, decoded["records"][2])

    def test_zero_edit_writes_the_same_bytes(self) -> None:
        document = decode_records(IMAGE, PARAMETERS, "things")
        self.assertEqual(IMAGE, runs_image(encode_records(document, PARAMETERS, "things")))

    def test_signed_fields_and_shared_bytes(self) -> None:
        image = bytearray(IMAGE)
        image[11] = 0xFE
        document = decode_records(bytes(image), PARAMETERS, "things")
        self.assertEqual(-2, document["records"][0]["y"])
        document["records"][0]["y"] = -128
        self.assertEqual(0x80, runs_image(encode_records(document, PARAMETERS, "things"))[11])
        # Speed 3 and flag 1 are one byte; flag 1 is locked, so speed 3
        # writes it alone.
        document["records"][2]["speed"] = 99
        self.assertEqual(99, runs_image(encode_records(document, PARAMETERS, "things"))[22])
        # Flag 2 shares nothing; two unlocked fields on one byte must agree.
        shared = copy.deepcopy(PARAMETERS)
        shared["tables"][2]["fields"][0].pop("locked")
        document = decode_records(IMAGE, shared, "things")
        document["records"][2]["speed"] = 99
        with self.assertRaisesRegex(RecordTableError, "one byte"):
            encode_records(document, shared, "things")

    def test_ranges_choices_none_and_unique(self) -> None:
        parameters = {"records": 3, "record_labels": ["a", "b", "c"], "tables": [{"file_offset": 0, "fields": [
            {"name": "route", "label": "Route", "maximum": 8, "none": 255},
            {"name": "plays", "label": "Plays", "unique": True,
             "choices": [{"value": 1, "label": "one"}, {"value": 4, "label": "four"}, {"value": 7, "label": "seven"}]},
        ]}]}
        image = bytes((255, 1, 2, 4, 8, 7))
        document = decode_records(image, parameters, "routes")
        self.assertEqual([None, 2, 8], [row["route"] for row in document["records"]])
        self.assertEqual(image, runs_image(encode_records(document, parameters, "routes"))[:6])
        for field, value, message in (("route", 9, "0-8"), ("plays", 2, "choices"), ("plays", 4, "differ")):
            edited = copy.deepcopy(document)
            edited["records"][0][field] = value
            with self.assertRaisesRegex(RecordTableError, message):
                encode_records(edited, parameters, "routes")

    def test_the_document_cannot_redescribe_the_fields(self) -> None:
        document = decode_records(IMAGE, PARAMETERS, "things")
        document["fields"][0]["maximum"] = 300
        with self.assertRaisesRegex(RecordTableError, "differ from the profile"):
            encode_records(document, PARAMETERS, "things")
        document = decode_records(IMAGE, PARAMETERS, "things")
        document["records"][1].pop("x")
        with self.assertRaisesRegex(RecordTableError, "fields differ"):
            encode_records(document, PARAMETERS, "things")


if __name__ == "__main__":
    unittest.main()
