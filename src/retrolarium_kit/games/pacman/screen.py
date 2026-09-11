"""Profile-driven Pac-Man screen, PPU packet, HUD and intermission assets."""

from __future__ import annotations

import copy
from typing import Mapping, Sequence

from .authoring import AuthoringError


CODEC = "pacman.screen-collection.v1"


class PacmanScreenError(AuthoringError):
    """A Pac-Man screen layout or editable document is invalid."""


def _integer(value: object, name: str, minimum: int = 0, maximum: int = 0xFFFFFFFF) -> int:
    if (not isinstance(value, int) or isinstance(value, bool)
            or not minimum <= value <= maximum):
        raise PacmanScreenError(f"{name} must be an integer in {minimum}..{maximum}")
    return value


def _bytes(value: object, name: str, count: int | None = None, maximum: int = 0xFF) -> list[int]:
    if not isinstance(value, list) or (count is not None and len(value) != count):
        expected = "a byte array" if count is None else f"exactly {count} bytes"
        raise PacmanScreenError(f"{name} must contain {expected}")
    return [_integer(item, f"{name}[{index}]", maximum=maximum)
            for index, item in enumerate(value)]


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise PacmanScreenError(f"{name} must be an object")
    return value


def _records(value: object, name: str) -> list[Mapping[str, object]]:
    if not isinstance(value, list):
        raise PacmanScreenError(f"{name} must be an array")
    return [_mapping(item, f"{name}[{index}]") for index, item in enumerate(value)]


def _section(spec: Mapping[str, object], name: str) -> tuple[int, int]:
    if set(spec) != {"file_offset", "size"}:
        raise PacmanScreenError(f"{name} layout has the wrong shape")
    return (
        _integer(spec.get("file_offset"), f"{name}.file_offset"),
        _integer(spec.get("size"), f"{name}.size", 1),
    )


def _slice(source: bytes, offset: int, size: int, name: str) -> bytes:
    payload = source[offset:offset + size]
    if len(payload) != size:
        raise PacmanScreenError(f"{name} exceeds the source image")
    return payload


def _packet(source: bytes, offset: int, payload_size: int, name: str) -> dict[str, object]:
    raw = _slice(source, offset, payload_size + 3, name)
    if raw[-1] != 0xFF:
        raise PacmanScreenError(f"{name} lacks its $FF terminator")
    address = raw[0] << 8 | raw[1]
    if not 0x2000 <= address <= 0x3FFF:
        raise PacmanScreenError(f"{name} has an address outside PPU memory")
    if any(value == 0xFF for value in raw[2:-1]):
        raise PacmanScreenError(f"{name} payload contains an early $FF")
    return {"address": address, "bytes": list(raw[2:-1])}


def _encode_packet(value: object, payload_size: int, name: str) -> bytes:
    packet = _mapping(value, name)
    if set(packet) != {"address", "bytes"}:
        raise PacmanScreenError(f"{name} has the wrong shape")
    address = _integer(packet.get("address"), f"{name}.address", 0x2000, 0x3FFF)
    payload = _bytes(packet.get("bytes"), f"{name}.bytes", payload_size, 0xFE)
    return bytes((address >> 8, address & 0xFF, *payload, 0xFF))


