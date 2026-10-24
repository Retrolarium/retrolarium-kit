"""Alien Soldier's LZSS: the game's decompressor and an encoder it accepts.

A payload is a big-endian word counting the stream's bytes, then the stream.
`Data_LZSSDecomp` reads five kinds of token:

| Control     | Token                                              | Reads   | Writes   |
| ----------- | -------------------------------------------------- | ------- | -------- |
| `$00-$1F`   | `(c & $1F) + 1` literal bytes                      | `1 + n` | `n`      |
| `$20-$3F`   | one byte, `(c & $1F) + 2` times                    | 2       | `n`      |
| `$40-$5F`   | one pair, `(c & $1F) + 2` times                    | 3       | `2n`     |
| `$60-$7F`   | a fixed byte before each of `(c & $1F) + 2` bytes  | `2 + n` | `2n`     |
| `$80-$FF`   | a match of `((c >> 2) & $1F) + 2` bytes, distance  | 2       | `length` |
|             | the low ten bits of the token word plus one        |         |          |

The VRAM loader calls the decompressor once per `$400` bytes of output and
resets its output pointer each time, so a match must never reach back across a
`$400` boundary: the encoder parses each block on its own. Every original
payload obeys the same rule, whichever loader reads it.

The encoder's parse is optimal within a block, so it is smaller than the
original encoder's and cannot reproduce the cartridge's bytes; documents
therefore keep an unedited stream as it was and only compress what changed.
"""

from __future__ import annotations

from functools import lru_cache
import struct


BLOCK = 0x400
MAX_DISTANCE = 0x400
MIN_MATCH = 2
MAX_MATCH = 33
MAX_LITERAL = 32
MAX_REPEAT = 33


class LzssError(ValueError):
    """A stream the game's decompressor would not read as intended."""


def stream_end(payload: bytes) -> int:
    """Where the stream ends: two header bytes plus the count they hold."""
    if len(payload) < 3:
        raise LzssError(f"{len(payload)} bytes are too few for an LZSS payload")
    end = 2 + struct.unpack_from(">H", payload)[0]
    if end > len(payload):
        raise LzssError(f"the header counts {end - 2} bytes, the payload holds {len(payload) - 2}")
    return end


def decompress(payload: bytes, *, blocks: bool = False) -> bytes:
    """What the decompressor writes. With `blocks`, as the VRAM loader runs
    it: a match may not reach before the start of its $400 block. Readers
    ask for the same payloads many times while a project opens, so the
    output is kept by the input's bytes."""
    return _decompress(bytes(payload), blocks)


@lru_cache(maxsize=4096)
def _decompress(payload: bytes, blocks: bool) -> bytes:
    end = stream_end(payload)
    out = bytearray()
    at = 2
    try:
        while at < end:
            control = payload[at]
            if control >= 0x80:
                length = ((control >> 2) & 0x1F) + 2
                distance = (((control << 8) | payload[at + 1]) & 0x3FF) + 1
                source = len(out) - distance
                floor = len(out) - len(out) % BLOCK if blocks else 0
                if source < floor:
                    raise LzssError(f"a match at {len(out)} reaches before its block")
                for _ in range(length):
                    out.append(out[source])
                    source += 1
                at += 2
            elif control & 0x20:
                count = (control & 0x1F) + 2
                if control & 0x40:
                    fixed = payload[at + 1]
                    for step in range(count):
                        out += bytes((fixed, payload[at + 2 + step]))
                    at += 2 + count
                else:
                    out += bytes((payload[at + 1],)) * count
                    at += 2
            elif control & 0x40:
                out += payload[at + 1 : at + 3] * ((control & 0x1F) + 2)
                at += 3
            else:
                count = (control & 0x1F) + 1
                out += payload[at + 1 : at + 1 + count]
                at += 1 + count
    except IndexError:
        raise LzssError("the stream runs past its payload") from None
    if at != end:
        raise LzssError("the last token runs past the counted bytes")
    return bytes(out)


