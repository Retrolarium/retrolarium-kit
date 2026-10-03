"""Source-bound Solomon title screens and attract-demo controller program."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any, Mapping

from .authoring import (
    Address, AddressRange, AuthoringError, BuildPlan, Change, ChangeSet,
    GameProfile, Provenance, RevisionSpec,
)
from .ines import parse_ines


CODEC = "solomon.presentation.v1"
SCHEMA = "retrolarium.solomon-presentation"
VERSION = 1
STREAMS = (("record", 155), ("logo", 247))
DEMO_STEPS = 34                 # the game's own demo; longer ones grow into the filler
# Offsets from RunDemoInputPlayback (the demo_code_cpu parameter) of the
# operands the build patches when the demo changes length: LDA durations,X,
# CPX #count and LDA values,X.
DEMO_DURATIONS_OPERAND = 0x1B
DEMO_COUNT_OPERAND = 0x22
DEMO_VALUES_OPERAND = 0x26
BUTTON_BITS = (
    (0x80, "A"), (0x40, "B"), (0x20, "SELECT"), (0x10, "START"),
    (0x08, "UP"), (0x04, "DOWN"), (0x02, "LEFT"), (0x01, "RIGHT"),
)
FIELDS = (
    "record_cpu", "logo_cpu", "demo_duration_cpu", "demo_input_cpu",
)


class SolomonPresentationError(AuthoringError):
    """Packed title or demo data is invalid or exceeds its fixed allocation."""


@dataclass(frozen=True)
class SolomonPresentationDocument:
    game_id: str
    revision_id: str
    source_sha256: str
    layout: dict[str, Any]
    streams: tuple[dict[str, Any], ...]
    demo_steps: tuple[dict[str, Any], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA, "schema_version": VERSION,
            "game": self.game_id, "revision": self.revision_id,
            "source_sha256": self.source_sha256,
            "layout": {
                "streams": [dict(item) for item in self.layout["streams"]],
                "demo": dict(self.layout["demo"]),
            },
            "streams": [
                {"name": stream["name"], "tokens": [
                    {key: list(value) if key == "tiles" else value
                     for key, value in token.items()}
                    for token in stream["tokens"]
                ]}
                for stream in self.streams
            ],
            "demo_steps": [
                {"index": step["index"], "duration": step["duration"],
                 "buttons": list(step["buttons"])}
                for step in self.demo_steps
            ],
        }


def _byte(value: object, name: str) -> int:
    if type(value) is not int or not 0 <= value <= 255:
        raise SolomonPresentationError(f"{name} must be a byte")
    return value


def _layout(profile: GameProfile, revision: RevisionSpec) -> dict[str, Any]:
    resources = [
        resource for resource in profile.resources_for(revision)
        if resource.codec == CODEC
    ]
    if len(resources) != 1:
        raise SolomonPresentationError("Solomon profile needs one presentation resource")
    parameters = resources[0].parameters
    if any(type(parameters.get(name)) is not int for name in FIELDS):
        raise SolomonPresentationError("presentation needs four CPU addresses")
    record, logo, duration, inputs = (parameters[name] for name in FIELDS)
    if (not 0x8000 <= record < logo < duration < inputs <= 0xFFFF
            or logo - record != STREAMS[0][1]
            or duration - logo != STREAMS[1][1]
            or inputs - duration != DEMO_STEPS
            or inputs + DEMO_STEPS > 0x10000):
        raise SolomonPresentationError("presentation layout ranges differ")
    return {
        "streams": [
            {"name": "record", "address": record, "encoded_size": 155},
            {"name": "logo", "address": logo, "encoded_size": 247},
        ],
        "demo": {
            "duration_address": duration,
            "input_address": inputs,
            "step_count": DEMO_STEPS,
        },
    }


def _demo_room(profile: GameProfile, revision: RevisionSpec) -> dict[str, int] | None:
    """Where a demo of another length may live: its code and the end of the
    free filler after the tables. None keeps the game's 34 steps."""
    resource = next(
        resource for resource in profile.resources_for(revision) if resource.codec == CODEC
    )
    parameters = resource.parameters
    if "demo_code_cpu" not in parameters and "demo_end_cpu" not in parameters:
        return None
    code, end = parameters.get("demo_code_cpu"), parameters.get("demo_end_cpu")
    start = parameters["demo_duration_cpu"]
    if type(code) is not int or type(end) is not int or not 0x8000 <= code <= 0xFFC0 \
            or not start + 2 * DEMO_STEPS <= end <= 0x10000:
        raise SolomonPresentationError("presentation demo room is invalid")
    return {"code": code, "end": end, "capacity": (end - start) // 2}


def demo_capacity(profile: GameProfile, revision: RevisionSpec) -> int:
    room = _demo_room(profile, revision)
    return DEMO_STEPS if room is None else room["capacity"]


def _demo_count(prg: bytes, layout: dict[str, Any], room: dict[str, int] | None) -> int:
    """The step count the code holds, its operands checked."""
    if room is None:
        return DEMO_STEPS
    code = room["code"] - 0x8000
    start = layout["demo"]["duration_address"]
    count = prg[code + DEMO_COUNT_OPERAND]
    word = lambda offset: prg[code + offset] | (prg[code + offset + 1] << 8)
    if prg[code + DEMO_DURATIONS_OPERAND - 1] != 0xBD or word(DEMO_DURATIONS_OPERAND) != start \
            or prg[code + DEMO_COUNT_OPERAND - 1] != 0xE0 \
            or prg[code + DEMO_VALUES_OPERAND - 1] != 0xBD or word(DEMO_VALUES_OPERAND) != start + count \
            or not 1 <= count <= room["capacity"]:
        raise SolomonPresentationError("demo playback code differs from the guarded layout")
    return count


def _source(source: bytes, revision: RevisionSpec, *, verify: bool = True):
    if verify and hashlib.sha256(source).hexdigest() != revision.sha256:
        raise SolomonPresentationError("presentation source identity mismatch")
    image = parse_ines(source)
    if image.mapper != revision.mapper or len(image.prg) != 0x8000:
        raise SolomonPresentationError("Solomon presentation needs 32 KiB PRG")
    return image


def _slice(prg: bytes, start: int, count: int) -> bytes:
    if not 0x8000 <= start <= 0x10000 - count:
        raise SolomonPresentationError("presentation range escapes PRG")
    result = prg[start - 0x8000:start - 0x8000 + count]
    if len(result) != count:
        raise SolomonPresentationError("presentation range is truncated")
    return result


def title_ppu_address(row: int, column: int) -> int:
    """Reproduce SetPackedTitlePpuAddress, including carry-fed rotates."""
    if not 0 <= row <= 255 or not 0 <= column < 64:
        raise SolomonPresentationError("packed title cursor is invalid")
    biased = (row + 0x10) & 255
    high = ((biased >> 2) & 0x2B) | 0x20
    carry = (biased >> 1) & 1
    low = row
    for _ in range(3):
        next_carry = low & 1
        low = (carry << 7) | (low >> 1)
        carry = next_carry
    return (high << 8) | ((low & 0xC0) | column)


def _decode_stream(data: bytes) -> list[dict[str, Any]]:
    tokens: list[dict[str, Any]] = []
    cursor = 0
    while cursor < len(data):
        opcode = data[cursor]
        if opcode >= 0x80:
            end = cursor
            while end < len(data) and data[end] >= 0x80:
                end += 1
            tokens.append({"kind": "literal", "tiles": list(data[cursor:end])})
            cursor = end
            continue
        cursor += 1
        if opcode < 0x40:
            tokens.append({"kind": "set_column", "value": opcode})
        elif opcode < 0x60:
            tokens.append({"kind": "set_row", "value": opcode})
        elif opcode < 0x7F:
            tokens.append({"kind": "add_row", "value": (opcode & 31) + 1})
        else:
            tokens.append({"kind": "terminate"})
            if cursor != len(data):
                raise SolomonPresentationError("title stream terminates before allocation")
            return tokens
    raise SolomonPresentationError("title stream lacks terminator")


def project_title_surface(
    document: SolomonPresentationDocument, stream_index: int,
) -> list[list[int | None]]:
    if stream_index not in (0, 1):
        raise SolomonPresentationError("title stream index is invalid")
    surface: list[list[int | None]] = [[None] * 32 for _ in range(30)]
    row = 0
    column = 0
    for token in document.streams[stream_index]["tokens"]:
        kind = token["kind"]
        if kind == "set_column":
            column = token["value"]
        elif kind == "set_row":
            row = token["value"]
        elif kind == "add_row":
            row = (row + token["value"]) & 255
        elif kind == "literal":
            start = title_ppu_address(row, column)
            for delta, tile in enumerate(token["tiles"]):
                address = start + delta
                if not 0x2800 <= address < 0x2BC0:
                    raise SolomonPresentationError(
                        "title literal targets unsupported nametable address"
                    )
                target_row, target_column = divmod(address - 0x2800, 32)
                surface[target_row][target_column] = tile
    return surface


def decode_presentation(
    source: bytes, profile: GameProfile, revision: RevisionSpec,
    *, _verify_identity: bool = True,
) -> SolomonPresentationDocument:
    layout = _layout(profile, revision)
    prg = _source(source, revision, verify=_verify_identity).prg
    streams = []
    for declaration in layout["streams"]:
        payload = _slice(
            prg, declaration["address"], declaration["encoded_size"]
        )
        streams.append({
            "name": declaration["name"],
            "tokens": _decode_stream(payload),
        })
    demo = layout["demo"]
    count = _demo_count(prg, layout, _demo_room(profile, revision))
    durations = _slice(prg, demo["duration_address"], count)
    inputs = _slice(prg, demo["duration_address"] + count, count)
    steps = [
        {"index": index, "duration": duration, "buttons": [
            name for mask, name in BUTTON_BITS if buttons & mask
        ]}
        for index, (duration, buttons) in enumerate(zip(durations, inputs))
    ]
    result = SolomonPresentationDocument(
        profile.game_id, revision.revision_id, revision.sha256,
        layout, tuple(streams), tuple(steps),
    )
    for index in range(2):
        project_title_surface(result, index)
    return result


def _encode(value: Mapping[str, Any], layout: dict[str, Any], capacity: int = DEMO_STEPS) -> bytes:
    """The title streams and the demo tables, one run of bytes; the demo
    has 34 steps unless `capacity` lets it grow."""
    if value.get("layout") != layout:
        raise SolomonPresentationError("presentation layout differs from profile")
    streams = value.get("streams")
    if not isinstance(streams, list) or len(streams) != 2:
        raise SolomonPresentationError("presentation needs two title streams")
    output = bytearray()
    for index, (declaration, stream) in enumerate(zip(layout["streams"], streams)):
        if not isinstance(stream, Mapping) or stream.get("name") != declaration["name"]:
            raise SolomonPresentationError(f"title stream {index} identity differs")
        tokens = stream.get("tokens")
        if not isinstance(tokens, list) or not tokens:
            raise SolomonPresentationError(f"title stream {index} has no tokens")
        encoded = bytearray()
        terminated = False
        for token_index, token in enumerate(tokens):
            if not isinstance(token, Mapping):
                raise SolomonPresentationError("title token is invalid")
            kind = token.get("kind")
            if kind == "literal":
                tiles = token.get("tiles")
                if not isinstance(tiles, list) or not tiles:
                    raise SolomonPresentationError("title literal run is empty")
                if any(type(tile) is not int or not 0x80 <= tile <= 255
                       for tile in tiles):
                    raise SolomonPresentationError("title literal tiles must be $80-$FF")
                encoded.extend(tiles)
            elif kind == "set_column":
                operand = _byte(token.get("value"), "title column")
                if operand >= 0x40:
                    raise SolomonPresentationError("title column is invalid")
                encoded.append(operand)
            elif kind == "set_row":
                operand = _byte(token.get("value"), "title row opcode")
                if not 0x40 <= operand < 0x60:
                    raise SolomonPresentationError("title row opcode is invalid")
                encoded.append(operand)
            elif kind == "add_row":
                operand = _byte(token.get("value"), "title row increment")
                if not 1 <= operand <= 31:
                    raise SolomonPresentationError("title row increment is invalid")
                encoded.append(0x60 | (operand - 1))
            elif kind == "terminate":
                if token_index != len(tokens) - 1 or set(token) != {"kind"}:
                    raise SolomonPresentationError("title terminator must be final")
                encoded.append(0x7F)
                terminated = True
            else:
                raise SolomonPresentationError(f"unknown title token kind {kind!r}")
        if not terminated or len(encoded) != declaration["encoded_size"]:
            raise SolomonPresentationError(
                f"title stream {index} exceeds or underfills allocation"
            )
        output.extend(encoded)
    steps = value.get("demo_steps")
    if not isinstance(steps, list) or not steps or len(steps) > capacity \
            or (capacity == DEMO_STEPS and len(steps) != DEMO_STEPS):
        raise SolomonPresentationError(
            "demo needs 34 input steps" if capacity == DEMO_STEPS
            else f"demo needs 1-{capacity} input steps"
        )
    durations = bytearray()
    inputs = bytearray()
    button_names = [name for _mask, name in BUTTON_BITS]
    for index, step in enumerate(steps):
        if not isinstance(step, Mapping) or step.get("index") != index:
            raise SolomonPresentationError("demo step indices differ")
        duration = _byte(step.get("duration"), "demo duration")
        # PollDemoInputPlayback waits for its 8-bit frame counter to pass
        # the duration; it never passes 255 and the demo would stall.
        if duration == 255:
            raise SolomonPresentationError(
                f"demo step {index + 1} waits 255 frames, which the game never reaches; use 0-254"
            )
        durations.append(duration)
        buttons = step.get("buttons")
        if (not isinstance(buttons, list)
                or buttons != [name for name in button_names if name in buttons]):
            raise SolomonPresentationError("demo buttons are unknown or unordered")
        inputs.append(sum(
            mask for mask, name in BUTTON_BITS if name in buttons
        ))
    output.extend(durations)
    output.extend(inputs)
    if len(output) != 155 + 247 + 2 * len(steps):
        raise SolomonPresentationError("presentation bank size differs")
    return bytes(output)


def parse_presentation_document(
    value: object, profile: GameProfile, revision: RevisionSpec,
) -> SolomonPresentationDocument:
    layout = _layout(profile, revision)
    if not isinstance(value, Mapping) or set(value) != {
        "schema", "schema_version", "game", "revision", "source_sha256",
        "layout", "streams", "demo_steps",
    }:
        raise SolomonPresentationError("presentation document fields differ")
    for key, expected in (
        ("schema", SCHEMA), ("schema_version", VERSION),
        ("game", profile.game_id), ("revision", revision.revision_id),
        ("source_sha256", revision.sha256),
    ):
        if value[key] != expected:
            raise SolomonPresentationError(f"presentation {key} differs from profile")
    _encode(value, layout, demo_capacity(profile, revision))
    result = SolomonPresentationDocument(
        profile.game_id, revision.revision_id, revision.sha256,
        layout, tuple(value["streams"]), tuple(value["demo_steps"]),
    )
    for index in range(2):
        project_title_surface(result, index)
    return result


def build_presentation(
    source: bytes, document: SolomonPresentationDocument,
    profile: GameProfile, revision: RevisionSpec,
) -> tuple[bytes, dict[str, object]]:
    image = _source(source, revision)
    checked = parse_presentation_document(
        document.as_dict(), profile, revision
    )
    layout = _layout(profile, revision)
    room = _demo_room(profile, revision)
    encoded = _encode(checked.as_dict(), layout, demo_capacity(profile, revision))
    count = len(checked.demo_steps)
    base = len(image.header) + len(image.trainer)
    pieces = [(layout["streams"][0]["address"], encoded)]
    if room is not None:
        values = layout["demo"]["duration_address"] + count
        pieces += [
            (room["code"] + DEMO_COUNT_OPERAND, bytes((count,))),
            (room["code"] + DEMO_VALUES_OPERAND, bytes((values & 0xFF, values >> 8))),
        ]
    changes = []
    for address, payload in pieces:
        file_start = base + address - 0x8000
        if source[file_start:file_start + len(payload)] != payload:
            changes.append(Change(
                AddressRange(Address("file", file_start), len(payload)),
                source[file_start:file_start + len(payload)], payload,
                Provenance(revision.sha256, "presentation", "encode"),
            ))
    output = BuildPlan(revision.sha256, ChangeSet(tuple(changes))).apply(source) if changes else source
    rebuilt = decode_presentation(
        output, profile, revision, _verify_identity=False
    )
    if rebuilt.as_dict() != checked.as_dict():
        raise SolomonPresentationError(
            "rebuilt presentation does not decode to document"
        )
    return output, {
        "schema": "retrolarium.solomon-presentation-build",
        "schema_version": 1,
        "game": profile.game_id, "revision": revision.revision_id,
        "source_sha256": revision.sha256,
        "output_sha256": hashlib.sha256(output).hexdigest(),
        "size": len(output),
        "changed_bytes": sum(a != b for a, b in zip(source, output)),
        "title_streams": 2, "demo_steps": count,
    }