def _layout(parameters: Mapping[str, object]) -> dict[str, object]:
    required = {
        "prg_cpu_base", "prg_file_offset", "title_logo", "title_packets",
        "title_attributes", "fixed_blocks", "attract", "intermission_visual",
        "read_only_tables", "encoding", "text_fields",
    }
    if set(parameters) != required:
        raise PacmanScreenError("Pac-Man screen layout parameters have the wrong shape")
    prg_cpu_base = _integer(parameters.get("prg_cpu_base"), "prg_cpu_base", maximum=0xFFFF)
    prg_file_offset = _integer(parameters.get("prg_file_offset"), "prg_file_offset")

    logo = _mapping(parameters.get("title_logo"), "title_logo")
    if set(logo) != {"file_offset", "ppu_address", "width", "height"}:
        raise PacmanScreenError("title_logo layout has the wrong shape")
    logo_layout = {
        "file_offset": _integer(logo.get("file_offset"), "title_logo.file_offset"),
        "ppu_address": _integer(logo.get("ppu_address"), "title_logo.ppu_address", 0x2000, 0x3FFF),
        "width": _integer(logo.get("width"), "title_logo.width", 1, 256),
        "height": _integer(logo.get("height"), "title_logo.height", 1, 256),
    }

    title = _mapping(parameters.get("title_packets"), "title_packets")
    if set(title) != {"file_offset", "payload_sizes"}:
        raise PacmanScreenError("title_packets layout has the wrong shape")
    title_sizes = tuple(_integer(item, "title packet size", 0, 0x10000)
                        for item in title.get("payload_sizes", [])) \
        if isinstance(title.get("payload_sizes"), list) else ()
    if not title_sizes:
        raise PacmanScreenError("title_packets.payload_sizes must not be empty")

    attributes = _mapping(parameters.get("title_attributes"), "title_attributes")
    if set(attributes) != {"file_offset", "ppu_address", "size"}:
        raise PacmanScreenError("title_attributes layout has the wrong shape")
    attributes_layout = {
        "file_offset": _integer(attributes.get("file_offset"), "title_attributes.file_offset"),
        "ppu_address": _integer(attributes.get("ppu_address"), "title_attributes.ppu_address", 0x2000, 0x3FFF),
        "size": _integer(attributes.get("size"), "title_attributes.size", 1),
    }

    fixed = []
    fixed_keys: set[str] = set()
    for index, item in enumerate(_records(parameters.get("fixed_blocks"), "fixed_blocks")):
        if set(item) != {"key", "label", "file_offset", "size"}:
            raise PacmanScreenError(f"fixed_blocks[{index}] has the wrong shape")
        key = item.get("key")
        label = item.get("label")
        if not isinstance(key, str) or not key or key in fixed_keys:
            raise PacmanScreenError("fixed block keys must be unique non-empty strings")
        if not isinstance(label, str) or not label:
            raise PacmanScreenError(f"fixed block {key} label must be non-empty")
        fixed_keys.add(key)
        fixed.append({"key": key, "label": label,
                      "file_offset": _integer(item.get("file_offset"), f"{key}.file_offset"),
                      "size": _integer(item.get("size"), f"{key}.size", 1)})

    attract = _mapping(parameters.get("attract"), "attract")
    if set(attract) != {"pointer_file_offset", "packet_file_offsets", "payload_sizes"}:
        raise PacmanScreenError("attract layout has the wrong shape")
    attract_offsets = attract.get("packet_file_offsets")
    attract_sizes = attract.get("payload_sizes")
    if (not isinstance(attract_offsets, list) or not isinstance(attract_sizes, list)
            or not attract_offsets or len(attract_offsets) != len(attract_sizes)):
        raise PacmanScreenError("attract offsets and payload sizes must have equal non-zero length")
    normalized_offsets = tuple(_integer(item, "attract packet offset") for item in attract_offsets)
    normalized_sizes = tuple(_integer(item, "attract packet size", 0, 0x10000)
                             for item in attract_sizes)
    if tuple(sorted(normalized_offsets)) != normalized_offsets:
        raise PacmanScreenError("attract packet offsets must be ordered")

    visual = []
    visual_keys: set[str] = set()
    for index, item in enumerate(_records(parameters.get("intermission_visual"), "intermission_visual")):
        if set(item) != {"key", "file_offset", "size", "allowed_values"}:
            raise PacmanScreenError(f"intermission_visual[{index}] has the wrong shape")
        key = item.get("key")
        if not isinstance(key, str) or not key or key in visual_keys:
            raise PacmanScreenError("intermission visual keys must be unique")
        allowed = item.get("allowed_values")
        if not isinstance(allowed, list):
            raise PacmanScreenError(f"intermission visual {key} allowed_values must be an array")
        visual_keys.add(key)
        visual.append({"key": key,
                       "file_offset": _integer(item.get("file_offset"), f"{key}.file_offset"),
                       "size": _integer(item.get("size"), f"{key}.size", 1),
                       "allowed_values": tuple(_integer(value, f"{key}.allowed_values", maximum=0xFF)
                                               for value in allowed)})

    read_only = []
    for index, item in enumerate(_records(parameters.get("read_only_tables"), "read_only_tables")):
        if set(item) != {"key", "cpu_address", "kind", "count"}:
            raise PacmanScreenError(f"read_only_tables[{index}] has the wrong shape")
        key, kind = item.get("key"), item.get("kind")
        if not isinstance(key, str) or not key or kind not in ("bytes", "words"):
            raise PacmanScreenError(f"read_only_tables[{index}] metadata is invalid")
        cpu_address = _integer(item.get("cpu_address"), f"{key}.cpu_address", prg_cpu_base, 0xFFFF)
        read_only.append({"key": key, "cpu_address": cpu_address, "kind": kind,
                          "count": _integer(item.get("count"), f"{key}.count", 1),
                          "file_offset": prg_file_offset + cpu_address - prg_cpu_base})

    encoding = copy.deepcopy(_mapping(parameters.get("encoding"), "encoding"))
    characters = encoding.get("characters")
    codes = encoding.get("codes")
    if (not isinstance(characters, str) or not isinstance(codes, list)
            or len(characters) != len(codes) or len(set(characters)) != len(characters)):
        raise PacmanScreenError("screen text encoding characters/codes are invalid")
    normalized_codes = _bytes(codes, "encoding.codes", len(codes))
    if len(set(normalized_codes)) != len(normalized_codes):
        raise PacmanScreenError("screen text encoding codes must be unique")
    if encoding.get("case_fold") not in ("upper", "lower", "none"):
        raise PacmanScreenError("screen text encoding case_fold is invalid")
    padding = _integer(encoding.get("padding"), "encoding.padding", maximum=0xFF)
    if padding not in normalized_codes:
        raise PacmanScreenError("screen text padding is not mapped")
    encoding = {"characters": characters, "codes": normalized_codes,
                "case_fold": encoding["case_fold"], "padding": padding}

    text_fields = copy.deepcopy(_records(parameters.get("text_fields"), "text_fields"))
    keys: set[str] = set()
    for index, field in enumerate(text_fields):
        if set(field) != {"key", "label", "path", "start", "width"}:
            raise PacmanScreenError(f"text_fields[{index}] has the wrong shape")
        key, label, path = field.get("key"), field.get("label"), field.get("path")
        if not isinstance(key, str) or not key or key in keys:
            raise PacmanScreenError("screen text field keys must be unique")
        if not isinstance(label, str) or not label or not isinstance(path, list):
            raise PacmanScreenError(f"screen text field {key} metadata is invalid")
        keys.add(key)
        field["start"] = _integer(field.get("start"), f"{key}.start")
        field["width"] = _integer(field.get("width"), f"{key}.width", 1)

    return {
        "prg_cpu_base": prg_cpu_base, "prg_file_offset": prg_file_offset,
        "title_logo": logo_layout,
        "title_packets": {"file_offset": _integer(title.get("file_offset"),
            "title_packets.file_offset"), "payload_sizes": title_sizes},
        "title_attributes": attributes_layout, "fixed_blocks": fixed,
        "attract": {"pointer_file_offset": _integer(attract.get("pointer_file_offset"),
            "attract.pointer_file_offset"), "packet_file_offsets": normalized_offsets,
            "payload_sizes": normalized_sizes},
        "intermission_visual": visual, "read_only_tables": read_only,
        "encoding": encoding, "text_fields": text_fields,
    }