def _parse_block(data: bytes) -> list[tuple[str, int, object]]:
    """The cheapest tokens for one block (a shortest path over positions)."""
    size = len(data)
    # The longest match at each position, nearest source first.
    heads: dict[int, int] = {}
    previous = [-1] * size
    matches = [(0, 0)] * size
    for at in range(size):
        limit = min(MAX_MATCH, size - at)
        if at + 1 < size:
            key = (data[at] << 8) | data[at + 1]
            best_length = best_distance = 0
            source = heads.get(key, -1)
            while source >= 0 and at - source <= MAX_DISTANCE:
                length = 0
                while length < limit and data[source + length] == data[at + length]:
                    length += 1
                if length > best_length:
                    best_length, best_distance = length, at - source
                    if length == limit:
                        break
                source = previous[source]
            if best_length >= MIN_MATCH:
                matches[at] = (best_length, best_distance)
            previous[at] = heads.get(key, -1)
            heads[key] = at
    cost = [0] * (size + 1)
    choice: list[tuple[str, int, object]] = [("literal", 1, None)] * size
    for at in range(size - 1, -1, -1):
        left = size - at
        best, pick = 2 + cost[at + 1], ("literal", 1, None)
        for length in range(2, min(MAX_LITERAL, left) + 1):
            here = 1 + length + cost[at + length]
            if here < best:
                best, pick = here, ("literal", length, None)
        run = 1
        while run < min(MAX_REPEAT, left) and data[at + run] == data[at]:
            run += 1
        for length in range(MIN_MATCH, run + 1):
            here = 2 + cost[at + length]
            if here < best:
                best, pick = here, ("run", length, data[at])
        if left >= 4:
            pairs = alternating = 1
            while pairs < min(MAX_REPEAT, left // 2) and data[at + 2 * pairs : at + 2 * pairs + 2] == data[at : at + 2]:
                pairs += 1
            while alternating < min(MAX_REPEAT, left // 2) and data[at + 2 * alternating] == data[at]:
                alternating += 1
            for count in range(2, pairs + 1):
                here = 3 + cost[at + 2 * count]
                if here < best:
                    best, pick = here, ("pair", 2 * count, data[at : at + 2])
            for count in range(2, alternating + 1):
                here = 2 + count + cost[at + 2 * count]
                if here < best:
                    best, pick = here, ("alternate", 2 * count, data[at])
        length, distance = matches[at]
        for take in range(MIN_MATCH, length + 1):
            here = 2 + cost[at + take]
            if here < best:
                best, pick = here, ("match", take, distance)
        cost[at], choice[at] = best, pick
    tokens, at = [], 0
    while at < size:
        tokens.append(choice[at])
        at += choice[at][1]
    return tokens


def _emit(data: bytes, tokens: list[tuple[str, int, object]]) -> bytes:
    out = bytearray()
    at = 0
    for kind, length, detail in tokens:
        if kind == "literal":
            out.append(length - 1)
            out += data[at : at + length]
        elif kind == "run":
            out += bytes((0x20 | (length - 2), detail))
        elif kind == "pair":
            out.append(0x40 | (length // 2 - 2))
            out += detail
        elif kind == "alternate":
            count = length // 2
            out += bytes((0x60 | (count - 2), detail))
            out += bytes(data[at + 2 * step + 1] for step in range(count))
        else:
            distance = detail - 1
            out += bytes((0x80 | ((length - 2) << 2) | (distance >> 8), distance & 0xFF))
        at += length
    return bytes(out)


def compress(data: bytes) -> bytes:
    """A payload the game decompresses back to `data`, in either loader. An
    odd payload gets a pad byte past its count: whatever follows it in the
    ROM may be DMA art, which needs an even address."""
    if not data:
        raise LzssError("there is nothing to compress")
    body = b"".join(
        _emit(data[start : start + BLOCK], _parse_block(data[start : start + BLOCK]))
        for start in range(0, len(data), BLOCK)
    )
    if len(body) > 0xFFFF:
        raise LzssError("the stream is longer than its header can count")
    payload = struct.pack(">H", len(body)) + body
    return payload + bytes(len(payload) % 2)


def fit(data: bytes, original: bytes) -> bytes:
    """`data` compressed into the original payload's slot: the original
    payload itself while its contents are unchanged, else a new stream padded
    with zeros to the slot's size, so nothing after it moves."""
    if decompress(original, blocks=True) == data:
        return original
    payload = compress(data)
    if len(payload) > len(original):
        raise LzssError(f"compressed to {len(payload)} bytes, the slot holds {len(original)}")
    return payload + bytes(len(original) - len(payload))
