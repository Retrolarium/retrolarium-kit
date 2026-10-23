"""Alien Soldier's DAC samples: nine banks of DPCM the Z80 plays.

A bank starts with four-byte little-endian records, an in-bank start (bit 15
ignored) and a byte count, one per sample; the first start is also the size
of the record table, and the samples follow it end to end. Bytes after the
last sample stay as they are.

The Z80 decodes a byte into two levels, starting from $80: it adds the
delta its high-nibble page gives for the byte, then the delta of its
low-nibble page. The pages (`Dac_HighNibbleDeltas`, `Dac_LowNibbleDeltas`)
are read from the Z80 program's source, and so is everything else here: the
music and voice descriptor tables name each request's bank and the record
of its sample.

The document keeps every sample's bytes; a sample keeps its length, so a
replaced sound is cut or padded to it.
"""

from __future__ import annotations

import re
from typing import Any

from retrolarium_kit.platform.megadrive.asl import SourceTree, parse_number


SCHEMA = "alien-soldier.pcm"
DOCUMENT = "sound/pcm.json"
BANK_PREFIX = "Sound_PCMBank"
Z80_ENTRY = "src/sound/z80/driver.asm"
DELTA_PAGES = ("Dac_HighNibbleDeltas", "Dac_LowNibbleDeltas")
DESCRIPTORS = (("music", "Sound_PCMSampleDescriptors", 0x81), ("voice", "Sound_VoiceDACDescriptors", 0x10))
BANK_OPERAND = re.compile(r"^\((?P<bank>[A-Za-z_][A-Za-z0-9_]*)\s*>>\s*\$8\)$")
INITIAL_LEVEL = 0x80


class PcmError(ValueError):
    """A sample document that does not fit the source."""


def samples(data: bytes) -> list[tuple[int, int]]:
    """A bank's samples as (start, length)."""
    table = int.from_bytes(data[:2], "little") & 0x7FFF
    if not table or table % 4 or table > len(data):
        raise PcmError("not a sample bank")
    found, cursor = [], table
    for record in range(0, table, 4):
        start = int.from_bytes(data[record : record + 2], "little") & 0x7FFF
        length = int.from_bytes(data[record + 2 : record + 4], "little")
        if start != cursor or start + length > len(data):
            raise PcmError("the samples are not end to end")
        found.append((start, length))
        cursor = start + length
    return found


def deltas(root) -> tuple[list[int], list[int]]:
    """The Z80's high- and low-nibble delta pages, 256 bytes each."""
    tree = SourceTree(root, entry=Z80_ENTRY)
    high, low = (list(tree.data(label)) for label in DELTA_PAGES)
    if len(high) != 256 or len(low) != 256:
        raise PcmError("the delta pages are 256 bytes each")
    return high, low


def decode(data: bytes, high: list[int], low: list[int]) -> list[int]:
    """The levels a sample plays."""
    level, levels = INITIAL_LEVEL, []
    for byte in data:
        level = (level + high[byte]) & 0xFF
        levels.append(level)
        level = (level + low[byte]) & 0xFF
        levels.append(level)
    return levels


def encode(levels: list[int], high: list[int], low: list[int]) -> bytes:
    """Levels back to bytes, each byte the one whose two steps land nearest
    the next two levels (exact for levels a sample decoded to)."""
    out, level = bytearray(), INITIAL_LEVEL
    for at in range(0, len(levels) - 1, 2):
        best = min(
            range(256),
            key=lambda byte: (
                abs(((level + high[byte]) & 0xFF) - levels[at])
                + abs(((level + high[byte] + low[byte]) & 0xFF) - levels[at + 1])
            ),
        )
        out.append(best)
        level = (level + high[best] + low[best]) & 0xFF
    return bytes(out)


def banks(tree: SourceTree) -> list[str]:
    return [label for label in tree.order if label.startswith(BANK_PREFIX) and tree.block(label).kind == "binclude"]


def descriptors(tree: SourceTree, bank_labels: list[str]) -> list[dict[str, Any]]:
    """Each request's sample, by bank and record."""
    found = []
    for kind, label, first in DESCRIPTORS:
        operands = tree.block(label).operands
        for index in range(0, len(operands) - 3, 4):
            bank = BANK_OPERAND.match(operands[index].text)
            word = parse_number(operands[index + 1].text)
            if bank is None or word is None or bank["bank"] not in bank_labels:
                raise PcmError(f"{label} entry {index // 4} names no bank")
            header = (((word & 0xFF) << 8) | (word >> 8 & 0xFF)) & 0x7FFF
            found.append({"kind": kind, "id": first + index // 4, "bank": bank["bank"], "sample": header // 4})
    return found


def export(tree: SourceTree) -> dict[str, Any]:
    labels = banks(tree)
    high, low = deltas(tree.root)
    items = []
    for label in labels:
        data = tree.data(label)
        items.append(
            {
                "label": label,
                "samples": [data[start : start + length].hex().upper() for start, length in samples(data)],
            }
        )
    return {
        "schema": SCHEMA,
        "schema_version": 1,
        "deltas": {"high": high, "low": low, "initial": INITIAL_LEVEL},
        "descriptors": descriptors(tree, labels),
        "banks": items,
    }


def apply(tree: SourceTree, document: dict[str, Any]) -> None:
    if document.get("schema") != SCHEMA or document.get("schema_version") != 1:
        raise PcmError("not an Alien Soldier sample document")
    labels = banks(tree)
    items = document.get("banks")
    if not isinstance(items, list) or [item.get("label") for item in items] != labels:
        raise PcmError("the sample banks differ from the source")
    for item in items:
        label = item["label"]
        data = bytearray(tree.data(label))
        spans = samples(bytes(data))
        texts = item.get("samples")
        if not isinstance(texts, list) or len(texts) != len(spans):
            raise PcmError(f"{label}: the samples are the source's")
        for index, ((start, length), text) in enumerate(zip(spans, texts)):
            try:
                sample = bytes.fromhex(text)
            except (TypeError, ValueError):
                raise PcmError(f"{label} sample {index} is not hexadecimal") from None
            if len(sample) != length:
                raise PcmError(f"{label} sample {index} keeps its {length} bytes")
            data[start : start + length] = sample
        tree.set_data(label, bytes(data))