def _field_bytes(document: Mapping[str, object], path: Sequence[object]) -> list[int]:
    if len(path) == 3 and path[0] in ("title_packets", "attract_packets") \
            and isinstance(path[1], int) and path[2] == "bytes":
        packets = document.get(path[0])
        if isinstance(packets, list) and 0 <= path[1] < len(packets):
            values = packets[path[1]].get("bytes") if isinstance(packets[path[1]], Mapping) else None
            if isinstance(values, list):
                return values
    if len(path) == 2 and path[0] == "fixed_blocks" and isinstance(path[1], str):
        blocks = document.get("fixed_blocks")
        if isinstance(blocks, Mapping) and isinstance(blocks.get(path[1]), list):
            return blocks[path[1]]
    raise PacmanScreenError("screen text field path is not supported")


def text_field_value(document: Mapping[str, object], key: str) -> str:
    fields = document.get("text_fields")
    encoding = _mapping(document.get("encoding"), "encoding")
    if not isinstance(fields, list):
        raise PacmanScreenError("text_fields must be an array")
    matches = [item for item in fields if isinstance(item, Mapping) and item.get("key") == key]
    if len(matches) != 1:
        raise PacmanScreenError(f"unknown screen text field {key!r}")
    field = matches[0]
    values = _field_bytes(document, field["path"])
    start, width = int(field["start"]), int(field["width"])
    if start + width > len(values):
        raise PacmanScreenError(f"screen text field {key} exceeds its source")
    decode = dict(zip(encoding["codes"], encoding["characters"]))
    try:
        return "".join(decode[value] for value in values[start:start + width]).rstrip()
    except KeyError as exc:
        raise PacmanScreenError(f"screen text field {key} contains unmapped tile ${exc.args[0]:02X}") from exc


