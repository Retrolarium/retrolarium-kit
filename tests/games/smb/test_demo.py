from __future__ import annotations

import hashlib
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from retrolarium_kit.authoring import GameProfile, ResourceSpec, RevisionSpec
from retrolarium_kit.smb_demo import SmbDemoError, build_demo, decode_demo, parse_demo_document


def fixture() -> tuple[bytes, GameProfile, RevisionSpec]:
    actions, durations, engine = 0x8340, 0x8355, 0x836B
    prg = bytearray(0x8000)

    def put(address: int, payload: bytes) -> None:
        prg[address - 0x8000:address - 0x8000 + len(payload)] = payload

    put(actions, bytes((0x01, 0x80, 0x81)) + bytes(18))
    put(durations, bytes((30, 10, 20, 0)) + bytes(18))
    # LDA durations-1,X at +13 and LDA actions-1,X at +21, as sub_demo_engine.
    put(engine + 13, b"\xBD" + (durations - 1).to_bytes(2, "little"))
    put(engine + 21, b"\xBD" + (actions - 1).to_bytes(2, "little"))
    header = b"NES\x1a" + bytes((2, 1, 0x01, 0)) + bytes(8)
    source = header + bytes(prg) + bytes(0x2000)
    revision = RevisionSpec(
        "ju", "SMB probe", len(source), hashlib.sha1(source).hexdigest(),
        hashlib.sha256(source).hexdigest(), "ines", 0,
    )
    profile = GameProfile(
        "smb1", 1, "SMB probe", "nes", ("demo",), (revision,),
        (ResourceSpec("demo", "demo", "smb.demo-input.v1", "fixed", {
            "actions_cpu": actions, "durations_cpu": durations, "engine_cpu": engine, "capacity": 21,
        }),),
    )
    return source, profile, revision


class SmbDemoTests(unittest.TestCase):
    def test_round_trip_and_edit(self) -> None:
        source, profile, revision = fixture()
        document = decode_demo(source, profile, revision)
        self.assertEqual([(30, ["RIGHT"]), (10, ["A"]), (20, ["A", "RIGHT"])],
                         [(s["frames"], s["buttons"]) for s in document.steps])
        self.assertEqual(source, build_demo(source, document, profile, revision)[0])
        value = document.as_dict()
        value["steps"] = value["steps"] + [{"index": 3, "frames": 255, "buttons": ["B", "LEFT"]}]
        output, _ = build_demo(source, parse_demo_document(value, profile, revision), profile, revision)
        self.assertEqual(value, decode_demo(output, profile, revision, _verify_identity=False).as_dict())

    def test_limits(self) -> None:
        source, profile, revision = fixture()
        value = decode_demo(source, profile, revision).as_dict()
        value["steps"][0]["frames"] = 0
        with self.assertRaisesRegex(SmbDemoError, "1-255 frames"):
            parse_demo_document(value, profile, revision)
        value = decode_demo(source, profile, revision).as_dict()
        value["steps"] = [dict(value["steps"][0], index=i) for i in range(22)]
        with self.assertRaisesRegex(SmbDemoError, "1-21 steps"):
            parse_demo_document(value, profile, revision)


if __name__ == "__main__":
    unittest.main()
