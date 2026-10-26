"""Alien Soldier's art payloads by what draws with them, so the studio can
list and colour each as the game shows it: the player's pieces (the art its
dynamic mappings upload), the art the recorded objects draw from (the
bosses' apart), each level's art (what its stages load into VRAM), the
screens' art, the screen maps some loaders send to VRAM as plane name
tables, then by the names the source gives them the weapons', the bosses',
the screens' and fonts', and the credits' art, and the rest.

Each payload names the colours to show it in: a boss's asset set's palette
command and the line it writes (a boss's art), a recorded object's palette
commands and the line its pieces use, or a stage's or screen's palette list
and the line its tiles are drawn with most there; the weapons' art takes the
player's colours. A payload goes to the
first of those groups it is in.

Nothing here edits anything: the document is read from the source when a
project opens.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from retrolarium_kit.games.alien_soldier import compressed_tiles, mappings, objects, palettes, screens, stages, tiles
from retrolarium_kit.games.alien_soldier.assets import asset_lists
from retrolarium_kit.platform.megadrive.asl import SourceTree, parse_number
from retrolarium_kit.platform.megadrive.vdp import TILE_BYTES


SCHEMA = "alien-soldier.art"
DOCUMENT = "graphics/art.json"
# Gfx_GameVDPRegisterValues puts plane A's name table at $C000 and plane B's
# at $E000; a payload a loader sends only there is a screen map, not tiles.
NAME_TABLES = {0xC000, 0xE000}
# A list a boss's code points a0 at.
LIST_REFERENCE = re.compile(r"(?:lea|movea\.l)\s+#?\(?([A-Za-z_][A-Za-z0-9_]*)\)?(?:\.l|\(pc\))?,a0")
GROUPS = ("hero", "weapons", "objects", "bosses", "level", "screen", "credits", "map", "other")
# The rest by name: (part of the label, group), first match.
BY_NAME = (
    ("Weapon_", "weapons"),
    ("WeaponState", "weapons"),
    ("Boss_", "bosses"),
    ("Font", "screen"),
    ("SegaScreen", "screen"),
    ("Frontend", "screen"),
    ("WeaponSetup", "screen"),
    ("Results", "screen"),
    ("GameOver", "screen"),
    ("DebugMenu", "screen"),
    ("Credits", "credits"),
    ("Ending", "credits"),
)


def _sizes(tree: SourceTree) -> dict[str, int]:
    """Each art payload's size in tiles."""
    found = {}
    for label, codec in compressed_tiles.payloads(tree).items():
        data = tree.data(label)
        found[label] = len(compressed_tiles.decode(codec, data)) // TILE_BYTES
    for label in tiles.payload_labels(tree):
        found[label] = sum(len(block) for _, block in tiles.read_blocks(tree.data(label))) // TILE_BYTES
    return found


def _piece_lines(block: dict[str, Any], attribute: int) -> Counter:
    return Counter(
        (((piece["attributes"] & 0x7FFF) ^ (attribute & 0xF800)) >> 13) & 3
        for item in block["mappings"]
        for piece in item["pieces"]
    )


def _scene_lines(tree: SourceTree, scene: dict[str, Any], sizes: dict[str, int]) -> dict[str, Counter]:
    """The palette lines of the plane words that draw each loaded payload's
    tiles in a stage."""
    ram = stages._ram(tree, scene["map_writes"])
    spans = []
    for load in scene["vram"]:
        if load["source"] in sizes:
            first = (load["vram"] & 0xFFFF) // TILE_BYTES
            spans.append((first, first + sizes[load["source"]], load["source"]))
    found: dict[str, Counter] = {}
    for plane in scene["planes"]:
        if not plane:
            continue
        low, high = stages.regions(plane)["patterns"]
        for at in range(low - stages.RAM_BASE, high - stages.RAM_BASE, 2):
            word = int.from_bytes(ram[at : at + 2], "big")
            tile = word & 0x7FF
            # The last load over a tile is the one it shows.
            for first, end, label in reversed(spans):
                if first <= tile < end:
                    found.setdefault(label, Counter())[(word >> 13) & 3] += 1
                    break
    return found