def set_text_field(document: Mapping[str, object], key: str, text: str) -> dict[str, object]:
    result = copy.deepcopy(document)
    fields = result.get("text_fields")
    if not isinstance(fields, list):
        raise PacmanScreenError("text_fields must be an array")
    matches = [item for item in fields if isinstance(item, Mapping) and item.get("key") == key]
    if len(matches) != 1:
        raise PacmanScreenError(f"unknown screen text field {key!r}")
    field = matches[0]
    encoding = _mapping(result.get("encoding"), "encoding")
    normalized = text.upper() if encoding["case_fold"] == "upper" else (
        text.lower() if encoding["case_fold"] == "lower" else text)
    width = int(field["width"])
    if len(normalized) > width:
        raise PacmanScreenError(f"screen text field {key} exceeds its fixed width")
    encode = dict(zip(encoding["characters"], encoding["codes"]))
    unsupported = sorted(set(normalized).difference(encode))
    if unsupported:
        raise PacmanScreenError(f"screen text field {key} contains unsupported characters")
    replacement = [encode[character] for character in normalized]
    replacement.extend([encoding["padding"]] * (width - len(replacement)))
    values = _field_bytes(result, field["path"])
    start = int(field["start"])
    values[start:start + width] = replacement
    return result


def decode_screen_collection(source: bytes, parameters: Mapping[str, object]) -> dict[str, object]:
    layout = _layout(parameters)
    logo = layout["title_logo"]
    flat_logo = _slice(source, logo["file_offset"], logo["width"] * logo["height"], "title logo")
    title_packets = []
    cursor = layout["title_packets"]["file_offset"]
    for index, size in enumerate(layout["title_packets"]["payload_sizes"]):
        title_packets.append(_packet(source, cursor, size, f"title packet {index}"))
        cursor += size + 3

    attract_layout = layout["attract"]
    pointer_data = _slice(source, attract_layout["pointer_file_offset"],
                          len(attract_layout["packet_file_offsets"]) * 2, "attract pointers")
    attract_packets = []
    for index, (offset, size) in enumerate(zip(
            attract_layout["packet_file_offsets"], attract_layout["payload_sizes"])):
        pointer = int.from_bytes(pointer_data[index * 2:index * 2 + 2], "little")
        expected = layout["prg_cpu_base"] + offset - layout["prg_file_offset"]
        if pointer != expected:
            raise PacmanScreenError(f"attract pointer {index} differs from the profile")
        attract_packets.append(_packet(source, offset, size, f"attract packet {index}"))

    attributes = layout["title_attributes"]
    fixed_blocks = {
        item["key"]: list(_slice(source, item["file_offset"], item["size"], item["key"]))
        for item in layout["fixed_blocks"]
    }
    visual = {
        item["key"]: list(_slice(source, item["file_offset"], item["size"], item["key"]))
        for item in layout["intermission_visual"]
    }
    read_only = []
    for item in layout["read_only_tables"]:
        width = 2 if item["kind"] == "words" else 1
        raw = _slice(source, item["file_offset"], item["count"] * width, item["key"])
        values = ([int.from_bytes(raw[index:index + 2], "little")
                   for index in range(0, len(raw), 2)] if width == 2 else list(raw))
        read_only.append({key: item[key] for key in ("key", "cpu_address", "kind", "count")}
                         | {"values": values})
    result: dict[str, object] = {
        "title_logo": {"ppu_address": logo["ppu_address"], "width": logo["width"],
                       "height": logo["height"],
                       "tiles": [list(flat_logo[row * logo["width"]:(row + 1) * logo["width"]])
                                 for row in range(logo["height"])]},
        "title_packets": title_packets,
        "title_attributes": {"ppu_address": attributes["ppu_address"],
                             "bytes": list(_slice(source, attributes["file_offset"],
                                                  attributes["size"], "title attributes"))},
        "fixed_blocks": fixed_blocks, "attract_packets": attract_packets,
        "intermission_visual": visual, "read_only_code_tables": read_only,
        "encoding": copy.deepcopy(layout["encoding"]),
        "text_fields": copy.deepcopy(layout["text_fields"]),
    }
    validate_screen_collection(result, parameters)
    return result


