"""Source-bound Solomon's Key screen text: static PPU programs and ending messages.

The game draws its fixed screens from eighteen ROM-resident PPU programs
(``StaticPpuUpdateStream00``-``17``: the HUD labels, the room-clear and game
over messages, the title and licence text and four palettes), the room
intro template with the special-room names, and the ending's two initial and
ten sequenced messages. A document keeps every program as commands and
every message as tiles with its PPU addresses.

Programs and messages may change length within their regions: the build
repacks the static programs and rewrites their split pointer table and the
one direct reference to program 05, and repacks the ending messages,
rewriting their offset table and the two immediates that encode message
boundaries (the second initial message's start and the first message with
a single address). The room intro template and the special-room names keep
their widths: code patches them at fixed offsets.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any, Mapping

from .authoring import (
    Address, AddressRange, AuthoringError, BuildPlan, Change, ChangeSet,
    GameProfile, Provenance, RevisionSpec,
)
from .ines import parse_ines


CODEC = "solomon.text.v1"
SCHEMA = "retrolarium.solomon-text"
VERSION = 1
STATIC_COUNT = 18
FIXED_PROGRAM = 5
INITIAL_COUNT = 2
MESSAGE_COUNT = 10
# Messages before this index carry two PPU addresses (one per scroll
# position); later ones carry one.
DOUBLE_ADDRESS_MESSAGES = 4
INTRO_SIZE = 27
SPECIAL_NAME_COUNT = 3
SPECIAL_NAME_WIDTH = 8
FIELDS = (
    "static_pointer_low_cpu", "static_pointer_high_cpu",
    "static_region_cpu", "static_region_end_cpu", "static_05_reference_cpu",
    "ending_initial_cpu", "ending_initial_end_cpu",
    "ending_initial_second_operand_cpu",
    "ending_offsets_cpu", "ending_data_cpu", "ending_data_end_cpu",
    "ending_single_address_operand_cpu",
    "room_intro_cpu", "special_names_cpu",
)


class SolomonTextError(AuthoringError):
    """Screen text is invalid or exceeds the ROM regions it lives in."""


def static_order(count: int) -> tuple[int, ...]:
    """The programs in ROM order: 05, which BuildEndingPpuMessage copies 19
    bytes from its own address and so keeps its shape, and the palette 13
    come last. Solomon no Kagi has no 13-17 (the licence screen)."""
    return (*(index for index in range(count) if index not in (FIXED_PROGRAM, 13)),
            FIXED_PROGRAM, *((13,) if count > 13 else ()))


@dataclass(frozen=True)
class SolomonTextDocument:
    game_id: str
    revision_id: str
    source_sha256: str
    layout: dict[str, int]
    static_programs: tuple[dict[str, Any], ...]
    ending_initial: tuple[dict[str, Any], ...]
    ending_messages: tuple[dict[str, Any], ...]
    room_intro: tuple[dict[str, Any], ...]
    special_room_names: tuple[tuple[int, ...], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA, "schema_version": VERSION,
            "game": self.game_id, "revision": self.revision_id,
            "source_sha256": self.source_sha256,
            "layout": dict(self.layout),
            "static_programs": [
                {"index": item["index"], "commands": [_command_dict(c) for c in item["commands"]]}
                for item in self.static_programs
            ],
            "ending_initial": [_message_dict(m) for m in self.ending_initial],
            "ending_messages": [_message_dict(m) for m in self.ending_messages],
            "room_intro": [_command_dict(c) for c in self.room_intro],
            "special_room_names": [list(name) for name in self.special_room_names],
        }


def _command_dict(command: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "address": command["address"], "literal": command["literal"],
        "vertical": command["vertical"], "bytes": list(command["bytes"]),
    }


def _message_dict(message: Mapping[str, Any]) -> dict[str, Any]:
    return {"tiles": list(message["tiles"]), "addresses": list(message["addresses"])}


def _byte(value: object, name: str) -> int:
    if type(value) is not int or not 0 <= value <= 255:
        raise SolomonTextError(f"{name} must be a byte")
    return value


def _layout(profile: GameProfile, revision: RevisionSpec) -> dict[str, int]:
    resources = [
        resource for resource in profile.resources_for(revision)
        if resource.codec == CODEC
    ]
    if len(resources) != 1:
        raise SolomonTextError("Solomon profile needs one text resource")
    parameters = resources[0].parameters
    if any(type(parameters.get(name)) is not int or not 0x8000 <= parameters[name] <= 0xFFFF
           for name in FIELDS):
        raise SolomonTextError("text resource needs its CPU addresses")
    layout = {name: parameters[name] for name in FIELDS}
    layout["static_count"] = parameters.get("static_count", STATIC_COUNT)
    if type(layout["static_count"]) is not int or not FIXED_PROGRAM < layout["static_count"] <= STATIC_COUNT:
        raise SolomonTextError("text resource static_count is invalid")
    if layout["static_pointer_high_cpu"] - layout["static_pointer_low_cpu"] != layout["static_count"] \
            or layout["static_region_end_cpu"] <= layout["static_region_cpu"] \
            or layout["ending_initial_end_cpu"] <= layout["ending_initial_cpu"] \
            or layout["ending_data_end_cpu"] <= layout["ending_data_cpu"] \
            or layout["ending_data_end_cpu"] - layout["ending_data_cpu"] > 256:
        raise SolomonTextError("text resource ranges differ")
    return layout


def _capacities(layout: Mapping[str, int]) -> dict[str, int]:
    return {
        "static_bytes": layout["static_region_end_cpu"] - layout["static_region_cpu"],
        "ending_initial_bytes": layout["ending_initial_end_cpu"] - layout["ending_initial_cpu"],
        "ending_message_bytes": layout["ending_data_end_cpu"] - layout["ending_data_cpu"],
    }


def _source(source: bytes, revision: RevisionSpec, *, verify: bool = True):
    if verify and hashlib.sha256(source).hexdigest() != revision.sha256:
        raise SolomonTextError("text source identity mismatch")
    image = parse_ines(source)
    if image.mapper != revision.mapper or len(image.prg) != 0x8000:
        raise SolomonTextError("Solomon text needs 32 KiB PRG")
    return image


def _read(prg: bytes, address: int, count: int) -> bytes:
    if not 0x8000 <= address <= 0x10000 - count:
        raise SolomonTextError("text range escapes PRG")
    return prg[address - 0x8000:address - 0x8000 + count]


# ---------------------------------------------------------------- PPU programs

def _decode_program(prg: bytes, address: int, end: int) -> tuple[list[dict[str, Any]], int]:
    """Commands of a zero-terminated program and its byte size."""
    commands: list[dict[str, Any]] = []
    cursor = address
    while True:
        if cursor >= end:
            raise SolomonTextError(f"PPU program at ${address:04X} runs past its region")
        high = _read(prg, cursor, 1)[0]
        if high == 0:
            return commands, cursor + 1 - address
        low, flags = _read(prg, cursor + 1, 2)
        count = (flags & 0x3F) + 1
        literal = bool(flags & 0x40)
        payload = _read(prg, cursor + 3, count if literal else 1)
        commands.append({
            "address": (high << 8) | low, "literal": literal,
            "vertical": bool(flags & 0x80), "bytes": list(payload) if literal else [payload[0]] * count,
        })
        cursor += 3 + len(payload)


def _encode_program(commands: object, name: str, *, terminate: bool = True) -> bytes:
    if not isinstance(commands, list):
        raise SolomonTextError(f"{name} commands must be a list")
    output = bytearray()
    for index, command in enumerate(commands):
        context = f"{name} command {index}"
        if not isinstance(command, Mapping) or set(command) != {"address", "literal", "vertical", "bytes"}:
            raise SolomonTextError(f"{context} fields differ")
        address = command["address"]
        if type(address) is not int or not 0x0100 <= address <= 0x3FFF:
            raise SolomonTextError(f"{context} address must be $0100-$3FFF")
        if type(command["literal"]) is not bool or type(command["vertical"]) is not bool:
            raise SolomonTextError(f"{context} flags must be booleans")
        payload = command["bytes"]
        if not isinstance(payload, list) or not 1 <= len(payload) <= 64:
            raise SolomonTextError(f"{context} writes 1-64 bytes")
        values = [_byte(value, f"{context} byte") for value in payload]
        if not command["literal"] and len(set(values)) != 1:
            raise SolomonTextError(f"{context} repeats one byte")
        flags = (0x40 if command["literal"] else 0) | (0x80 if command["vertical"] else 0) | (len(values) - 1)
        output += bytes((address >> 8, address & 0xFF, flags))
        output += bytes(values) if command["literal"] else bytes((values[0],))
    if terminate:
        output.append(0)
    return bytes(output)


# ---------------------------------------------------------------- messages

def _decode_message(prg: bytes, address: int, addresses: int) -> tuple[dict[str, Any], int]:
    header = _read(prg, address, 1)[0]
    tiles: list[int] = []
    cursor = address + 1
    while True:
        tile = _read(prg, cursor, 1)[0]
        cursor += 1
        if tile == 0:
            break
        tiles.append(tile)
    if not tiles or header != 0x40 | (len(tiles) - 1):
        raise SolomonTextError(f"ending message at ${address:04X} has an unexpected header")
    raw = _read(prg, cursor, addresses * 2)
    pairs = [raw[i] | (raw[i + 1] << 8) for i in range(0, len(raw), 2)]
    return {"tiles": tiles, "addresses": pairs}, cursor + len(raw) - address


def _encode_message(message: object, name: str, addresses: int) -> bytes:
    if not isinstance(message, Mapping) or set(message) != {"tiles", "addresses"}:
        raise SolomonTextError(f"{name} fields differ")
    tiles = message["tiles"]
    if not isinstance(tiles, list) or not 1 <= len(tiles) <= 64:
        raise SolomonTextError(f"{name} needs 1-64 tiles")
    values = [_byte(tile, f"{name} tile") for tile in tiles]
    if 0 in values:
        raise SolomonTextError(f"{name} cannot use tile $00: it ends the message")
    pairs = message["addresses"]
    if not isinstance(pairs, list) or len(pairs) != addresses:
        raise SolomonTextError(f"{name} needs {addresses} PPU address(es)")
    output = bytearray((0x40 | (len(values) - 1),))
    output += bytes(values)
    output.append(0)
    for value in pairs:
        if type(value) is not int or not 0x2000 <= value <= 0x2FFF:
            raise SolomonTextError(f"{name} addresses must be name-table addresses")
        output += bytes((value & 0xFF, value >> 8))
    return bytes(output)


# ---------------------------------------------------------------- decode

def decode_text(
    source: bytes, profile: GameProfile, revision: RevisionSpec,
    *, _verify_identity: bool = True,
) -> SolomonTextDocument:
    layout = _layout(profile, revision)
    prg = _source(source, revision, verify=_verify_identity).prg

    count = layout["static_count"]
    lows = _read(prg, layout["static_pointer_low_cpu"], count)
    highs = _read(prg, layout["static_pointer_high_cpu"], count)
    pointers = [low | (high << 8) for low, high in zip(lows, highs)]
    if sorted(range(count), key=lambda index: pointers[index]) != list(static_order(count)):
        raise SolomonTextError("static PPU programs are not in the expected order")
    programs: list[dict[str, Any]] = []
    cursor = layout["static_region_cpu"]
    for index in static_order(count):
        if pointers[index] != cursor:
            raise SolomonTextError(f"static PPU program {index:02d} does not follow the previous one")
        commands, size = _decode_program(prg, cursor, layout["static_region_end_cpu"])
        programs.append({"index": index, "commands": commands})
        cursor += size
    # A shorter build leaves zeros after the last program.
    if any(_read(prg, cursor, layout["static_region_end_cpu"] - cursor)):
        raise SolomonTextError("static PPU programs do not fill their region")
    reference = _read(prg, layout["static_05_reference_cpu"], 2)
    if reference[0] | (reference[1] << 8) != pointers[FIXED_PROGRAM]:
        raise SolomonTextError("the ending's copy of program 05 points elsewhere")
    programs.sort(key=lambda item: item["index"])

    initial: list[dict[str, Any]] = []
    cursor = layout["ending_initial_cpu"]
    for index in range(INITIAL_COUNT):
        if index == 1 and _read(prg, layout["ending_initial_second_operand_cpu"], 1)[0] + 1 \
                != cursor - layout["ending_initial_cpu"]:
            raise SolomonTextError("the second initial ending message starts elsewhere")
        message, size = _decode_message(prg, cursor, 2)
        initial.append(message)
        cursor += size
    if cursor > layout["ending_initial_end_cpu"]             or any(_read(prg, cursor, layout["ending_initial_end_cpu"] - cursor)):
        raise SolomonTextError("initial ending messages do not fill their region")

    offsets = _read(prg, layout["ending_offsets_cpu"], MESSAGE_COUNT)
    messages: list[dict[str, Any]] = []
    cursor = 0
    threshold = _read(prg, layout["ending_single_address_operand_cpu"], 1)[0]
    for index in range(MESSAGE_COUNT):
        if (offsets[index] + 1) & 0xFF != cursor:
            raise SolomonTextError(f"ending message {index} does not follow the previous one")
        doubled = index < DOUBLE_ADDRESS_MESSAGES
        message, size = _decode_message(prg, layout["ending_data_cpu"] + cursor, 2 if doubled else 1)
        terminator = cursor + 1 + len(message["tiles"])
        if doubled != (terminator < threshold):
            raise SolomonTextError("ending message address counts differ from the game's threshold")
        messages.append(message)
        cursor += size
    if cursor > layout["ending_data_end_cpu"] - layout["ending_data_cpu"]:
        raise SolomonTextError("ending messages run past their region")

    intro_bytes = _read(prg, layout["room_intro_cpu"], INTRO_SIZE)
    intro, size = _decode_program(prg, layout["room_intro_cpu"], layout["room_intro_cpu"] + INTRO_SIZE)
    if size != INTRO_SIZE or _encode_program(intro, "room intro") != intro_bytes:
        raise SolomonTextError("room intro template differs")
    names_raw = _read(prg, layout["special_names_cpu"], SPECIAL_NAME_COUNT * SPECIAL_NAME_WIDTH)
    names = tuple(
        tuple(names_raw[i * SPECIAL_NAME_WIDTH:(i + 1) * SPECIAL_NAME_WIDTH])
        for i in range(SPECIAL_NAME_COUNT)
    )
    result = SolomonTextDocument(
        profile.game_id, revision.revision_id, revision.sha256,
        _capacities(layout), tuple(programs), tuple(initial), tuple(messages),
        tuple(intro), names,
    )
    _encode(result.as_dict(), layout)
    return result


# ---------------------------------------------------------------- encode

def _encode(value: Mapping[str, Any], layout: Mapping[str, int]) -> dict[str, Any]:
    """Every region's bytes and patched operands, checked against capacity."""
    if value.get("layout") != _capacities(layout):
        raise SolomonTextError("text layout differs from profile")
    capacities = _capacities(layout)

    programs = value.get("static_programs")
    count = layout["static_count"]
    if not isinstance(programs, list) or len(programs) != count:
        raise SolomonTextError(f"text needs {count} static PPU programs")
    encoded: dict[int, bytes] = {}
    for index, program in enumerate(programs):
        if not isinstance(program, Mapping) or set(program) != {"index", "commands"} \
                or program["index"] != index:
            raise SolomonTextError("static PPU program indices differ")
        encoded[index] = _encode_program(program["commands"], f"static PPU program {index:02d}")
    static = bytearray()
    pointers = [0] * count
    for index in static_order(count):
        pointers[index] = layout["static_region_cpu"] + len(static)
        static += encoded[index]
    if len(static) > capacities["static_bytes"]:
        raise SolomonTextError(
            f"static PPU programs need {len(static)} bytes; their region has {capacities['static_bytes']}"
        )
    static += bytes(capacities["static_bytes"] - len(static))

    initial = value.get("ending_initial")
    if not isinstance(initial, list) or len(initial) != INITIAL_COUNT:
        raise SolomonTextError(f"text needs {INITIAL_COUNT} initial ending messages")
    initial_bytes = [
        _encode_message(message, f"initial ending message {index}", 2)
        for index, message in enumerate(initial)
    ]
    initial_data = b"".join(initial_bytes)
    if len(initial_data) > capacities["ending_initial_bytes"]:
        raise SolomonTextError(
            f"initial ending messages need {len(initial_data)} bytes; "
            f"their region has {capacities['ending_initial_bytes']}"
        )
    initial_data += bytes(capacities["ending_initial_bytes"] - len(initial_data))

    messages = value.get("ending_messages")
    if not isinstance(messages, list) or len(messages) != MESSAGE_COUNT:
        raise SolomonTextError(f"text needs {MESSAGE_COUNT} ending messages")
    data = bytearray()
    offsets = bytearray()
    threshold = None
    for index, message in enumerate(messages):
        offsets.append((len(data) - 1) & 0xFF)
        piece = _encode_message(
            message, f"ending message {index}", 2 if index < DOUBLE_ADDRESS_MESSAGES else 1
        )
        if index == DOUBLE_ADDRESS_MESSAGES:
            # The game compares a message's terminator index with this; the
            # first single-address message's offset entry is what it uses.
            threshold = (len(data) - 1) & 0xFF
        data += piece
    if len(data) > capacities["ending_message_bytes"]:
        raise SolomonTextError(
            f"ending messages need {len(data)} bytes; their region has {capacities['ending_message_bytes']}"
        )
    if threshold is None or threshold > 0xFF:
        raise SolomonTextError("ending messages do not fit the game's 256-byte index")
    message_used = len(data)
    data += bytes(capacities["ending_message_bytes"] - len(data))

    intro = value.get("room_intro")
    intro_data = _encode_program(intro, "room intro")
    if len(intro_data) != INTRO_SIZE or [
        (c["address"], c["literal"], c["vertical"], len(c["bytes"])) for c in intro
    ] != [(0x216B, True, False, 6), (0x21CB, True, False, 8), (0x226F, True, False, 3)]:
        raise SolomonTextError("the room intro keeps its three runs; only their tiles change")
    names = value.get("special_room_names")
    if not isinstance(names, list) or len(names) != SPECIAL_NAME_COUNT \
            or any(not isinstance(name, list) or len(name) != SPECIAL_NAME_WIDTH for name in names):
        raise SolomonTextError(f"special-room names are {SPECIAL_NAME_COUNT} runs of {SPECIAL_NAME_WIDTH} tiles")
    names_data = bytes(_byte(tile, "special-room name tile") for name in names for tile in name)

    fixed = programs[FIXED_PROGRAM]["commands"]
    if len(encoded[FIXED_PROGRAM]) != 20 or len(fixed) != 1 or fixed[0]["address"] != 0x3F00 \
            or not fixed[0]["literal"]:
        raise SolomonTextError("program 05 stays one 16-colour palette write: the ending copies it")

    return {
        "static_pointer_low_cpu": bytes(p & 0xFF for p in pointers),
        "static_pointer_high_cpu": bytes(p >> 8 for p in pointers),
        "static_region_cpu": bytes(static),
        "static_05_reference_cpu": bytes((pointers[FIXED_PROGRAM] & 0xFF, pointers[FIXED_PROGRAM] >> 8)),
        "ending_initial_cpu": initial_data,
        "ending_initial_second_operand_cpu": bytes((len(initial_bytes[0]) - 1,)),
        "ending_offsets_cpu": bytes(offsets),
        "ending_data_cpu": bytes(data),
        "ending_single_address_operand_cpu": bytes((threshold,)),
        "room_intro_cpu": intro_data,
        "special_names_cpu": names_data,
        "used": {
            "static_bytes": sum(len(piece) for piece in encoded.values()),
            "ending_initial_bytes": sum(len(piece) for piece in initial_bytes),
            "ending_message_bytes": message_used,
        },
    }


