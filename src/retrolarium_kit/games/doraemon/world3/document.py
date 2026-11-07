"""Validated room documents and ROM-derived environment context."""

from __future__ import annotations
from collections.abc import Mapping
from retrolarium_kit.core.authoring import AuthoringError
from retrolarium_kit.games.doraemon.hierarchy import encode_hierarchy
from retrolarium_kit.games.doraemon.palettes import encode_room_selectors
from retrolarium_kit.platform.nes.preview import color_rgb
from retrolarium_kit.games.doraemon.world3.geometry import room_number


def generation_constraints(value):
    """Read explicit game-owned event rules, without guessed room exceptions."""
    required = {"fixed_rooms", "extra_passable", "event_destinations", "excluded_rooms"}
    if not isinstance(value, Mapping) or not required <= value.keys() or value.keys() - required - {"start"}:
        raise AuthoringError(
            "generation constraints need fixed_rooms, extra_passable, event_destinations and excluded_rooms"
        )
    result = {}
    for key in ("fixed_rooms", "excluded_rooms"):
        items = value[key]
        if not isinstance(items, list):
            raise AuthoringError(f"{key} must be a list of room IDs")
        result[key] = sorted({room_number(i) for i in items})
        if len(result[key]) != len(items):
            raise AuthoringError(f"{key} contains duplicate rooms")
    extras = value["extra_passable"]
    if not isinstance(extras, Mapping):
        raise AuthoringError("extra_passable must map decimal room IDs to CHR tile lists")
    result["extra_passable"] = {}
    for key, tiles in extras.items():
        if not isinstance(key, str) or not key.isdecimal() or str(int(key)) != key:
            raise AuthoringError("conditional terrain room IDs must be canonical decimal strings")
        room = room_number(int(key))
        if not isinstance(tiles, list) or any(type(i) is not int or not 0 <= i < 256 for i in tiles):
            raise AuthoringError("conditional passable tiles must be byte IDs")
        result["extra_passable"][room] = sorted(set(tiles))
    result["event_destinations"] = value["event_destinations"]
    if not isinstance(result["event_destinations"], list):
        raise AuthoringError("event_destinations must be a list of room, x, y entries")
    result["start"] = value.get("start", [0, 128, 128])
    for entry in [result["start"], *result["event_destinations"]]:
        if not isinstance(entry, (list, tuple)) or len(entry) != 3 or any(type(i) is not int for i in entry):
            raise AuthoringError("entries need integer room, x and y coordinates")
        room_number(entry[0])
        if not 1 <= entry[1] < 240 or not 8 <= entry[2] < 212:
            raise AuthoringError("entry coordinates are outside the player area")
    return result


def resource(document: Mapping, identifier: str) -> dict:
    matches = [
        item for item in document.get("resources", []) if isinstance(item, dict) and item.get("id") == identifier
    ]
    if len(matches) != 1:
        raise AuthoringError(f"resource document needs exactly one {identifier} resource")
    return matches[0]


def hierarchy_of(document: Mapping) -> dict:
    item = resource(document, "world3-hierarchy")
    hierarchy = {key: value for key, value in item.items() if key not in ("id", "kind", "codec")}
    encode_hierarchy(hierarchy, "world3")
    return hierarchy


def room_themes(document: Mapping) -> list[dict]:
    selectors = resource(document, "world3-room-palette-selectors")["selectors"]
    encode_room_selectors(selectors)
    tables = resource(document, "world3-room-music").get("tables", {})
    classes = tables.get("world3_room_music_classes")
    tracks = tables.get("world3_music_tracks")
    flags = resource(document, "world3-rooms").get("records", [])
    if not isinstance(classes, list) or len(classes) != 64 or not isinstance(tracks, list) or len(tracks) != 4:
        raise AuthoringError("prepare the project with a profile exposing the World 3 music tables")
    if (
        len(flags) != 64
        or any(not isinstance(item, Mapping) for item in flags)
        or any(type(value) is not int or not 0 <= value < 4 for value in classes)
        or any(type(value) is not int or not 0 <= value <= 255 for value in tracks)
    ):
        raise AuthoringError("World 3 themes need 64 valid room flags and music classes")
    result = []
    for room, palette in enumerate(selectors):
        music = classes[room]
        temple = bool(flags[room].get("type03_metasprite")) or music >= 2
        kind = "temple" if temple else "sea" if room < 8 else "cave"
        backdrop = resource(document, f"world3-palette-{palette:02d}")["values"][0]
        result.append(
            {
                "room": room,
                "kind": kind,
                "palette": palette,
                "music_class": music,
                "music_track": tracks[music],
                "backdrop_rgb": color_rgb(backdrop).hex(),
            }
        )
    return result