def export(tree: SourceTree, definitions: list[dict[str, Any]], profile: str) -> dict[str, Any]:
    sizes = _sizes(tree)
    records = objects.recorded(tree)
    blocks = mappings.export(tree)["blocks"]
    found: dict[str, dict[str, Any]] = {}

    def claim(label: str, entry: dict[str, Any]) -> None:
        if label in sizes and label not in found:
            found[label] = entry

    # A boss's art: the palette command its asset set names, on the line that
    # command writes (destination bytes / 32).
    from retrolarium_kit.games.alien_soldier.levels import handlers

    lists = asset_lists(tree)
    commands = palettes.blocks(tree)
    types = handlers(tree)
    for label in tree.order:
        if not label.endswith("AssetSet") or tree.block(label).kind != "mixed":
            continue
        # Type, graphics list (0 when the boss loads its art itself), palette.
        fields = [operand.text for directive in tree.block(label).directives for operand in directive.operands]
        if len(fields) < 3 or parse_number(fields[0]) is None:
            continue
        boss = {"type": parse_number(fields[0]), "list": fields[1], "palette": fields[2]}
        if boss["palette"] not in commands or not commands[boss["palette"]]:
            continue
        line = commands[boss["palette"]][0]["destination"] // 32
        # The set's list, then the lists the boss's own code plays (Sniper
        # Honeyviper loads its art itself).
        names = [boss["list"]] if boss["list"] in lists else []
        handler = types.get(f"${boss['type']:X}")
        if handler in tree.blocks:
            for path in stages._boss_files(tree, handler):
                for item in tree.labels_in(path):
                    names += [name for name in LIST_REFERENCE.findall("\n".join(tree.body(item))) if name in lists]
        for name in names:
            for command in lists[name]:
                if command.command == 7:
                    claim(
                        command.source,
                        {"group": "bosses", "colours": {"commands": [[boss["palette"], 0]]}, "line": line},
                    )
    hero: dict[str, Any] = {"colours": {}, "line": 0}
    for block in blocks:
        if block["kind"] != "dynamic":
            continue
        record = records.get(block["label"])
        attribute = record["attribute"] if record else 0
        line = _piece_lines(block, attribute).most_common(1)[0][0]
        colours = {"commands": record["palettes"]} if record else {}
        if colours and not hero["colours"]:
            hero = {"colours": colours, "line": line}
        for item in block["mappings"]:
            for piece in item["pieces"]:
                claim(piece["art"], {"group": "hero", "colours": colours, "line": line})
    by_label = {block["label"]: block for block in blocks}
    for label, record in records.items():
        line = _piece_lines(by_label[label], record["attribute"]).most_common(1)[0][0]
        for load in record["loads"]:
            group = "bosses" if load["source"].startswith("Boss_") else "objects"
            claim(load["source"], {"group": group, "colours": {"commands": record["palettes"]}, "line": line})

    scene_list = stages.scenes(tree)
    sets, _ = stages.levels(tree, scene_list)
    set_of = {stage: index for index, item in enumerate(sets) for stage in item["stages"]}
    for scene in scene_list:
        lines = _scene_lines(tree, scene, sizes)
        colours = {"list": scene["palette_list"]}
        if scene["visual_palette"]:
            colours["command"] = scene["visual_palette"]
        for load in scene["vram"]:
            counts = lines.get(load["source"])
            entry = {"group": "level", "colours": colours, "line": counts.most_common(1)[0][0] if counts else 0}
            if scene["index"] in set_of:
                entry["set"] = set_of[scene["index"]]
            claim(load["source"], entry)

    loaded_at: dict[str, set[int]] = {}
    for screen in screens.scenes(tree, definitions, profile):
        for load in screen["vram"]:
            loaded_at.setdefault(load["source"], set()).add(load["vram"] & 0xFFFF)
    for screen in screens.scenes(tree, definitions, profile):
        for load in screen["vram"]:
            claim(
                load["source"],
                {"group": "screen", "colours": dict(screen["palette"]), "line": 0, "screen": screen["id"]},
            )
    for commands in asset_lists(tree).values():
        for command in commands:
            if command.command in (2, 3, 7):
                loaded_at.setdefault(command.source, set()).add(command.destination & 0xFFFF)
    for label, addresses in loaded_at.items():
        if addresses <= NAME_TABLES and "Mapping" in label:
            found.pop(label, None)
            claim(label, {"group": "map", "colours": {}, "line": 0})
    for label in sizes:
        group = next((group for part, group in BY_NAME if part in label), "other")
        claim(label, {"group": group, **(hero if group == "weapons" else {"colours": {}, "line": 0})})
    return {
        "schema": SCHEMA,
        "schema_version": 1,
        "payloads": [{"label": label, **found[label]} for label in sizes],
    }
