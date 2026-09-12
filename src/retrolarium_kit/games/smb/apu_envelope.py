"""Typed fixed-capacity Super Mario Bros. APU effect envelopes."""

from __future__ import annotations

from typing import Mapping

from .authoring import AuthoringError


CODEC = "smb.apu-envelope.v1"


class SmbApuEnvelopeError(AuthoringError):
    """An SMB APU effect envelope violates its fixed profile."""


def _integer(
    value: object, name: str, *, minimum: int = 0, maximum: int
) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not minimum <= value <= maximum
    ):
        raise SmbApuEnvelopeError(
            f"{name} must be an integer in {minimum}..{maximum}"
        )
    return value


def _size(parameters: Mapping[str, object]) -> int:
    return _integer(parameters.get("size"), "size", minimum=1, maximum=0xFFFF)


def decode_envelope(
    payload: bytes, parameters: Mapping[str, object]
) -> dict[str, object]:
    """Decode packed duty, mode and volume fields from an SMB envelope."""

    size = _size(parameters)
    if len(payload) != size:
        raise SmbApuEnvelopeError(
            f"SMB APU envelope has {len(payload)} bytes, expected {size}"
        )
    return {
        "steps": [
            {
                "duty": value >> 6,
                "mode": (value >> 4) & 0x03,
                "volume": value & 0x0F,
            }
            for value in payload
        ]
    }


def encode_envelope(
    value: object, parameters: Mapping[str, object]
) -> bytes:
    """Encode an SMB envelope without changing its fixed step count."""

    size = _size(parameters)
    if not isinstance(value, dict) or set(value) != {"steps"}:
        raise SmbApuEnvelopeError("SMB APU envelope fields differ from contract")
    steps = value.get("steps")
    if not isinstance(steps, list) or len(steps) != size:
        raise SmbApuEnvelopeError(f"steps must contain {size} entries")
    output = bytearray()
    for index, step in enumerate(steps):
        context = f"steps[{index}]"
        if not isinstance(step, dict) or set(step) != {"duty", "mode", "volume"}:
            raise SmbApuEnvelopeError(f"{context} fields differ from contract")
        duty = _integer(step.get("duty"), f"{context}.duty", maximum=3)
        mode = _integer(step.get("mode"), f"{context}.mode", maximum=3)
        volume = _integer(step.get("volume"), f"{context}.volume", maximum=15)
        output.append((duty << 6) | (mode << 4) | volume)
    return bytes(output)
