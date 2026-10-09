"""Typed fixed-capacity FDS ending music bank."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from retrolarium_kit.core.authoring import AuthoringError, stable_identifier
from retrolarium_kit.platform.fds.synthesis import decode_synthesis, encode_synthesis


CODEC = "fds.music.v1"


class FdsMusicError(AuthoringError):
    """An FDS ending bank differs from its fixed engine layout."""


@dataclass(frozen=True)
class FdsMusicLayout:
    size: int
    cpu_address: int
    sequence_names: tuple[str, ...]
    header_names: tuple[str, ...]
    stream_offset: int
    synthesis_offset: int
    synthesis_size: int
    note_lengths_offset: int
    note_length_count: int

    @property
    def header_offset(self) -> int:
        return len(self.sequence_names)

    @property
    def header_end(self) -> int:
        return self.header_offset + len(self.header_names) * 8

    @property
    def stream_size(self) -> int:
        return self.synthesis_offset - self.stream_offset

    @property
    def synthesis_end(self) -> int:
        return self.synthesis_offset + self.synthesis_size


def _integer(
    value: object, name: str, *, minimum: int = 0, maximum: int = 0x7FFFFFFF
) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not minimum <= value <= maximum
    ):
        raise FdsMusicError(f"{name} must be an integer in {minimum}..{maximum}")
    return value


def _names(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise FdsMusicError(f"{name} must be a non-empty array")
    result = []
    for index, raw in enumerate(value):
        try:
            result.append(stable_identifier(raw, f"{name}[{index}]"))
        except AuthoringError as exc:
            raise FdsMusicError(str(exc)) from exc
    if len(set(result)) != len(result):
        raise FdsMusicError(f"{name} must contain unique identifiers")
    return tuple(result)


def parse_layout(parameters: Mapping[str, object]) -> FdsMusicLayout:
    layout = FdsMusicLayout(
        size=_integer(parameters.get("size"), "size", minimum=1),
        cpu_address=_integer(
            parameters.get("cpu_address"), "cpu_address", maximum=0xFFFF
        ),
        sequence_names=_names(parameters.get("sequence_names"), "sequence_names"),
        header_names=_names(parameters.get("header_names"), "header_names"),
        stream_offset=_integer(parameters.get("stream_offset"), "stream_offset"),
        synthesis_offset=_integer(
            parameters.get("synthesis_offset"), "synthesis_offset"
        ),
        synthesis_size=_integer(
            parameters.get("synthesis_size"), "synthesis_size", minimum=1
        ),
        note_lengths_offset=_integer(
            parameters.get("note_lengths_offset"), "note_lengths_offset"
        ),
        note_length_count=_integer(
            parameters.get("note_length_count"),
            "note_length_count",
            minimum=1,
            maximum=0x100,
        ),
    )
    if len(layout.sequence_names) > 0xFF or layout.header_end > 0x100:
        raise FdsMusicError("FDS sequence and headers must fit one-byte offsets")
    if not (
        layout.header_end <= layout.stream_offset
        < layout.synthesis_offset
        and layout.synthesis_end <= layout.note_lengths_offset
        and layout.note_lengths_offset + layout.note_length_count == layout.size
    ):
        raise FdsMusicError("FDS music regions are inconsistent")
    if layout.cpu_address + layout.size > 0x10000:
        raise FdsMusicError("FDS music bank crosses CPU address space")
    _synthesis_parameters(parameters, layout)
    return layout


def _synthesis_parameters(
    parameters: Mapping[str, object], layout: FdsMusicLayout
) -> dict[str, object]:
    return {
        "size": layout.synthesis_size,
        "waveforms": parameters.get("waveforms"),
        "volume_programs": parameters.get("volume_programs"),
    }


def _bytes(value: object, count: int, context: str) -> bytes:
    if not isinstance(value, list) or len(value) != count:
        raise FdsMusicError(f"{context} must contain {count} bytes")
    return bytes(
        _integer(item, f"{context}[{index}]", maximum=0xFF)
        for index, item in enumerate(value)
    )


def _header_offsets(layout: FdsMusicLayout) -> dict[str, int]:
    return {
        name: layout.header_offset + index * 8
        for index, name in enumerate(layout.header_names)
    }


def _channel_offsets(
    header: Mapping[str, object],
    layout: FdsMusicLayout,
    context: str,
) -> tuple[int, int, int, int, int]:
    stream = _integer(
        header.get("stream_offset"),
        f"{context}.stream_offset",
        maximum=layout.stream_size - 1,
    )
    values = tuple(
        _integer(header.get(key), f"{context}.{key}", maximum=0xFF)
        for key in (
            "triangle_offset",
            "square1_offset",
            "noise_offset",
            "wave_offset",
        )
    )
    for key, value in zip(
        ("triangle", "square1", "noise", "wave"), values
    ):
        if value and stream + value >= layout.stream_size:
            raise FdsMusicError(f"{context}.{key}_offset leaves the stream arena")
    return stream, *values


def decode_music(
    payload: bytes, parameters: Mapping[str, object]
) -> dict[str, object]:
    """Decode the complete ending bank without losing fixed or unknown bytes."""
    layout = parse_layout(parameters)
    if len(payload) != layout.size:
        raise FdsMusicError(
            f"FDS music resource has {len(payload)} bytes, expected {layout.size}"
        )
    offsets = _header_offsets(layout)
    names_by_offset = {offset: name for name, offset in offsets.items()}
    sequence = []
    for index, offset in enumerate(payload[:layout.header_offset]):
        if offset not in names_by_offset:
            raise FdsMusicError(
                f"sequence[{index}] references unknown header offset {offset}"
            )
        sequence.append({
            "name": layout.sequence_names[index],
            "header": names_by_offset[offset],
        })

    headers = []
    stream_address = layout.cpu_address + layout.stream_offset
    for index, name in enumerate(layout.header_names):
        cursor = layout.header_offset + index * 8
        raw = payload[cursor:cursor + 8]
        pointer = raw[1] | raw[2] << 8
        header = {
            "name": name,
            "length_table_offset": raw[0],
            "stream_offset": pointer - stream_address,
            "triangle_offset": raw[3],
            "square1_offset": raw[4],
            "noise_offset": raw[5],
            "wave_offset": raw[6],
            "wave_id": raw[7],
        }
        _channel_offsets(header, layout, f"headers[{index}]")
        headers.append(header)

    synthesis_parameters = _synthesis_parameters(parameters, layout)
    result = {
        "sequence": sequence,
        "headers": headers,
        "prefix_padding": list(payload[layout.header_end:layout.stream_offset]),
        "shared_stream_arena": list(
            payload[layout.stream_offset:layout.synthesis_offset]
        ),
        "synthesis": decode_synthesis(
            payload[layout.synthesis_offset:layout.synthesis_end],
            synthesis_parameters,
        ),
        "suffix_padding": list(
            payload[layout.synthesis_end:layout.note_lengths_offset]
        ),
        "note_lengths": list(payload[layout.note_lengths_offset:]),
    }
    encode_music(result, parameters)
    return result


def encode_music(
    value: object, parameters: Mapping[str, object]
) -> bytes:
    """Validate and encode a complete fixed-size FDS ending bank."""
    layout = parse_layout(parameters)
    expected = {
        "sequence", "headers", "prefix_padding", "shared_stream_arena",
        "synthesis", "suffix_padding", "note_lengths",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise FdsMusicError("FDS music fields differ from codec v1")
    offsets = _header_offsets(layout)
    output = bytearray(layout.size)

    sequence = value.get("sequence")
    if not isinstance(sequence, list) or len(sequence) != len(layout.sequence_names):
        raise FdsMusicError(
            f"sequence must contain {len(layout.sequence_names)} entries"
        )
    for index, (raw, expected_name) in enumerate(
        zip(sequence, layout.sequence_names)
    ):
        context = f"sequence[{index}]"
        if not isinstance(raw, Mapping) or set(raw) != {"name", "header"}:
            raise FdsMusicError(f"{context} must contain name and header")
        if raw.get("name") != expected_name:
            raise FdsMusicError(f"{context}.name differs from the profile")
        header = raw.get("header")
        if header not in offsets:
            raise FdsMusicError(f"{context}.header is unknown")
        output[index] = offsets[header]

    headers = value.get("headers")
    if not isinstance(headers, list) or len(headers) != len(layout.header_names):
        raise FdsMusicError(
            f"headers must contain {len(layout.header_names)} entries"
        )
    header_fields = {
        "name", "length_table_offset", "stream_offset", "triangle_offset",
        "square1_offset", "noise_offset", "wave_offset", "wave_id",
    }
    stream_address = layout.cpu_address + layout.stream_offset
    for index, (raw, expected_name) in enumerate(
        zip(headers, layout.header_names)
    ):
        context = f"headers[{index}]"
        if not isinstance(raw, Mapping) or set(raw) != header_fields:
            raise FdsMusicError(f"{context} has the wrong shape")
        if raw.get("name") != expected_name:
            raise FdsMusicError(f"{context}.name differs from the profile")
        stream, triangle, square1, noise, wave = _channel_offsets(
            raw, layout, context
        )
        pointer = stream_address + stream
        encoded = bytes((
            _integer(
                raw.get("length_table_offset"),
                f"{context}.length_table_offset",
                maximum=0xFF,
            ),
            pointer & 0xFF,
            pointer >> 8,
            triangle,
            square1,
            noise,
            wave,
            _integer(raw.get("wave_id"), f"{context}.wave_id", maximum=0xFF),
        ))
        cursor = layout.header_offset + index * 8
        output[cursor:cursor + 8] = encoded

    output[layout.header_end:layout.stream_offset] = _bytes(
        value.get("prefix_padding"),
        layout.stream_offset - layout.header_end,
        "prefix_padding",
    )
    output[layout.stream_offset:layout.synthesis_offset] = _bytes(
        value.get("shared_stream_arena"),
        layout.stream_size,
        "shared_stream_arena",
    )
    output[layout.synthesis_offset:layout.synthesis_end] = encode_synthesis(
        value.get("synthesis"), _synthesis_parameters(parameters, layout)
    )
    output[layout.synthesis_end:layout.note_lengths_offset] = _bytes(
        value.get("suffix_padding"),
        layout.note_lengths_offset - layout.synthesis_end,
        "suffix_padding",
    )
    output[layout.note_lengths_offset:] = _bytes(
        value.get("note_lengths"), layout.note_length_count, "note_lengths"
    )
    return bytes(output)
