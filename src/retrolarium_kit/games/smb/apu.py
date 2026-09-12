"""Typed Super Mario Bros. APU music tables and shared stream arena."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .authoring import AuthoringError


CODEC = "smb.apu-music.v1"
CHANNELS = frozenset({"square2", "square1", "triangle", "noise"})


class SmbApuError(AuthoringError):
    """An SMB APU music resource violates its fixed engine layout."""


@dataclass(frozen=True)
class HeaderLayout:
    name: str
    size: int


@dataclass(frozen=True)
class EnvelopeLayout:
    name: str
    size: int
    location: str


@dataclass(frozen=True)
class SmbApuLayout:
    cpu_address: int
    selector_names: tuple[str, ...]
    ground_schedule_count: int
    headers: tuple[HeaderLayout, ...]
    shared_stream_size: int
    note_period_count: int
    note_length_count: int
    envelopes: tuple[EnvelopeLayout, ...]

    @property
    def leading_envelope_size(self) -> int:
        return sum(
            item.size for item in self.envelopes if item.location == "leading"
        )

    @property
    def selector_offset(self) -> int:
        return self.leading_envelope_size

    @property
    def header_relative_start(self) -> int:
        return len(self.selector_names) + self.ground_schedule_count

    @property
    def header_start(self) -> int:
        return self.selector_offset + self.header_relative_start

    @property
    def shared_stream_offset(self) -> int:
        return self.header_start + sum(item.size for item in self.headers)

    @property
    def note_period_offset(self) -> int:
        return self.shared_stream_offset + self.shared_stream_size

    @property
    def note_length_offset(self) -> int:
        return self.note_period_offset + self.note_period_count * 2

    @property
    def envelope_offset(self) -> int:
        return self.note_length_offset + self.note_length_count

    @property
    def encoded_size(self) -> int:
        return self.envelope_offset + sum(
            item.size for item in self.envelopes if item.location == "trailing"
        )


def _integer(
    value: object, name: str, *, minimum: int = 0, maximum: int = 0xFFFFFFFF
) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not minimum <= value <= maximum
    ):
        raise SmbApuError(f"{name} must be an integer in {minimum}..{maximum}")
    return value


def _name(value: object, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise SmbApuError(f"{context} must be a non-empty string")
    return value


def _bytes(value: object, count: int, context: str) -> bytes:
    if not isinstance(value, list) or len(value) != count:
        raise SmbApuError(f"{context} must contain {count} bytes")
    return bytes(
        _integer(item, f"{context}[{index}]", maximum=0xFF)
        for index, item in enumerate(value)
    )


def decode_channel_byte(value: int, channel: str) -> dict[str, object]:
    """Classify one byte using the selected SMB music-channel grammar."""
    raw = _integer(value, "channel byte", maximum=0xFF)
    if channel not in CHANNELS:
        raise SmbApuError(f"unknown SMB music channel {channel!r}")
    if channel == "square2":
        if raw == 0:
            return {"kind": "end", "raw": raw}
        if raw & 0x80:
            return {"kind": "length", "raw": raw, "length_index": raw & 7}
        return {"kind": "note", "raw": raw, "period_index": raw // 2}
    if channel == "triangle":
        if raw == 0:
            return {"kind": "rest", "raw": raw}
        if raw & 0x80:
            return {"kind": "length", "raw": raw, "length_index": raw & 7}
        return {"kind": "note", "raw": raw, "period_index": raw // 2}
    if channel == "square1":
        if raw == 0:
            return {"kind": "control", "raw": raw}
        return {
            "kind": "note",
            "raw": raw,
            "period_index": (raw & 0x3E) // 2,
            "length_index": ((raw & 1) << 2) | ((raw >> 6) & 3),
        }
    if raw == 0:
        return {"kind": "loop", "raw": raw}
    return {
        "kind": "beat",
        "raw": raw,
        "beat": ("rest", "short", "strong", "long")[(raw >> 4) & 3],
        "length_index": ((raw & 1) << 2) | ((raw >> 6) & 3),
    }


def encode_channel_command(
    channel: str,
    kind: str,
    *,
    period_index: int | None = None,
    length_index: int | None = None,
    beat: str | None = None,
) -> int:
    """Lower one semantic command to its canonical SMB channel byte."""
    if channel not in CHANNELS:
        raise SmbApuError(f"unknown SMB music channel {channel!r}")
    allowed = {
        "square2": {"end", "length", "note"},
        "triangle": {"rest", "length", "note"},
        "square1": {"control", "note"},
        "noise": {"loop", "beat"},
    }[channel]
    if kind not in allowed:
        raise SmbApuError(f"{kind!r} is not valid for SMB music channel {channel}")
    if kind in {"end", "rest", "control", "loop"}:
        if period_index is not None or length_index is not None or beat is not None:
            raise SmbApuError(f"{kind} does not accept command parameters")
        return 0
    if kind == "length":
        if period_index is not None or beat is not None:
            raise SmbApuError("length accepts only length_index")
        return 0x80 | _integer(length_index, "length_index", maximum=7)
    if kind == "note":
        if beat is not None:
            raise SmbApuError("note does not accept beat")
        maximum = 31 if channel == "square1" else 63
        period = _integer(period_index, "period_index", maximum=maximum)
        if channel != "square1":
            if length_index is not None:
                raise SmbApuError("square2/triangle note does not accept length_index")
            if period == 0:
                raise SmbApuError("period_index 0 has a reserved zero-byte meaning")
            return period * 2
        length = _integer(length_index, "length_index", maximum=7)
        encoded = period * 2 | ((length & 3) << 6) | (length >> 2)
        if encoded == 0:
            raise SmbApuError("square1 note would encode as the control marker")
        return encoded

    if period_index is not None:
        raise SmbApuError("beat does not accept period_index")
    beats = {"rest": 0, "short": 1, "strong": 2, "long": 3}
    if beat not in beats:
        raise SmbApuError("beat must be rest, short, strong or long")
    length = _integer(length_index, "length_index", maximum=7)
    encoded = (beats[beat] << 4) | ((length & 3) << 6) | (length >> 2)
    if encoded == 0:
        raise SmbApuError("noise beat would encode as the loop marker")
    return encoded


def _header_layouts(value: object) -> tuple[HeaderLayout, ...]:
    if not isinstance(value, list) or not value:
        raise SmbApuError("headers must be a non-empty array")
    result = []
    names: set[str] = set()
    for index, raw in enumerate(value):
        item_context = f"headers[{index}]"
        if not isinstance(raw, Mapping) or set(raw) != {"name", "size"}:
            raise SmbApuError(f"{item_context} must contain name and size")
        item_name = _name(raw.get("name"), f"{item_context}.name")
        if item_name in names:
            raise SmbApuError(f"{item_context}.name is duplicated")
        names.add(item_name)
        size = _integer(
            raw.get("size"), f"{item_context}.size",
            minimum=4, maximum=6,
        )
        result.append(HeaderLayout(item_name, size))
    return tuple(result)


def _envelope_layouts(value: object) -> tuple[EnvelopeLayout, ...]:
    if not isinstance(value, list) or not value:
        raise SmbApuError("envelopes must be a non-empty array")
    result = []
    names: set[str] = set()
    for index, raw in enumerate(value):
        context = f"envelopes[{index}]"
        if not isinstance(raw, Mapping) or set(raw) not in (
            {"name", "size"}, {"name", "size", "location"}
        ):
            raise SmbApuError(
                f"{context} must contain name, size and optional location"
            )
        name = _name(raw.get("name"), f"{context}.name")
        if name in names:
            raise SmbApuError(f"{context}.name is duplicated")
        names.add(name)
        size = _integer(
            raw.get("size"), f"{context}.size", minimum=1, maximum=0xFFFF
        )
        location = raw.get("location", "trailing")
        if location not in {"leading", "trailing"}:
            raise SmbApuError(f"{context}.location must be leading or trailing")
        result.append(EnvelopeLayout(name, size, location))
    return tuple(result)


def parse_layout(parameters: Mapping[str, object]) -> SmbApuLayout:
    """Validate the fixed layout needed to decode one SMB music region."""
    required = {
        "cpu_address",
        "selector_names",
        "ground_schedule_count",
        "headers",
        "shared_stream_size",
        "note_period_count",
        "note_length_count",
        "envelopes",
    }
    optional = {"file_offset", "size"}
    if set(parameters) - required - optional or required - set(parameters):
        raise SmbApuError("SMB APU layout parameters have the wrong shape")
    cpu_address = _integer(
        parameters.get("cpu_address"), "cpu_address", maximum=0xFFFF
    )
    raw_selector_names = parameters.get("selector_names")
    if not isinstance(raw_selector_names, list) or not raw_selector_names:
        raise SmbApuError("selector_names must be a non-empty array")
    selector_names = tuple(
        _name(item, f"selector_names[{index}]")
        for index, item in enumerate(raw_selector_names)
    )
    if len(set(selector_names)) != len(selector_names):
        raise SmbApuError("selector_names must be unique")
    headers = _header_layouts(parameters.get("headers"))
    envelopes = _envelope_layouts(parameters.get("envelopes"))
    layout = SmbApuLayout(
        cpu_address=cpu_address,
        selector_names=selector_names,
        ground_schedule_count=_integer(
            parameters.get("ground_schedule_count"), "ground_schedule_count",
            minimum=1, maximum=0xFFFF,
        ),
        headers=tuple(headers),
        shared_stream_size=_integer(
            parameters.get("shared_stream_size"), "shared_stream_size",
            minimum=1, maximum=0xFFFF,
        ),
        note_period_count=_integer(
            parameters.get("note_period_count"), "note_period_count",
            minimum=1, maximum=0x7FFF,
        ),
        note_length_count=_integer(
            parameters.get("note_length_count"), "note_length_count",
            minimum=1, maximum=0xFFFF,
        ),
        envelopes=tuple(envelopes),
    )
    if layout.header_relative_start + sum(
        item.size for item in layout.headers
    ) > 0x100:
        raise SmbApuError("SMB music headers exceed their one-byte offset range")
    if layout.cpu_address < layout.leading_envelope_size or (
        layout.cpu_address + layout.encoded_size - layout.selector_offset > 0x10000
    ):
        raise SmbApuError("SMB APU resource exceeds the CPU address space")
    if "size" in parameters and _integer(
        parameters.get("size"), "size", minimum=1
    ) != layout.encoded_size:
        raise SmbApuError("SMB APU resource size differs from its layout")
    return layout


def _header_offsets(layout: SmbApuLayout) -> dict[str, int]:
    cursor = layout.header_relative_start
    result: dict[str, int] = {}
    for header in layout.headers:
        if cursor > 0xFF:
            raise SmbApuError("SMB music header offset exceeds one byte")
        result[header.name] = cursor
        cursor += header.size
    return result


def _decode_references(
    payload: bytes, start: int, count: int, names_by_offset: Mapping[int, str],
    context: str,
) -> list[str]:
    result = []
    for index, offset in enumerate(payload[start : start + count]):
        if offset not in names_by_offset:
            raise SmbApuError(
                f"{context}[{index}] references unknown header offset {offset}"
            )
        result.append(names_by_offset[offset])
    return result


def _header_fields(size: int) -> set[str]:
    fields = {
        "name", "length_table_offset", "stream_offset", "triangle_offset"
    }
    if size >= 5:
        fields.add("square1_offset")
    if size == 6:
        fields.add("noise_offset")
    return fields


def _validate_channel_offsets(
    header: Mapping[str, object], layout: SmbApuLayout, context: str
) -> tuple[int, int, int | None, int | None]:
    stream = _integer(
        header.get("stream_offset"), f"{context}.stream_offset",
        maximum=layout.shared_stream_size - 1,
    )
    triangle = _integer(
        header.get("triangle_offset"), f"{context}.triangle_offset", maximum=0xFF
    )
    square1 = None
    noise = None
    if "square1_offset" in header:
        square1 = _integer(
            header.get("square1_offset"), f"{context}.square1_offset",
            maximum=0xFF,
        )
    if "noise_offset" in header:
        noise = _integer(
            header.get("noise_offset"), f"{context}.noise_offset", maximum=0xFF
        )
    for channel, relative in (
        ("triangle", triangle), ("square1", square1), ("noise", noise)
    ):
        if relative is not None and stream + relative >= layout.shared_stream_size:
            raise SmbApuError(
                f"{context}.{channel}_offset leaves the shared stream arena"
            )
    return stream, triangle, square1, noise


def decode_music(
    payload: bytes, parameters: Mapping[str, object]
) -> dict[str, object]:
    """Decode selectors, headers and the single shared SMB channel arena."""
    layout = parse_layout(parameters)
    if len(payload) != layout.encoded_size:
        raise SmbApuError(
            f"SMB APU resource has {len(payload)} bytes, expected {layout.encoded_size}"
        )
    offsets = _header_offsets(layout)
    names_by_offset = {offset: name for name, offset in offsets.items()}
    selectors = _decode_references(
        payload, layout.selector_offset, len(layout.selector_names),
        names_by_offset, "selectors"
    )
    schedule = _decode_references(
        payload, layout.selector_offset + len(layout.selector_names),
        layout.ground_schedule_count,
        names_by_offset, "ground_schedule",
    )
    cursor = layout.header_start
    arena_address = (
        layout.cpu_address + layout.shared_stream_offset - layout.selector_offset
    )
    headers: list[dict[str, object]] = []
    for specification in layout.headers:
        raw = payload[cursor : cursor + specification.size]
        pointer = raw[1] | raw[2] << 8
        stream_offset = pointer - arena_address
        header: dict[str, object] = {
            "name": specification.name,
            "length_table_offset": raw[0],
            "stream_offset": stream_offset,
            "triangle_offset": raw[3],
        }
        if specification.size >= 5:
            header["square1_offset"] = raw[4]
        if specification.size == 6:
            header["noise_offset"] = raw[5]
        _validate_channel_offsets(header, layout, f"headers.{specification.name}")
        headers.append(header)
        cursor += specification.size

    period_start = layout.note_period_offset
    periods = [
        payload[index] << 8 | payload[index + 1]
        for index in range(
            period_start, period_start + layout.note_period_count * 2, 2
        )
    ]
    envelope_cursors = {"leading": 0, "trailing": layout.envelope_offset}
    envelopes = []
    for specification in layout.envelopes:
        envelope_cursor = envelope_cursors[specification.location]
        envelopes.append({
            "name": specification.name,
            "values": list(
                payload[envelope_cursor : envelope_cursor + specification.size]
            ),
        })
        envelope_cursors[specification.location] += specification.size
    result: dict[str, object] = {
        "selectors": [
            {"name": name, "header": header}
            for name, header in zip(layout.selector_names, selectors)
        ],
        "ground_schedule": schedule,
        "headers": headers,
        "shared_stream_arena": list(payload[
            layout.shared_stream_offset : layout.note_period_offset
        ]),
        "note_periods": periods,
        "note_lengths": list(payload[
            layout.note_length_offset : layout.envelope_offset
        ]),
        "envelopes": envelopes,
    }
    encode_music(result, parameters)
    return result


def _encode_references(
    value: object, count: int, offsets: Mapping[str, int], context: str
) -> bytes:
    if not isinstance(value, list) or len(value) != count:
        raise SmbApuError(f"{context} must contain {count} entries")
    result = bytearray()
    for index, raw in enumerate(value):
        name = _name(raw, f"{context}[{index}]")
        if name not in offsets:
            raise SmbApuError(f"{context}[{index}] references unknown header {name!r}")
        result.append(offsets[name])
    return bytes(result)


def encode_music(
    value: object, parameters: Mapping[str, object]
) -> bytes:
    """Validate and encode one fixed-size SMB shared-stream music region."""
    layout = parse_layout(parameters)
    if not isinstance(value, Mapping):
        raise SmbApuError("SMB APU resource must be an object")
    expected = {
        "selectors", "ground_schedule", "headers", "shared_stream_arena",
        "note_periods", "note_lengths", "envelopes",
    }
    if set(value) != expected:
        raise SmbApuError("SMB APU resource fields differ from codec v1")
    offsets = _header_offsets(layout)
    raw_selectors = value.get("selectors")
    if not isinstance(raw_selectors, list) or len(raw_selectors) != len(
        layout.selector_names
    ):
        raise SmbApuError(
            f"selectors must contain {len(layout.selector_names)} entries"
        )
    selector_headers = []
    for index, (raw, expected_name) in enumerate(
        zip(raw_selectors, layout.selector_names)
    ):
        context = f"selectors[{index}]"
        if not isinstance(raw, Mapping) or set(raw) != {"name", "header"}:
            raise SmbApuError(f"{context} must contain name and header")
        if raw.get("name") != expected_name:
            raise SmbApuError(f"{context}.name differs from the profile")
        selector_headers.append(raw.get("header"))

    output = bytearray(layout.encoded_size)
    selector_end = layout.selector_offset + len(layout.selector_names)
    output[layout.selector_offset : selector_end] = _encode_references(
        selector_headers, len(layout.selector_names), offsets, "selectors"
    )
    schedule_start = selector_end
    output[schedule_start : layout.header_start] = _encode_references(
        value.get("ground_schedule"), layout.ground_schedule_count, offsets,
        "ground_schedule",
    )

    raw_headers = value.get("headers")
    if not isinstance(raw_headers, list) or len(raw_headers) != len(layout.headers):
        raise SmbApuError(f"headers must contain {len(layout.headers)} records")
    cursor = layout.header_start
    arena_address = (
        layout.cpu_address + layout.shared_stream_offset - layout.selector_offset
    )
    for index, (raw, specification) in enumerate(zip(raw_headers, layout.headers)):
        context = f"headers[{index}]"
        if not isinstance(raw, Mapping) or set(raw) != _header_fields(
            specification.size
        ):
            raise SmbApuError(f"{context} has the wrong shape")
        if raw.get("name") != specification.name:
            raise SmbApuError(f"{context}.name differs from the profile")
        length_offset = _integer(
            raw.get("length_table_offset"), f"{context}.length_table_offset",
            maximum=0xFF,
        )
        stream, triangle, square1, noise = _validate_channel_offsets(
            raw, layout, context
        )
        pointer = arena_address + stream
        encoded = [length_offset, pointer & 0xFF, pointer >> 8, triangle]
        if square1 is not None:
            encoded.append(square1)
        if noise is not None:
            encoded.append(noise)
        output[cursor : cursor + specification.size] = bytes(encoded)
        cursor += specification.size

    output[layout.shared_stream_offset : layout.note_period_offset] = _bytes(
        value.get("shared_stream_arena"), layout.shared_stream_size,
        "shared_stream_arena",
    )
    raw_periods = value.get("note_periods")
    if not isinstance(raw_periods, list) or len(raw_periods) != layout.note_period_count:
        raise SmbApuError(
            f"note_periods must contain {layout.note_period_count} entries"
        )
    period_bytes = bytearray()
    for index, period in enumerate(raw_periods):
        number = _integer(period, f"note_periods[{index}]", maximum=0xFFFF)
        period_bytes.extend((number >> 8, number & 0xFF))
    output[layout.note_period_offset : layout.note_length_offset] = period_bytes
    output[layout.note_length_offset : layout.envelope_offset] = _bytes(
        value.get("note_lengths"), layout.note_length_count, "note_lengths"
    )

    raw_envelopes = value.get("envelopes")
    if not isinstance(raw_envelopes, list) or len(raw_envelopes) != len(
        layout.envelopes
    ):
        raise SmbApuError(f"envelopes must contain {len(layout.envelopes)} tables")
    envelope_cursors = {"leading": 0, "trailing": layout.envelope_offset}
    for index, (raw, specification) in enumerate(
        zip(raw_envelopes, layout.envelopes)
    ):
        context = f"envelopes[{index}]"
        if not isinstance(raw, Mapping) or set(raw) != {"name", "values"}:
            raise SmbApuError(f"{context} must contain name and values")
        if raw.get("name") != specification.name:
            raise SmbApuError(f"{context}.name differs from the profile")
        encoded = _bytes(raw.get("values"), specification.size, f"{context}.values")
        cursor = envelope_cursors[specification.location]
        output[cursor : cursor + specification.size] = encoded
        envelope_cursors[specification.location] += specification.size
    return bytes(output)


def music_channel_spans(
    value: object, parameters: Mapping[str, object]
) -> list[dict[str, object]]:
    """Partition the shared arena by channel entry point and retain aliases."""
    layout = parse_layout(parameters)
    encode_music(value, parameters)
    assert isinstance(value, Mapping)
    raw_headers = value["headers"]
    assert isinstance(raw_headers, list)

    owners_by_offset: dict[int, list[dict[str, str]]] = {}
    for raw_header in raw_headers:
        assert isinstance(raw_header, Mapping)
        header_name = str(raw_header["name"])
        stream, triangle, square1, noise = _validate_channel_offsets(
            raw_header, layout, f"headers.{header_name}"
        )
        entries = [("square2", stream)]
        if square1 not in (None, 0):
            entries.append(("square1", stream + square1))
        entries.append(("triangle", stream + triangle))
        if noise is not None:
            entries.append(("noise", stream + noise))
        for channel, offset in entries:
            owners_by_offset.setdefault(offset, []).append({
                "header": header_name,
                "channel": channel,
            })

    starts = sorted(owners_by_offset)
    return [
        {
            "offset": start,
            "size": (starts[index + 1] if index + 1 < len(starts)
                     else layout.shared_stream_size) - start,
            "owners": owners_by_offset[start],
        }
        for index, start in enumerate(starts)
    ]
