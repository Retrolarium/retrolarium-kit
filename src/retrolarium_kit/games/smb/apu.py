"""Typed Super Mario Bros. APU music tables and shared stream arena."""

from __future__ import annotations

import copy
from dataclasses import dataclass
import io
import math
import struct
from typing import Mapping
import wave

from retrolarium_kit.core.authoring import AuthoringError
from retrolarium_kit.core.midi import MidiError, parse_midi, parse_midi_channel, tick_seconds


CODEC = "smb.apu-music.v1"
CHANNELS = frozenset({"square2", "square1", "triangle", "noise"})
NTSC_FRAME_RATE = 60.0988
NTSC_CPU_FREQUENCY = 1_789_773.0
PULSE_DUTY_SEQUENCES = (
    (0, 1, 0, 0, 0, 0, 0, 0),
    (0, 1, 1, 0, 0, 0, 0, 0),
    (0, 1, 1, 1, 1, 0, 0, 0),
    (1, 0, 0, 1, 1, 1, 1, 1),
)
NOISE_PERIODS = (
    4,
    8,
    16,
    32,
    64,
    96,
    128,
    160,
    202,
    254,
    380,
    508,
    762,
    1016,
    2034,
    4068,
)
LENGTH_COUNTER_TABLE = (
    10,
    254,
    20,
    2,
    40,
    4,
    80,
    6,
    160,
    8,
    60,
    10,
    14,
    12,
    26,
    14,
    12,
    16,
    24,
    18,
    48,
    20,
    96,
    22,
    192,
    24,
    72,
    26,
    16,
    28,
    32,
    30,
)
NOISE_REGISTER_SETTINGS = {
    "short": (0x03, 0x18),
    "strong": (0x0C, 0x18),
    "long": (0x03, 0x58),
}


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
        return sum(item.size for item in self.envelopes if item.location == "leading")

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
        return self.envelope_offset + sum(item.size for item in self.envelopes if item.location == "trailing")


def _integer(value: object, name: str, *, minimum: int = 0, maximum: int = 0xFFFFFFFF) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise SmbApuError(f"{name} must be an integer in {minimum}..{maximum}")
    return value


