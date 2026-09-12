"""Typed fixed-capacity FDS waveform and volume-program resources."""

from __future__ import annotations

from typing import Mapping

from .authoring import AuthoringError, stable_identifier


CODEC = "fds.synthesis.v1"
WAVEFORM_SAMPLES = 32


class FdsSynthesisError(AuthoringError):
    """Raised when FDS synthesis data differs from its fixed profile."""


def _integer(
    value: object, name: str, *, minimum: int = 0, maximum: int = 0x7FFFFFFF
) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not minimum <= value <= maximum
    ):
        raise FdsSynthesisError(
            f"{name} must be an integer in {minimum}..{maximum}"
        )
    return value


def _descriptors(
    parameters: Mapping[str, object], key: str, *, unit_size: int
) -> tuple[tuple[str, int, int], ...]:
    raw = parameters.get(key)
    if not isinstance(raw, list) or not raw:
        raise FdsSynthesisError(f"{key} must be a non-empty array")
    size = _integer(parameters.get("size"), "size", minimum=1)
    result = []
    names = set()
    occupied = set()
    for index, item in enumerate(raw):
        context = f"{key}[{index}]"
        if not isinstance(item, dict) or set(item) != {"name", "offset", "count"}:
            raise FdsSynthesisError(f"{context} fields differ from contract")
        try:
            name = stable_identifier(item.get("name"), f"{context}.name")
        except AuthoringError as exc:
            raise FdsSynthesisError(str(exc)) from exc
        if name in names:
            raise FdsSynthesisError(f"duplicate {key} name {name!r}")
        names.add(name)
        offset = _integer(item.get("offset"), f"{context}.offset")
        count = _integer(item.get("count"), f"{context}.count", minimum=1)
        end = offset + count * unit_size
        if end > size:
            raise FdsSynthesisError(f"{context} exceeds the synthesis arena")
        indexes = set(range(offset, end))
        if occupied.intersection(indexes):
            raise FdsSynthesisError(f"{key} spans overlap")
        occupied.update(indexes)
        result.append((name, offset, count))
    return tuple(result)


def _layout(
    parameters: Mapping[str, object],
) -> tuple[
    int,
    tuple[tuple[str, int, int], ...],
    tuple[tuple[str, int, int], ...],
]:
    size = _integer(parameters.get("size"), "size", minimum=1)
    waveforms = _descriptors(parameters, "waveforms", unit_size=1)
    if any(count != WAVEFORM_SAMPLES for _name, _offset, count in waveforms):
        raise FdsSynthesisError(
            f"waveforms must contain exactly {WAVEFORM_SAMPLES} samples"
        )
    programs = _descriptors(parameters, "volume_programs", unit_size=2)
    occupied: set[int] = set()
    for descriptors, unit_size in ((waveforms, 1), (programs, 2)):
        for _name, offset, count in descriptors:
            indexes = set(range(offset, offset + count * unit_size))
            if occupied.intersection(indexes):
                raise FdsSynthesisError(
                    "waveform and volume-program spans overlap"
                )
            occupied.update(indexes)
    return size, waveforms, programs


def _arena(value: object, size: int) -> bytearray:
    if not isinstance(value, list) or len(value) != size:
        raise FdsSynthesisError(f"arena must contain {size} bytes")
    return bytearray(
        _integer(item, f"arena[{index}]", maximum=0xFF)
        for index, item in enumerate(value)
    )


def _decode_step(register: int, frames: int, step: int) -> dict[str, object]:
    frames = _integer(
        frames, f"volume step {step} frames", minimum=1, maximum=0xFF
    )
    if register & 0x80:
        mode = "direct"
    elif register & 0x40:
        mode = "increase"
    else:
        mode = "decrease"
    return {
        "step": step,
        "mode": mode,
        "value": register & 0x3F,
        "frames": frames,
    }


