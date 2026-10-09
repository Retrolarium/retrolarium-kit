"""Castle Excellent fixed-arena textual MML codec."""

from __future__ import annotations

from collections import Counter
from typing import Mapping

from retrolarium_kit.core.authoring import AuthoringError


CODEC = "castle.mml-music.v1"
LENGTH_BYTES = frozenset(b"@1248")
NOTE_BYTES = frozenset(b"abcdefg")
COMMAND_BYTES = frozenset(b"oltv")


class CastleMmlError(AuthoringError):
    """The Castle music layout or document violates the engine contract."""


def _integer(value: object, name: str, *, minimum: int = 0) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise CastleMmlError(f"{name} must be an integer >= {minimum}")
    return value


def _byte(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 0xFF:
        raise CastleMmlError(f"{name} must be a byte")
    return value


def _names(value: object, name: str, count: int) -> list[str]:
    if (not isinstance(value, list) or len(value) != count
            or any(not isinstance(item, str) or not item for item in value)
            or len(set(value)) != len(value)):
        raise CastleMmlError(f"{name} must contain {count} unique names")
    return list(value)


def _slice(image: bytes, offset: int, size: int, name: str) -> bytes:
    payload = image[offset:offset + size]
    if len(payload) != size:
        raise CastleMmlError(f"{name} exceeds the source image")
    return payload


def validate_mml_text(text: str, name: str = "MML stream") -> dict[str, int]:
    try:
        data = text.encode("ascii")
    except UnicodeEncodeError as exc:
        raise CastleMmlError(f"{name} must be ASCII") from exc
    if not data:
        raise CastleMmlError(f"{name} must not be empty")
    counts: Counter[str] = Counter()
    cursor = 0
    while cursor < len(data):
        value = data[cursor]
        cursor += 1
        if value in COMMAND_BYTES:
            command = chr(value)
            if cursor >= len(data):
                raise CastleMmlError(f"{name} has a missing {command!r} argument")
            argument = data[cursor]
            cursor += 1
            if command in "ot" and argument not in range(ord("0"), ord("9") + 1):
                raise CastleMmlError(f"{name} has an invalid {command!r} argument")
            if command == "l" and argument not in LENGTH_BYTES:
                raise CastleMmlError(f"{name} has an invalid default length")
            if command == "v" and argument not in b"0123456789abcdef":
                raise CastleMmlError(f"{name} has an invalid volume")
            counts[command] += 1
            continue
        if value not in NOTE_BYTES and value != ord("r"):
            raise CastleMmlError(
                f"{name} has invalid byte ${value:02X} at offset {cursor - 1}"
            )
        token = "rest" if value == ord("r") else "note"
        if value in NOTE_BYTES and cursor < len(data) and data[cursor] in b"+-":
            cursor += 1
            counts["accidental"] += 1
        if cursor < len(data) and data[cursor] in LENGTH_BYTES:
            cursor += 1
            counts["explicit_length"] += 1
        if cursor < len(data) and data[cursor] == ord("."):
            cursor += 1
            counts["dotted"] += 1
        counts[token] += 1
    return dict(sorted(counts.items()))


def _layout(parameters: Mapping[str, object]) -> dict[str, object]:
    track_count = _integer(parameters.get("track_count"), "track_count", minimum=1)
    stream_count = _integer(parameters.get("stream_count"), "stream_count", minimum=1)
    channel_names = _names(parameters.get("channel_names"), "channel_names", 3)
    layout = {
        "track_table_file_offset": _integer(
            parameters.get("track_table_file_offset"), "track_table_file_offset"
        ),
        "track_count": track_count,
        "track_names": _names(parameters.get("track_names"), "track_names", track_count),
        "channel_names": channel_names,
        "playback_prefix_file_offset": _integer(
            parameters.get("playback_prefix_file_offset"),
            "playback_prefix_file_offset",
        ),
        "playback_prefix_cpu_address": _integer(
            parameters.get("playback_prefix_cpu_address"),
            "playback_prefix_cpu_address",
        ),
        "playback_prefix_size": _integer(
            parameters.get("playback_prefix_size"),
            "playback_prefix_size",
            minimum=1,
        ),
        "mml_file_offset": _integer(parameters.get("mml_file_offset"), "mml_file_offset"),
        "mml_cpu_address": _integer(parameters.get("mml_cpu_address"), "mml_cpu_address"),
        "mml_capacity": _integer(parameters.get("mml_capacity"), "mml_capacity", minimum=1),
        "stream_count": stream_count,
    }
    if (int(layout["playback_prefix_cpu_address"])
            + int(layout["playback_prefix_size"]) != int(layout["mml_cpu_address"])):
        raise CastleMmlError("Castle playback prefix must end at the MML arena")
    return layout


def decode_music(image: bytes, parameters: Mapping[str, object]) -> dict[str, object]:
    layout = _layout(parameters)
    track_count = int(layout["track_count"])
    stream_count = int(layout["stream_count"])
    mml_cpu = int(layout["mml_cpu_address"])
    track_table = _slice(
        image, int(layout["track_table_file_offset"]), track_count * 8,
        "Castle track table",
    )
    prefix = _slice(
        image, int(layout["playback_prefix_file_offset"]),
        int(layout["playback_prefix_size"]), "Castle playback prefix",
    )
    arena = _slice(
        image, int(layout["mml_file_offset"]), int(layout["mml_capacity"]),
        "Castle MML arena",
    )
    raw_streams: list[tuple[int, str, int]] = []
    cursor = 0
    for index in range(stream_count):
        try:
            end = arena.index(0, cursor)
        except ValueError as exc:
            raise CastleMmlError(f"stream_{index:02d} is unterminated") from exc
        if end + 2 >= len(arena):
            raise CastleMmlError(f"stream_{index:02d} has a truncated restart pointer")
        try:
            text = arena[cursor:end].decode("ascii")
        except UnicodeDecodeError as exc:
            raise CastleMmlError(f"stream_{index:02d} is not ASCII") from exc
        validate_mml_text(text, f"stream_{index:02d}")
        restart = arena[end + 1] | arena[end + 2] << 8
        raw_streams.append((mml_cpu + cursor, text, restart))
        cursor = end + 3
    if any(arena[cursor:]):
        raise CastleMmlError("Castle MML padding must be zero-filled")
    address_to_id = {
        address: f"stream_{index:02d}"
        for index, (address, _text, _restart) in enumerate(raw_streams)
    }
    streams = []
    for index, (_address, text, restart) in enumerate(raw_streams):
        if restart and restart not in address_to_id:
            raise CastleMmlError(
                f"stream_{index:02d} restart does not target a stream boundary"
            )
        streams.append({
            "id": f"stream_{index:02d}",
            "text": text,
            "restart": address_to_id.get(restart),
        })
    track_names = list(layout["track_names"])
    track_sets = []
    for index in range(track_count):
        record = track_table[index * 8:(index + 1) * 8]
        pointers = [record[pos] | record[pos + 1] << 8 for pos in (0, 2, 4)]
        if any(pointer and pointer not in address_to_id for pointer in pointers):
            raise CastleMmlError(f"track set {index} points outside stream boundaries")
        track_sets.append({
            "index": index,
            "name": track_names[index],
            "streams": [address_to_id.get(pointer) for pointer in pointers],
            "channel_parameters": [record[6], record[7]],
        })
    return {
        "capacity_bytes": int(layout["mml_capacity"]),
        "used_bytes": cursor,
        "mml_cpu_address": mml_cpu,
        "playback_prefix_cpu_address": int(layout["playback_prefix_cpu_address"]),
        "channel_names": list(layout["channel_names"]),
        "playback_prefix": list(prefix),
        "track_sets": track_sets,
        "streams": streams,
    }


def encode_music(
    document: Mapping[str, object], parameters: Mapping[str, object]
) -> list[tuple[int, bytes]]:
    layout = _layout(parameters)
    expected_fields = {
        "capacity_bytes", "used_bytes", "mml_cpu_address",
        "playback_prefix_cpu_address", "channel_names", "playback_prefix",
        "track_sets", "streams",
    }
    if set(document) != expected_fields:
        raise CastleMmlError("Castle music fields differ from codec v1")
    capacity = int(layout["mml_capacity"])
    mml_cpu = int(layout["mml_cpu_address"])
    if document.get("capacity_bytes") != capacity:
        raise CastleMmlError("Castle music capacity differs from the profile")
    if document.get("mml_cpu_address") != mml_cpu:
        raise CastleMmlError("Castle MML CPU address differs from the profile")
    if document.get("playback_prefix_cpu_address") != layout["playback_prefix_cpu_address"]:
        raise CastleMmlError("Castle playback CPU address differs from the profile")
    if document.get("channel_names") != layout["channel_names"]:
        raise CastleMmlError("Castle channel names differ from the profile")
    prefix_values = document.get("playback_prefix")
    prefix_size = int(layout["playback_prefix_size"])
    if not isinstance(prefix_values, list) or len(prefix_values) != prefix_size:
        raise CastleMmlError(f"Castle playback prefix must contain {prefix_size} bytes")
    prefix = bytes(
        _byte(value, f"playback_prefix[{index}]")
        for index, value in enumerate(prefix_values)
    )
    stream_count = int(layout["stream_count"])
    raw_streams = document.get("streams")
    if not isinstance(raw_streams, list) or len(raw_streams) != stream_count:
        raise CastleMmlError(f"Castle music must contain {stream_count} streams")
    normalized_streams = []
    ids = {f"stream_{index:02d}" for index in range(stream_count)}
    starts: dict[str, int] = {}
    cursor = 0
    for index, stream in enumerate(raw_streams):
        identifier = f"stream_{index:02d}"
        if not isinstance(stream, dict) or set(stream) != {"id", "text", "restart"}:
            raise CastleMmlError(f"{identifier} fields differ from codec v1")
        if stream.get("id") != identifier:
            raise CastleMmlError(f"music stream {index} must be {identifier}")
        text = stream.get("text")
        if not isinstance(text, str):
            raise CastleMmlError(f"{identifier}.text must be a string")
        validate_mml_text(text, identifier)
        restart = stream.get("restart")
        if restart is not None and restart not in ids:
            raise CastleMmlError(f"{identifier} has an unknown restart stream")
        encoded = text.encode("ascii")
        starts[identifier] = mml_cpu + cursor
        cursor += len(encoded) + 3
        normalized_streams.append((identifier, encoded, restart))
    if cursor > capacity:
        raise CastleMmlError(
            f"Castle MML arena needs {cursor} bytes; capacity is {capacity}"
        )
    if document.get("used_bytes") != cursor:
        raise CastleMmlError("Castle MML used byte count is stale")
    arena = bytearray()
    for _identifier, text, restart in normalized_streams:
        arena.extend(text)
        arena.append(0)
        address = starts[restart] if restart is not None else 0
        arena.extend(address.to_bytes(2, "little"))
    arena.extend(bytes(capacity - len(arena)))

    track_count = int(layout["track_count"])
    raw_tracks = document.get("track_sets")
    if not isinstance(raw_tracks, list) or len(raw_tracks) != track_count:
        raise CastleMmlError(f"Castle music must contain {track_count} track sets")
    track_names = list(layout["track_names"])
    track_table = bytearray()
    for index, track in enumerate(raw_tracks):
        if not isinstance(track, dict) or set(track) != {
                "index", "name", "streams", "channel_parameters"}:
            raise CastleMmlError(f"track set {index} fields differ from codec v1")
        if track.get("index") != index or track.get("name") != track_names[index]:
            raise CastleMmlError(f"track set {index} identity differs from the profile")
        pointers = track.get("streams")
        if (not isinstance(pointers, list) or len(pointers) != 3
                or any(pointer is not None and pointer not in ids for pointer in pointers)):
            raise CastleMmlError(f"track set {index} must contain three stream ids")
        for pointer in pointers:
            track_table.extend((starts[pointer] if pointer is not None else 0)
                               .to_bytes(2, "little"))
        parameters_raw = track.get("channel_parameters")
        if not isinstance(parameters_raw, list) or len(parameters_raw) != 2:
            raise CastleMmlError(f"track set {index} must contain two parameters")
        for parameter_index, value in enumerate(parameters_raw):
            if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 0xFF:
                raise CastleMmlError(
                    f"track set {index} parameter {parameter_index} must be a byte"
                )
            track_table.append(value)
    return [
        (int(layout["track_table_file_offset"]), bytes(track_table)),
        (int(layout["playback_prefix_file_offset"]), prefix),
        (int(layout["mml_file_offset"]), bytes(arena)),
    ]