def _name(value: object, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise SmbApuError(f"{context} must be a non-empty string")
    return value


def _positive_number(value: object, name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value <= 0:
        raise SmbApuError(f"{name} must be a positive finite number")
    return float(value)


def _nonnegative_number(value: object, name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < 0:
        raise SmbApuError(f"{name} must be a non-negative finite number")
    return float(value)


def _bytes(value: object, count: int, context: str) -> bytes:
    if not isinstance(value, list) or len(value) != count:
        raise SmbApuError(f"{context} must contain {count} bytes")
    return bytes(_integer(item, f"{context}[{index}]", maximum=0xFF) for index, item in enumerate(value))


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
            raw.get("size"),
            f"{item_context}.size",
            minimum=4,
            maximum=6,
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
        if not isinstance(raw, Mapping) or set(raw) not in ({"name", "size"}, {"name", "size", "location"}):
            raise SmbApuError(f"{context} must contain name, size and optional location")
        name = _name(raw.get("name"), f"{context}.name")
        if name in names:
            raise SmbApuError(f"{context}.name is duplicated")
        names.add(name)
        size = _integer(raw.get("size"), f"{context}.size", minimum=1, maximum=0xFFFF)
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
    optional = {
        "file_offset",
        "size",
        "frame_rate",
        "cpu_frequency",
        "hardware_envelope_event_mask",
    }
    if set(parameters) - required - optional or required - set(parameters):
        raise SmbApuError("SMB APU layout parameters have the wrong shape")
    cpu_address = _integer(parameters.get("cpu_address"), "cpu_address", maximum=0xFFFF)
    raw_selector_names = parameters.get("selector_names")
    if not isinstance(raw_selector_names, list) or not raw_selector_names:
        raise SmbApuError("selector_names must be a non-empty array")
    selector_names = tuple(_name(item, f"selector_names[{index}]") for index, item in enumerate(raw_selector_names))
    if len(set(selector_names)) != len(selector_names):
        raise SmbApuError("selector_names must be unique")
    headers = _header_layouts(parameters.get("headers"))
    envelopes = _envelope_layouts(parameters.get("envelopes"))
    layout = SmbApuLayout(
        cpu_address=cpu_address,
        selector_names=selector_names,
        ground_schedule_count=_integer(
            parameters.get("ground_schedule_count"),
            "ground_schedule_count",
            minimum=1,
            maximum=0xFFFF,
        ),
        headers=tuple(headers),
        shared_stream_size=_integer(
            parameters.get("shared_stream_size"),
            "shared_stream_size",
            minimum=1,
            maximum=0xFFFF,
        ),
        note_period_count=_integer(
            parameters.get("note_period_count"),
            "note_period_count",
            minimum=1,
            maximum=0x7FFF,
        ),
        note_length_count=_integer(
            parameters.get("note_length_count"),
            "note_length_count",
            minimum=1,
            maximum=0xFFFF,
        ),
        envelopes=tuple(envelopes),
    )
    if layout.header_relative_start + sum(item.size for item in layout.headers) > 0x100:
        raise SmbApuError("SMB music headers exceed their one-byte offset range")
    if layout.cpu_address < layout.leading_envelope_size or (
        layout.cpu_address + layout.encoded_size - layout.selector_offset > 0x10000
    ):
        raise SmbApuError("SMB APU resource exceeds the CPU address space")
    if "size" in parameters and _integer(parameters.get("size"), "size", minimum=1) != layout.encoded_size:
        raise SmbApuError("SMB APU resource size differs from its layout")
    for timing_name in ("frame_rate", "cpu_frequency"):
        if timing_name in parameters:
            _positive_number(parameters.get(timing_name), timing_name)
    if "hardware_envelope_event_mask" in parameters:
        _integer(
            parameters.get("hardware_envelope_event_mask"),
            "hardware_envelope_event_mask",
            maximum=0xFF,
        )
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
    payload: bytes,
    start: int,
    count: int,
    names_by_offset: Mapping[int, str],
    context: str,
) -> list[str]:
    result = []
    for index, offset in enumerate(payload[start : start + count]):
        if offset not in names_by_offset:
            raise SmbApuError(f"{context}[{index}] references unknown header offset {offset}")
        result.append(names_by_offset[offset])
    return result


def _header_fields(size: int) -> set[str]:
    fields = {"name", "length_table_offset", "stream_offset", "triangle_offset"}
    if size >= 5:
        fields.add("square1_offset")
    if size == 6:
        fields.add("noise_offset")
    return fields


def _validate_channel_offsets(
    header: Mapping[str, object], layout: SmbApuLayout, context: str
) -> tuple[int, int, int | None, int | None]:
    stream = _integer(
        header.get("stream_offset"),
        f"{context}.stream_offset",
        maximum=layout.shared_stream_size - 1,
    )
    triangle = _integer(header.get("triangle_offset"), f"{context}.triangle_offset", maximum=0xFF)
    square1 = None
    noise = None
    if "square1_offset" in header:
        square1 = _integer(
            header.get("square1_offset"),
            f"{context}.square1_offset",
            maximum=0xFF,
        )
    if "noise_offset" in header:
        noise = _integer(header.get("noise_offset"), f"{context}.noise_offset", maximum=0xFF)
    for channel, relative in (("triangle", triangle), ("square1", square1), ("noise", noise)):
        if relative is not None and stream + relative >= layout.shared_stream_size:
            raise SmbApuError(f"{context}.{channel}_offset leaves the shared stream arena")
    return stream, triangle, square1, noise


def decode_music(payload: bytes, parameters: Mapping[str, object]) -> dict[str, object]:
    """Decode selectors, headers and the single shared SMB channel arena."""
    layout = parse_layout(parameters)
    if len(payload) != layout.encoded_size:
        raise SmbApuError(f"SMB APU resource has {len(payload)} bytes, expected {layout.encoded_size}")
    offsets = _header_offsets(layout)
    names_by_offset = {offset: name for name, offset in offsets.items()}
    selectors = _decode_references(
        payload, layout.selector_offset, len(layout.selector_names), names_by_offset, "selectors"
    )
    schedule = _decode_references(
        payload,
        layout.selector_offset + len(layout.selector_names),
        layout.ground_schedule_count,
        names_by_offset,
        "ground_schedule",
    )
    cursor = layout.header_start
    arena_address = layout.cpu_address + layout.shared_stream_offset - layout.selector_offset
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
        for index in range(period_start, period_start + layout.note_period_count * 2, 2)
    ]
    envelope_cursors = {"leading": 0, "trailing": layout.envelope_offset}
    envelopes = []
    for specification in layout.envelopes:
        envelope_cursor = envelope_cursors[specification.location]
        envelopes.append(
            {
                "name": specification.name,
                "values": list(payload[envelope_cursor : envelope_cursor + specification.size]),
            }
        )
        envelope_cursors[specification.location] += specification.size
    result: dict[str, object] = {
        "selectors": [{"name": name, "header": header} for name, header in zip(layout.selector_names, selectors)],
        "ground_schedule": schedule,
        "headers": headers,
        "shared_stream_arena": list(payload[layout.shared_stream_offset : layout.note_period_offset]),
        "note_periods": periods,
        "note_lengths": list(payload[layout.note_length_offset : layout.envelope_offset]),
        "envelopes": envelopes,
    }
    encode_music(result, parameters)
    return result


def _encode_references(value: object, count: int, offsets: Mapping[str, int], context: str) -> bytes:
    if not isinstance(value, list) or len(value) != count:
        raise SmbApuError(f"{context} must contain {count} entries")
    result = bytearray()
    for index, raw in enumerate(value):
        name = _name(raw, f"{context}[{index}]")
        if name not in offsets:
            raise SmbApuError(f"{context}[{index}] references unknown header {name!r}")
        result.append(offsets[name])
    return bytes(result)


def encode_music(value: object, parameters: Mapping[str, object]) -> bytes:
    """Validate and encode one fixed-size SMB shared-stream music region."""
    layout = parse_layout(parameters)
    if not isinstance(value, Mapping):
        raise SmbApuError("SMB APU resource must be an object")
    expected = {
        "selectors",
        "ground_schedule",
        "headers",
        "shared_stream_arena",
        "note_periods",
        "note_lengths",
        "envelopes",
    }
    if set(value) != expected:
        raise SmbApuError("SMB APU resource fields differ from codec v1")
    offsets = _header_offsets(layout)
    raw_selectors = value.get("selectors")
    if not isinstance(raw_selectors, list) or len(raw_selectors) != len(layout.selector_names):
        raise SmbApuError(f"selectors must contain {len(layout.selector_names)} entries")
    selector_headers = []
    for index, (raw, expected_name) in enumerate(zip(raw_selectors, layout.selector_names)):
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
        value.get("ground_schedule"),
        layout.ground_schedule_count,
        offsets,
        "ground_schedule",
    )

    raw_headers = value.get("headers")
    if not isinstance(raw_headers, list) or len(raw_headers) != len(layout.headers):
        raise SmbApuError(f"headers must contain {len(layout.headers)} records")
    cursor = layout.header_start
    arena_address = layout.cpu_address + layout.shared_stream_offset - layout.selector_offset
    for index, (raw, specification) in enumerate(zip(raw_headers, layout.headers)):
        context = f"headers[{index}]"
        if not isinstance(raw, Mapping) or set(raw) != _header_fields(specification.size):
            raise SmbApuError(f"{context} has the wrong shape")
        if raw.get("name") != specification.name:
            raise SmbApuError(f"{context}.name differs from the profile")
        length_offset = _integer(
            raw.get("length_table_offset"),
            f"{context}.length_table_offset",
            maximum=0xFF,
        )
        stream, triangle, square1, noise = _validate_channel_offsets(raw, layout, context)
        pointer = arena_address + stream
        encoded = [length_offset, pointer & 0xFF, pointer >> 8, triangle]
        if square1 is not None:
            encoded.append(square1)
        if noise is not None:
            encoded.append(noise)
        output[cursor : cursor + specification.size] = bytes(encoded)
        cursor += specification.size

    output[layout.shared_stream_offset : layout.note_period_offset] = _bytes(
        value.get("shared_stream_arena"),
        layout.shared_stream_size,
        "shared_stream_arena",
    )
    raw_periods = value.get("note_periods")
    if not isinstance(raw_periods, list) or len(raw_periods) != layout.note_period_count:
        raise SmbApuError(f"note_periods must contain {layout.note_period_count} entries")
    period_bytes = bytearray()
    for index, period in enumerate(raw_periods):
        number = _integer(period, f"note_periods[{index}]", maximum=0xFFFF)
        period_bytes.extend((number >> 8, number & 0xFF))
    output[layout.note_period_offset : layout.note_length_offset] = period_bytes
    output[layout.note_length_offset : layout.envelope_offset] = _bytes(
        value.get("note_lengths"), layout.note_length_count, "note_lengths"
    )

    raw_envelopes = value.get("envelopes")
    if not isinstance(raw_envelopes, list) or len(raw_envelopes) != len(layout.envelopes):
        raise SmbApuError(f"envelopes must contain {len(layout.envelopes)} tables")
    envelope_cursors = {"leading": 0, "trailing": layout.envelope_offset}
    for index, (raw, specification) in enumerate(zip(raw_envelopes, layout.envelopes)):
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


