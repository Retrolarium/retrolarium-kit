"""Flicky's sound banks assembly made from a layout and the ROM's banks."""

from __future__ import annotations

from pathlib import Path
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from helpers import game_repository
from retrolarium_kit.games.flicky.workspace import sound
from retrolarium_kit.games.flicky.workspace.sound_source import (
    BANKS_ASSET,
    LAYOUT,
    RENDERED,
    SoundSourceError,
    baseline_path,
    banks_from_asset,
    layout,
    render,
)


# A made-up pair of banks in the game's notation: one effect, one song.
SOURCE = """\
                cpu     68000
                org     0

z80ptr          macro   target,bank,zbase
                dc.b    ((zbase+target-bank)&$FF)
                dc.b    (((zbase+target-bank)>>8)&$FF)
                endm

zSFXBank:
zSFXPointerTable:
                z80ptr  zSFX90Header,zSFXBank,$1000 ; $90
zSFX90Header:
                sfxheader zSFX90Voices,$01,1
                sfxtrack  $80,$05,zSFX90Sequence,$F4,$00
zSFX90Sequence:
                dc.b    $EF,$00,$F2 ; $100A
zSFX90Voices:
                dc.b    0,$3A
zMusicBank:
                z80ptr  zMusic81Header,zMusicBank,$1200
zMusic81Header:
                musicheader zMusic81Voices,1,0,$02,$00
                musicfm zMusic81FM0,$00,$20
zMusic81FM0:
                dc.b    $F2
zMusic81Voices:
                dc.b    $2C
"""


# The banks SOURCE writes: the effect's bank is loaded at Z80 $1000, the
# song's at $1200, and each pointer is the label's place in its bank.
BANKS = bytes(
    [0x02, 0x10]  # zSFXPointerTable: zSFX90Header ($1002)
    + [0x0F, 0x10, 0x01, 0x01]  # sfxheader: voices ($100F), scale, count
    + [0x80, 0x05, 0x0C, 0x10, 0xF4, 0x00]  # sfxtrack: sequence ($100C)
    + [0xEF, 0x00, 0xF2]  # zSFX90Sequence
    + [0x00, 0x3A]  # zSFX90Voices
    + [0x02, 0x12]  # zMusicBank: zMusic81Header ($1202)
    + [0x0D, 0x12, 0x01, 0x00, 0x02, 0x00]  # musicheader: voices ($120D)
    + [0x0C, 0x12, 0x00, 0x20]  # musicfm: zMusic81FM0 ($120C)
    + [0xF2]  # zMusic81FM0
    + [0x2C]  # zMusic81Voices
)


class SoundSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.layout = layout(SOURCE)
        self.banks = BANKS

    def test_the_layout_holds_no_data_values(self) -> None:
        self.assertNotIn("$EF", self.layout)
        self.assertNotIn("$3A", self.layout)
        self.assertIn("sfxtrack  $??,$??,zSFX90Sequence,$??,$??", self.layout)
        self.assertIn("dc.b    ?,$??", self.layout)
        # Pointers, bank addresses, comments and macro bodies stay.
        self.assertIn("z80ptr  zSFX90Header,zSFXBank,$1000 ; $90", self.layout)
        self.assertIn("dc.b    ((zbase+target-bank)&$FF)", self.layout)
        self.assertIn("; $100A", self.layout)

    def test_render_gives_back_the_assembly(self) -> None:
        self.assertEqual(SOURCE, render(self.layout, self.banks))
        self.assertEqual(self.layout, layout(render(self.layout, self.banks)))

    def test_a_pointer_that_misses_its_label_is_refused(self) -> None:
        data = bytearray(self.banks)
        data[0] ^= 1
        with self.assertRaisesRegex(SoundSourceError, "zSFX90Header"):
            render(self.layout, bytes(data))

    def test_banks_of_another_size_are_refused(self) -> None:
        with self.assertRaisesRegex(SoundSourceError, "covers"):
            render(self.layout, self.banks + b"\x00")
        with self.assertRaisesRegex(SoundSourceError, "end before"):
            render(self.layout, self.banks[:-1])

    def test_a_layout_needs_numbers_where_the_data_is(self) -> None:
        with self.assertRaisesRegex(SoundSourceError, "not a number"):
            layout(SOURCE.replace("dc.b    $F2", "dc.b    Value"))


FLICKY = game_repository("flicky_src")


@unittest.skipUnless(
    (FLICKY / LAYOUT).is_file() and (FLICKY / BANKS_ASSET).is_file(),
    "the Flicky source with its sound layout and split assets is not available",
)
class FlickySoundBanksTests(unittest.TestCase):
    def test_the_rendered_banks_are_the_document_the_studio_edits(self) -> None:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        for relative in (LAYOUT, BANKS_ASSET):
            (root / relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(FLICKY / relative, root / relative)
        path = baseline_path(root)
        self.assertEqual(root / RENDERED, path)
        text = path.read_text(encoding="utf-8")
        self.assertEqual((root / LAYOUT).read_text(encoding="utf-8"), layout(text))
        self.assertEqual(text, render(layout(text), banks_from_asset(root)))
        document = sound.export_document(path)
        self.assertTrue(any(header["id"] == "zMusic85Header" for header in document["headers"]))
        self.assertTrue(document["sequences"] and document["voice_banks"])


if __name__ == "__main__":
    unittest.main()
