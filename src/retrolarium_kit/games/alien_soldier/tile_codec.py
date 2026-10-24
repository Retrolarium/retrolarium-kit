"""Alien Soldier's second codec, `TileCodec_DecodeTile` (asset commands two
and three), used by seven payloads of interface art.

Byte zero is the tile count less one; the rest is one bit stream, most
significant bit first, padded to an even payload. A tile fills 64 slots, one
per pixel, token by token:

- five bits: a colour (top four) and whether markers follow (low bit). The
  colour goes into the next slot;
- markers, if any: steps forward that seed the same colour further on. `01`,
  `10`, `11` step 7, 8, 9 pixels; `001` then a bit steps 6 or 10; `000` ends
  them. Six to ten pixels ahead is the row below, one column left to one
  column right, so markers trace an edge downward. Steps add up from the
  token's own pixel; a seed past the tile is dropped;
- a run, always: ones counted to a zero give `k` (the zero included), then
  `k` bits of payload; the run is `2**k + payload - 2` slots of the colour,
  and where it reaches a seeded slot it takes up that slot's colour.

The original encoder's marker choices are not known, so edits keep what they
can: an unchanged tile keeps its exact bits, and a changed tile either
recolours its original tokens (when every pixel a token paints still shares
one colour) or is written as plain runs, whichever is shorter.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache


TILE_PIXELS = 64
TILE_BYTES = 32
NEAR = {1: 7, 2: 8, 3: 9}


class TileCodecError(ValueError):
    """A payload the decoder would not read as tiles."""


class _Bits:
    def __init__(self, data: bytes, at: int):
        self.data, self.position = data, at * 8

    def read(self, count: int) -> int:
        value = 0
        for _ in range(count):
            byte = self.position >> 3
            if byte >= len(self.data):
                raise TileCodecError("the bit stream runs out")
            value = (value << 1) | ((self.data[byte] >> (7 - (self.position & 7))) & 1)
            self.position += 1
        return value


class _Writer:
    def __init__(self) -> None:
        self.bits: list[int] = []

    def write(self, value: int, count: int) -> None:
        self.bits += [(value >> shift) & 1 for shift in range(count - 1, -1, -1)]

    def payload(self, count: int) -> bytes:
        bits = self.bits + [0] * (-len(self.bits) % 8)
        body = bytes(int("".join(map(str, bits[at : at + 8])), 2) for at in range(0, len(bits), 8))
        payload = bytes((count - 1,)) + body
        return payload + bytes(len(payload) % 2)


@dataclass
class _Token:
    colour_bit: int  # stream position of the four colour bits
    start: int  # stream span of the whole token
    end: int


def _decode_tile(bits: _Bits) -> tuple[list[int], list[int], list[_Token]]:
    """The tile's 64 slots, the token that painted each, and the tokens."""
    slot, owner, seeded = [0] * TILE_PIXELS, [0] * TILE_PIXELS, [False] * TILE_PIXELS
    tokens: list[_Token] = []
    at = 0
    while at < TILE_PIXELS:
        start = bits.position
        token = bits.read(5)
        index, colour = len(tokens), token >> 1
        slot[at], owner[at], seeded[at] = colour, index, False
        at += 1
        if token & 1:
            ahead = 0
            while True:
                near = bits.read(2)
                if near:
                    ahead += NEAR[near]
                elif not bits.read(1):
                    break
                else:
                    ahead += 10 if bits.read(1) else 6
                target = at - 1 + ahead
                if target < TILE_PIXELS:
                    slot[target], owner[target], seeded[target] = colour, index, True
        k = 1
        while bits.read(1):
            k += 1
        run = (1 << k) + bits.read(k) - 2
        painter = index
        for _ in range(run):
            if at >= TILE_PIXELS:
                break
            if seeded[at]:
                colour, painter, seeded[at] = slot[at], owner[at], False
            slot[at], owner[at] = colour, painter
            at += 1
        tokens.append(_Token(start, start, bits.position))
    return slot, owner, tokens


def _pack(slot: list[int]) -> bytes:
    return bytes((slot[at] << 4) | slot[at + 1] for at in range(0, TILE_PIXELS, 2))


def _unpack(tile: bytes) -> list[int]:
    return [nibble for byte in tile for nibble in (byte >> 4, byte & 15)]


def decompress(payload: bytes) -> bytes:
    """The tiles a payload decodes to, kept by the payload's bytes."""
    return _decompress(bytes(payload))


@lru_cache(maxsize=1024)
def _decompress(payload: bytes) -> bytes:
    if len(payload) < 2:
        raise TileCodecError("a payload needs a count and a stream")
    bits = _Bits(payload, 1)
    return b"".join(_pack(_decode_tile(bits)[0]) for _ in range(payload[0] + 1))


def _tile_spans(payload: bytes) -> list[tuple[int, int, list[int], list[int], list[_Token]]]:
    bits = _Bits(payload, 1)
    spans = []
    for _ in range(payload[0] + 1):
        start = bits.position
        slot, owner, tokens = _decode_tile(bits)
        spans.append((start, bits.position, slot, owner, tokens))
    return spans


def _runs(writer: _Writer, slot: list[int]) -> None:
    at = 0
    while at < TILE_PIXELS:
        end = at + 1
        while end < TILE_PIXELS and slot[end] == slot[at]:
            end += 1
        writer.write(slot[at] << 1, 5)
        run = end - at - 1
        k = (run + 2).bit_length() - 1
        writer.write((1 << k) - 2, k)  # k - 1 ones, then a zero
        writer.write(run + 2 - (1 << k), k)
        at = end


def _bits_of(payload: bytes, start: int, end: int) -> list[int]:
    return [(payload[position >> 3] >> (7 - (position & 7))) & 1 for position in range(start, end)]


def fit(data: bytes, original: bytes) -> bytes:
    """`data` (whole tiles, as many as the original holds) encoded into the
    original payload's slot, padded with zeros; the original itself while
    nothing changed."""
    if len(data) % TILE_BYTES or len(data) // TILE_BYTES != original[0] + 1:
        raise TileCodecError(f"the payload holds {original[0] + 1} tiles")
    if decompress(original) == data:
        return original
    writer = _Writer()
    for index, (start, end, slot, owner, tokens) in enumerate(_tile_spans(original)):
        wanted = _unpack(data[index * TILE_BYTES : (index + 1) * TILE_BYTES])
        if wanted == slot:
            writer.bits += _bits_of(original, start, end)
            continue
        candidates = []
        colours: dict[int, set[int]] = {}
        for pixel, token in enumerate(owner):
            colours.setdefault(token, set()).add(wanted[pixel])
        if all(len(found) == 1 for found in colours.values()) and len(colours) == len(tokens):
            bits = _bits_of(original, start, end)
            for token_index, token in enumerate(tokens):
                colour = colours[token_index].pop()
                at = token.colour_bit - start
                bits[at : at + 4] = [(colour >> shift) & 1 for shift in (3, 2, 1, 0)]
            candidates.append(bits)
        plain = _Writer()
        _runs(plain, wanted)
        candidates.append(plain.bits)
        writer.bits += min(candidates, key=len)
    payload = writer.payload(original[0] + 1)
    if decompress(payload) != data:
        raise TileCodecError("the encoded tiles do not decode back")
    if len(payload) > len(original):
        raise TileCodecError(f"encoded to {len(payload)} bytes, the slot holds {len(original)}")
    return payload + bytes(len(original) - len(payload))