def music_channel_spans(value: object, parameters: Mapping[str, object]) -> list[dict[str, object]]:
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
        stream, triangle, square1, noise = _validate_channel_offsets(raw_header, layout, f"headers.{header_name}")
        entries = [("square2", stream)]
        if square1 not in (None, 0):
            entries.append(("square1", stream + square1))
        entries.append(("triangle", stream + triangle))
        if noise is not None:
            entries.append(("noise", stream + noise))
        for channel, offset in entries:
            owners_by_offset.setdefault(offset, []).append(
                {
                    "header": header_name,
                    "channel": channel,
                }
            )

    starts = sorted(owners_by_offset)
    return [
        {
            "offset": start,
            "size": (starts[index + 1] if index + 1 < len(starts) else layout.shared_stream_size) - start,
            "owners": owners_by_offset[start],
        }
        for index, start in enumerate(starts)
    ]


def music_selector_context(value: object, parameters: Mapping[str, object], selector_name: str) -> dict[str, object]:
    """Resolve engine flags, envelope mode and timing for one logical selector."""
    encode_music(value, parameters)
    assert isinstance(value, Mapping)
    selectors = value["selectors"]
    assert isinstance(selectors, list)
    matches = [
        (index, item)
        for index, item in enumerate(selectors)
        if isinstance(item, Mapping) and item.get("name") == selector_name
    ]
    if len(matches) != 1:
        raise SmbApuError(f"unknown SMB music selector {selector_name!r}")
    index, selector = matches[0]
    if index < 8:
        kind = "event"
        mask = 1 << index
        hardware_mask = _integer(
            parameters.get("hardware_envelope_event_mask", 0x91),
            "hardware_envelope_event_mask",
            maximum=0xFF,
        )
        if mask & hardware_mask:
            envelope = "hardware"
        elif mask & 0x08:
            envelope = "castle_clear"
        else:
            envelope = "water_event"
    else:
        kind = "area"
        mask = 1 << (index - 8)
        envelope = "area" if mask & 0x7D else "water_event"
    return {
        "name": selector_name,
        "index": index,
        "kind": kind,
        "mask": mask,
        "header": selector["header"],
        "envelope": envelope,
        "length_table_adder": 8 if index == 6 else 0,
    }


def music_byte_readers(value: object, parameters: Mapping[str, object]) -> list[dict[str, object]]:
    """Describe every arena byte fetched by current selectors and schedule entries."""
    encode_music(value, parameters)
    assert isinstance(value, Mapping)
    selectors = value["selectors"]
    schedule = value["ground_schedule"]
    assert isinstance(selectors, list)
    assert isinstance(schedule, list)
    logical_by_context: dict[tuple[str, int], list[str]] = {}
    for raw_selector in selectors:
        assert isinstance(raw_selector, Mapping)
        selector_name = str(raw_selector["name"])
        context = music_selector_context(value, parameters, selector_name)
        key = (str(context["header"]), int(context["length_table_adder"]))
        logical_by_context.setdefault(key, []).append(f"selector:{selector_name}")
    for index, header_name in enumerate(schedule):
        key = (str(header_name), 0)
        logical_by_context.setdefault(key, []).append(f"ground_schedule:{index}")

    readers_by_offset: dict[int, list[dict[str, object]]] = {}
    for (header_name, length_adder), logical_owners in logical_by_context.items():
        timeline = music_header_timeline(
            value,
            parameters,
            header_name,
            length_table_adder=length_adder,
        )
        raw_channels = timeline["channels"]
        assert isinstance(raw_channels, list)
        for raw_channel in raw_channels:
            assert isinstance(raw_channel, Mapping)
            events = raw_channel["events"]
            assert isinstance(events, list)
            offsets = sorted({int(event["byte_offset"]) for event in events if isinstance(event, Mapping)})
            reader = {
                "header": header_name,
                "channel": raw_channel["channel"],
                "length_table_adder": length_adder,
                "logical_owners": list(logical_owners),
            }
            for offset in offsets:
                readers_by_offset.setdefault(offset, []).append(reader)
    return [{"offset": offset, "readers": readers_by_offset[offset]} for offset in sorted(readers_by_offset)]


