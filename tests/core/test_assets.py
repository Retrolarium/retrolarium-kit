"""Assets manifests: cut assets out of ROM images, check them, verify builds."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import helpers  # noqa: F401  (src/ on sys.path)
from retrolarium_kit.core import assets
from retrolarium_kit.core.assets import AssetsError

IMAGE = bytes(range(256)) * 4


def sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def write_repository(root: Path, **extra) -> Path:
    (root / "game.bin").write_bytes(IMAGE)
    manifest = {
        "schema": "retrolarium.assets",
        "schema_version": 1,
        "output": "assets/generated",
        "images": [
            {"id": "base", "file": "game.bin", "size": len(IMAGE), "sha1": sha1(IMAGE)},
            {"id": "other", "file": "other.bin", "size": 16, "sha1": sha1(bytes(16))},
        ],
        "assets": [
            {"path": "chr/a.chr", "image": "base", "offset": 16, "size": 32, "sha1": sha1(IMAGE[16:48])},
            {"path": "prg/b.prg", "image": "base", "offset": "0x100", "size": 8, "sha1": sha1(IMAGE[0x100:0x108])},
            {"path": "other/c.bin", "image": "other", "offset": 0, "size": 16, "sha1": sha1(bytes(16))},
        ],
        **extra,
    }
    path = root / "assets" / "manifest.json"
    path.parent.mkdir()
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


class AssetsTests(unittest.TestCase):
    def test_split_cuts_the_present_images_and_skips_the_rest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = assets.load(write_repository(root))
            report = assets.split(manifest)
            self.assertEqual(
                ["[OK] base: 2 asset(s) written, 0 unchanged", "[SKIP] other: other.bin is not here"], report
            )
            self.assertEqual(IMAGE[16:48], (root / "assets/generated/chr/a.chr").read_bytes())
            self.assertEqual(["[OK] base: 0 asset(s) written, 2 unchanged"], assets.split(manifest, ["base"]))
            self.assertEqual([], assets.check(manifest, ["base"]))
            self.assertEqual(["assets/generated/other/c.bin is missing"], assets.check(manifest))
            (root / "assets/generated/chr/a.chr").write_bytes(bytes(32))
            self.assertEqual([], assets.check(manifest, ["base"]))
            self.assertEqual(
                ["assets/generated/chr/a.chr differs from base"], assets.check(manifest, ["base"], exact=True)
            )

    def test_a_wrong_image_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = assets.load(write_repository(root))
            (root / "game.bin").write_bytes(IMAGE[:-1] + b"\x00")
            with self.assertRaisesRegex(AssetsError, "is not base"):
                assets.split(manifest)
            with self.assertRaisesRegex(AssetsError, "cannot read"):
                assets.split(manifest, ["other"])

    def test_verify_names_the_first_difference(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = assets.load(write_repository(root))
            built = root / "built.bin"
            built.write_bytes(IMAGE)
            self.assertIn("byte for byte", assets.verify(manifest, "base", built))
            built.write_bytes(IMAGE[:300] + b"\xee" + IMAGE[301:])
            with self.assertRaisesRegex(AssetsError, "first difference at file offset 0x12c"):
                assets.verify(manifest, "base", built)

    def test_manifest_mistakes_are_named(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = write_repository(root)
            data = json.loads(path.read_text(encoding="utf-8"))
            data["assets"][0]["offset"] = len(IMAGE)
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(AssetsError, "outside base"):
                assets.load(path)
            data["assets"][0]["offset"] = 16
            data["assets"][0]["path"] = "../escape.bin"
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(AssetsError, "inside the repository"):
                assets.load(path)


if __name__ == "__main__":
    unittest.main()
