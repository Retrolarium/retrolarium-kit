"""Doraemon shell text: the fixed presentation text of PRG bank 3.

Ported from the source reconstruction's shell-text codec
(`scripts/validation/reconstruction/shell_text.py`, manifest
`config/authoring/text/shell_text.json`). Five regions keep their size:

- the game-over line (tile $00 is a blank);
- the title stream: address/count/payload PPU records ending in $00; five
  records are text ($00 blank, $5B period), the rest graphics kept as hex;
- the 32x32 ending-opening name table, as hex rows;
- three chapter-help name tables with fixed-width item names between raw
  spans;
- 380 32-byte ending-credit rows, ASCII-like (the uploader maps space,
  period and ampersand to tiles $7F, $5B and $5F).
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any, Mapping
import zlib

from retrolarium_kit.core.authoring import (
    Address, AddressRange, AuthoringError, BuildPlan, Change, ChangeSet,
    GameProfile, Provenance, RevisionSpec,
)
from retrolarium_core.ines import parse_ines


CODEC = "doraemon.shell-text.v1"
SCHEMA = "retrolarium.doraemon-text"
VERSION = 1
BANK_SIZE = 0x8000
CPU_BASE = 0x8000
FIELDS = ("game_over", "title_records", "ending_nametable_rows", "chapter_help_screens", "ending_credit_rows")


class DoraemonTextError(AuthoringError):
    """A text document does not fit the shell's fixed regions."""


@dataclass(frozen=True)
class DoraemonTextDocument:
    game_id: str
    revision_id: str
    source_sha256: str
    content: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA, "schema_version": VERSION,
            "game": self.game_id, "revision": self.revision_id,
            "source_sha256": self.source_sha256,
            **{name: self.content[name] for name in FIELDS},
        }


def number(value: str | int) -> int:
    return value if isinstance(value, int) else int(value, 0)


def _manifest(profile: GameProfile, revision: RevisionSpec) -> Mapping[str, Any]:
    resources = [item for item in profile.resources_for(revision) if item.codec == CODEC]
    if len(resources) != 1:
        raise DoraemonTextError("Doraemon profile needs one shell-text resource")
    return resources[0].parameters


def _prg(source: bytes, revision: RevisionSpec, *, verify: bool = True) -> bytes:
    if verify and hashlib.sha256(source).hexdigest() != revision.sha256:
        raise DoraemonTextError("text source identity mismatch")
    image = parse_ines(source)
    if len(image.prg) != 4 * BANK_SIZE:
        raise DoraemonTextError("Doraemon text needs four 32 KiB PRG banks")
    return image.prg


def _bank_slice(prg: bytes, bank: int, address: int, size: int) -> bytes:
    offset = bank * BANK_SIZE + address - CPU_BASE
    if not 0 <= bank < 4 or not 0 <= offset <= len(prg) - size:
        raise DoraemonTextError("shell-text range is outside PRG")
    return prg[offset:offset + size]


def _region_size(spec: Mapping[str, Any]) -> int:
    address = number(spec["address"])
    if "size" in spec:
        return int(spec["size"])
    if "end_address" in spec:
        return number(spec["end_address"]) - address + 1
    return int(spec["row_width"]) * int(spec["row_count"])


def _region(prg: bytes, bank: int, spec: Mapping[str, Any]) -> bytes:
    return _bank_slice(prg, bank, number(spec["address"]), _region_size(spec))


def _check_signatures(prg: bytes, manifest: Mapping[str, Any]) -> None:
    """The shell code the text codec relies on is where the profile says:
    each signature is the CRC-32 of a stretch of code, not the code."""
    bank = int(manifest["bank"])
    for signature in manifest.get("signatures", []):
        code = _bank_slice(prg, bank, number(signature["address"]), int(signature["size"]))
        if f"{zlib.crc32(code):08x}" != str(signature["crc32"]).lower():
            raise DoraemonTextError(f"the shell code at ${number(signature['address']):04X} differs from the profile")


