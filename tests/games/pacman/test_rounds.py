from __future__ import annotations

from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from helpers import game_repository
from helpers import game_profile
from retrolarium_kit.platform.nes.resources import build_resources, decode_resources, parse_resource_document


REPOSITORY = game_repository("pacman_src")
V10 = REPOSITORY / "Pac-Man (J) (V1.0) [!].nes"
# make build-expanded, or Retrolarium opening the expanded revision, makes it.
EXPANDED = REPOSITORY / "build/expanded/pacman.nes"
TABLES = ("round-profiles", "round-speed-sets", "round-timing-sets", "round-pellet-thresholds",
          "round-release-targets")


def revision(revision_id: str):
    profile = game_profile("pacman")
    return profile, next(item for item in profile.revisions if item.revision_id == revision_id)


def tables(rom: Path, revision_id: str) -> dict[str, dict]:
    profile, spec = revision(revision_id)
    document = decode_resources(rom.read_bytes(), profile, spec)
    return {item["id"]: item for item in document.resources if item["id"] in TABLES}


@unittest.skipUnless(V10.is_file(), "the Pac-Man v1.0 ROM is not available")
class RoundTableTests(unittest.TestCase):
    def test_round_one_and_the_restart_set(self) -> None:
        decoded = tables(V10, "japan-v10")
        self.assertEqual(23, len(decoded["round-profiles"]["records"]))
        self.assertEqual({"speed_set": 0, "frightened_time": 7}, {
            key: decoded["round-profiles"]["records"][0][key] for key in ("speed_set", "frightened_time")})
        self.assertEqual("Restart", decoded["round-release-targets"]["record_labels"][3])

    def test_an_edited_round_lands_in_the_stage_table(self) -> None:
        profile, spec = revision("japan-v10")
        source = V10.read_bytes()
        document = decode_resources(source, profile, spec).as_dict()
        rounds = next(item for item in document["resources"] if item["id"] == "round-profiles")
        rounds["records"][1]["frightened_time"] = 14
        built, _report = build_resources(source, parse_resource_document(document, profile, spec), profile, spec)
        # $EB42 + 6 + 1: round 2's frightened time.
        at = 16 + 0xEB42 - 0xC000 + 7
        self.assertEqual((6, 14), (source[at], built[at]))
        self.assertEqual(1, sum(a != b for a, b in zip(source, built)))

    @unittest.skipUnless(EXPANDED.is_file(), "the expanded Pac-Man image is not built")
    def test_the_expanded_revision_reads_the_relocated_tables(self) -> None:
        self.assertEqual(tables(V10, "japan-v10"), tables(EXPANDED, "japan-v10-nrom256"))


if __name__ == "__main__":
    unittest.main()