def decode_synthesis(
    payload: bytes, parameters: Mapping[str, object]
) -> dict[str, object]:
    """Decode semantic FDS controls while retaining every arena byte."""

    size, waveforms, programs = _layout(parameters)
    if len(payload) != size:
        raise FdsSynthesisError(
            f"FDS synthesis resource has {len(payload)} bytes, expected {size}"
        )
    return {
        "arena": list(payload),
        "waveforms": [
            {
                "name": name,
                "samples": [
                    _integer(
                        payload[offset + sample],
                        f"{name} sample {sample}",
                        maximum=0x3F,
                    )
                    for sample in range(count)
                ],
            }
            for name, offset, count in waveforms
        ],
        "volume_programs": [
            {
                "name": name,
                "steps": [
                    _decode_step(
                        payload[offset + step * 2],
                        payload[offset + step * 2 + 1],
                        step,
                    )
                    for step in range(count)
                ],
            }
            for name, offset, count in programs
        ],
    }


def encode_synthesis(
    value: object, parameters: Mapping[str, object]
) -> bytes:
    """Encode fixed-capacity FDS controls without normalizing unknown bytes."""

    size, waveforms, programs = _layout(parameters)
    if not isinstance(value, dict) or set(value) != {
        "arena", "waveforms", "volume_programs"
    }:
        raise FdsSynthesisError("FDS synthesis fields differ from contract")
    output = _arena(value.get("arena"), size)
    raw_waveforms = value.get("waveforms")
    if (
        not isinstance(raw_waveforms, list)
        or len(raw_waveforms) != len(waveforms)
    ):
        raise FdsSynthesisError(
            f"waveforms must contain {len(waveforms)} entries"
        )
    for index, ((name, offset, count), raw) in enumerate(
        zip(waveforms, raw_waveforms)
    ):
        if not isinstance(raw, dict) or set(raw) != {"name", "samples"}:
            raise FdsSynthesisError(f"waveforms[{index}] fields differ from contract")
        if raw.get("name") != name:
            raise FdsSynthesisError(f"waveforms[{index}].name differs from profile")
        samples = raw.get("samples")
        if not isinstance(samples, list) or len(samples) != count:
            raise FdsSynthesisError(
                f"waveforms[{index}].samples must contain {count} values"
            )
        output[offset:offset + count] = bytes(
            _integer(
                sample,
                f"waveforms[{index}].samples[{sample_index}]",
                maximum=0x3F,
            )
            for sample_index, sample in enumerate(samples)
        )

    modes = {"direct": 0x80, "increase": 0x40, "decrease": 0x00}
    raw_programs = value.get("volume_programs")
    if not isinstance(raw_programs, list) or len(raw_programs) != len(programs):
        raise FdsSynthesisError(
            f"volume_programs must contain {len(programs)} entries"
        )
    for index, ((name, offset, count), raw) in enumerate(
        zip(programs, raw_programs)
    ):
        if not isinstance(raw, dict) or set(raw) != {"name", "steps"}:
            raise FdsSynthesisError(
                f"volume_programs[{index}] fields differ from contract"
            )
        if raw.get("name") != name:
            raise FdsSynthesisError(
                f"volume_programs[{index}].name differs from profile"
            )
        steps = raw.get("steps")
        if not isinstance(steps, list) or len(steps) != count:
            raise FdsSynthesisError(
                f"volume_programs[{index}].steps must contain {count} entries"
            )
        for step_index, step in enumerate(steps):
            context = f"volume_programs[{index}].steps[{step_index}]"
            if not isinstance(step, dict) or set(step) != {
                "step", "mode", "value", "frames"
            }:
                raise FdsSynthesisError(f"{context} fields differ from contract")
            if step.get("step") != step_index:
                raise FdsSynthesisError(f"{context}.step differs from its index")
            mode = step.get("mode")
            if mode not in modes:
                raise FdsSynthesisError(f"{context}.mode is unsupported")
            register = modes[mode] | _integer(
                step.get("value"), f"{context}.value", maximum=0x3F
            )
            frames = _integer(
                step.get("frames"), f"{context}.frames", minimum=1, maximum=0xFF
            )
            output[offset + step_index * 2:offset + step_index * 2 + 2] = bytes(
                (register, frames)
            )
    return bytes(output)
