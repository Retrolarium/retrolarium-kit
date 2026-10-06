from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from retrolarium_kit.games import build_game, extract_game


# The ROM is not part of the repository; the test runs where the Solomon's
# Key disassembly sits beside this checkout.
JAPAN = Path("D:/Projects/oranguthang/solomon_no_kagi_src/Solomon no Kagi (J).nes")
RESOURCES = ("levels", "room-art", "animations", "audio", "presentation", "text", "resource-bundle")


@unittest.skipUnless(JAPAN.is_file(), "the Solomon no Kagi ROM is not available")
class SolomonJapanTests(unittest.TestCase):
    def test_every_resource_rebuilds_the_rom_exactly(self) -> None:
        source = JAPAN.read_bytes()
        with tempfile.TemporaryDirectory() as directory:
            for resource in RESOURCES:
                with self.subTest(resource=resource):
                    document = Path(directory) / f"{resource}.json"
                    output = Path(directory) / f"{resource}.nes"
                    extract_game(JAPAN, document, resource=resource)
                    build_game(JAPAN, document, output)
                    self.assertEqual(source, output.read_bytes())


if __name__ == "__main__":
    unittest.main()
