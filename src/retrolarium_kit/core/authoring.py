"""Versioned authoring contracts and transactional binary writes.

This module deliberately contains no game-specific knowledge.  A profile
describes where a resource lives, a codec turns bytes into a document and a
``BuildPlan`` is the only object allowed to compose changes into a new image.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import re
from types import MappingProxyType
from typing import Any, Generic, Iterable, Mapping, TypeVar


_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


class AuthoringError(ValueError):
    """An authoring contract, document or binary operation is invalid."""


def stable_identifier(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise AuthoringError(f"{field_name} is not a stable identifier: {value!r}")
    return value


@dataclass(frozen=True, order=True)
class Address:
    """An offset whose address space and optional bank cannot be confused."""

    space: str
    offset: int
    bank: int | None = None

    def __post_init__(self) -> None:
        stable_identifier(self.space, "address space")
        if not isinstance(self.offset, int) or self.offset < 0:
            raise AuthoringError("address offset must be a non-negative integer")
        if self.bank is not None and (
            not isinstance(self.bank, int) or self.bank < 0
        ):
            raise AuthoringError("address bank must be a non-negative integer")

    def add(self, amount: int) -> Address:
        if not isinstance(amount, int) or self.offset + amount < 0:
            raise AuthoringError("address displacement leaves its address space")
        return Address(self.space, self.offset + amount, self.bank)

    def display(self) -> str:
        bank = "" if self.bank is None else f"[{self.bank}]"
        return f"{self.space}{bank}:0x{self.offset:X}"


@dataclass(frozen=True)
class AddressRange:
    start: Address
    size: int

    def __post_init__(self) -> None:
        if not isinstance(self.size, int) or self.size <= 0:
            raise AuthoringError("address range size must be positive")

    @property
    def end(self) -> Address:
        return self.start.add(self.size)

    def contains(self, address: Address) -> bool:
        return (
            address.space == self.start.space
            and address.bank == self.start.bank
            and self.start.offset <= address.offset < self.start.offset + self.size
        )


class BinaryReader:
    """Bounds-checked reads over an immutable byte sequence."""

    def __init__(self, payload: bytes | bytearray | memoryview, name: str = "binary"):
        self._payload = bytes(payload)
        self.name = name

    def __len__(self) -> int:
        return len(self._payload)

    def read(self, offset: int, size: int) -> bytes:
        if not isinstance(offset, int) or not isinstance(size, int):
            raise AuthoringError(f"{self.name} read offset and size must be integers")
        end = offset + size
        if offset < 0 or size < 0 or end < offset or end > len(self._payload):
            raise AuthoringError(
                f"{self.name} read 0x{offset:X}+0x{size:X} exceeds "
                f"0x{len(self._payload):X} bytes"
            )
        return self._payload[offset:end]

    def u8(self, offset: int) -> int:
        return self.read(offset, 1)[0]

    def u16le(self, offset: int) -> int:
        return int.from_bytes(self.read(offset, 2), "little")


class BinaryWriter:
    """A checked mutable view used only after a complete plan is validated."""

    def __init__(self, payload: bytes | bytearray, name: str = "binary"):
        self._payload = bytearray(payload)
        self.name = name

    def write(self, offset: int, replacement: bytes) -> None:
        end = offset + len(replacement)
        if offset < 0 or end < offset or end > len(self._payload):
            raise AuthoringError(
                f"{self.name} write 0x{offset:X}+0x{len(replacement):X} "
                f"exceeds 0x{len(self._payload):X} bytes"
            )
        self._payload[offset:end] = replacement

    def finish(self) -> bytes:
        return bytes(self._payload)


@dataclass(frozen=True)
class Provenance:
    source_sha256: str
    resource_id: str
    operation: str

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[0-9a-f]{64}", self.source_sha256):
            raise AuthoringError("provenance source_sha256 must be lowercase SHA-256")
        stable_identifier(self.resource_id, "provenance resource id")
        stable_identifier(self.operation, "provenance operation")


@dataclass(frozen=True)
class RevisionSpec:
    revision_id: str
    name: str
    size: int
    sha1: str
    sha256: str
    container: str
    mapper: int | None = None

    def __post_init__(self) -> None:
        stable_identifier(self.revision_id, "revision id")
        if not isinstance(self.name, str) or not self.name:
            raise AuthoringError("revision name must be non-empty")
        if not isinstance(self.size, int) or self.size <= 0:
            raise AuthoringError("revision size must be positive")
        if not re.fullmatch(r"[0-9a-f]{40}", self.sha1):
            raise AuthoringError("revision sha1 must be lowercase SHA-1")
        if not re.fullmatch(r"[0-9a-f]{64}", self.sha256):
            raise AuthoringError("revision sha256 must be lowercase SHA-256")
        stable_identifier(self.container, "revision container")
        if self.mapper is not None and (
            not isinstance(self.mapper, int) or self.mapper < 0
        ):
            raise AuthoringError("revision mapper must be a non-negative integer")


@dataclass(frozen=True)
class ResourceSpec:
    resource_id: str
    kind: str
    codec: str
    capacity_policy: str
    parameters: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        stable_identifier(self.resource_id, "resource id")
        stable_identifier(self.kind, "resource kind")
        stable_identifier(self.codec, "codec id")
        stable_identifier(self.capacity_policy, "capacity policy")
        if not isinstance(self.parameters, Mapping):
            raise AuthoringError("resource parameters must be a mapping")
        object.__setattr__(self, "parameters", MappingProxyType(dict(self.parameters)))


@dataclass(frozen=True)
class GameProfile:
    game_id: str
    profile_version: int
    name: str
    platform: str
    capabilities: tuple[str, ...]
    revisions: tuple[RevisionSpec, ...]
    resources: tuple[ResourceSpec, ...]

    def __post_init__(self) -> None:
        stable_identifier(self.game_id, "game id")
        stable_identifier(self.platform, "platform")
        if not isinstance(self.profile_version, int) or self.profile_version <= 0:
            raise AuthoringError("profile version must be positive")
        if not self.name or not self.revisions or not self.resources:
            raise AuthoringError("game profile needs a name, revisions and resources")
        if len(set(self.capabilities)) != len(self.capabilities):
            raise AuthoringError("game profile capabilities must be unique")
        for capability in self.capabilities:
            stable_identifier(capability, "capability")
        revision_ids = [value.revision_id for value in self.revisions]
        resource_ids = [value.resource_id for value in self.resources]
        if len(set(revision_ids)) != len(revision_ids):
            raise AuthoringError("game profile revision ids must be unique")
        if len(set(resource_ids)) != len(resource_ids):
            raise AuthoringError("game profile resource ids must be unique")

    def resource(self, resource_id: str) -> ResourceSpec:
        matches = [item for item in self.resources if item.resource_id == resource_id]
        if len(matches) != 1:
            raise AuthoringError(f"unknown resource {resource_id!r} in {self.game_id}")
        return matches[0]


@dataclass(frozen=True)
class ResourceDocument:
    schema: str
    schema_version: int
    game_id: str
    revision_id: str
    resource_id: str
    content: Any
    provenance: Provenance

    def __post_init__(self) -> None:
        stable_identifier(self.schema, "document schema")
        stable_identifier(self.game_id, "document game id")
        stable_identifier(self.revision_id, "document revision id")
        stable_identifier(self.resource_id, "document resource id")
        if not isinstance(self.schema_version, int) or self.schema_version <= 0:
            raise AuthoringError("document schema version must be positive")


@dataclass(frozen=True)
class Change:
    span: AddressRange
    expected: bytes
    replacement: bytes
    provenance: Provenance

    def __post_init__(self) -> None:
        if self.span.start.space != "file" or self.span.start.bank is not None:
            raise AuthoringError("build changes must use unbanked file addresses")
        if len(self.expected) != self.span.size or len(self.replacement) != self.span.size:
            raise AuthoringError("change payload sizes must match the declared span")


@dataclass(frozen=True)
class ChangeSet:
    changes: tuple[Change, ...]

    @classmethod
    def from_iterable(cls, changes: Iterable[Change]) -> ChangeSet:
        return cls(tuple(changes))


@dataclass(frozen=True)
class BuildPlan:
    source_sha256: str
    changes: ChangeSet

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[0-9a-f]{64}", self.source_sha256):
            raise AuthoringError("build plan source_sha256 must be lowercase SHA-256")

    def apply(self, source: bytes) -> bytes:
        actual_sha256 = hashlib.sha256(source).hexdigest()
        if actual_sha256 != self.source_sha256:
            raise AuthoringError(
                f"build plan source SHA-256 is {actual_sha256}, "
                f"expected {self.source_sha256}"
            )

        reader = BinaryReader(source, "build source")
        replacements: dict[int, int] = {}
        expectations: dict[int, int] = {}
        for change in self.changes.changes:
            offset = change.span.start.offset
            actual = reader.read(offset, change.span.size)
            if actual != change.expected:
                raise AuthoringError(
                    f"stale change at {change.span.start.display()}: "
                    "source bytes differ from the plan"
                )
            for relative, (expected, replacement) in enumerate(
                zip(change.expected, change.replacement)
            ):
                position = offset + relative
                previous_expected = expectations.get(position)
                previous_replacement = replacements.get(position)
                if previous_expected is not None and (
                    previous_expected != expected or previous_replacement != replacement
                ):
                    raise AuthoringError(
                        f"conflicting build changes at file:0x{position:X}"
                    )
                expectations[position] = expected
                replacements[position] = replacement

        writer = BinaryWriter(source, "build output")
        for start, payload in _coalesced_replacements(replacements):
            writer.write(start, payload)
        return writer.finish()


def _coalesced_replacements(values: Mapping[int, int]) -> list[tuple[int, bytes]]:
    if not values:
        return []
    positions = sorted(values)
    result: list[tuple[int, bytes]] = []
    start = positions[0]
    previous = start
    payload = bytearray((values[start],))
    for position in positions[1:]:
        if position != previous + 1:
            result.append((start, bytes(payload)))
            start = position
            payload.clear()
        payload.append(values[position])
        previous = position
    result.append((start, bytes(payload)))
    return result


T = TypeVar("T")


class Registry(Generic[T]):
    """A small explicit registry with deterministic duplicate handling."""

    def __init__(self, kind: str):
        self.kind = kind
        self._items: dict[str, T] = {}

    def register(self, identifier: str, value: T) -> None:
        stable_identifier(identifier, f"{self.kind} id")
        if identifier in self._items:
            raise AuthoringError(f"duplicate {self.kind} id: {identifier}")
        self._items[identifier] = value

    def get(self, identifier: str) -> T:
        try:
            return self._items[identifier]
        except KeyError as exc:
            raise AuthoringError(f"unknown {self.kind} id: {identifier}") from exc

    def identifiers(self) -> tuple[str, ...]:
        return tuple(sorted(self._items))
