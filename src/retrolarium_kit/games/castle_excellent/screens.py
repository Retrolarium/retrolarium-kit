"""Castle Excellent screens: the ending, title and castle-map name tables.

``CopyChrNametablePagesToVram`` copies a whole 1 KiB name table (960 tiles
and 64 attribute bytes) from CHR bank 3 into the PPU: the ending (credits)
from page $14, the title from page $18 and the castle map from page $1C,
the 3 KiB after the rooms' compressed streams. The document also keeps the
"GAME OVER" message the game writes over a room, a fixed run of font codes
in PRG.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any, Mapping

from retrolarium_kit.core.authoring import (
    Address,
    AddressRange,
    AuthoringError,
    BuildPlan,
    Change,
    ChangeSet,
    GameProfile,
    Provenance,
    RevisionSpec,
)
from retrolarium_core.ines import parse_ines


CODEC = "castle-excellent.screens.v1"
SCHEMA = "retrolarium.castle-excellent-screens"
VERSION = 1
TILES = 960
ATTRIBUTES = 64
PAGE = 256
FIELDS = ("chr_file_offset", "pages", "page_names", "chr_banks", "game_over_cpu", "game_over_length")


class CastleExcellentScreensError(AuthoringError):
    """A screens document is invalid or does not fit the game's pages."""


@dataclass(frozen=True)
class CastleExcellentScreensDocument:
    game_id: str
    revision_id: str
    source_sha256: str
    layout: dict[str, Any]
    screens: tuple[dict[str, Any], ...]
    game_over: tuple[int, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "schema_version": VERSION,
            "game": self.game_id,
            "revision": self.revision_id,
            "source_sha256": self.source_sha256,
            "layout": dict(self.layout),
            "screens": [
                {
                    "id": s["id"],
                    "page": s["page"],
                    "chr_bank": s["chr_bank"],
                    "tiles": list(s["tiles"]),
                    "attributes": list(s["attributes"]),
                }
                for s in self.screens
            ],
            "game_over": list(self.game_over),
        }


def _layout(profile: GameProfile, revision: RevisionSpec) -> dict[str, Any]:
    resources = [r for r in profile.resources_for(revision) if r.codec == CODEC]
    if len(resources) != 1:
        raise CastleExcellentScreensError("Castle Excellent profile needs one screens resource")
    parameters = resources[0].parameters
    if any(name not in parameters for name in FIELDS):
        raise CastleExcellentScreensError("screens resource needs its pages and message")
    layout = {name: parameters[name] for name in FIELDS}
    counts = {len(layout["pages"]), len(layout["page_names"]), len(layout["chr_banks"])}
    if len(counts) != 1 or not 1 <= layout["game_over_length"] <= 32:
        raise CastleExcellentScreensError("screens resource lists differ")
    return layout


def _source(source: bytes, revision: RevisionSpec, *, verify: bool = True):
    if verify and hashlib.sha256(source).hexdigest() != revision.sha256:
        raise CastleExcellentScreensError("screens source identity mismatch")
    image = parse_ines(source)
    if len(image.prg) != 0x8000:
        raise CastleExcellentScreensError("Castle Excellent screens need 32 KiB PRG")
    return image


def _spans(layout: Mapping[str, Any], base: int) -> list[tuple[str, int, int]]:
    """(field, file offset, size) of every byte range the document owns."""
    spans = [
        (f"screen-{name}", layout["chr_file_offset"] + page * PAGE, TILES + ATTRIBUTES)
        for name, page in zip(layout["page_names"], layout["pages"])
    ]
    spans.append(("game_over", base + layout["game_over_cpu"] - 0x8000, layout["game_over_length"]))
    return spans


def decode_screens(
    source: bytes,
    profile: GameProfile,
    revision: RevisionSpec,
    *,
    _verify_identity: bool = True,
) -> CastleExcellentScreensDocument:
    layout = _layout(profile, revision)
    image = _source(source, revision, verify=_verify_identity)
    base = len(image.header) + len(image.trainer)
    spans = _spans(layout, base)
    screens = []
    for (field, offset, size), name, page, bank in zip(
        spans, layout["page_names"], layout["pages"], layout["chr_banks"]
    ):
        payload = source[offset : offset + size]
        if len(payload) != size:
            raise CastleExcellentScreensError(f"{field} exceeds the source image")
        screens.append(
            {
                "id": name,
                "page": page,
                "chr_bank": bank,
                "tiles": list(payload[:TILES]),
                "attributes": list(payload[TILES:]),
            }
        )
    _field, offset, size = spans[-1]
    message = source[offset : offset + size]
    if source[offset + size] != 0:
        raise CastleExcellentScreensError("the GAME OVER message is not where the profile says")
    return CastleExcellentScreensDocument(
        profile.game_id, revision.revision_id, revision.sha256, layout, tuple(screens), tuple(message)
    )


