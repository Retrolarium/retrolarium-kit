"""The four attract-mode demos of the pinned Flicky source snapshot.

Each demo is a stream of (buttons, hold) byte pairs that Demo_ReadInput plays
into Ram_Joypad: the buttons stay held for `hold` frames (0 holds 256). The
four streams sit back to back in game/screens/attract_mode.s, inside the code
the game copies to RAM, so edited streams keep the block's size: they are
packed from its start and the rest is padded with idle pairs. Nothing after
the block moves, and each stream pointer follows its label.
"""

from __future__ import annotations

from pathlib import Path
import re


DEMO_SOURCE = "game/screens/attract_mode.s"
DEMO_COUNT = 4
# Demo_RoundIndexTable: the round each demo plays, 1-based.
DEMO_ROUNDS = (1, 10, 20, 24)
BUTTONS = (("A", 0x40), ("B", 0x10), ("C", 0x20), ("START", 0x80),
           ("UP", 0x01), ("DOWN", 0x02), ("LEFT", 0x04), ("RIGHT", 0x08))
FIRST_LABEL = "Demo_InputStream0:"
LAST_LABEL = "Demo_InputStream3_End:"
LABEL_RE = re.compile(r"(?m)^([A-Za-z_][A-Za-z0-9_]*):")
BINCLUDE_RE = re.compile(r'^\s*binclude\s+"([^"]+)"\s*(?:;.*)?$', re.IGNORECASE)
DATA_RE = re.compile(r"^\s*dc\.([bw])\s+([^;]+)", re.IGNORECASE)


def _number(token: str) -> int:
    token = token.strip()
    if re.fullmatch(r"\$[0-9A-Fa-f]+", token):
        return int(token[1:], 16)
    if re.fullmatch(r"[0-9]+", token):
        return int(token)
    raise ValueError(f"invalid Flicky demo value: {token}")


def _region(source: str) -> tuple[int, int]:
    start = re.search(r"(?m)^" + re.escape(FIRST_LABEL), source)
    end = re.search(r"(?m)^" + re.escape(LAST_LABEL) + r"[^\n]*\n?", source)
    if start is None or end is None or end.start() < start.start():
        raise ValueError("Flicky attract demo streams are missing")
    return start.start(), end.end()


def _stream_bytes(block: str, source_root: Path) -> bytes:
    lines = block.splitlines()
    lines[0] = lines[0].split(":", 1)[1]
    result = bytearray()
    for line in lines:
        if not line.strip() or line.lstrip().startswith(";"):
            continue
        include = BINCLUDE_RE.match(line)
        data = DATA_RE.match(line)
        if include:
            path = source_root / include.group(1)
            result += path.read_bytes()
        elif data:
            size = 1 if data.group(1).lower() == "b" else 2
            for token in data.group(2).split(","):
                value = _number(token)
                if not 0 <= value < 1 << (8 * size):
                    raise ValueError(f"Flicky demo value out of range: {token.strip()}")
                result += value.to_bytes(size, "big")
        else:
            raise ValueError(f"unexpected directive in Flicky demo stream: {line.strip()}")
    return bytes(result)


def _streams(source: str, source_root: Path) -> list[bytes]:
    start, end = _region(source)
    region = source[start:end]
    labels = list(LABEL_RE.finditer(region))
    spans = {match.group(1): (match.start(), labels[index + 1].start()
                              if index + 1 < len(labels) else len(region))
             for index, match in enumerate(labels)}
    streams = []
    for index in range(DEMO_COUNT):
        label = f"Demo_InputStream{index}"
        if label not in spans:
            raise ValueError(f"missing Flicky demo stream: {label}")
        first, last = spans[label]
        stream = _stream_bytes(region[first:last], source_root)
        if not stream or len(stream) % 2:
            raise ValueError(f"Flicky demo stream is not (buttons, hold) pairs: {label}")
        streams.append(stream)
    for label, (first, last) in spans.items():
        if not label.startswith("Demo_InputStream") \
                or (label.endswith("_End") and region[first:last].split(":", 1)[1].strip()):
            raise ValueError(f"unexpected data in the Flicky demo block: {label}")
    return streams


def button_names(mask: int) -> list[str]:
    return [name for name, bit in BUTTONS if mask & bit]