def lower_midi_channel_commands(
    data: bytes,
    value: object,
    parameters: Mapping[str, object],
    selector_name: str,
    target_channel: str,
    *,
    track: int = 0,
    midi_channel: int = 0,
    max_pitch_error_cents: float = 50.0,
    max_duration_error_frames: float = 1.0,
) -> tuple[list[int], dict[str, object]]:
    """Lower guarded monophonic MIDI notes to canonical SMB channel bytes."""
    if target_channel not in CHANNELS:
        raise SmbApuError("unknown SMB MIDI target channel")
    pitch_limit = _nonnegative_number(max_pitch_error_cents, "max_pitch_error_cents")
    duration_limit = _nonnegative_number(max_duration_error_frames, "max_duration_error_frames")
    try:
        parsed = parse_midi_channel(data, midi_channel) if track < 0 else parse_midi(data, track, midi_channel)
    except MidiError as exc:
        raise SmbApuError(str(exc)) from exc
    if not parsed.notes:
        raise SmbApuError("selected MIDI track contains no notes")
    encode_music(value, parameters)
    assert isinstance(value, Mapping)
    context = music_selector_context(value, parameters, selector_name)
    headers = value["headers"]
    periods = value["note_periods"]
    lengths = value["note_lengths"]
    assert isinstance(headers, list)
    assert isinstance(periods, list)
    assert isinstance(lengths, list)
    header = next(item for item in headers if isinstance(item, Mapping) and item.get("name") == context["header"])
    if target_channel == "square1" and header.get("square1_offset") in (None, 0):
        raise SmbApuError(f"SMB music header {context['header']!r} has no square1 channel")
    if target_channel == "noise" and header.get("noise_offset") is None:
        raise SmbApuError(f"SMB music header {context['header']!r} has no noise channel")
    length_base = _integer(
        header.get("length_table_offset"),
        "length_table_offset",
        maximum=len(lengths) - 1,
    ) + int(context["length_table_adder"])
    duration_choices = [
        (_integer(lengths[length_base + index], "note length", minimum=1, maximum=0xFF), index)
        for index in range(8)
        if length_base + index < len(lengths)
    ]
    if not duration_choices:
        raise SmbApuError("SMB MIDI target has no accessible note lengths")

    frame_rate = _positive_number(parameters.get("frame_rate", NTSC_FRAME_RATE), "frame_rate")
    if target_channel == "noise":
        commands: list[int] = []
        duration_errors: list[float] = []
        output_frames = 0

        def append_noise_segment(start_tick: int, end_tick: int, beat: str, note_index: int) -> None:
            nonlocal output_frames
            seconds = tick_seconds(end_tick, parsed.division, parsed.tempos) - tick_seconds(
                start_tick, parsed.division, parsed.tempos
            )
            exact_frames = seconds * frame_rate
            choices = []
            for duration, length_index in duration_choices:
                try:
                    command = encode_channel_command("noise", "beat", length_index=length_index, beat=beat)
                except SmbApuError:
                    continue
                choices.append((duration, length_index, command))
            if not choices:
                raise SmbApuError(f"SMB noise has no encoding for {beat}")
            maximum_total = max(
                1, math.ceil(exact_frames + duration_limit + max(duration for duration, _index, _command in choices))
            )
            paths: dict[int, list[int]] = {0: []}
            for total in range(1, maximum_total + 1):
                candidates = [
                    paths[total - duration] + [command]
                    for duration, _length_index, command in choices
                    if total >= duration and total - duration in paths
                ]
                if candidates:
                    paths[total] = min(candidates, key=len)
            duration, encoded = min(
                ((total, encoded) for total, encoded in paths.items() if total > 0),
                key=lambda item: (abs(item[0] - exact_frames), len(item[1])),
            )
            duration_error = abs(duration - exact_frames)
            if duration_error > duration_limit:
                raise SmbApuError(
                    f"MIDI noise event {note_index} duration differs from the "
                    f"nearest SMB length by {duration_error:.3f} frames"
                )
            commands.extend(encoded)
            output_frames += duration
            duration_errors.append(duration_error)

        previous_end = 0
        percussion = {
            35: "strong",
            36: "strong",
            38: "strong",
            40: "strong",
            42: "short",
            44: "short",
            46: "short",
            49: "long",
            51: "long",
            52: "long",
            55: "long",
            57: "long",
        }
        for note_index, note in enumerate(parsed.notes):
            if note.start_tick < previous_end:
                raise SmbApuError("SMB noise MIDI events must not overlap")
            if note.start_tick > previous_end:
                append_noise_segment(previous_end, note.start_tick, "rest", note_index)
            append_noise_segment(
                note.start_tick,
                note.end_tick,
                percussion.get(note.note, "short"),
                note_index,
            )
            previous_end = note.end_tick
        if parsed.end_tick > previous_end:
            append_noise_segment(previous_end, parsed.end_tick, "rest", len(parsed.notes))

        uncompressed = list(commands)
        for size in range(1, len(uncompressed)):
            if len(uncompressed) % size == 0 and all(
                command == uncompressed[index % size] for index, command in enumerate(uncompressed)
            ):
                commands = uncompressed[:size] + [encode_channel_command("noise", "loop")]
                break
        source_seconds = tick_seconds(parsed.end_tick, parsed.division, parsed.tempos)
        return commands, {
            "schema": "retrolarium.smb-midi-lowering",
            "schema_version": 1,
            "engine": CODEC,
            "selector": selector_name,
            "header": context["header"],
            "target_channel": target_channel,
            "track": track,
            "midi_channel": midi_channel,
            "source_notes": len(parsed.notes),
            "encoded_bytes": len(commands),
            "output_frames": output_frames,
            "source_seconds": round(source_seconds, 6),
            "maximum_pitch_error_cents": 0.0,
            "maximum_duration_error_frames": round(max(duration_errors, default=0.0), 3),
            "discarded": [{"feature": feature, "events": count} for feature, count in parsed.discarded_events],
        }

    divider = 32.0 if target_channel == "triangle" else 16.0
    cpu_frequency = _positive_number(parameters.get("cpu_frequency", NTSC_CPU_FREQUENCY), "cpu_frequency")
    maximum_period_index = 31 if target_channel == "square1" else 63
    pitch_choices = []
    for index, raw_period in enumerate(periods[: maximum_period_index + 1]):
        period = _integer(raw_period, f"note_periods[{index}]", maximum=0xFFFF)
        if period < 8 or (period & 0xFF) == 0:
            continue
        frequency = cpu_frequency / (divider * ((period & 0x7FF) + 1))
        pitch_choices.append((frequency, index))
    if not pitch_choices:
        raise SmbApuError("SMB MIDI target has no playable note periods")

    commands: list[int] = []
    current_length: int | None = None
    previous_end = 0
    source_seconds = 0.0
    output_frames = 0
    pitch_errors = []
    duration_errors = []
    for note_index, note in enumerate(parsed.notes):
        if note.start_tick != previous_end:
            raise SmbApuError("SMB MIDI lowering requires contiguous notes without rests")
        seconds = tick_seconds(note.end_tick, parsed.division, parsed.tempos) - tick_seconds(
            note.start_tick, parsed.division, parsed.tempos
        )
        exact_frames = seconds * frame_rate
        duration, length_index = min(duration_choices, key=lambda item: (abs(item[0] - exact_frames), item[1]))
        duration_error = abs(duration - exact_frames)
        if duration_error > duration_limit:
            raise SmbApuError(
                f"MIDI note {note_index} duration differs from the nearest SMB length by {duration_error:.3f} frames"
            )
        target_frequency = 440.0 * math.pow(2.0, (note.note - 69) / 12.0)
        frequency, period_index = min(
            pitch_choices,
            key=lambda item: abs(1200.0 * math.log2(item[0] / target_frequency)),
        )
        pitch_error = abs(1200.0 * math.log2(frequency / target_frequency))
        if pitch_error > pitch_limit:
            raise SmbApuError(f"MIDI note {note_index} differs from the nearest SMB period by {pitch_error:.3f} cents")
        if target_channel in {"square2", "triangle"}:
            if current_length != length_index:
                commands.append(encode_channel_command(target_channel, "length", length_index=length_index))
                current_length = length_index
            commands.append(encode_channel_command(target_channel, "note", period_index=period_index))
        else:
            commands.append(
                encode_channel_command(
                    target_channel,
                    "note",
                    period_index=period_index,
                    length_index=length_index,
                )
            )
        source_seconds += seconds
        output_frames += duration
        duration_errors.append(duration_error)
        pitch_errors.append(pitch_error)
        previous_end = note.end_tick
    if target_channel == "square2":
        commands.append(encode_channel_command("square2", "end"))
    return commands, {
        "schema": "retrolarium.smb-midi-lowering",
        "schema_version": 1,
        "engine": CODEC,
        "selector": selector_name,
        "header": context["header"],
        "target_channel": target_channel,
        "track": track,
        "midi_channel": midi_channel,
        "source_notes": len(parsed.notes),
        "encoded_bytes": len(commands),
        "output_frames": output_frames,
        "source_seconds": round(source_seconds, 6),
        "maximum_pitch_error_cents": round(max(pitch_errors), 3),
        "maximum_duration_error_frames": round(max(duration_errors), 3),
        "discarded": [{"feature": feature, "events": count} for feature, count in parsed.discarded_events],
    }


