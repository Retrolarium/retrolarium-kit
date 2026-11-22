"""Build original synthetic SMB test documents, or local corpora from a user's ROM.

Synthetic expectations below are hand specified; they do not call a renderer or
read any game files. The ROM subcommand is deliberately separate and writes only
under an ignored build/ directory.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT.parent / "retrolarium-core/python"))

PROVENANCE = {"kind": "synthetic", "generator": "retrolarium-kit/tools/smb_test_corpus.py", "version": 1}
HEADER_FIELDS = (
    "timer_setting",
    "entrance_control",
    "foreground_or_color",
    "area_style",
    "background_scenery",
    "terrain_control",
)
FILES = ("levels.json", "screens.json", "music.json")


def level_tables() -> dict:
    """Invented marker tiles and simple masks; no values extracted from a game."""
    tables = {
        "terrain_metatiles": [41, 42, 43, 44],
        "terrain_render_masks": [0, 0, 0, 24, 255, 31, 1, 0, 1, 0] + [0, 0] * 11,
        "block_metatiles": list(range(51, 65)),
        "brick_metatiles": [71, 72, 73, 74, 75],
        "hole_metatiles": [0, 0, 0, 0],
        "mushroom_ledge_start": [81],
        "mushroom_ledge_middle": [82],
        "mushroom_ledge_end": [83],
        "mushroom_stem_top": [84],
        "mushroom_stem": [85],
        "cannon_top": [91],
        "cannon_middle": [92],
        "cannon_bottom": [93],
        "flagpole_ball": [101],
        "flagpole_shaft": [102],
        "flagpole_base": [103],
    }
    return {"id": "level-tables", "codec": "nes.byte-tables.v1", "tables": tables}


def _area(name, header, objects, enemies, width, cells, placed_objects, placed_enemies):
    grid = [[0] * 13 for _ in range(width)]
    for x, y, value in cells:
        grid[x][y] = value
    return {
        "name": name,
        "header": header,
        "objects": objects,
        "enemies": enemies,
        "world": 0,
        "expected": {
            "width": width,
            "metatile_sha256": hashlib.sha256(bytes(v for column in grid for v in column)).hexdigest(),
            "objects": placed_objects,
            "enemies": placed_enemies,
        },
    }


def synthetic_levels() -> dict:
    areas = [
        _area(
            "ground_1",
            [0, 0, 0, 0, 0, 0],
            [[3, 4, False, 1], [6, 6, False, 0x22]],
            [["page", 0, 15, False, False, 2, 0, 0], ["enemy", 6, 5, False, True, 6, 0, 0]],
            42,
            [(3, 4, 52)] + [(x, 6, 72) for x in range(6, 9)],
            [[0, 0, 3, 1], [1, 0, 6, 3]],
            [[1, 2, 38]],
        ),
        _area(
            "ground_2",
            [0, 0, 0, 1, 0, 0],
            [[0, 4, False, 0x12]],
            [],
            34,
            [(0, 4, 81), (1, 4, 82), (2, 4, 83), (1, 5, 84)] + [(1, y, 85) for y in range(6, 13)],
            [[0, 0, 0, 3]],
            [],
        ),
        _area(
            "ground_3",
            [0, 0, 0, 2, 0, 0],
            [[0, 4, False, 0x12]],
            [],
            34,
            [(0, 4, 91), (0, 5, 92), (0, 6, 93)],
            [[0, 0, 0, 3]],
            [],
        ),
        # Deliberately unsorted columns, an explicit page jump and an entrance.
        _area(
            "ground_4",
            [0, 0, 0, 0, 0, 0],
            [[10, 3, False, 1], [2, 5, False, 2], [0, 13, False, 2], [1, 7, True, 3]],
            [["entrance", 1, 14, False, False, 4, 2, 3], ["enemy", 2, 4, True, False, 7, 0, 0]],
            52,
            [(10, 3, 52), (2, 5, 53), (49, 7, 54)],
            [[0, 0, 10, 1], [1, 0, 2, 1], [2, 2, 32, 1], [3, 3, 49, 1]],
            [[1, 1, 18]],
        ),
        _area("water_1", [0, 0, 0, 0, 0, 1], [], [], 34, [(x, y, 41) for x in range(34) for y in (11, 12)], [], []),
        _area(
            "underground_1", [0, 0, 0, 0, 0, 1], [], [], 34, [(x, y, 42) for x in range(34) for y in (11, 12)], [], []
        ),
        _area("castle_1", [0, 0, 0, 0, 0, 1], [], [], 34, [(x, y, 44) for x in range(34) for y in (11, 12)], [], []),
        _area(
            "ground_5",
            [0, 0, 0, 0, 0, 1],
            [[4, 12, False, 2]],
            [],
            34,
            [(x, y, 42) for x in range(34) if x not in (4, 5, 6) for y in (11, 12)],
            [[0, 0, 4, 3]],
            [],
        ),
    ]
    return {
        "schema": "retrolarium.smb-level-scene-golden",
        "schema_version": 2,
        "profile": "synthetic",
        "provenance": PROVENANCE,
        "rendering": "smb1",
        "level_tables": level_tables(),
        "areas": areas,
    }


def synthetic_screens() -> dict:
    characters = " ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    encoding = {"characters": characters, "codes": list(range(len(characters))), "case_fold": "upper", "padding": 0}

    def command(start, values, *, repeat=False, vertical=False, length=None):
        return {
            "address": 0x2000 + start,
            "values": values,
            "repeat": repeat,
            "vertical": vertical,
            "length": len(values) if length is None else length,
        }

    def text(start, value):
        return command(start, [characters.index(c) for c in value])

    def screen(key, commands):
        tiles = [0] * 960
        attributes = [0] * 64
        for c in commands:
            for i in range(c["length"]):
                index = c["address"] - 0x2000 + i * (32 if c["vertical"] else 1)
                value = c["values"][0 if c["repeat"] else i]
                if index < 960:
                    tiles[index] = value
                else:
                    attributes[index - 960] = value
        return {
            "key": key,
            "label": key,
            "width": 32,
            "height": 30,
            "capacity": 1 + sum(3 + len(c["values"]) for c in commands),
            "commands": commands,
            "tiles": [tiles[i : i + 32] for i in range(0, 960, 32)],
            "attributes": attributes,
            "trailing": [],
        }

    def field(key, screen_key, start, width):
        return {"key": key, "label": key, "screen": screen_key, "start": start, "width": width}

    # Contract identifiers match the editor API. Contents and write locations are invented.
    game = {
        "id": "game-screen-packets",
        "codec": "nes.ppu-screen-packets.v1",
        "encoding": encoding,
        "screens": [
            screen(
                "world-lives",
                [
                    text(0, "ALPHA"),
                    text(32, "BETA  "),
                    command(64, [7], repeat=True, length=4),
                    command(960, [0x55, 0xAA]),
                    command(100, [2, 3, 4], vertical=True),
                ],
            ),
            screen("time-up", [text(0, "WAITING")]),
            screen("game-over", [text(11, "TRY AGAIN")]),
        ],
        "text_fields": [
            field("status-player", "world-lives", 0, 5),
            field("status-headings", "world-lives", 32, 6),
            field("time-up", "time-up", 0, 7),
            field("game-over", "game-over", 11, 9),
        ],
    }
    warp = {
        "id": "warp-zone-screen",
        "codec": "nes.ppu-screen-packets.v1",
        "encoding": encoding,
        "screens": [screen("warp", [text(2, "GO")])],
        "text_fields": [field("warp", "warp", 2, 2)],
    }
    fixed = {
        "id": "fixed-text",
        "codec": "nes.fixed-text-fields.v1",
        "encoding": encoding,
        "messages": [
            {"key": "probe-a", "label": "Probe A", "width": 5, "text": "DELTA"},
            {"key": "probe-b", "label": "Probe B", "width": 5, "text": "OMEGA"},
        ],
    }
    return {
        "schema": "retrolarium.nes-resource-bundle",
        "schema_version": 1,
        "game": "synthetic",
        "revision": "synthetic",
        "provenance": PROVENANCE,
        "resources": [fixed, game, warp],
    }


def synthetic_music() -> dict:
    # Six ticks, all four channels, a shared header, a rest, control and a noise loop.
    header = {
        "name": "ground_part_1",
        "length_table_offset": 0,
        "stream_offset": 0,
        "triangle_offset": 5,
        "square1_offset": 9,
        "noise_offset": 13,
    }
    resource = {
        "id": "music",
        "codec": "smb.apu-music.v1",
        "headers": [header, {**header, "name": "probe_shared"}],
        "shared_stream_arena": [0x80, 2, 4, 6, 0, 0x80, 2, 0, 4, 0, 2, 4, 6, 0x10, 0],
        "note_lengths": [2, 1, 4, 5, 6, 7, 8, 9],
        "note_periods": [0, 253, 225, 201],
        "selectors": [],
        "ground_schedule": [],
    }
    channels = [
        ["square2", [[3, 0, 0, 2, -1], [0, 1, 0, 2, 1], [0, 2, 2, 2, 2], [0, 3, 4, 2, 3], [6, 4, 6, 0, -1]]],
        ["square1", [[4, 9, 0, 0, -1], [0, 10, 0, 2, 1], [0, 11, 2, 2, 2], [0, 12, 4, 2, 3]]],
        ["triangle", [[3, 5, 0, 2, -1], [0, 6, 0, 2, 1], [1, 7, 2, 2, -1], [0, 8, 4, 2, 2]]],
        ["noise", [[2, 13, 0, 2, -1], [5, 14, 2, 0, -1], [2, 13, 2, 2, -1], [5, 14, 4, 0, -1], [2, 13, 4, 2, -1]]],
    ]
    return {
        "provenance": PROVENANCE,
        "resource": resource,
        "timelines": {h["name"]: {"duration": 6, "channels": copy.deepcopy(channels)} for h in resource["headers"]},
    }


def synthetic_documents() -> dict[str, dict]:
    return dict(zip(FILES, (synthetic_levels(), synthetic_screens(), synthetic_music()), strict=True))


def write_documents(output: Path, documents: dict[str, dict]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    for name, document in documents.items():
        (output / name).write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8", newline="\n")


def rom_output(output: Path) -> Path:
    output = output.resolve()
    if output == (ROOT / "build").resolve() or not output.is_relative_to((ROOT / "build").resolve()):
        raise ValueError("ROM corpora must go in a subdirectory of retrolarium-kit's ignored build/ directory")
    relative = output.relative_to(ROOT.resolve()).as_posix()
    ignored = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "--quiet", "--", relative], check=False)
    tracked = subprocess.check_output(["git", "-C", str(ROOT), "ls-files", "--", relative])
    if ignored.returncode != 0 or tracked:
        raise ValueError("ROM output must be ignored by Git and contain no tracked files")
    return output


def private_publication_files(root: Path) -> list[str]:
    """Check Git publication candidates, including force-added ignored files."""
    names = (
        subprocess.check_output(
            ["git", "-C", str(root), "ls-files", "--cached", "--others", "--exclude-standard", "-z"]
        )
        .decode("utf-8")
        .split("\0")
    )
    problems = []
    for name in filter(None, names):
        path = root / name
        if not path.is_file():
            continue
        if path.suffix.lower() in {".nes", ".fds", ".rom"} or path.name.lower() in {
            "smb1_ju_level_scenes.json",
            "smb1_ju_screens.json",
            "smb1_ju_music.json",
        }:
            problems.append(name)
        elif path.suffix.lower() == ".json":
            try:
                value = json.loads(path.read_bytes())
            except (ValueError, UnicodeError):
                continue
            if isinstance(value, dict) and isinstance(value.get("provenance"), dict):
                if value["provenance"].get("kind") == "rom-derived":
                    problems.append(name)
    return problems


def rom_documents(image: bytes, profile) -> dict[str, dict]:
    """Decode only a hash-verified SMB1 JU ROM, never a guessed offset layout."""
    from retrolarium_kit.core.game_profiles import recognize_revision
    from retrolarium_kit.games.smb.apu import music_header_timeline
    from retrolarium_kit.games.smb.level import positioned_area_objects
    from retrolarium_kit.games.smb.level_render import SmbLevelRendering, object_width, render_level_scene
    from retrolarium_kit.platform.nes.resources import decode_resources

    profile, revision = recognize_revision(image, [profile])
    if profile.game_id != "smb1" or revision.revision_id != "ju":
        raise ValueError("the full integration corpus currently supports only SMB1 JU")
    document = decode_resources(image, profile, revision).as_dict()
    resources = {r["id"]: r for r in document["resources"]}
    specs = {r.resource_id: r for r in profile.resources_for(revision)}
    levels = next(r for r in resources.values() if r["codec"] == "smb.levels.v1")
    tables = resources["level-tables"]
    engine = specs[levels["id"]].parameters["rendering"]
    rendering = SmbLevelRendering.from_resource(tables, engine)
    areas = []
    for area in levels["areas"]:
        # World 0 is explicit: these are engine streams, not playthrough routes.
        scene = render_level_scene(area["name"], area["header"], area["objects"], area["enemies"], rendering=rendering)
        objects = [[o["column"], o["row"], o["page_advance"], o["object_control"]] for o in area["objects"]]
        enemies = [
            [
                e["kind"],
                e["column"],
                e["row"],
                e["page_advance"],
                e["hard_mode"],
                e["object_or_page"],
                e.get("destination_world", 0),
                e.get("destination_page", 0),
            ]
            for e in area["enemies"]
        ]
        areas.append(
            {
                "name": area["name"],
                "world": 0,
                "header": [area["header"][key] for key in HEADER_FIELDS],
                "objects": objects,
                "enemies": enemies,
                "expected": {
                    "width": scene.width,
                    "metatile_sha256": hashlib.sha256(bytes(v for c in scene.metatiles for v in c)).hexdigest(),
                    "objects": [
                        [o["index"], o["page"], o["x"], object_width(o)]
                        for o in positioned_area_objects(area["objects"])
                    ],
                    "enemies": [[e["index"], e["page"], e["x"]] for e in scene.enemies],
                },
            }
        )
    parameters = specs[levels["id"]].parameters
    source = {}
    for kind in ("area", "enemy"):
        start, size = parameters[f"{kind}_file_offset"], parameters[f"{kind}_size"]
        source[f"{kind}_streams_sha1"] = hashlib.sha1(image[start : start + size]).hexdigest()
    provenance = {"kind": "rom-derived", "rom_sha256": revision.sha256, "local_only": True}
    golden = {
        "schema": "retrolarium.smb-level-scene-golden",
        "schema_version": 2,
        "profile": "smb1.ju",
        "provenance": provenance,
        "source": source,
        "rendering": engine,
        "level_tables": tables,
        "areas": areas,
    }
    screen_codecs = {"nes.ppu-screen-packets.v1", "nes.fixed-text-fields.v1"}
    screens = {
        **document,
        "provenance": provenance,
        "resources": [r for r in document["resources"] if r["codec"] in screen_codecs],
    }
    music = next(r for r in resources.values() if r["codec"] == "smb.apu-music.v1")
    music_payload = {key: value for key, value in music.items() if key not in {"id", "kind", "codec"}}
    kinds = {name: i for i, name in enumerate(("note", "rest", "beat", "length", "control", "loop", "end"))}
    timelines = {}
    for header in music["headers"]:
        timeline = music_header_timeline(music_payload, specs[music["id"]].parameters, header["name"])
        timelines[header["name"]] = {
            "duration": timeline["duration"],
            "channels": [
                [
                    c["channel"],
                    [
                        [kinds[e["kind"]], e["byte_offset"], e["tick"], e.get("duration", 0), e.get("period_index", -1)]
                        for e in c["events"]
                    ],
                ]
                for c in timeline["channels"]
            ],
        }
    return dict(
        zip(
            FILES, (golden, screens, {"provenance": provenance, "resource": music, "timelines": timelines}), strict=True
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    synthetic = sub.add_parser("synthetic", help="original public fixtures; no ROM or game checkout needed")
    synthetic.add_argument("--output", type=Path, required=True)
    rom = sub.add_parser("rom", help="private local integration data from your own SMB1 JU ROM")
    rom.add_argument("--rom", type=Path, required=True)
    rom.add_argument("--profile", type=Path, required=True, help="smb1_src/config/game_profile.json")
    rom.add_argument("--output", type=Path, default=ROOT / "build/smb-rom-corpus")
    args = parser.parse_args()
    try:
        if args.command == "synthetic":
            write_documents(args.output, synthetic_documents())
        else:
            from retrolarium_kit.core.game_profiles import load_game_profile

            output = rom_output(args.output)
            documents = rom_documents(args.rom.read_bytes(), load_game_profile(args.profile))
            write_documents(output, documents)
    except (ValueError, OSError) as error:
        parser.exit(1, f"{error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