def validate_screen_collection(document: Mapping[str, object], parameters: Mapping[str, object]) -> None:
    layout = _layout(parameters)
    expected = {"title_logo", "title_packets", "title_attributes", "fixed_blocks",
                "attract_packets", "intermission_visual", "read_only_code_tables",
                "encoding", "text_fields"}
    if set(document) != expected:
        raise PacmanScreenError("screen collection has the wrong shape")
    logo = _mapping(document.get("title_logo"), "title_logo")
    configured_logo = layout["title_logo"]
    if (logo.get("ppu_address"), logo.get("width"), logo.get("height")) != (
            configured_logo["ppu_address"], configured_logo["width"], configured_logo["height"]):
        raise PacmanScreenError("title logo geometry or PPU origin differs from the profile")
    rows = logo.get("tiles")
    if not isinstance(rows, list) or len(rows) != configured_logo["height"]:
        raise PacmanScreenError("title logo has the wrong height")
    for row in rows:
        _bytes(row, "title_logo.tiles", configured_logo["width"])
    for group, sizes in (("title_packets", layout["title_packets"]["payload_sizes"]),
                         ("attract_packets", layout["attract"]["payload_sizes"])):
        packets = document.get(group)
        if not isinstance(packets, list) or len(packets) != len(sizes):
            raise PacmanScreenError(f"{group} has the wrong packet count")
        for index, (packet, size) in enumerate(zip(packets, sizes)):
            _encode_packet(packet, size, f"{group}[{index}]")
    attributes = _mapping(document.get("title_attributes"), "title_attributes")
    if attributes.get("ppu_address") != layout["title_attributes"]["ppu_address"]:
        raise PacmanScreenError("title attribute PPU origin differs from the profile")
    _bytes(attributes.get("bytes"), "title_attributes.bytes", layout["title_attributes"]["size"])
    fixed = _mapping(document.get("fixed_blocks"), "fixed_blocks")
    if set(fixed) != {item["key"] for item in layout["fixed_blocks"]}:
        raise PacmanScreenError("fixed screen blocks differ from the profile")
    for item in layout["fixed_blocks"]:
        _bytes(fixed.get(item["key"]), item["key"], item["size"])
    if "menu_prompt" in fixed:
        menu = fixed["menu_prompt"]
        if len(menu) < 3 or not 0x2000 <= (menu[0] << 8 | menu[1]) <= 0x3FFF \
                or menu[-1] != 0xFF:
            raise PacmanScreenError("menu_prompt must retain a PPU address and terminator")
    if "pause_tiles" in fixed:
        pause = fixed["pause_tiles"]
        if len(pause) != 12 or pause[5] != 0xFF or pause[11] != 0xFF:
            raise PacmanScreenError("pause_tiles packets must retain their terminators")
    visual = _mapping(document.get("intermission_visual"), "intermission_visual")
    if set(visual) != {item["key"] for item in layout["intermission_visual"]}:
        raise PacmanScreenError("intermission visual tables differ from the profile")
    for item in layout["intermission_visual"]:
        values = _bytes(visual.get(item["key"]), item["key"], item["size"])
        if item["allowed_values"] and any(value not in item["allowed_values"] for value in values):
            raise PacmanScreenError(f"{item['key']} contains a value outside its allowlist")
    if document.get("encoding") != layout["encoding"] or document.get("text_fields") != layout["text_fields"]:
        raise PacmanScreenError("screen text metadata differs from the profile")
    read_only = document.get("read_only_code_tables")
    if not isinstance(read_only, list) or len(read_only) != len(layout["read_only_tables"]):
        raise PacmanScreenError("read-only screen table catalogue differs from the profile")
    for actual, configured in zip(read_only, layout["read_only_tables"]):
        item = _mapping(actual, "read-only table")
        metadata = (item.get("key"), item.get("cpu_address"), item.get("kind"), item.get("count"))
        expected_metadata = tuple(configured[key] for key in ("key", "cpu_address", "kind", "count"))
        if metadata != expected_metadata:
            raise PacmanScreenError("read-only screen table metadata changed")
        maximum = 0xFFFF if configured["kind"] == "words" else 0xFF
        _bytes(item.get("values"), f"{configured['key']}.values", configured["count"], maximum)
    for field in layout["text_fields"]:
        text_field_value(document, field["key"])