def apply_midi_channel_commands(
    data: bytes,
    value: object,
    parameters: Mapping[str, object],
    selector_name: str,
    target_channel: str,
    *,
    allow_same_channel_sharing: bool = False,
    **options: object,
) -> tuple[dict[str, object], dict[str, object]]:
    """Apply MIDI only when existing timing and physical byte ownership are safe."""
    commands, lowering = lower_midi_channel_commands(data, value, parameters, selector_name, target_channel, **options)
    assert isinstance(value, Mapping)
    context = music_selector_context(value, parameters, selector_name)
    length_adder = int(context["length_table_adder"])
    header_name = str(context["header"])
    timeline = music_header_timeline(value, parameters, header_name, length_table_adder=length_adder)
    if int(lowering["output_frames"]) != int(timeline["duration"]):
        raise SmbApuError(
            "SMB MIDI output duration must equal the existing header duration "
            f"({lowering['output_frames']} != {timeline['duration']} frames)"
        )
    raw_channels = timeline["channels"]
    assert isinstance(raw_channels, list)
    channel = next(
        (item for item in raw_channels if isinstance(item, Mapping) and item.get("channel") == target_channel),
        None,
    )
    if channel is None:
        raise SmbApuError(f"SMB music header {header_name!r} has no {target_channel} channel")
    entry_offset = int(channel["entry_offset"])
    raw_events = channel["events"]
    assert isinstance(raw_events, list)
    existing_offsets = {int(event["byte_offset"]) for event in raw_events if isinstance(event, Mapping)}
    replacement_offsets = list(range(entry_offset, entry_offset + len(commands)))
    if any(offset not in existing_offsets for offset in replacement_offsets):
        raise SmbApuError("SMB MIDI bytes exceed the channel's currently consumed prefix")

    readers = {int(item["offset"]): item["readers"] for item in music_byte_readers(value, parameters)}
    target_identity = (header_name, target_channel, length_adder)
    logical_owners: list[str] = []
    for offset in replacement_offsets:
        raw_readers = readers.get(offset, [])
        assert isinstance(raw_readers, list)
        foreign = [
            reader
            for reader in raw_readers
            if isinstance(reader, Mapping)
            and (
                reader.get("header"),
                reader.get("channel"),
                reader.get("length_table_adder"),
            )
            != target_identity
        ]
        disallowed = [
            reader for reader in foreign if not allow_same_channel_sharing or reader.get("channel") != target_channel
        ]
        if disallowed:
            reader = disallowed[0]
            raise SmbApuError(
                f"SMB MIDI byte {offset} is shared with "
                f"{reader['header']}/{reader['channel']} "
                f"(length adder {reader['length_table_adder']})"
            )
        target_reader = next(
            (
                reader
                for reader in raw_readers
                if isinstance(reader, Mapping)
                and (
                    reader.get("header"),
                    reader.get("channel"),
                    reader.get("length_table_adder"),
                )
                == target_identity
            ),
            None,
        )
        if target_reader is None:
            raise SmbApuError(f"SMB MIDI byte {offset} is not read by the target channel")
        for owner in target_reader["logical_owners"]:
            if owner not in logical_owners:
                logical_owners.append(str(owner))
        for reader in foreign:
            for owner in reader["logical_owners"]:
                if owner not in logical_owners:
                    logical_owners.append(str(owner))

    edited = copy.deepcopy(dict(value))
    arena = edited["shared_stream_arena"]
    assert isinstance(arena, list)
    changed_offsets = []
    for offset, command in zip(replacement_offsets, commands):
        if arena[offset] != command:
            arena[offset] = command
            changed_offsets.append(offset)
    music_byte_readers(edited, parameters)
    return edited, {
        **lowering,
        "operation": "apply",
        "entry_offset": entry_offset,
        "available_bytes": len(existing_offsets),
        "changed_bytes": len(changed_offsets),
        "changed_offsets": changed_offsets,
        "affected_logical_owners": logical_owners,
        "shared_channel_edit": bool(
            allow_same_channel_sharing and any(len(readers.get(offset, [])) > 1 for offset in replacement_offsets)
        ),
    }