def parse_text_document(
    value: object, profile: GameProfile, revision: RevisionSpec,
) -> SolomonTextDocument:
    layout = _layout(profile, revision)
    if not isinstance(value, Mapping) or set(value) != {
        "schema", "schema_version", "game", "revision", "source_sha256", "layout",
        "static_programs", "ending_initial", "ending_messages", "room_intro",
        "special_room_names",
    }:
        raise SolomonTextError("text document fields differ")
    for key, expected in (
        ("schema", SCHEMA), ("schema_version", VERSION),
        ("game", profile.game_id), ("revision", revision.revision_id),
        ("source_sha256", revision.sha256),
    ):
        if value[key] != expected:
            raise SolomonTextError(f"text {key} differs from profile")
    _encode(value, layout)
    return SolomonTextDocument(
        profile.game_id, revision.revision_id, revision.sha256,
        _capacities(layout),
        tuple({"index": p["index"], "commands": [_command_dict(c) for c in p["commands"]]}
              for p in value["static_programs"]),
        tuple(_message_dict(m) for m in value["ending_initial"]),
        tuple(_message_dict(m) for m in value["ending_messages"]),
        tuple(_command_dict(c) for c in value["room_intro"]),
        tuple(tuple(name) for name in value["special_room_names"]),
    )


