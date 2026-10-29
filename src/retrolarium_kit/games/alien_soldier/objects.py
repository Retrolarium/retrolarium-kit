"""Alien Soldier's objects: the sprite mappings grouped by the source file
that holds them, each block with what it draws with — the object attribute
(base tile, palette line, priority), the art loaded where its tiles are and
the palette commands that colour its line — and the animation scripts that
show them.

A static piece takes its tiles from VRAM that the stage, a boss or the
object itself filled; the source does not say which for each mapping. The
game repository records what was seen in play (config/object_art.json, by
mapping block: the attribute, each art payload with the VRAM address it was
loaded at, the palette blocks). A block it does not list takes the context
of the nearest listed block of its file, marked as borrowed. A file with no
listed block that a stage spawns draws with what that stage loads and its
colours; a file with no listed block that is named after a boss (back_stringer_sprite_mappings and
Boss_BackStringerAssetSet) takes that boss's context from the source, marked
as derived: the art its asset set loads (or the lists its code plays), the
stage's palette list and the set's palette command, and an attribute whose base tile is where that art
goes, the priority the boss's code gives the objects drawn from it
($E(a5)), and the palette line that, exclusive-ored with most of the file's
pieces' lines, is the one the boss's command writes: in play the bosses'
own tiles show in that line (Joker's and Deep Strider's in line 3). Other files have no context and their static pieces
draw as outlines. Dynamic pieces carry their own art and need none.

The repository also records what each placement of the stages' spawn lists
drew when the game spawned it (`spawns`: the list and index, the type and
parameter that find it again after edits, the mapping block and frame and
the object attribute), so the levels can show the objects in their art.

Nothing here edits anything: the document is read from the source when a
project opens.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any

from retrolarium_kit.games.alien_soldier import animations, compressed_tiles, mappings, palettes, tiles
from retrolarium_kit.platform.megadrive.asl import SourceTree, parse_number


SCHEMA = "alien-soldier.objects"
DOCUMENT = "graphics/objects.json"
ART = Path("config/object_art.json")
ART_SCHEMA = "alien-soldier.object-art"
# Commands the studio loads art with: LZSS to VRAM, or an uncompressed block.
LZSS_TO_VRAM, UNCOMPRESSED = 7, 1


# A boss's code setting an object's attribute word.
ATTRIBUTE = re.compile(r"^\s+move\.w\s+#(?P<value>[^,\s]+),\$E\(a[0-6]\)\s*$")
# A list a boss's code points a0 at.
LIST_REFERENCE = re.compile(r"(?:lea|movea\.l)\s+#?\(?([A-Za-z_][A-Za-z0-9_]*)\)?(?:\.l|\(pc\))?,a0")
# Words of a file name too general to name a boss by.
GENERAL = {"stage", "shared", "data", "entity", "frontend", "palette", "scene", "options", "password", "planet", "ship"}
PRIORITY = 0x8000
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# Words that close a file name without naming anything (back_stringer_sprite_mappings).
TAIL = {
    "sprite",
    "sprites",
    "mapping",
    "mappings",
    "animation",
    "animations",
    "frames",
    "assets",
    "data",
    "core",
    "and",
}


class ObjectError(ValueError):
    """An object art record that does not fit the source."""


def _number(value: Any) -> int | None:
    return parse_number(value) if isinstance(value, str) else value if isinstance(value, int) else None


def _document(tree: SourceTree) -> dict[str, Any]:
    path = tree.root / ART
    if not path.is_file():
        return {}
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("schema") != ART_SCHEMA or document.get("schema_version") != 1:
        raise ObjectError(f"{ART.as_posix()} is not an Alien Soldier object art record")
    return document


def recorded(tree: SourceTree) -> dict[str, dict[str, Any]]:
    """config/object_art.json's records by mapping block, checked against the
    source; empty when the repository has none."""
    document = _document(tree)
    if not document:
        return {}
    blocks = {block["label"] for block in mappings.export(tree)["blocks"]}
    compressed = set(compressed_tiles.payloads(tree))
    plain = set(tiles.payload_labels(tree))
    colours = palettes.blocks(tree)
    found = {}
    for label, record in document.get("mappings", {}).items():
        attribute = _number(record.get("attribute"))
        if label not in blocks or attribute is None or not 0 <= attribute <= 0xFFFF:
            raise ObjectError(f"{ART.as_posix()}: {label} is not a mapping block with an attribute")
        loads = []
        for source, address in record.get("art", []):
            vram = _number(address)
            if source not in compressed | plain or vram is None or not 0 <= vram < 0x10000 or vram % 32:
                raise ObjectError(f"{ART.as_posix()}: {label} loads {source} at {address}")
            loads.append(
                {"source": source, "command": LZSS_TO_VRAM if source in compressed else UNCOMPRESSED, "vram": vram}
            )
        commands = []
        for name, offset in record.get("palettes", []):
            if name not in colours or not any(command["offset"] == offset for command in colours[name]):
                raise ObjectError(f"{ART.as_posix()}: {label} names no palette command {name}+{offset}")
            commands.append([name, offset])
        found[label] = {"attribute": attribute, "loads": loads, "palettes": commands}
    return found


def boss_contexts(tree: SourceTree, artless: bool = False) -> dict[str, dict[str, Any]]:
    """What each boss's asset set draws with, by the set's label: the loads
    (its list's, or those of the lists its code plays), its palette command
    and an attribute for the first load (see the module text). With
    `artless`, sets that load no art (a Seven Force form keeps the art
    before it and brings its colours) come too, with no loads, as do the
    lettered sets (Sirene's timed ones, which install the cut forms)."""
    from retrolarium_kit.games.alien_soldier.assets import asset_lists
    from retrolarium_kit.games.alien_soldier.levels import handlers
    from retrolarium_kit.games.alien_soldier.stages import _boss_files

    lists = asset_lists(tree)
    types = handlers(tree)
    colours = palettes.blocks(tree)
    offsets = {item["label"]: item["commands"] for item in palettes._lists(tree)}
    fought_on = _stage_palettes(tree)
    found = {}
    for label in tree.order:
        named = label.endswith("AssetSet") or (artless and re.search(r"AssetSet[A-Z]$", label))
        if not named or tree.block(label).kind != "mixed":
            continue
        fields = [operand.text for directive in tree.block(label).directives for operand in directive.operands]
        kind = parse_number(fields[0]) if fields else None
        if len(fields) < 3 or kind is None or fields[2] not in colours:
            continue
        handler = types.get(f"${kind:X}")
        code = (
            [line for path in _boss_files(tree, handler) for item in tree.labels_in(path) for line in tree.body(item)]
            if handler in tree.blocks
            else []
        )
        names = [fields[1]] if fields[1] in lists else []
        names += [name for name in LIST_REFERENCE.findall("\n".join(code)) if name in lists and name not in names]
        loads = [
            {"source": command.source, "command": LZSS_TO_VRAM, "vram": command.destination}
            for name in names
            for command in lists[name]
            if command.command == LZSS_TO_VRAM
        ]
        if not loads and not artless:
            continue
        base = loads[0]["vram"] // 32 if loads else 0
        words = Counter(
            parse_number(match["value"])
            for line in code
            if (match := ATTRIBUTE.match(line)) and parse_number(match["value"]) is not None
        )
        # The priority the code gives the objects drawn from that art.
        priority = next((word & PRIORITY for word, _ in words.most_common() if word & 0x7FF == base), 0)
        # The colours the fight shows: the shared prelude, the stage's list,
        # then the boss's command.
        stage = [list(pair) for pair in offsets.get(fought_on.get(label, ""), [])]
        commands = [[palettes.PRELUDE, 0], *stage, [fields[2], 0]]
        line = colours[fields[2]][0]["destination"] // 32 if colours[fields[2]] else 0
        found[label] = {
            "attribute": priority | base,
            "loads": loads,
            "palettes": commands,
            "line": line,
            "prefix": PurePosixPath(tree.block(handler).file.path).stem.split("_")[0] if handler in tree.blocks else "",
            "type": kind,
        }
    return found


def _stage_palettes(tree: SourceTree) -> dict[str, str]:
    """The palette list of the stage whose handlers load each boss asset set."""
    from retrolarium_kit.games.alien_soldier import stages as stage_module

    scenes = stage_module.scenes(tree)
    found_records = stage_module.records(tree)
    loaders = stage_module._table_routines(tree, stage_module.LOADERS)
    states = stage_module.stage_states(tree, scenes)
    from retrolarium_kit.games.alien_soldier.levels import handlers

    types = handlers(tree)
    found: dict[str, str] = {}
    by_file: dict[str, str] = {}
    for scene in scenes:
        record = found_records.get(scene["record"])
        if not record or scene["index"] >= len(loaders) or not scene["palette_list"]:
            continue
        loader = loaders[scene["index"]]
        own = stage_module._own_handlers(tree, loader, record["state_offset"], states.get(loader, []))
        # The stage's handlers, the routines they call, and the code of the
        # objects they spawn (Viblack, the Seven Force).
        for lines in stage_module._reached(tree, own):
            for line in lines:
                if (match := stage_module.BOSS_SET.match(line)) and match["label"].endswith("AssetSet"):
                    found.setdefault(match["label"], scene["palette_list"])
                elif (match := stage_module.ENTITY.match(line)) and parse_number(match["value"]):
                    spawned = types.get(f"${parse_number(match['value']):X}")
                    for path in stage_module._boss_files(tree, spawned) if spawned in tree.blocks else []:
                        by_file.setdefault(path, scene["palette_list"])
        for routine in own:
            by_file.setdefault(tree.block(routine).file.path, scene["palette_list"])
    # A set loaded elsewhere takes the stage of the file that loads it: a
    # stage's handlers' file, or the code of a boss whose stage is known
    # (Viblack brings Back Stringer in).
    for label, palette_list in list(found.items()):
        fields = [operand.text for directive in tree.block(label).directives for operand in directive.operands]
        handler = types.get(f"${parse_number(fields[0]) or 0:X}") if fields else None
        for path in stage_module._boss_files(tree, handler) if handler in tree.blocks else []:
            by_file.setdefault(path, palette_list)

    # Or a file named like a stage's handlers' (seven_force_form_sequences
    # and seven_force_stage_states).
    def prefix(path: str) -> str:
        return "_".join(PurePosixPath(path).stem.split("_")[:2])

    by_prefix: dict[str, str] = {}
    for path, palette_list in by_file.items():
        by_prefix.setdefault(prefix(path), palette_list)
    for item in tree.order:
        body = tree.body(item)
        path = tree.block(item).file.path
        stage_list = by_file.get(path) or by_prefix.get(prefix(path))
        for line in body:
            match = stage_module.BOSS_SET.match(line)
            if match and match["label"].endswith("AssetSet") and match["label"] not in found and stage_list:
                found[match["label"]] = stage_list
    return found


def _screen_contexts(
    tree: SourceTree,
    contexts: dict[str, dict[str, Any]],
    blocks: list[dict[str, Any]],
    definitions: list[dict[str, Any]],
    profile: str,
) -> None:
    """Blocks still without a context that a screen's code draws (the ending
    planet's burst: ending_starfield_and_planet and the "ending" screen)
    take that screen's art and colours, and the attribute its code gives
    the objects drawn from that art."""
    from retrolarium_kit.games.alien_soldier import screens

    if not definitions:
        return
    missing = {block["label"] for block in blocks if block["label"] not in contexts and block["kind"] == "static"}
    scripts: dict[str, list[str]] = {}
    for script in animations.export(tree)["scripts"]:
        for item in script["entries"]:
            if item["target"] in missing and item["target"] not in scripts.setdefault(script["label"], []):
                scripts[script["label"]].append(item["target"])
    wanted = missing | set(scripts)
    files: dict[str, str] = {}
    for label in tree.order:
        path = tree.block(label).file.path
        for line in tree.body(label):
            for name in IDENTIFIER.findall(line.split(";")[0]):
                if name in wanted and name != label:
                    for target in scripts.get(name, [name]):
                        files.setdefault(target, path)
    offsets = {item["label"]: item["commands"] for item in palettes._lists(tree)}
    by_id = {screen["id"]: screen for screen in screens.scenes(tree, definitions, profile)}
    for block, path in files.items():
        screen = by_id.get(PurePosixPath(path).stem.split("_")[0])
        if screen is None:
            continue
        loads = [
            {"source": load["source"], "command": load["command"], "vram": load["vram"] & 0xFFFF}
            for load in screen["vram"]
        ]
        colour = screen.get("palette", {})
        commands = [[palettes.PRELUDE, 0], *(list(pair) for pair in offsets.get(colour.get("list", ""), []))]
        if colour.get("command"):
            commands.append([colour["command"], 0])
        tiles = {load["vram"] // 32 + offset for load in loads for offset in range(_tile_count(tree, load))}
        code = [line for label in tree.labels_in(path) for line in tree.body(label)]
        words = Counter(
            parse_number(match["value"])
            for line in code
            if (match := ATTRIBUTE.match(line)) and parse_number(match["value"]) is not None
        )
        attribute = next((word for word, _ in words.most_common() if word & 0x7FF in tiles), 0)
        contexts[block] = {
            "attribute": attribute,
            "loads": loads,
            "palettes": commands,
            "borrowed": True,
            "derived": True,
        }


def _tile_count(tree: SourceTree, load: dict[str, Any]) -> int:
    data = tree.data(load["source"])
    codec = compressed_tiles.payloads(tree).get(load["source"])
    return len(compressed_tiles.decode(codec, data) if codec else data) // 32


def _spawning_stage(tree: SourceTree, spawn_list: str) -> dict[str, Any] | None:
    """The loads and colours of the stage whose record names a spawn list."""
    from retrolarium_kit.games.alien_soldier import stages as stage_module

    if not hasattr(_spawning_stage, "cache") or _spawning_stage.cache[0] is not tree:
        found_records = stage_module.records(tree)
        by_list = {}
        for scene in stage_module.scenes(tree):
            record = found_records.get(scene["record"])
            if not record or record["spawn_list"] in by_list:
                continue
            by_list[record["spawn_list"]] = scene_context(tree, scene)
        _spawning_stage.cache = (tree, by_list)
    return _spawning_stage.cache[1].get(spawn_list)


def scene_context(tree: SourceTree, scene: dict[str, Any]) -> dict[str, Any]:
    """The art a stage's scene loads into VRAM and the colours it sets."""
    offsets = {item["label"]: item["commands"] for item in palettes._lists(tree)}
    colours = [[palettes.PRELUDE, 0], *(list(pair) for pair in offsets.get(scene["palette_list"], []))]
    if scene["visual_palette"]:
        colours.append([scene["visual_palette"], 0])
    loads = [
        {"source": load["source"], "command": load["command"], "vram": load["vram"]}
        for load in scene["vram"]
        if load["command"] in (UNCOMPRESSED, LZSS_TO_VRAM)
    ]
    return {"loads": loads, "palettes": colours}


def _boss_of(stem: str, sets: dict[str, dict[str, Any]]) -> str | None:
    """The asset set a mapping file is named after: the longest run of its
    leading words that the set's label holds (joker_sprite_mappings and
    Boss_JokerAssetSet)."""
    words = stem.split("_")
    # The boss whose code's files start like this one (valkirie_core and
    # valkirie_sprite_mappings), else the set named like it.
    by_code = next((label for label, item in sets.items() if item.get("prefix") == words[0]), None)
    if by_code and words[0] not in GENERAL:
        return by_code
    for count in range(len(words), 0, -1):
        key = "".join(words[:count]).lower()
        if len(key) < 4 or (count == 1 and key in GENERAL):
            continue
        match = next((label for label in sets if key in label.lower().replace("_", "")), None)
        if match:
            return match
    return None


def title(stem: str, names: list[str]) -> str:
    """A group's title from its file's name: the words without the closing
    ones, numbers apart, the game's own names as it spells them."""
    words = [part for word in stem.split("_") for part in re.findall(r"[a-z]+|\d[a-z0-9]*", word)]
    while len(words) > 1 and words[-1] in TAIL:
        words.pop()
    spelled = {re.sub(r"[^a-z0-9]", "", name.lower()): name for name in names}
    out, at = [], 0
    while at < len(words):
        for end in range(len(words), at, -1):
            name = spelled.get("".join(words[at:end]))
            if name:
                out.append(name)
                at = end
                break
        else:
            # A number with letters is an entity type in hex (1c0).
            out.append(words[at].upper() if words[at][0].isdigit() else words[at])
            at += 1
    text = " ".join(out)
    return text[:1].upper() + text[1:]


def spawns(tree: SourceTree) -> list[dict[str, Any]]:
    """config/object_art.json's placement records, checked against the
    source."""
    frames = {block["label"]: len(block["mappings"]) for block in mappings.export(tree)["blocks"]}
    found = []
    for record in _document(tree).get("spawns", []):
        values = {key: _number(record.get(key)) for key in ("index", "type", "parameter", "frame", "attribute")}
        if (
            record.get("list") not in tree.blocks
            or record.get("mapping") not in frames
            or any(value is None or not 0 <= value <= 0xFFFF for value in values.values())
            or values["frame"] >= frames[record["mapping"]]
        ):
            raise ObjectError(f"{ART.as_posix()}: spawn record {record} does not fit the source")
        found.append({"list": record["list"], "block": record["mapping"], **values})
    return found


def export(tree: SourceTree, definitions: list[dict[str, Any]] = (), profile: str = "jp") -> dict[str, Any]:
    blocks = mappings.export(tree)["blocks"]
    records = recorded(tree)
    groups: dict[str, list[str]] = {}
    for block in blocks:
        stem = PurePosixPath(tree.block(block["label"]).file.path).stem
        groups.setdefault(stem, []).append(block["label"])
    from retrolarium_kit.core.game_names import GameNames

    # The stage names as the game spells them ("Viblack / Back Stringer": both).
    names = [
        part.strip()
        for value in GameNames.load(tree.root).names.get("stages", {}).values()
        for part in value.split("/")
    ]
    contexts = {}
    sets = boss_contexts(tree)
    by_label = {block["label"]: block for block in blocks}
    for stem, labels in groups.items():
        listed = [index for index, label in enumerate(labels) if label in records]
        boss = None if listed else _boss_of(stem, sets)
        for index, label in enumerate(labels):
            if label in records:
                contexts[label] = {**records[label], "borrowed": False}
            elif listed:
                nearest = min(listed, key=lambda other: (abs(other - index), other))
                contexts[label] = {**records[labels[nearest]], "borrowed": True}
            elif boss and "Cutscene" not in label:
                # The object's palette line, exclusive-ored with the pieces'
                # (most of the file's), gives the line the boss's command
                # writes: Joker's pieces carry it, the Valkirie's object does.
                lines = Counter(
                    piece["attributes"] >> 13 & 3
                    for name in labels
                    for item in by_label[name]["mappings"]
                    for piece in item["pieces"]
                )
                piece_line = lines.most_common(1)[0][0] if lines else 0
                context = {key: value for key, value in sets[boss].items() if key not in ("line", "prefix")}
                context["attribute"] |= (piece_line ^ sets[boss]["line"]) << 13
                contexts[label] = {**context, "borrowed": True, "derived": True}
    _screen_contexts(tree, contexts, blocks, list(definitions), profile)
    # What play recorded matched palette blocks by their colours, not by what
    # wrote them: a block drawn from a boss's art takes that boss's colours
    # (the Seven Force, Xi-Tiger's fighting form).
    owners = {load["source"]: label for label, item in sets.items() for load in item["loads"]}
    for context in contexts.values():
        owner = next((owners[load["source"]] for load in context["loads"] if load["source"] in owners), None)
        if owner and not context.get("derived"):
            context["palettes"] = sets[owner]["palettes"]
    # A borrowed block a stage spawns draws with what that stage loads, in its
    # colours (stage 7's counter alien draws from the art its first state
    # loads, not its file-mate's).
    placed = spawns(tree)
    for spawn in placed:
        context = contexts.get(spawn["block"])
        stage = _spawning_stage(tree, spawn["list"])
        if context and context.get("borrowed") and not context.get("derived") and stage:
            contexts[spawn["block"]] = {"attribute": spawn["attribute"], **stage, "borrowed": True}
    shown: dict[str, list[str]] = {}
    for script in animations.export(tree)["scripts"]:
        for item in script["entries"]:
            if item["duration"] and script["label"] not in shown.setdefault(item["target"], []):
                shown[item["target"]].append(script["label"])
    return {
        "schema": SCHEMA,
        "schema_version": 1,
        "groups": [{"id": stem, "title": title(stem, names), "blocks": labels} for stem, labels in groups.items()],
        "contexts": contexts,
        "animations": shown,
        "spawns": placed,
    }