def apply_midi_song_commands(
    data: bytes,
    value: object,
    parameters: Mapping[str, object],
    selector_name: str,
    *,
    track: int = -1,
    max_pitch_error_cents: float = 50.0,
    max_duration_error_frames: float = 1.0,
) -> tuple[dict[str, object], dict[str, object]]:
    """Apply the conventional four NES voices from one Standard MIDI file."""
    encode_music(value, parameters)
    context = music_selector_context(value, parameters, selector_name)
    timeline = music_header_timeline(
        value,
        parameters,
        str(context["header"]),
        length_table_adder=int(context["length_table_adder"]),
    )
    available = {str(channel["channel"]) for channel in timeline["channels"] if isinstance(channel, Mapping)}
    mappings = (("square2", 0), ("square1", 1), ("triangle", 2), ("noise", 9))
    edited = copy.deepcopy(dict(value))
    reports: list[dict[str, object]] = []
    for target_channel, midi_channel in mappings:
        try:
            parsed = parse_midi_channel(data, midi_channel) if track < 0 else parse_midi(data, track, midi_channel)
        except MidiError as exc:
            raise SmbApuError(str(exc)) from exc
        if not parsed.notes:
            continue
        if target_channel not in available:
            raise SmbApuError(f"MIDI channel {midi_channel + 1} targets unavailable SMB channel {target_channel}")
        edited, report = apply_midi_channel_commands(
            data,
            edited,
            parameters,
            selector_name,
            target_channel,
            allow_same_channel_sharing=True,
            track=track,
            midi_channel=midi_channel,
            max_pitch_error_cents=max_pitch_error_cents,
            max_duration_error_frames=max_duration_error_frames,
        )
        reports.append(report)
    if not reports:
        raise SmbApuError("SMB song MIDI contains no notes on channels 1, 2, 3 or 10")
    assert isinstance(value, Mapping)
    before_arena = value["shared_stream_arena"]
    after_arena = edited["shared_stream_arena"]
    assert isinstance(before_arena, list)
    assert isinstance(after_arena, list)
    changed_offsets = [index for index, (before, after) in enumerate(zip(before_arena, after_arena)) if before != after]
    return edited, {
        "schema": "retrolarium.smb-midi-song-lowering",
        "schema_version": 1,
        "engine": CODEC,
        "selector": selector_name,
        "header": context["header"],
        "target_channel": "all",
        "track": track,
        "source_notes": sum(int(report["source_notes"]) for report in reports),
        "encoded_bytes": sum(int(report["encoded_bytes"]) for report in reports),
        "changed_bytes": len(changed_offsets),
        "changed_offsets": changed_offsets,
        "channels": [report["target_channel"] for report in reports],
        "channel_reports": reports,
    }