def build_text(
    source: bytes, document: SolomonTextDocument,
    profile: GameProfile, revision: RevisionSpec,
) -> tuple[bytes, dict[str, object]]:
    image = _source(source, revision)
    layout = _layout(profile, revision)
    checked = parse_text_document(document.as_dict(), profile, revision)
    pieces = _encode(checked.as_dict(), layout)
    base = len(image.header) + len(image.trainer)
    changes = []
    for field in (
        "static_pointer_low_cpu", "static_pointer_high_cpu", "static_region_cpu",
        "static_05_reference_cpu", "ending_initial_cpu", "ending_initial_second_operand_cpu",
        "ending_offsets_cpu", "ending_data_cpu", "ending_single_address_operand_cpu",
        "room_intro_cpu", "special_names_cpu",
    ):
        payload = pieces[field]
        start = base + layout[field] - 0x8000
        if source[start:start + len(payload)] == payload:
            continue
        changes.append(Change(
            AddressRange(Address("file", start), len(payload)),
            source[start:start + len(payload)], payload,
            Provenance(revision.sha256, "text", field),
        ))
    output = BuildPlan(revision.sha256, ChangeSet(tuple(changes))).apply(source) if changes else source
    rebuilt = decode_text(output, profile, revision, _verify_identity=False)
    if rebuilt.as_dict() != checked.as_dict():
        raise SolomonTextError("rebuilt text does not decode to document")
    return output, {
        "schema": "retrolarium.solomon-text-build",
        "schema_version": 1,
        "game": profile.game_id, "revision": revision.revision_id,
        "source_sha256": revision.sha256,
        "output_sha256": hashlib.sha256(output).hexdigest(),
        "size": len(output),
        "changed_bytes": sum(a != b for a, b in zip(source, output)),
        "used": pieces["used"],
        "capacity": _capacities(layout),
    }