def _decode_title_text(data: bytes) -> str:
    return "".join(" " if value == 0 else "." if value == 0x5B else chr(value) for value in data)


def _encode_title_text(text: str) -> bytes:
    try:
        return bytes(0 if c == " " else 0x5B if c == "." else ord(c) for c in text)
    except ValueError as exc:
        raise DoraemonTextError("title text contains a non-byte character") from exc


def _parse_title_stream(data: bytes, address: int, text_records: Mapping[int, str]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    cursor = 0
    while cursor < len(data):
        source_address = address + cursor
        high = data[cursor]
        if high == 0:
            if cursor != len(data) - 1:
                raise DoraemonTextError("title stream terminator is not the final byte")
            return records
        if cursor + 3 > len(data):
            raise DoraemonTextError("title stream has a truncated header")
        ppu_address = high << 8 | data[cursor + 1]
        size = data[cursor + 2] or 256
        end = cursor + 3 + size
        if end > len(data):
            raise DoraemonTextError("title stream has a truncated payload")
        payload = data[cursor + 3:end]
        record: dict[str, Any] = {"source_address": f"0x{source_address:04X}", "ppu_address": f"0x{ppu_address:04X}"}
        if ppu_address in text_records:
            record["id"] = text_records[ppu_address]
            record["text"] = _decode_title_text(payload)
        else:
            record["hex"] = payload.hex(" ")
        records.append(record)
        cursor = end
    raise DoraemonTextError("title stream has no terminator")


def _encode_title_stream(records: Any, start_address: int, expected_count: int) -> bytes:
    if not isinstance(records, list) or len(records) != expected_count:
        raise DoraemonTextError(f"the title stream holds {expected_count} records")
    output = bytearray()
    for record in records:
        if not isinstance(record, Mapping) or number(record["source_address"]) != start_address + len(output):
            raise DoraemonTextError("title record source addresses are not contiguous")
        ppu_address = number(record["ppu_address"])
        if not 0x2000 <= ppu_address <= 0x3FFF:
            raise DoraemonTextError("title record PPU address is outside name-table space")
        if ("text" in record) == ("hex" in record):
            raise DoraemonTextError("a title record holds text or hex")
        payload = _encode_title_text(str(record["text"])) if "text" in record else bytes.fromhex(str(record["hex"]))
        if not 1 <= len(payload) <= 256:
            raise DoraemonTextError("title record payload size is outside 1..256")
        output += ppu_address.to_bytes(2, "big") + bytes((len(payload) & 0xFF,)) + payload
    output.append(0)
    return bytes(output)


def _decode_help_screen(data: bytes, screen: Mapping[str, Any]) -> dict[str, Any]:
    base = number(screen["address"])
    cursor = 0
    segments: list[dict[str, Any]] = []
    for span in screen["text_spans"]:
        start = number(span["address"]) - base
        size = int(span["size"])
        if start < cursor or start + size > len(data):
            raise DoraemonTextError("a chapter-help text span is outside or overlaps its screen")
        if start > cursor:
            segments.append({"address": f"0x{base + cursor:04X}", "hex": data[cursor:start].hex(" ")})
        segments.append({"id": span["id"], "address": f"0x{base + start:04X}", "text": data[start:start + size].decode("ascii")})
        cursor = start + size
    if cursor < len(data):
        segments.append({"address": f"0x{base + cursor:04X}", "hex": data[cursor:].hex(" ")})
    return {"id": screen["id"], "address": screen["address"], "segments": segments}


def _encode_help_screen(screen: Any, expected: Mapping[str, Any], screen_size: int) -> bytes:
    if not isinstance(screen, Mapping) or screen.get("id") != expected["id"]:
        raise DoraemonTextError("chapter-help screen identity differs")
    base = number(expected["address"])
    if number(screen["address"]) != base:
        raise DoraemonTextError("chapter-help screen address differs")
    spans = {span["id"]: span for span in expected["text_spans"]}
    output = bytearray()
    for segment in screen.get("segments", []):
        if number(segment["address"]) != base + len(output):
            raise DoraemonTextError("chapter-help segments are not contiguous")
        if ("text" in segment) == ("hex" in segment):
            raise DoraemonTextError("a chapter-help segment holds text or hex")
        if "text" in segment:
            span = spans.pop(segment.get("id"), None)
            if span is None:
                raise DoraemonTextError("chapter-help text span identity differs")
            try:
                raw = str(segment["text"]).encode("ascii")
            except UnicodeEncodeError as exc:
                raise DoraemonTextError("chapter-help text is not ASCII") from exc
            if len(raw) != int(span["size"]):
                raise DoraemonTextError(f"{span['id']} keeps {span['size']} places")
        else:
            raw = bytes.fromhex(str(segment["hex"]))
        output += raw
    if spans or len(output) != screen_size:
        raise DoraemonTextError("chapter-help screen coverage differs")
    return bytes(output)


def _decode_content(prg: bytes, manifest: Mapping[str, Any]) -> dict[str, Any]:
    bank = int(manifest["bank"])
    title = manifest["title_stream"]
    text_records = {number(record["ppu_address"]): str(record["id"]) for record in title["text_records"]}
    ending = manifest["ending_nametable"]
    help_spec = manifest["chapter_help_screens"]
    help_raw = _region(prg, bank, help_spec)
    screen_size = int(help_spec["screen_size"])
    credits = manifest["ending_credits"]
    credit_raw = _region(prg, bank, credits)
    width = int(credits["row_width"])
    ending_raw = _region(prg, bank, ending)
    row = int(ending["row_width"])
    try:
        return {
            "game_over": "".join(" " if v == 0 else chr(v) for v in _region(prg, bank, manifest["game_over"])),
            "title_records": _parse_title_stream(_region(prg, bank, title), number(title["address"]), text_records),
            "ending_nametable_rows": [ending_raw[o:o + row].hex(" ") for o in range(0, len(ending_raw), row)],
            "chapter_help_screens": [
                _decode_help_screen(help_raw[i * screen_size:(i + 1) * screen_size], screen)
                for i, screen in enumerate(help_spec["screens"])
            ],
            "ending_credit_rows": [credit_raw[o:o + width].decode("ascii") for o in range(0, len(credit_raw), width)],
        }
    except UnicodeDecodeError as exc:
        raise DoraemonTextError("shell text holds bytes outside ASCII") from exc


def _encode_content(content: Mapping[str, Any], manifest: Mapping[str, Any]) -> dict[str, bytes]:
    game = manifest["game_over"]
    try:
        game_raw = bytes(0 if c == " " else ord(c) for c in str(content["game_over"]))
    except ValueError as exc:
        raise DoraemonTextError("game-over text contains a non-byte character") from exc
    if len(game_raw) != int(game["size"]):
        raise DoraemonTextError(f"GAME OVER keeps {game['size']} places")
    title = manifest["title_stream"]
    title_raw = _encode_title_stream(content["title_records"], number(title["address"]), int(title["record_count"]))
    if len(title_raw) != _region_size(title):
        raise DoraemonTextError("the title text must keep its total length")
    ending = manifest["ending_nametable"]
    rows = content["ending_nametable_rows"]
    if not isinstance(rows, list) or len(rows) != int(ending["row_count"]):
        raise DoraemonTextError("the ending name table has 32 rows")
    ending_raw = b"".join(bytes.fromhex(str(row)) for row in rows)
    if len(ending_raw) != _region_size(ending):
        raise DoraemonTextError("an ending name-table row has the wrong width")
    help_spec = manifest["chapter_help_screens"]
    screens = content["chapter_help_screens"]
    if not isinstance(screens, list) or len(screens) != int(help_spec["count"]):
        raise DoraemonTextError("chapter-help screen count differs")
    help_raw = b"".join(
        _encode_help_screen(screen, expected, int(help_spec["screen_size"]))
        for screen, expected in zip(screens, help_spec["screens"])
    )
    credits = manifest["ending_credits"]
    credit_rows = content["ending_credit_rows"]
    if not isinstance(credit_rows, list) or len(credit_rows) != int(credits["row_count"]):
        raise DoraemonTextError("ending credits row count differs")
    if any(not isinstance(row, str) or len(row) != int(credits["row_width"]) for row in credit_rows):
        raise DoraemonTextError(f"every credits row keeps {credits['row_width']} places")
    try:
        credit_raw = "".join(credit_rows).encode("ascii")
    except UnicodeEncodeError as exc:
        raise DoraemonTextError("ending credits contain non-ASCII text") from exc
    return {
        "game_over": game_raw, "title_stream": title_raw, "ending_nametable": ending_raw,
        "chapter_help_screens": help_raw, "ending_credits": credit_raw,
    }


def decode_text(source: bytes, profile: GameProfile, revision: RevisionSpec,
                *, _verify_identity: bool = True) -> DoraemonTextDocument:
    manifest = _manifest(profile, revision)
    prg = _prg(source, revision, verify=_verify_identity)
    _check_signatures(prg, manifest)
    content = _decode_content(prg, manifest)
    encoded = _encode_content(content, manifest)
    for name, raw in encoded.items():
        if raw != _region(prg, int(manifest["bank"]), manifest[name]):
            raise DoraemonTextError(f"{name} does not re-encode to its bytes")
    return DoraemonTextDocument(profile.game_id, revision.revision_id, revision.sha256, content)


def parse_text_document(value: object, profile: GameProfile, revision: RevisionSpec) -> DoraemonTextDocument:
    manifest = _manifest(profile, revision)
    if not isinstance(value, Mapping) or set(value) != {
        "schema", "schema_version", "game", "revision", "source_sha256", *FIELDS,
    }:
        raise DoraemonTextError("text document fields differ")
    for key, expected in (
        ("schema", SCHEMA), ("schema_version", VERSION), ("game", profile.game_id),
        ("revision", revision.revision_id), ("source_sha256", revision.sha256),
    ):
        if value[key] != expected:
            raise DoraemonTextError(f"text {key} differs from profile")
    content = {name: value[name] for name in FIELDS}
    _encode_content(content, manifest)
    return DoraemonTextDocument(profile.game_id, revision.revision_id, revision.sha256, content)


def build_text(source: bytes, document: DoraemonTextDocument, profile: GameProfile,
               revision: RevisionSpec) -> tuple[bytes, dict[str, object]]:
    manifest = _manifest(profile, revision)
    image = parse_ines(source)
    prg = _prg(source, revision)
    _check_signatures(prg, manifest)
    checked = parse_text_document(document.as_dict(), profile, revision)
    encoded = _encode_content(checked.content, manifest)
    bank = int(manifest["bank"])
    changes = []
    base = len(image.header) + len(image.trainer) + bank * BANK_SIZE - CPU_BASE
    for name, raw in encoded.items():
        start = base + number(manifest[name]["address"])
        if source[start:start + len(raw)] != raw:
            changes.append(Change(
                AddressRange(Address("file", start), len(raw)), source[start:start + len(raw)], raw,
                Provenance(revision.sha256, "text", name),
            ))
    output = BuildPlan(revision.sha256, ChangeSet(tuple(changes))).apply(source) if changes else source
    rebuilt = decode_text(output, profile, revision, _verify_identity=False)
    if _encode_content(rebuilt.content, manifest) != encoded:
        raise DoraemonTextError("rebuilt text does not decode to the document")
    return output, {
        "schema": "retrolarium.doraemon-text-build", "schema_version": 1,
        "game": profile.game_id, "revision": revision.revision_id,
        "source_sha256": revision.sha256,
        "output_sha256": hashlib.sha256(output).hexdigest(),
        "size": len(output),
        "changed_bytes": sum(a != b for a, b in zip(source, output)),
        "credit_rows": len(checked.content["ending_credit_rows"]),
    }