def music_header_timeline(
    value: object,
    parameters: Mapping[str, object],
    header_name: str,
    *,
    length_table_adder: int = 0,
) -> dict[str, object]:
    """Decode one SMB header into synchronized channel events and engine ticks."""
    encode_music(value, parameters)
    assert isinstance(value, Mapping)
    headers = value["headers"]
    arena = value["shared_stream_arena"]
    periods = value["note_periods"]
    lengths = value["note_lengths"]
    assert isinstance(headers, list)
    assert isinstance(arena, list)
    assert isinstance(periods, list)
    assert isinstance(lengths, list)
    header = next(
        (item for item in headers if isinstance(item, Mapping) and item.get("name") == header_name),
        None,
    )
    if header is None:
        raise SmbApuError(f"unknown SMB music header {header_name!r}")
    adder = _integer(length_table_adder, "length_table_adder", maximum=len(lengths) - 1)
    base = (
        _integer(
            header.get("length_table_offset"),
            "length_table_offset",
            maximum=len(lengths) - 1,
        )
        + adder
    )
    stream = _integer(header.get("stream_offset"), "stream_offset", maximum=len(arena) - 1)
    event_limit = max(1024, len(arena) * 512)

    def duration(index: int) -> int:
        position = base + index
        if position >= len(lengths):
            raise SmbApuError("SMB music length lookup leaves the note-length table")
        result = _integer(lengths[position], "note length", minimum=1, maximum=0xFF)
        return result

    def note_event(
        command: Mapping[str, object], tick: int, event_duration: int, byte_offset: int
    ) -> dict[str, object]:
        period_index = _integer(command.get("period_index"), "period_index", maximum=len(periods) - 1)
        return {
            "kind": "note",
            "byte_offset": byte_offset,
            "tick": tick,
            "duration": event_duration,
            "period_index": period_index,
            "period": periods[period_index],
        }

    square2_events: list[dict[str, object]] = []
    cursor = stream
    tick = 0
    current_duration: int | None = None
    while len(square2_events) < event_limit:
        if cursor >= len(arena):
            raise SmbApuError("square2 stream has no terminator inside the arena")
        command = decode_channel_byte(arena[cursor], "square2")
        kind = command["kind"]
        if kind == "length":
            current_duration = duration(int(command["length_index"]))
            square2_events.append(
                {
                    "kind": "length",
                    "byte_offset": cursor,
                    "tick": tick,
                    "length_index": command["length_index"],
                    "duration": current_duration,
                }
            )
        elif kind == "note":
            if current_duration is None:
                raise SmbApuError("square2 note precedes its length command")
            square2_events.append(note_event(command, tick, current_duration, cursor))
            tick += current_duration
        else:
            square2_events.append({"kind": "end", "byte_offset": cursor, "tick": tick})
            break
        cursor += 1
    else:
        raise SmbApuError("square2 stream exceeds the timeline event limit")
    total_ticks = tick

    def timed_channel(channel: str, start: int) -> list[dict[str, object]]:
        events: list[dict[str, object]] = []
        channel_tick = 0
        channel_cursor = start
        channel_duration: int | None = None
        while channel_tick < total_ticks and len(events) < event_limit:
            if channel_cursor >= len(arena):
                raise SmbApuError(f"{channel} stream leaves the shared arena")
            command = decode_channel_byte(arena[channel_cursor], channel)
            kind = str(command["kind"])
            if kind == "length":
                channel_duration = duration(int(command["length_index"]))
                events.append(
                    {
                        "kind": "length",
                        "byte_offset": channel_cursor,
                        "tick": channel_tick,
                        "length_index": command["length_index"],
                        "duration": channel_duration,
                    }
                )
            elif kind == "control":
                events.append(
                    {
                        "kind": "control",
                        "byte_offset": channel_cursor,
                        "tick": channel_tick,
                    }
                )
            else:
                if channel == "square1":
                    event_duration = duration(int(command["length_index"]))
                else:
                    if channel_duration is None:
                        raise SmbApuError(f"{channel} event precedes its length command")
                    event_duration = channel_duration
                if kind == "note":
                    events.append(note_event(command, channel_tick, event_duration, channel_cursor))
                else:
                    events.append(
                        {
                            "kind": "rest",
                            "byte_offset": channel_cursor,
                            "tick": channel_tick,
                            "duration": event_duration,
                        }
                    )
                channel_tick += event_duration
            channel_cursor += 1
        if channel_tick < total_ticks:
            raise SmbApuError(f"{channel} stream exceeds the timeline event limit")
        return events

    channels: list[dict[str, object]] = [
        {
            "channel": "square2",
            "entry_offset": stream,
            "events": square2_events,
        }
    ]
    square1 = header.get("square1_offset")
    if isinstance(square1, int) and not isinstance(square1, bool) and square1 != 0:
        channels.append(
            {
                "channel": "square1",
                "entry_offset": stream + square1,
                "events": timed_channel("square1", stream + square1),
            }
        )
    triangle = _integer(header.get("triangle_offset"), "triangle_offset", maximum=0xFF)
    channels.append(
        {
            "channel": "triangle",
            "entry_offset": stream + triangle,
            "events": timed_channel("triangle", stream + triangle),
        }
    )

    noise = header.get("noise_offset")
    if isinstance(noise, int) and not isinstance(noise, bool):
        noise_start = stream + noise
        noise_events: list[dict[str, object]] = []
        noise_tick = 0
        noise_cursor = noise_start
        beats_since_loop = 0
        while noise_tick < total_ticks and len(noise_events) < event_limit:
            if noise_cursor >= len(arena):
                raise SmbApuError("noise stream leaves the shared arena")
            command = decode_channel_byte(arena[noise_cursor], "noise")
            if command["kind"] == "loop":
                if beats_since_loop == 0:
                    raise SmbApuError("noise stream has an empty loop")
                noise_events.append(
                    {
                        "kind": "loop",
                        "byte_offset": noise_cursor,
                        "tick": noise_tick,
                    }
                )
                noise_cursor = noise_start
                beats_since_loop = 0
                continue
            beat_duration = duration(int(command["length_index"]))
            beat = str(command["beat"])
            period_register, length_register = NOISE_REGISTER_SETTINGS.get(beat, (0, 0))
            noise_events.append(
                {
                    "kind": "beat",
                    "byte_offset": noise_cursor,
                    "tick": noise_tick,
                    "duration": beat_duration,
                    "beat": beat,
                    "length_index": command["length_index"],
                    "period_index": period_register & 0x0F,
                    "length_counter": (LENGTH_COUNTER_TABLE[length_register >> 3] if length_register else 0),
                }
            )
            noise_tick += beat_duration
            noise_cursor += 1
            beats_since_loop += 1
        if noise_tick < total_ticks:
            raise SmbApuError("noise stream exceeds the timeline event limit")
        channels.append(
            {
                "channel": "noise",
                "entry_offset": noise_start,
                "events": noise_events,
            }
        )

    return {
        "header": header_name,
        "length_table_offset": base,
        "duration": total_ticks,
        "channels": channels,
    }