def _bytes(values: object, count: int, name: str, *, nonzero: bool = False) -> bytes:
    if (
        not isinstance(values, list)
        or len(values) != count
        or any(type(v) is not int or not (1 if nonzero else 0) <= v <= 255 for v in values)
    ):
        raise CastleExcellentScreensError(f"{name} needs {count} byte values" + (" (no zero)" if nonzero else ""))
    return bytes(values)


def parse_screens_document(
    value: object, profile: GameProfile, revision: RevisionSpec
) -> CastleExcellentScreensDocument:
    layout = _layout(profile, revision)
    if not isinstance(value, Mapping) or set(value) != {
        "schema",
        "schema_version",
        "game",
        "revision",
        "source_sha256",
        "layout",
        "screens",
        "game_over",
    }:
        raise CastleExcellentScreensError("screens document fields differ")
    for key, expected in (
        ("schema", SCHEMA),
        ("schema_version", VERSION),
        ("game", profile.game_id),
        ("revision", revision.revision_id),
        ("source_sha256", revision.sha256),
        ("layout", layout),
    ):
        if value[key] != expected:
            raise CastleExcellentScreensError(f"screens {key} differs from profile")
    screens = value["screens"]
    if not isinstance(screens, list) or len(screens) != len(layout["pages"]):
        raise CastleExcellentScreensError("screens document lists another number of screens")
    parsed = []
    for screen, name, page, bank in zip(screens, layout["page_names"], layout["pages"], layout["chr_banks"]):
        if (
            not isinstance(screen, Mapping)
            or set(screen) != {"id", "page", "chr_bank", "tiles", "attributes"}
            or screen["id"] != name
            or screen["page"] != page
            or screen["chr_bank"] != bank
        ):
            raise CastleExcellentScreensError(f"screen {name} fields or identity differ")
        parsed.append(
            {
                "id": name,
                "page": page,
                "chr_bank": bank,
                "tiles": list(_bytes(screen["tiles"], TILES, f"{name} tiles")),
                "attributes": list(_bytes(screen["attributes"], ATTRIBUTES, f"{name} attributes")),
            }
        )
    message = _bytes(value["game_over"], layout["game_over_length"], "game_over", nonzero=True)
    return CastleExcellentScreensDocument(
        profile.game_id, revision.revision_id, revision.sha256, layout, tuple(parsed), tuple(message)
    )


def build_screens(
    source: bytes,
    document: CastleExcellentScreensDocument,
    profile: GameProfile,
    revision: RevisionSpec,
) -> tuple[bytes, dict[str, object]]:
    image = _source(source, revision)
    checked = parse_screens_document(document.as_dict(), profile, revision)
    base = len(image.header) + len(image.trainer)
    payloads = [bytes(s["tiles"]) + bytes(s["attributes"]) for s in checked.screens]
    payloads.append(bytes(checked.game_over))
    changes = []
    for (field, offset, size), payload in zip(_spans(checked.layout, base), payloads):
        if source[offset : offset + size] != payload:
            changes.append(
                Change(
                    AddressRange(Address("file", offset), size),
                    source[offset : offset + size],
                    payload,
                    Provenance(revision.sha256, "screens", field),
                )
            )
    output = BuildPlan(revision.sha256, ChangeSet(tuple(changes))).apply(source) if changes else source
    if decode_screens(output, profile, revision, _verify_identity=False).as_dict() != checked.as_dict():
        raise CastleExcellentScreensError("rebuilt screens do not decode to document")
    return output, {
        "schema": "retrolarium.castle-excellent-screens-build",
        "schema_version": 1,
        "game": profile.game_id,
        "revision": revision.revision_id,
        "source_sha256": revision.sha256,
        "output_sha256": hashlib.sha256(output).hexdigest(),
        "size": len(output),
        "changed_bytes": sum(a != b for a, b in zip(source, output)),
    }
