"""Source-bound Solomon's Key sound bank: timing, envelopes, effects and streams."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any, Mapping

from retrolarium_kit.core.authoring import (
    Address, AddressRange, AuthoringError, BuildPlan, Change, ChangeSet,
    GameProfile, Provenance, RevisionSpec,
)
from retrolarium_core.ines import parse_ines


CODEC = "solomon.audio.v1"
SCHEMA = "retrolarium.solomon-audio"
VERSION = 1
PERIOD_COUNT = 12
ENVELOPE_COUNT = 8
EFFECT_COUNT = 26
STREAM_COUNT = 114
ARGUMENT_SIZES = {0xF0: 1, 0xF1: 1, 0xF2: 2, 0xF3: 2, 0xF4: 0,
                  0xF5: 1, 0xF6: 0, 0xF7: 1, 0xF8: 1, 0xF9: 0}
NAMES = {0xF0: "set_sequence", 0xF1: "set_control", 0xF2: "jump",
         0xF3: "call", 0xF4: "return", 0xF5: "begin_loop",
         0xF6: "end_loop", 0xF7: "set_sweep", 0xF8: "set_volume",
         0xF9: "stop"}
VALUES = {NAMES[opcode]: opcode for opcode in (0xF0, 0xF1, 0xF5, 0xF7, 0xF8)}
POINTERS = {"jump": 0xF2, "call": 0xF3}
SIMPLE = {NAMES[opcode]: opcode for opcode in (0xF4, 0xF6, 0xF9)}
LAYOUT_FIELDS = (
    "period_cpu", "duration_cpu", "envelope_pointer_cpu", "envelope_data_cpu",
    "effect_pointer_cpu", "effect_data_cpu", "stream_cpu", "stream_end_cpu",
)


class SolomonAudioError(AuthoringError):
    """The sound bank is malformed or does not fit its fixed allocation."""


@dataclass(frozen=True)
class SolomonAudioDocument:
    game_id: str
    revision_id: str
    source_sha256: str
    periods: tuple[int, ...]
    durations: tuple[int, ...]
    timing_tail: tuple[int, ...]
    envelopes: tuple[dict[str, Any], ...]
    effects: tuple[dict[str, Any], ...]
    commands: tuple[dict[str, Any], ...]
    trailing_bytes: tuple[int, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA, "schema_version": VERSION,
            "game": self.game_id, "revision": self.revision_id,
            "source_sha256": self.source_sha256,
            "periods": list(self.periods), "durations": list(self.durations),
            "timing_tail": list(self.timing_tail),
            "envelopes": [
                {"index": item["index"], "steps": [
                    dict(step) for step in item["steps"]]}
                for item in self.envelopes
            ],
            "effects": [
                {"number": item["number"], "channels": [
                    dict(channel) for channel in item["channels"]]}
                for item in self.effects
            ],
            "commands": [dict(item) for item in self.commands],
            "trailing_bytes": list(self.trailing_bytes),
        }


def _layout(profile: GameProfile, revision: RevisionSpec) -> dict[str, int]:
    resources = [
        item for item in profile.resources_for(revision) if item.codec == CODEC
    ]
    if len(resources) != 1:
        raise SolomonAudioError("Solomon profile needs one audio resource")
    parameters = resources[0].parameters
    if any(type(parameters.get(key)) is not int for key in LAYOUT_FIELDS):
        raise SolomonAudioError("audio layout requires eight CPU addresses")
    layout = {key: parameters[key] for key in LAYOUT_FIELDS}
    values = list(layout.values())
    if not (0x8000 <= values[0] < values[1] < values[2] < values[3]
            < values[4] < values[5] < values[6] <= values[7] <= 0xFFF9):
        raise SolomonAudioError("audio layout addresses are unordered")
    if (values[1] - values[0] != PERIOD_COUNT * 2
            or values[3] - values[2] != ENVELOPE_COUNT * 2
            or values[5] - values[4] != EFFECT_COUNT * 2):
        raise SolomonAudioError("audio pointer or timing table size differs")
    return layout


def _source(source: bytes, revision: RevisionSpec, *, verify_identity: bool = True):
    if verify_identity and hashlib.sha256(source).hexdigest() != revision.sha256:
        raise SolomonAudioError("audio source image identity mismatch")
    image = parse_ines(source)
    if image.mapper != revision.mapper or len(image.prg) != 0x8000:
        raise SolomonAudioError("Solomon audio needs a 32 KiB mapper-3 PRG")
    return image


def _slice(prg: bytes, start: int, stop: int) -> bytes:
    if not 0x8000 <= start <= stop <= 0x10000:
        raise SolomonAudioError(f"audio address range ${start:04X}..${stop:04X} is invalid")
    result = prg[start - 0x8000:stop - 0x8000]
    if len(result) != stop - start:
        raise SolomonAudioError("audio range escapes PRG")
    return result


def _word(prg: bytes, address: int) -> int:
    return int.from_bytes(_slice(prg, address, address + 2), "little")


def _byte(value: object, context: str) -> int:
    if type(value) is not int or not 0 <= value <= 255:
        raise SolomonAudioError(f"{context} must be a byte")
    return value


def _label(index: int) -> str:
    return f"stream_{index:03d}"


def _records(value: object, count: int, key: str, context: str) -> list[Mapping]:
    if not isinstance(value, list) or len(value) != count:
        raise SolomonAudioError(f"{context} needs {count} records")
    for index, item in enumerate(value):
        identity = index + 1 if key == "number" else index
        if not isinstance(item, Mapping) or item.get(key) != identity:
            raise SolomonAudioError(f"{context} record {index} identity differs")
    return value


def decode_audio(
    source: bytes, profile: GameProfile, revision: RevisionSpec,
    *, _verify_identity: bool = True,
) -> SolomonAudioDocument:
    layout = _layout(profile, revision)
    prg = _source(source, revision, verify_identity=_verify_identity).prg
    ep = layout["envelope_pointer_cpu"]
    ed = layout["envelope_data_cpu"]
    fp = layout["effect_pointer_cpu"]
    fd = layout["effect_data_cpu"]
    stream_start = layout["stream_cpu"]
    stream_end = layout["stream_end_cpu"]
    envelope_pointers = [_word(prg, ep + 2 * i) for i in range(ENVELOPE_COUNT)]
    if envelope_pointers[0] != ed or envelope_pointers != sorted(envelope_pointers):
        raise SolomonAudioError("envelope pointers are invalid")
    envelopes = []
    for i, (start, stop) in enumerate(zip(
        envelope_pointers, envelope_pointers[1:] + [fp]
    )):
        data = _slice(prg, start, stop)
        if not data or len(data) % 2:
            raise SolomonAudioError(f"envelope {i} has invalid size")
        envelopes.append({"index": i, "steps": [
            {"duration": data[j], "volume": data[j + 1]}
            for j in range(0, len(data), 2)
        ]})
    effect_pointers = [_word(prg, fp + 2 * i) for i in range(EFFECT_COUNT)]
    if effect_pointers[0] != fd or effect_pointers != sorted(effect_pointers):
        raise SolomonAudioError("effect pointers are invalid")
    if _slice(prg, stream_start - 1, stream_start) != b"\xff":
        raise SolomonAudioError("effect descriptors lack terminator")
    effects_raw = []
    pending: set[int] = set()
    for i, (start, stop) in enumerate(zip(
        effect_pointers, effect_pointers[1:] + [stream_start - 1]
    )):
        data = _slice(prg, start, stop)
        if not data or len(data) % 3:
            raise SolomonAudioError(f"effect {i + 1} has invalid size")
        channels = []
        for j in range(0, len(data), 3):
            selector = data[j]
            address = int.from_bytes(data[j + 1:j + 3], "little")
            if (j == 0) != bool(selector & 0x80) or selector & 0x7f > 7:
                raise SolomonAudioError(f"effect {i + 1} selector is invalid")
            if not stream_start <= address <= stream_end:
                raise SolomonAudioError("effect stream escapes sound bank")
            channels.append((selector, address))
            pending.add(address)
        effects_raw.append(channels)
    commands_by_address: dict[int, tuple[int, bytes]] = {}
    stream_addresses: set[int] = set()
    while pending:
        start = min(pending)
        pending.remove(start)
        if start in stream_addresses:
            continue
        stream_addresses.add(start)
        cursor = start
        while cursor <= stream_end:
            opcode = _slice(prg, cursor, cursor + 1)[0]
            if opcode >= 0xFA:
                raise SolomonAudioError(f"unknown audio opcode ${opcode:02X}")
            args = _slice(prg, cursor + 1, cursor + 1 + ARGUMENT_SIZES.get(opcode, 0))
            command = (opcode, args)
            previous = commands_by_address.setdefault(cursor, command)
            if previous != command:
                raise SolomonAudioError("overlapping audio command decode")
            if opcode in (0xF2, 0xF3):
                target = int.from_bytes(args, "little")
                if not stream_start <= target <= stream_end:
                    raise SolomonAudioError("audio command target escapes bank")
                if target not in stream_addresses:
                    pending.add(target)
            cursor += 1 + len(args)
            if opcode in (0xF2, 0xF4, 0xF9):
                break
        else:
            raise SolomonAudioError("unterminated audio stream")
    labels = {address: _label(i) for i, address in enumerate(sorted(stream_addresses))}
    if len(labels) != STREAM_COUNT:
        raise SolomonAudioError("audio stream entry count differs")
    commands = []
    cursor = stream_start
    while cursor <= stream_end:
        if cursor not in commands_by_address:
            raise SolomonAudioError(f"audio command coverage gap at ${cursor:04X}")
        opcode, args = commands_by_address[cursor]
        if opcode < 0x80:
            item = {"kind": "note", "value": opcode}
        elif opcode < 0xF0:
            item = {"kind": "duration", "value": opcode}
        elif opcode in (0xF2, 0xF3):
            target = int.from_bytes(args, "little")
            if target not in labels:
                raise SolomonAudioError("unlabelled audio target")
            item = {"kind": NAMES[opcode], "target": labels[target]}
        elif args:
            item = {"kind": NAMES[opcode], "value": args[0]}
        else:
            item = {"kind": NAMES[opcode]}
        if cursor in labels:
            item["entry"] = labels[cursor]
        commands.append(item)
        cursor += 1 + len(args)
    duration_start = layout["duration_cpu"]
    duration_count = min(64, ep - duration_start)
    return SolomonAudioDocument(
        profile.game_id, revision.revision_id, revision.sha256,
        tuple(_word(prg, layout["period_cpu"] + 2 * i) for i in range(PERIOD_COUNT)),
        tuple(_slice(prg, duration_start, duration_start + duration_count)),
        tuple(_slice(prg, duration_start + duration_count, ep)),
        tuple(envelopes),
        tuple({"number": i + 1, "channels": [
            {"selector": selector, "stream": labels[address]}
            for selector, address in channels
        ]} for i, channels in enumerate(effects_raw)),
        tuple(commands),
        tuple(_slice(prg, stream_end + 1, 0xFFFA)),
    )


def _encode(value: Mapping, layout: dict[str, int]) -> bytes:
    period_start = layout["period_cpu"]
    duration_start = layout["duration_cpu"]
    ep = layout["envelope_pointer_cpu"]
    ed = layout["envelope_data_cpu"]
    fp = layout["effect_pointer_cpu"]
    fd = layout["effect_data_cpu"]
    stream_start = layout["stream_cpu"]
    stream_end = layout["stream_end_cpu"]
    periods = value.get("periods")
    if not isinstance(periods, list) or len(periods) != PERIOD_COUNT:
        raise SolomonAudioError("audio needs 12 period words")
    if any(type(item) is not int or not 0 <= item <= 65535 for item in periods):
        raise SolomonAudioError("audio period word is invalid")
    timing = b"".join(item.to_bytes(2, "little") for item in periods)
    duration_count = min(64, ep - duration_start)
    durations = value.get("durations")
    tail = value.get("timing_tail")
    if not isinstance(durations, list) or len(durations) != duration_count:
        raise SolomonAudioError(f"audio needs {duration_count} duration bytes")
    if not isinstance(tail, list) or len(tail) != ep - duration_start - duration_count:
        raise SolomonAudioError("audio timing tail size differs")
    timing += bytes(_byte(item, "duration") for item in durations)
    timing += bytes(_byte(item, "timing tail") for item in tail)
    if len(timing) != ep - period_start:
        raise SolomonAudioError("audio timing allocation differs")
    envelope_data = bytearray()
    envelope_pointers = bytearray()
    for envelope in _records(value.get("envelopes"), ENVELOPE_COUNT, "index", "envelopes"):
        steps = envelope.get("steps")
        if not isinstance(steps, list) or not steps:
            raise SolomonAudioError("audio envelope has no steps")
        envelope_pointers.extend((ed + len(envelope_data)).to_bytes(2, "little"))
        for step in steps:
            if not isinstance(step, Mapping):
                raise SolomonAudioError("audio envelope step is invalid")
            envelope_data.extend((
                _byte(step.get("duration"), "envelope duration"),
                _byte(step.get("volume"), "envelope volume"),
            ))
    if len(envelope_data) != fp - ed:
        raise SolomonAudioError("audio envelopes exceed or underfill fixed allocation")
    raw_commands = value.get("commands")
    if not isinstance(raw_commands, list) or not raw_commands:
        raise SolomonAudioError("audio commands are empty")
    entries: dict[str, int] = {}
    cursor = stream_start
    for item in raw_commands:
        if not isinstance(item, Mapping):
            raise SolomonAudioError("audio command is invalid")
        kind = item.get("kind")
        if kind in ("note", "duration") or kind in SIMPLE:
            size = 1
        elif kind in VALUES:
            size = 2
        elif kind in POINTERS:
            size = 3
        else:
            raise SolomonAudioError(f"unknown audio command {kind!r}")
        entry = item.get("entry")
        if entry is not None:
            if type(entry) is not str or entry in entries:
                raise SolomonAudioError("duplicate audio stream entry")
            entries[entry] = cursor
        cursor += size
    if set(entries) != {_label(i) for i in range(STREAM_COUNT)}:
        raise SolomonAudioError("audio requires stream_000..113 entries")
    if cursor != stream_end + 1:
        raise SolomonAudioError("audio commands exceed or underfill fixed allocation")
    stream_data = bytearray()
    for item in raw_commands:
        kind = item["kind"]
        if kind in ("note", "duration"):
            token = _byte(item.get("value"), "audio token")
            if (kind == "note") != (token < 0x80) or token >= 0xF0:
                raise SolomonAudioError("audio note/duration token is invalid")
            stream_data.append(token)
        elif kind in VALUES:
            stream_data.extend((VALUES[kind], _byte(item.get("value"), kind)))
        elif kind in POINTERS:
            target = item.get("target")
            if target not in entries:
                raise SolomonAudioError("audio command target is unknown")
            stream_data.append(POINTERS[kind])
            stream_data.extend(entries[target].to_bytes(2, "little"))
        else:
            stream_data.append(SIMPLE[kind])
    effect_data = bytearray()
    effect_pointers = bytearray()
    for effect in _records(value.get("effects"), EFFECT_COUNT, "number", "effects"):
        channels = effect.get("channels")
        if not isinstance(channels, list) or not channels:
            raise SolomonAudioError("audio effect has no channels")
        effect_pointers.extend((fd + len(effect_data)).to_bytes(2, "little"))
        for index, channel in enumerate(channels):
            if not isinstance(channel, Mapping):
                raise SolomonAudioError("audio effect channel is invalid")
            selector = _byte(channel.get("selector"), "audio channel selector")
            if selector & 0x7f > 7 or bool(selector & 0x80) != (index == 0):
                raise SolomonAudioError("audio channel selector is invalid")
            target = channel.get("stream")
            if target not in entries:
                raise SolomonAudioError("audio effect stream is unknown")
            effect_data.append(selector)
            effect_data.extend(entries[target].to_bytes(2, "little"))
    if len(effect_data) != stream_start - fd - 1:
        raise SolomonAudioError("audio effects exceed or underfill fixed allocation")
    trailing = value.get("trailing_bytes")
    if not isinstance(trailing, list) or len(trailing) != 0xFFFA - stream_end - 1:
        raise SolomonAudioError("audio trailing data size differs")
    result = (timing + bytes(envelope_pointers) + bytes(envelope_data)
              + bytes(effect_pointers) + bytes(effect_data) + b"\xff"
              + bytes(stream_data)
              + bytes(_byte(item, "trailing audio data") for item in trailing))
    if len(result) != 0xFFFA - period_start:
        raise SolomonAudioError("audio bank size differs")
    return result


def parse_audio_document(
    value: object, profile: GameProfile, revision: RevisionSpec,
) -> SolomonAudioDocument:
    layout = _layout(profile, revision)
    if not isinstance(value, Mapping) or set(value) != {
        "schema", "schema_version", "game", "revision", "source_sha256",
        "periods", "durations", "timing_tail", "envelopes", "effects",
        "commands", "trailing_bytes",
    }:
        raise SolomonAudioError("audio document fields differ")
    for key, expected in (
        ("schema", SCHEMA), ("schema_version", VERSION),
        ("game", profile.game_id), ("revision", revision.revision_id),
        ("source_sha256", revision.sha256),
    ):
        if value[key] != expected:
            raise SolomonAudioError(f"audio {key} differs from profile")
    _encode(value, layout)
    return SolomonAudioDocument(
        profile.game_id, revision.revision_id, revision.sha256,
        tuple(value["periods"]), tuple(value["durations"]),
        tuple(value["timing_tail"]), tuple(value["envelopes"]),
        tuple(value["effects"]), tuple(value["commands"]),
        tuple(value["trailing_bytes"]),
    )


def build_audio(
    source: bytes, document: SolomonAudioDocument,
    profile: GameProfile, revision: RevisionSpec,
) -> tuple[bytes, dict[str, object]]:
    image = _source(source, revision)
    layout = _layout(profile, revision)
    checked = parse_audio_document(document.as_dict(), profile, revision)
    encoded = _encode(checked.as_dict(), layout)
    start = layout["period_cpu"] - 0x8000
    if encoded == image.prg[start:start + len(encoded)]:
        output = source
    else:
        base = len(image.header) + len(image.trainer)
        file_start = base + start
        change = Change(
            AddressRange(Address("file", file_start), len(encoded)),
            source[file_start:file_start + len(encoded)], encoded,
            Provenance(revision.sha256, "audio", "encode"),
        )
        output = BuildPlan(revision.sha256, ChangeSet((change,))).apply(source)
    rebuilt_document = decode_audio(
        output, profile, revision, _verify_identity=False
    )
    if rebuilt_document.as_dict() != checked.as_dict():
        raise SolomonAudioError("rebuilt audio does not decode to the document")
    return output, {
        "schema": "retrolarium.solomon-audio-build", "schema_version": 1,
        "game": profile.game_id, "revision": revision.revision_id,
        "source_sha256": revision.sha256,
        "output_sha256": hashlib.sha256(output).hexdigest(),
        "size": len(output),
        "changed_bytes": sum(a != b for a, b in zip(source, output)),
        "effects": len(checked.effects), "streams": STREAM_COUNT,
    }