def render_music_header_pcm(
    value: object,
    parameters: Mapping[str, object],
    header_name: str,
    *,
    sample_rate: int = 44_100,
    frame_rate: float | None = None,
    cpu_frequency: float | None = None,
    length_table_adder: int | None = None,
    selector_name: str | None = None,
    muted_channels: tuple[str, ...] = (),
) -> bytes:
    """Render a deterministic mono authoring preview for one SMB music header."""
    rate = _integer(sample_rate, "sample_rate", minimum=1, maximum=384_000)
    resolved_frame_rate = _positive_number(
        parameters.get("frame_rate", NTSC_FRAME_RATE) if frame_rate is None else frame_rate,
        "frame_rate",
    )
    resolved_cpu_frequency = _positive_number(
        parameters.get("cpu_frequency", NTSC_CPU_FREQUENCY) if cpu_frequency is None else cpu_frequency,
        "cpu_frequency",
    )
    if not isinstance(muted_channels, tuple) or any(channel not in CHANNELS for channel in muted_channels):
        raise SmbApuError("muted_channels must contain known channel names")
    selector_context = music_selector_context(value, parameters, selector_name) if selector_name is not None else None
    if selector_context is not None and selector_context["header"] != header_name:
        raise SmbApuError(f"SMB music selector {selector_name!r} does not select header {header_name!r}")
    resolved_length_adder = (
        int(selector_context["length_table_adder"])
        if length_table_adder is None and selector_context is not None
        else 0
        if length_table_adder is None
        else length_table_adder
    )
    timeline = music_header_timeline(
        value,
        parameters,
        header_name,
        length_table_adder=resolved_length_adder,
    )
    assert isinstance(value, Mapping)
    raw_envelopes = value["envelopes"]
    assert isinstance(raw_envelopes, list)
    envelopes = {str(item["name"]): item["values"] for item in raw_envelopes if isinstance(item, Mapping)}

    def pulse_control(age_samples: int) -> tuple[int, int]:
        if selector_context is None:
            return 2, 15
        envelope_name = str(selector_context["envelope"])
        if envelope_name == "hardware":
            quarter_ticks = int(age_samples * resolved_frame_rate * 4.0 / rate)
            return 2, max(0, 15 - quarter_ticks // 3)
        raw_values = envelopes.get(envelope_name)
        if not isinstance(raw_values, list) or not raw_values:
            raise SmbApuError(f"SMB music envelope {envelope_name!r} is unavailable")
        age_frames = int(age_samples * resolved_frame_rate / rate)
        register = _integer(
            raw_values[max(0, len(raw_values) - 1 - age_frames)],
            f"{envelope_name} envelope value",
            maximum=0xFF,
        )
        return (register >> 6) & 3, register & 0x0F

    total_ticks = int(timeline["duration"])
    sample_count = round(total_ticks * rate / resolved_frame_rate)
    mix = [0.0] * sample_count
    triangle_phase = 0.0

    for raw_channel in timeline["channels"]:
        assert isinstance(raw_channel, Mapping)
        channel = str(raw_channel["channel"])
        if channel in muted_channels:
            continue
        events = raw_channel["events"]
        assert isinstance(events, list)
        lfsr = 1
        noise_phase = 0.0
        for event in events:
            assert isinstance(event, Mapping)
            kind = event["kind"]
            if kind not in {"note", "rest", "beat"}:
                continue
            start = round(int(event["tick"]) * rate / resolved_frame_rate)
            end = min(sample_count, round((int(event["tick"]) + int(event["duration"])) * rate / resolved_frame_rate))
            if kind == "rest":
                continue
            if kind == "beat":
                beat = str(event["beat"])
                period_index = _integer(
                    event.get("period_index"),
                    "noise period index",
                    maximum=len(NOISE_PERIODS) - 1,
                )
                length_counter = _integer(
                    event.get("length_counter"),
                    "noise length counter",
                    maximum=0xFF,
                )
                shift_rate = resolved_cpu_frequency / NOISE_PERIODS[period_index]
                gate_samples = round(length_counter * rate / (resolved_frame_rate * 2.0))
                gate_end = min(end, start + gate_samples)
                for sample in range(start, end):
                    noise_phase += shift_rate / rate
                    while noise_phase >= 1.0:
                        feedback = (lfsr ^ (lfsr >> 1)) & 1
                        lfsr = (lfsr >> 1) | (feedback << 14)
                        noise_phase -= 1.0
                    if beat != "rest" and sample < gate_end:
                        mix[sample] += 0.20 if lfsr & 1 == 0 else -0.20
                continue

            period = int(event["period"])
            if (period & 0xFF) == 0:
                continue
            divider = 32.0 if channel == "triangle" else 16.0
            frequency = resolved_cpu_frequency / (divider * ((period & 0x7FF) + 1))
            phase = triangle_phase if channel == "triangle" else 0.0
            for sample in range(start, end):
                if channel == "triangle":
                    amplitude = (1.0 - 4.0 * abs(phase - 0.5)) * 0.20
                else:
                    duty, volume = pulse_control(sample - start)
                    sequence = PULSE_DUTY_SEQUENCES[duty]
                    level = 0.18 * volume / 15.0
                    amplitude = level if sequence[int(phase * 8.0) % 8] else -level
                mix[sample] += amplitude
                phase = math.fmod(phase + frequency / rate, 1.0)
            if channel == "triangle":
                triangle_phase = phase

    pcm = bytearray()
    for amplitude in mix:
        clamped = max(-0.95, min(0.95, amplitude))
        pcm.extend(struct.pack("<h", round(clamped * 32767)))
    return bytes(pcm)


def render_music_header_wav(
    value: object,
    parameters: Mapping[str, object],
    header_name: str,
    **options: object,
) -> bytes:
    """Wrap a deterministic SMB music preview in mono 16-bit PCM WAV."""
    sample_rate = options.get("sample_rate", 44_100)
    pcm = render_music_header_pcm(value, parameters, header_name, **options)
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(int(sample_rate))
        wav.writeframes(pcm)
    return output.getvalue()