def encode_screen_collection(
    document: Mapping[str, object], parameters: Mapping[str, object]
) -> tuple[tuple[int, bytes], ...]:
    validate_screen_collection(document, parameters)
    layout = _layout(parameters)
    spans: list[tuple[int, bytes]] = []
    logo = document["title_logo"]
    spans.append((layout["title_logo"]["file_offset"],
                  bytes(value for row in logo["tiles"] for value in row)))
    title_payload = b"".join(_encode_packet(packet, size, f"title_packets[{index}]")
                             for index, (packet, size) in enumerate(zip(
                                 document["title_packets"],
                                 layout["title_packets"]["payload_sizes"])))
    spans.append((layout["title_packets"]["file_offset"], title_payload))
    spans.append((layout["title_attributes"]["file_offset"],
                  bytes(document["title_attributes"]["bytes"])))
    for item in layout["fixed_blocks"]:
        spans.append((item["file_offset"], bytes(document["fixed_blocks"][item["key"]])))
    for index, (offset, size, packet) in enumerate(zip(
            layout["attract"]["packet_file_offsets"],
            layout["attract"]["payload_sizes"], document["attract_packets"])):
        spans.append((offset, _encode_packet(packet, size, f"attract_packets[{index}]")))
    for item in layout["intermission_visual"]:
        spans.append((item["file_offset"], bytes(document["intermission_visual"][item["key"]])))
    return tuple(spans)
