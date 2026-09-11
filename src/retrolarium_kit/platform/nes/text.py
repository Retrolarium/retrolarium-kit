"""Config-driven fixed-width text fields stored in disjoint NES ranges."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Mapping

from .authoring import AuthoringError


CODEC = "nes.fixed-text-fields.v1"


class NesTextError(AuthoringError):
    """A fixed text codec configuration or authored value is invalid."""


@dataclass(frozen=True)
class TextEncoding:
    characters: str
    codes: tuple[int, ...]
    case_fold: str
    padding: int

    @property
    def encode_map(self) -> dict[str, int]:
        return dict(zip(self.characters, self.codes))

    @property
    def decode_map(self) -> dict[int, str]:
        return dict(zip(self.codes, self.characters))

    def as_dict(self) -> dict[str, object]:
        return {
            "characters": self.characters,
            "codes": list(self.codes),
            "case_fold": self.case_fold,
            "padding": self.padding,
        }


@dataclass(frozen=True)
class TextField:
    key: str
    label: str
    file_offset: int
    width: int


def _integer(value: object, name: str, *, minimum: int = 0, maximum: int = 0xFF) -> int:
    if (not isinstance(value, int) or isinstance(value, bool)
            or value < minimum or value > maximum):
        raise NesTextError(f"{name} must be an integer in {minimum}..{maximum}")
    return value


def parse_encoding(parameters: Mapping[str, object]) -> TextEncoding:
    value = parameters.get("encoding")
    if not isinstance(value, Mapping) or set(value) != {
        "characters", "codes", "case_fold", "padding"
    }:
        raise NesTextError("text encoding must define characters, codes, case_fold and padding")
    characters = value.get("characters")
    codes = value.get("codes")
    case_fold = value.get("case_fold")
    if not isinstance(characters, str) or not characters:
        raise NesTextError("text encoding characters must be a non-empty string")
    if len(set(characters)) != len(characters):
        raise NesTextError("text encoding characters must be unique")
    if not isinstance(codes, list) or len(codes) != len(characters):
        raise NesTextError("text encoding codes must match the character count")
    normalized_codes = tuple(
        _integer(item, f"text encoding codes[{index}]")
        for index, item in enumerate(codes)
    )
    if len(set(normalized_codes)) != len(normalized_codes):
        raise NesTextError("text encoding codes must be unique")
    if case_fold not in ("none", "upper", "lower"):
        raise NesTextError("text encoding case_fold must be none, upper or lower")
    padding = _integer(value.get("padding"), "text encoding padding")
    if padding not in normalized_codes:
        raise NesTextError("text encoding padding must have a character mapping")
    return TextEncoding(characters, normalized_codes, str(case_fold), padding)


def parse_fields(parameters: Mapping[str, object]) -> tuple[TextField, ...]:
    value = parameters.get("fields")
    if not isinstance(value, list) or not value:
        raise NesTextError("text fields must be a non-empty array")
    result = []
    keys: set[str] = set()
    occupied: set[int] = set()
    for index, item in enumerate(value):
        if not isinstance(item, Mapping) or set(item) != {
            "key", "label", "file_offset", "width"
        }:
            raise NesTextError(f"text fields[{index}] has the wrong shape")
        key = item.get("key")
        label = item.get("label")
        if not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9-]*", key):
            raise NesTextError(f"text fields[{index}].key is invalid")
        if key in keys:
            raise NesTextError(f"duplicate text field key: {key}")
        if not isinstance(label, str) or not label:
            raise NesTextError(f"text fields[{index}].label must be non-empty")
        offset = _integer(
            item.get("file_offset"), f"text fields[{index}].file_offset",
            maximum=0x7FFFFFFF,
        )
        width = _integer(
            item.get("width"), f"text fields[{index}].width",
            minimum=1, maximum=0x10000,
        )
        positions = set(range(offset, offset + width))
        if occupied.intersection(positions):
            raise NesTextError(f"text field {key} overlaps another field")
        occupied.update(positions)
        keys.add(key)
        result.append(TextField(key, label, offset, width))
    return tuple(result)


def _fold(text: str, encoding: TextEncoding) -> str:
    if encoding.case_fold == "upper":
        return text.upper()
    if encoding.case_fold == "lower":
        return text.lower()
    return text


def encode_text(text: object, width: int, encoding: TextEncoding, name: str) -> bytes:
    if not isinstance(text, str):
        raise NesTextError(f"{name}.text must be a string")
    normalized = _fold(text, encoding)
    if len(normalized) > width:
        raise NesTextError(f"{name}.text exceeds its fixed width of {width}")
    unsupported = sorted(set(normalized).difference(encoding.encode_map))
    if unsupported:
        rendered = " ".join(repr(character) for character in unsupported)
        raise NesTextError(f"{name}.text contains unsupported characters: {rendered}")
    result = [encoding.encode_map[character] for character in normalized]
    result.extend([encoding.padding] * (width - len(result)))
    return bytes(result)


def decode_text(payload: bytes, encoding: TextEncoding, name: str) -> str:
    try:
        rendered = "".join(encoding.decode_map[value] for value in payload)
    except KeyError as exc:
        raise NesTextError(
            f"{name} contains unmapped glyph ${exc.args[0]:02X}"
        ) from exc
    padding_character = encoding.decode_map[encoding.padding]
    return rendered.rstrip(padding_character)


def decode_text_fields(
    source: bytes, parameters: Mapping[str, object]
) -> dict[str, object]:
    encoding = parse_encoding(parameters)
    messages = []
    for field in parse_fields(parameters):
        payload = source[field.file_offset : field.file_offset + field.width]
        if len(payload) != field.width:
            raise NesTextError(f"text field {field.key} exceeds the source image")
        messages.append({
            "key": field.key,
            "label": field.label,
            "width": field.width,
            "text": decode_text(payload, encoding, field.key),
        })
    return {"encoding": encoding.as_dict(), "messages": messages}


def encode_text_fields(
    document: Mapping[str, object], parameters: Mapping[str, object]
) -> tuple[tuple[int, bytes], ...]:
    encoding = parse_encoding(parameters)
    if document.get("encoding") != encoding.as_dict():
        raise NesTextError("text encoding differs from the profile")
    fields = parse_fields(parameters)
    messages = document.get("messages")
    if not isinstance(messages, list):
        raise NesTextError("text messages must be an array")
    if len(messages) != len(fields):
        raise NesTextError("text message count differs from the profile")
    result = []
    for field, message in zip(fields, messages):
        if not isinstance(message, Mapping) or set(message) != {
            "key", "label", "width", "text"
        }:
            raise NesTextError(f"text message {field.key} has the wrong shape")
        if (message.get("key"), message.get("label"), message.get("width")) != (
            field.key, field.label, field.width
        ):
            raise NesTextError(f"text message {field.key} metadata differs from the profile")
        result.append((
            field.file_offset,
            encode_text(message.get("text"), field.width, encoding, field.key),
        ))
    return tuple(result)
