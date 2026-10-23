from __future__ import annotations

from pathlib import Path
import struct
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from retrolarium_kit.platform.megadrive.sound import VGM_HEADER_SIZE, ChipWrite, encode_vgm


class VgmTests(unittest.TestCase):
    def test_writes_become_commands_between_waits(self) -> None:
        writes = [
            ChipWrite(735, "sn76489", 0x9F),
            ChipWrite(0, "ym2612", 0xF6, 0, 0x28),
            ChipWrite(0, "ym2612", 0xC0, 1, 0xB6),
        ]
        data = encode_vgm(writes, 735 + 10)
        self.assertEqual(data[:4], b"Vgm ")
        self.assertEqual(struct.unpack_from("<I", data, 0x04)[0], len(data) - 4)
        self.assertEqual(struct.unpack_from("<I", data, 0x18)[0], 745)
        self.assertEqual(
            data[VGM_HEADER_SIZE:],
            bytes((0x52, 0x28, 0xF6, 0x53, 0xB6, 0xC0, 0x61, 0xDF, 0x02, 0x50, 0x9F, 0x79, 0x66)),
        )

    def test_unknown_chips_are_refused(self) -> None:
        with self.assertRaises(ValueError):
            encode_vgm([ChipWrite(0, "ay8910", 0)], 1)


if __name__ == "__main__":
    unittest.main()