def button_mask(names: list[str]) -> int:
    bits = dict(BUTTONS)
    if not isinstance(names, list) or any(name not in bits for name in names) \
            or len(set(names)) != len(names):
        raise ValueError("Flicky demo buttons are A, B, C, START, UP, DOWN, LEFT and RIGHT")
    mask = 0
    for name in names:
        mask |= bits[name]
    return mask


def decode_steps(stream: bytes) -> list[dict[str, object]]:
    return [{"frames": stream[index + 1] or 256, "buttons": button_names(stream[index])}
            for index in range(0, len(stream), 2)]


def encode_steps(steps: list[dict[str, object]]) -> bytes:
    result = bytearray()
    for step in steps:
        frames = step.get("frames") if isinstance(step, dict) else None
        if type(frames) is not int or not 1 <= frames <= 256:
            raise ValueError("a Flicky demo step lasts 1-256 frames")
        result += bytes((button_mask(step.get("buttons")), frames & 0xFF))
    return bytes(result)


def export_attract_demos(source_root: Path) -> list[dict[str, object]]:
    source = (source_root / "src" / DEMO_SOURCE).read_text(encoding="utf-8")
    return [{"id": f"demo{index}", "source": DEMO_SOURCE, "round": DEMO_ROUNDS[index],
             "baseline_bytes": list(stream), "steps": decode_steps(stream)}
            for index, stream in enumerate(_streams(source, source_root))]


def capacity_bytes(records: list[dict[str, object]]) -> int:
    return sum(len(record["baseline_bytes"]) for record in records)


def validate_attract_demos(document: dict, source_root: Path) -> None:
    """Allow legacy workspaces without the section; validate new ones strictly."""
    if "attract_demos" not in document:
        return
    records = document["attract_demos"]
    baseline = export_attract_demos(source_root)
    if not isinstance(records, list) or len(records) != DEMO_COUNT:
        raise ValueError("Flicky attract demos need exactly four streams")
    used = 0
    for record, expected in zip(records, baseline):
        if not isinstance(record, dict) or any(
            record.get(key) != expected[key]
            for key in ("id", "source", "round", "baseline_bytes")
        ):
            raise ValueError("Flicky attract demo identity or baseline changed")
        steps = record.get("steps")
        if not isinstance(steps, list) or not steps:
            raise ValueError(f"Flicky {record['id']} needs at least one step")
        used += len(encode_steps(steps))
    capacity = capacity_bytes(baseline)
    if used > capacity:
        raise ValueError(f"Flicky attract demos use {used} bytes; the block holds {capacity}")


def _data_lines(label: str, payload: bytes) -> list[str]:
    lines = []
    for index in range(0, len(payload), 16):
        values = ", ".join(f"${value:02X}" for value in payload[index:index + 16])
        prefix = f"{label}:  dc.b    " if index == 0 and label else "                dc.b    "
        lines.append(prefix + values)
    return lines


def render_demo_block(streams: list[bytes], capacity: int) -> str:
    used = sum(len(stream) for stream in streams)
    if used > capacity:
        raise ValueError(f"Flicky attract demos use {used} bytes; the block holds {capacity}")
    lines = [";!(RETROLARIUM) attract demos packed in the original block; idle pairs pad it"]
    for index, stream in enumerate(streams):
        lines += _data_lines(f"Demo_InputStream{index}", stream)
        if index != 1:
            lines.append(f"Demo_InputStream{index}_End:")
    lines += _data_lines("", bytes(capacity - used))
    return "\n".join(lines) + "\n"


def stage_attract_demos(document: dict, source_root: Path, staged_source: Path) -> None:
    validate_attract_demos(document, source_root)
    if "attract_demos" not in document:
        return
    records = document["attract_demos"]
    streams = [encode_steps(record["steps"]) for record in records]
    if all(stream == bytes(record["baseline_bytes"])
           for stream, record in zip(streams, records)):
        return
    path = staged_source / DEMO_SOURCE
    source = path.read_text(encoding="utf-8")
    baseline = [bytes(record["baseline_bytes"]) for record in records]
    if _streams(source, source_root) != baseline:
        raise ValueError("staged Flicky attract demos differ from the baseline")
    start, end = _region(source)
    block = render_demo_block(streams, sum(len(stream) for stream in baseline))
    path.write_text(source[:start] + block + source[end:], encoding="utf-8", newline="\n")
