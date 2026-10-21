"""Profile-driven game authoring operations used by the CLI and Qt bridge."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Sequence

from retrolarium_kit.core.authoring import AuthoringError, GameProfile, Registry, RevisionSpec
from retrolarium_kit.games.castle_excellent.rooms import (
    CastleExcellentLevelDocument,
    LEVEL_DOCUMENT_SCHEMA,
    build_levels,
    decode_levels,
    parse_level_document,
)
from retrolarium_kit.core.game_profiles import (
    load_game_profile,
    game_profiles,
    recognize_revision,
)
from retrolarium_core.ines import load_ines
from retrolarium_kit.platform.nes.resources import (
    ADAPTER_CODEC as NES_RESOURCE_ADAPTER_CODEC,
    DOCUMENT_SCHEMA as NES_RESOURCE_DOCUMENT_SCHEMA,
    NesResourceBundleDocument,
    build_resources,
    decode_resources,
    inspection_details as nes_resource_details,
    parse_resource_document,
)
from retrolarium_kit.core.project import launch_image_plan, launch_image_smoke, vs_machine
from retrolarium_kit.games.solomon.rooms import (
    BLOCK_CODEC as SOLOMON_BLOCK_CODEC,
    DOCUMENT_SCHEMA as SOLOMON_ROOM_DOCUMENT_SCHEMA,
    SolomonRoomDocument,
    build_rooms as build_solomon_rooms,
    decode_rooms as decode_solomon_rooms,
    parse_room_document as parse_solomon_room_document,
)
from retrolarium_kit.games.solomon.room_art import (
    CODEC as SOLOMON_ROOM_ART_CODEC,
    SCHEMA as SOLOMON_ROOM_ART_SCHEMA,
    SolomonRoomArtDocument,
    build_room_art,
    decode_room_art,
    parse_room_art_document,
)
from retrolarium_kit.games.solomon.sprite_frames import (
    CODEC as SOLOMON_SPRITE_FRAME_CODEC,
    SCHEMA as SOLOMON_SPRITE_FRAME_SCHEMA,
    SolomonSpriteFrameDocument,
    build_sprite_frames,
    decode_sprite_frames,
    parse_sprite_frame_document,
)
from retrolarium_kit.games.solomon.animations import (
    CODEC as SOLOMON_ANIMATION_CODEC,
    SCHEMA as SOLOMON_ANIMATION_SCHEMA,
    SolomonAnimationDocument,
    build_animations,
    decode_animations,
    parse_animation_document,
)
from retrolarium_kit.games.solomon.audio import (
    CODEC as SOLOMON_AUDIO_CODEC,
    SCHEMA as SOLOMON_AUDIO_SCHEMA,
    SolomonAudioDocument,
    build_audio as build_solomon_audio,
    decode_audio as decode_solomon_audio,
    parse_audio_document as parse_solomon_audio_document,
)
from retrolarium_kit.games.solomon.presentation import (
    CODEC as SOLOMON_PRESENTATION_CODEC,
    SCHEMA as SOLOMON_PRESENTATION_SCHEMA,
    SolomonPresentationDocument,
    build_presentation as build_solomon_presentation,
    decode_presentation as decode_solomon_presentation,
    parse_presentation_document as parse_solomon_presentation_document,
)
from retrolarium_kit.games.castle_excellent.screens import (
    CODEC as CASTLE_EXCELLENT_SCREENS_CODEC,
    SCHEMA as CASTLE_EXCELLENT_SCREENS_SCHEMA,
    CastleExcellentScreensDocument,
    build_screens as build_castle_excellent_screens,
    decode_screens as decode_castle_excellent_screens,
    parse_screens_document as parse_castle_excellent_screens_document,
)
from retrolarium_kit.games.castle_excellent.demo import (
    CODEC as CASTLE_EXCELLENT_DEMO_CODEC,
    SCHEMA as CASTLE_EXCELLENT_DEMO_SCHEMA,
    CastleExcellentDemoDocument,
    build_demo as build_castle_excellent_demo,
    decode_demo as decode_castle_excellent_demo,
    parse_demo_document as parse_castle_excellent_demo_document,
)
from retrolarium_kit.games.doraemon.demo import (
    CODEC as DORAEMON_DEMO_CODEC,
    SCHEMA as DORAEMON_DEMO_SCHEMA,
    DoraemonDemoDocument,
    build_demo as build_doraemon_demo,
    decode_demo as decode_doraemon_demo,
    parse_demo_document as parse_doraemon_demo_document,
)
from retrolarium_kit.games.doraemon.music import (
    CODEC as DORAEMON_MUSIC_CODEC,
    SCHEMA as DORAEMON_MUSIC_SCHEMA,
    DoraemonMusicDocument,
    build_music as build_doraemon_music,
    decode_music as decode_doraemon_music,
    parse_music_document as parse_doraemon_music_document,
)
from retrolarium_kit.games.doraemon.text import (
    CODEC as DORAEMON_TEXT_CODEC,
    SCHEMA as DORAEMON_TEXT_SCHEMA,
    DoraemonTextDocument,
    build_text as build_doraemon_text,
    decode_text as decode_doraemon_text,
    parse_text_document as parse_doraemon_text_document,
)
from retrolarium_kit.games.pacman.demo import (
    CODEC as PACMAN_DEMO_CODEC,
    SCHEMA as PACMAN_DEMO_SCHEMA,
    PacmanDemoDocument,
    build_demo as build_pacman_demo,
    decode_demo as decode_pacman_demo,
    parse_demo_document as parse_pacman_demo_document,
)
from retrolarium_kit.games.smb.demo import (
    CODEC as SMB_DEMO_CODEC,
    SCHEMA as SMB_DEMO_SCHEMA,
    SmbDemoDocument,
    build_demo as build_smb_demo,
    decode_demo as decode_smb_demo,
    parse_demo_document as parse_smb_demo_document,
)
from retrolarium_kit.games.solomon.text import (
    CODEC as SOLOMON_TEXT_CODEC,
    SCHEMA as SOLOMON_TEXT_SCHEMA,
    SolomonTextDocument,
    build_text as build_solomon_text,
    decode_text as decode_solomon_text,
    parse_text_document as parse_solomon_text_document,
)


def _profiles(custom_profile: Path | None) -> tuple[GameProfile, ...]:
    if custom_profile is not None:
        return (load_game_profile(custom_profile),)
    registry = game_profiles()
    return tuple(registry.get(identifier) for identifier in registry.identifiers())


class _CastleExcellentAuthoringAdapter:
    def decode(
        self, payload: bytes, profile: GameProfile, revision: RevisionSpec
    ) -> CastleExcellentLevelDocument:
        return decode_levels(payload, profile, revision)

    def parse(
        self, document: object, profile: GameProfile, revision: RevisionSpec
    ) -> CastleExcellentLevelDocument:
        return parse_level_document(document, profile, revision)

    def build(
        self,
        payload: bytes,
        document: CastleExcellentLevelDocument,
        profile: GameProfile,
        revision: RevisionSpec,
    ) -> tuple[bytes, dict[str, object]]:
        return build_levels(payload, document, profile, revision)

    @staticmethod
    def details(document: CastleExcellentLevelDocument) -> dict[str, int]:
        return {
            "rooms": len(document.rooms),
            "objects": sum(len(room) for room in document.objects),
            "width": document.width,
            "height": document.height,
        }

    resource_id = "levels"
    document_schema = LEVEL_DOCUMENT_SCHEMA


class _NesResourceAuthoringAdapter:
    def decode(
        self, payload: bytes, profile: GameProfile, revision: RevisionSpec
    ) -> NesResourceBundleDocument:
        return decode_resources(payload, profile, revision)

    def parse(
        self, document: object, profile: GameProfile, revision: RevisionSpec
    ) -> NesResourceBundleDocument:
        return parse_resource_document(document, profile, revision)

    def build(
        self,
        payload: bytes,
        document: NesResourceBundleDocument,
        profile: GameProfile,
        revision: RevisionSpec,
    ) -> tuple[bytes, dict[str, object]]:
        return build_resources(payload, document, profile, revision)

    @staticmethod
    def details(document: NesResourceBundleDocument) -> dict[str, int]:
        return nes_resource_details(document)

    resource_id = "resource-bundle"
    document_schema = NES_RESOURCE_DOCUMENT_SCHEMA


class _SolomonRoomAuthoringAdapter:
    def decode(
        self, payload: bytes, profile: GameProfile, revision: RevisionSpec
    ) -> SolomonRoomDocument:
        return decode_solomon_rooms(payload, profile, revision)

    def parse(
        self, document: object, profile: GameProfile, revision: RevisionSpec
    ) -> SolomonRoomDocument:
        return parse_solomon_room_document(document, profile, revision)

    def build(
        self,
        payload: bytes,
        document: SolomonRoomDocument,
        profile: GameProfile,
        revision: RevisionSpec,
    ) -> tuple[bytes, dict[str, object]]:
        return build_solomon_rooms(payload, document, profile, revision)

    @staticmethod
    def details(document: SolomonRoomDocument) -> dict[str, int]:
        return {
            "rooms": len(document.rooms),
            "width": document.width,
            "height": document.height,
        }

    resource_id = "levels"
    document_schema = SOLOMON_ROOM_DOCUMENT_SCHEMA


class _SolomonRoomArtAuthoringAdapter:
    def decode(
        self, payload: bytes, profile: GameProfile, revision: RevisionSpec
    ) -> SolomonRoomArtDocument:
        return decode_room_art(payload, profile, revision)

    def parse(
        self, document: object, profile: GameProfile, revision: RevisionSpec
    ) -> SolomonRoomArtDocument:
        return parse_room_art_document(document, profile, revision)

    def build(
        self, payload: bytes, document: SolomonRoomArtDocument,
        profile: GameProfile, revision: RevisionSpec,
    ) -> tuple[bytes, dict[str, object]]:
        return build_room_art(payload, document, profile, revision)

    @staticmethod
    def details(document: SolomonRoomArtDocument) -> dict[str, int]:
        return {
            "room_group_colors": len(document.group_colors),
            "room_map_patterns": len(document.patterns),
        }

    resource_id = "room-art"
    document_schema = SOLOMON_ROOM_ART_SCHEMA


class _SolomonSpriteFrameAuthoringAdapter:
    def decode(
        self, payload: bytes, profile: GameProfile, revision: RevisionSpec
    ) -> SolomonSpriteFrameDocument:
        return decode_sprite_frames(payload, profile, revision)

    def parse(
        self, document: object, profile: GameProfile, revision: RevisionSpec
    ) -> SolomonSpriteFrameDocument:
        return parse_sprite_frame_document(document, profile, revision)

    def build(
        self, payload: bytes, document: SolomonSpriteFrameDocument,
        profile: GameProfile, revision: RevisionSpec,
    ) -> tuple[bytes, dict[str, object]]:
        return build_sprite_frames(payload, document, profile, revision)

    @staticmethod
    def details(document: SolomonSpriteFrameDocument) -> dict[str, int]:
        return {
            "sprite_frames": len(document.frames),
            "enemy_types": sum(len(frame.enemy_types) for frame in document.frames),
        }

    resource_id = "sprite-frames"
    document_schema = SOLOMON_SPRITE_FRAME_SCHEMA


class _SolomonAnimationAuthoringAdapter:
    def decode(
        self, payload: bytes, profile: GameProfile, revision: RevisionSpec
    ) -> SolomonAnimationDocument:
        return decode_animations(payload, profile, revision)

    def parse(
        self, document: object, profile: GameProfile, revision: RevisionSpec
    ) -> SolomonAnimationDocument:
        return parse_animation_document(document, profile, revision)

    def build(
        self, payload: bytes, document: SolomonAnimationDocument,
        profile: GameProfile, revision: RevisionSpec,
    ) -> tuple[bytes, dict[str, object]]:
        return build_animations(payload, document, profile, revision)

    @staticmethod
    def details(document: SolomonAnimationDocument) -> dict[str, int]:
        return {
            "object_types": len(document.group_pointers),
            "descriptors": len(document.descriptors),
            "variant_selectors": len(document.selectors),
            "frames": len(document.frames),
        }

    resource_id = "animations"
    document_schema = SOLOMON_ANIMATION_SCHEMA


class _SolomonAudioAuthoringAdapter:
    def decode(
        self, payload: bytes, profile: GameProfile, revision: RevisionSpec
    ) -> SolomonAudioDocument:
        return decode_solomon_audio(payload, profile, revision)

    def parse(
        self, document: object, profile: GameProfile, revision: RevisionSpec
    ) -> SolomonAudioDocument:
        return parse_solomon_audio_document(document, profile, revision)

    def build(
        self, payload: bytes, document: SolomonAudioDocument,
        profile: GameProfile, revision: RevisionSpec,
    ) -> tuple[bytes, dict[str, object]]:
        return build_solomon_audio(payload, document, profile, revision)

    @staticmethod
    def details(document: SolomonAudioDocument) -> dict[str, int]:
        return {
            "effects": len(document.effects),
            "envelopes": len(document.envelopes),
            "streams": sum("entry" in item for item in document.commands),
            "commands": len(document.commands),
        }

    resource_id = "audio"
    document_schema = SOLOMON_AUDIO_SCHEMA


class _SolomonPresentationAuthoringAdapter:
    def decode(
        self, payload: bytes, profile: GameProfile, revision: RevisionSpec
    ) -> SolomonPresentationDocument:
        return decode_solomon_presentation(payload, profile, revision)

    def parse(
        self, document: object, profile: GameProfile, revision: RevisionSpec
    ) -> SolomonPresentationDocument:
        return parse_solomon_presentation_document(document, profile, revision)

    def build(
        self, payload: bytes, document: SolomonPresentationDocument,
        profile: GameProfile, revision: RevisionSpec,
    ) -> tuple[bytes, dict[str, object]]:
        return build_solomon_presentation(payload, document, profile, revision)

    @staticmethod
    def details(document: SolomonPresentationDocument) -> dict[str, int]:
        return {
            "title_streams": len(document.streams),
            "literal_tiles": sum(
                len(token["tiles"])
                for stream in document.streams
                for token in stream["tokens"]
                if token["kind"] == "literal"
            ),
            "demo_steps": len(document.demo_steps),
        }

    resource_id = "presentation"
    document_schema = SOLOMON_PRESENTATION_SCHEMA


class _SmbDemoAuthoringAdapter:
    def decode(
        self, payload: bytes, profile: GameProfile, revision: RevisionSpec
    ) -> SmbDemoDocument:
        return decode_smb_demo(payload, profile, revision)

    def parse(
        self, document: object, profile: GameProfile, revision: RevisionSpec
    ) -> SmbDemoDocument:
        return parse_smb_demo_document(document, profile, revision)

    def build(
        self, payload: bytes, document: SmbDemoDocument,
        profile: GameProfile, revision: RevisionSpec,
    ) -> tuple[bytes, dict[str, object]]:
        return build_smb_demo(payload, document, profile, revision)

    @staticmethod
    def details(document: SmbDemoDocument) -> dict[str, int]:
        return {"steps": len(document.steps)}

    resource_id = "demo"
    document_schema = SMB_DEMO_SCHEMA


class _CastleExcellentDemoAuthoringAdapter:
    def decode(
        self, payload: bytes, profile: GameProfile, revision: RevisionSpec
    ) -> CastleExcellentDemoDocument:
        return decode_castle_excellent_demo(payload, profile, revision)

    def parse(
        self, document: object, profile: GameProfile, revision: RevisionSpec
    ) -> CastleExcellentDemoDocument:
        return parse_castle_excellent_demo_document(document, profile, revision)

    def build(
        self, payload: bytes, document: CastleExcellentDemoDocument,
        profile: GameProfile, revision: RevisionSpec,
    ) -> tuple[bytes, dict[str, object]]:
        return build_castle_excellent_demo(payload, document, profile, revision)

    @staticmethod
    def details(document: CastleExcellentDemoDocument) -> dict[str, int]:
        return {"steps": len(document.steps)}

    resource_id = "title-demo"
    document_schema = CASTLE_EXCELLENT_DEMO_SCHEMA


class _DoraemonDemoAuthoringAdapter:
    def decode(
        self, payload: bytes, profile: GameProfile, revision: RevisionSpec
    ) -> DoraemonDemoDocument:
        return decode_doraemon_demo(payload, profile, revision)

    def parse(
        self, document: object, profile: GameProfile, revision: RevisionSpec
    ) -> DoraemonDemoDocument:
        return parse_doraemon_demo_document(document, profile, revision)

    def build(
        self, payload: bytes, document: DoraemonDemoDocument,
        profile: GameProfile, revision: RevisionSpec,
    ) -> tuple[bytes, dict[str, object]]:
        return build_doraemon_demo(payload, document, profile, revision)

    @staticmethod
    def details(document: DoraemonDemoDocument) -> dict[str, int]:
        return {"steps": len(document.steps)}

    resource_id = "demo"
    document_schema = DORAEMON_DEMO_SCHEMA


class _DoraemonMusicAuthoringAdapter:
    def decode(
        self, payload: bytes, profile: GameProfile, revision: RevisionSpec
    ) -> DoraemonMusicDocument:
        return decode_doraemon_music(payload, profile, revision)

    def parse(
        self, document: object, profile: GameProfile, revision: RevisionSpec
    ) -> DoraemonMusicDocument:
        return parse_doraemon_music_document(document, profile, revision)

    def build(
        self, payload: bytes, document: DoraemonMusicDocument,
        profile: GameProfile, revision: RevisionSpec,
    ) -> tuple[bytes, dict[str, object]]:
        return build_doraemon_music(payload, document, profile, revision)

    @staticmethod
    def details(document: DoraemonMusicDocument) -> dict[str, int]:
        return {"drivers": len(document.drivers)}

    resource_id = "music"
    document_schema = DORAEMON_MUSIC_SCHEMA


class _DoraemonTextAuthoringAdapter:
    def decode(
        self, payload: bytes, profile: GameProfile, revision: RevisionSpec
    ) -> DoraemonTextDocument:
        return decode_doraemon_text(payload, profile, revision)

    def parse(
        self, document: object, profile: GameProfile, revision: RevisionSpec
    ) -> DoraemonTextDocument:
        return parse_doraemon_text_document(document, profile, revision)

    def build(
        self, payload: bytes, document: DoraemonTextDocument,
        profile: GameProfile, revision: RevisionSpec,
    ) -> tuple[bytes, dict[str, object]]:
        return build_doraemon_text(payload, document, profile, revision)

    @staticmethod
    def details(document: DoraemonTextDocument) -> dict[str, int]:
        return {"credit_rows": len(document.content["ending_credit_rows"])}

    resource_id = "text"
    document_schema = DORAEMON_TEXT_SCHEMA


class _PacmanDemoAuthoringAdapter:
    def decode(
        self, payload: bytes, profile: GameProfile, revision: RevisionSpec
    ) -> PacmanDemoDocument:
        return decode_pacman_demo(payload, profile, revision)

    def parse(
        self, document: object, profile: GameProfile, revision: RevisionSpec
    ) -> PacmanDemoDocument:
        return parse_pacman_demo_document(document, profile, revision)

    def build(
        self, payload: bytes, document: PacmanDemoDocument,
        profile: GameProfile, revision: RevisionSpec,
    ) -> tuple[bytes, dict[str, object]]:
        return build_pacman_demo(payload, document, profile, revision)

    @staticmethod
    def details(document: PacmanDemoDocument) -> dict[str, int]:
        return {"steps": len(document.steps)}

    resource_id = "attract-demo"
    document_schema = PACMAN_DEMO_SCHEMA


class _CastleExcellentScreensAuthoringAdapter:
    def decode(
        self, payload: bytes, profile: GameProfile, revision: RevisionSpec
    ) -> CastleExcellentScreensDocument:
        return decode_castle_excellent_screens(payload, profile, revision)

    def parse(
        self, document: object, profile: GameProfile, revision: RevisionSpec
    ) -> CastleExcellentScreensDocument:
        return parse_castle_excellent_screens_document(document, profile, revision)

    def build(
        self, payload: bytes, document: CastleExcellentScreensDocument,
        profile: GameProfile, revision: RevisionSpec,
    ) -> tuple[bytes, dict[str, object]]:
        return build_castle_excellent_screens(payload, document, profile, revision)

    @staticmethod
    def details(document: CastleExcellentScreensDocument) -> dict[str, int]:
        return {"screens": len(document.screens)}

    resource_id = "screens"
    document_schema = CASTLE_EXCELLENT_SCREENS_SCHEMA


class _SolomonTextAuthoringAdapter:
    def decode(
        self, payload: bytes, profile: GameProfile, revision: RevisionSpec
    ) -> SolomonTextDocument:
        return decode_solomon_text(payload, profile, revision)

    def parse(
        self, document: object, profile: GameProfile, revision: RevisionSpec
    ) -> SolomonTextDocument:
        return parse_solomon_text_document(document, profile, revision)

    def build(
        self, payload: bytes, document: SolomonTextDocument,
        profile: GameProfile, revision: RevisionSpec,
    ) -> tuple[bytes, dict[str, object]]:
        return build_solomon_text(payload, document, profile, revision)

    @staticmethod
    def details(document: SolomonTextDocument) -> dict[str, int]:
        return {
            "static_programs": len(document.static_programs),
            "ending_messages": len(document.ending_initial) + len(document.ending_messages),
        }

    resource_id = "text"
    document_schema = SOLOMON_TEXT_SCHEMA


def authoring_adapters() -> Registry[object]:
    registry: Registry[object] = Registry("authoring adapter")
    registry.register("castle-excellent.prefix-rooms.v1", _CastleExcellentAuthoringAdapter())
    registry.register(NES_RESOURCE_ADAPTER_CODEC, _NesResourceAuthoringAdapter())
    registry.register(SOLOMON_BLOCK_CODEC, _SolomonRoomAuthoringAdapter())
    registry.register(SOLOMON_ROOM_ART_CODEC, _SolomonRoomArtAuthoringAdapter())
    registry.register(SOLOMON_SPRITE_FRAME_CODEC, _SolomonSpriteFrameAuthoringAdapter())
    registry.register(SOLOMON_ANIMATION_CODEC, _SolomonAnimationAuthoringAdapter())
    registry.register(SOLOMON_AUDIO_CODEC, _SolomonAudioAuthoringAdapter())
    registry.register(
        SOLOMON_PRESENTATION_CODEC, _SolomonPresentationAuthoringAdapter()
    )
    registry.register(SOLOMON_TEXT_CODEC, _SolomonTextAuthoringAdapter())
    registry.register(SMB_DEMO_CODEC, _SmbDemoAuthoringAdapter())
    registry.register(CASTLE_EXCELLENT_DEMO_CODEC, _CastleExcellentDemoAuthoringAdapter())
    registry.register(CASTLE_EXCELLENT_SCREENS_CODEC, _CastleExcellentScreensAuthoringAdapter())
    registry.register(PACMAN_DEMO_CODEC, _PacmanDemoAuthoringAdapter())
    registry.register(DORAEMON_TEXT_CODEC, _DoraemonTextAuthoringAdapter())
    registry.register(DORAEMON_MUSIC_CODEC, _DoraemonMusicAuthoringAdapter())
    registry.register(DORAEMON_DEMO_CODEC, _DoraemonDemoAuthoringAdapter())
    return registry


def _adapters(profile: GameProfile, revision: RevisionSpec) -> tuple[Any, ...]:
    registry = authoring_adapters()
    identifiers = set(registry.identifiers())
    matches = tuple(
        registry.get(resource.codec)
        for resource in profile.resources_for(revision)
        if resource.codec in identifiers
    )
    if not matches:
        raise AuthoringError(
            f"game profile {profile.game_id} must select an authoring adapter"
        )
    if len({adapter.resource_id for adapter in matches}) != len(matches):
        raise AuthoringError(
            f"game profile {profile.game_id} selects duplicate authoring adapters"
        )
    return matches


def _adapter(
    profile: GameProfile, revision: RevisionSpec, resource: str | None = None
) -> Any:
    matches = _adapters(profile, revision)
    if resource is None:
        return matches[0]
    selected = [adapter for adapter in matches if adapter.resource_id == resource]
    if len(selected) != 1:
        available = ", ".join(adapter.resource_id for adapter in matches)
        raise AuthoringError(
            f"game profile {profile.game_id} has no authoring resource {resource!r}; "
            f"available: {available}"
        )
    return selected[0]


def _adapter_for_document(
    value: dict[str, Any], profile: GameProfile, revision: RevisionSpec
) -> Any:
    schema = value.get("schema")
    selected = [
        adapter for adapter in _adapters(profile, revision)
        if adapter.document_schema == schema
    ]
    if len(selected) != 1:
        raise AuthoringError(
            f"authoring document schema {schema!r} is not registered for "
            f"{profile.game_id}"
        )
    return selected[0]


def resolve_profile_revision(
    profile_reference: str, source_sha256: str
) -> tuple[GameProfile, RevisionSpec]:
    """Resolve a ``game-id.vN`` Studio reference and its immutable revision."""

    match = re.fullmatch(r"([a-z0-9][a-z0-9._-]*)\.v([1-9][0-9]*)", profile_reference)
    if match is None:
        raise AuthoringError(
            "Studio profile must use the game-id.vN form"
        )
    game_id, version_text = match.groups()
    registry = game_profiles()
    try:
        profile = registry.get(game_id)
    except AuthoringError as exc:
        raise AuthoringError(f"unknown Studio game profile: {game_id}") from exc
    version = int(version_text)
    if profile.profile_version != version:
        raise AuthoringError(
            f"Studio profile {profile_reference} does not match the repository's profile "
            f"version {profile.profile_version}"
        )
    matches = [
        revision for revision in profile.revisions
        if revision.sha256 == source_sha256
    ]
    if len(matches) != 1:
        raise AuthoringError(
            f"Studio source is not a recognized revision of {profile_reference}"
        )
    return profile, matches[0]


def validate_game_document_for_revision(
    document_path: Path, *, profile_reference: str, source_sha256: str
) -> dict[str, object]:
    """Validate an authoring document without requiring a commercial ROM path."""

    profile, revision = resolve_profile_revision(profile_reference, source_sha256)
    value = _load_json(document_path)
    adapter = _adapter_for_document(value, profile, revision)
    document = adapter.parse(value, profile, revision)
    return {
        "schema": "retrolarium.game-document-validation",
        "schema_version": 1,
        "status": "valid",
        "game": profile.game_id,
        "profile_version": profile.profile_version,
        "revision": revision.revision_id,
        "document": str(document_path.resolve()),
        "details": adapter.details(document),
    }


def _read_image(
    path: Path, custom_profile: Path | None
) -> tuple[bytes, GameProfile, RevisionSpec]:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise AuthoringError(f"cannot read game image {path}: {exc}") from exc
    profile, revision = recognize_revision(payload, _profiles(custom_profile))
    return payload, profile, revision


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AuthoringError(f"cannot read authoring document {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise AuthoringError("authoring document must be a JSON object")
    return value


def _atomic_write(path: Path, payload: bytes, *, durable: bool = True) -> None:
    """Replace path with payload in one step. durable also flushes it to the
    disk first; a built image skips that (the next build makes it again, and
    the flush costs most of a second on Windows)."""
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            if durable:
                stream.flush()
                os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_write_json(path: Path, value: object) -> None:
    _atomic_write(
        path,
        (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )


def profile_catalog() -> dict[str, object]:
    registry = game_profiles()
    profiles = []
    for identifier in registry.identifiers():
        profile = registry.get(identifier)
        profiles.append(
            {
                "id": profile.game_id,
                "name": profile.name,
                "platform": profile.platform,
                "profile_version": profile.profile_version,
                "capabilities": list(profile.capabilities),
                "revisions": [revision.revision_id for revision in profile.revisions],
                "resources": [resource.resource_id for resource in profile.resources],
                "revision_details": [
                    {
                        "id": revision.revision_id,
                        "name": revision.name,
                        "size": revision.size,
                        "sha256": revision.sha256,
                        "container": revision.container,
                        "capabilities": list(profile.capabilities_for(revision)),
                        "resources": [
                            resource.resource_id
                            for resource in profile.resources_for(revision)
                        ],
                        "payloads": [
                            {
                                "id": payload.payload_id,
                                "storage": payload.storage,
                                "size": payload.size,
                                "load_address": payload.load_address,
                            }
                            for payload in revision.payloads
                        ],
                    }
                    for revision in profile.revisions
                ],
            }
        )
    return {
        "schema": "retrolarium.game-profile-catalog",
        "schema_version": 1,
        "profiles": profiles,
    }


def inspect_game(path: Path, *, custom_profile: Path | None = None) -> dict[str, object]:
    payload, profile, revision = _read_image(path, custom_profile)
    adapter = _adapter(profile, revision)
    document = adapter.decode(payload, profile, revision)
    details = adapter.details(document)
    return {
        "schema": "retrolarium.game-inspection",
        "schema_version": 1,
        "path": str(path.resolve()),
        "game": profile.game_id,
        "name": profile.name,
        "profile_version": profile.profile_version,
        "platform": profile.platform,
        "revision": revision.revision_id,
        "sha256": revision.sha256,
        "capabilities": list(profile.capabilities_for(revision)),
        "resources": [
            {
                "id": resource.resource_id,
                "kind": resource.kind,
                "codec": resource.codec,
                "capacity_policy": resource.capacity_policy,
            }
            for resource in profile.resources_for(revision)
        ],
        "details": details,
    }


def extract_game(
    path: Path,
    output: Path,
    *,
    custom_profile: Path | None = None,
    resource: str | None = None,
) -> dict[str, object]:
    payload, profile, revision = _read_image(path, custom_profile)
    adapter = _adapter(profile, revision, resource)
    document = adapter.decode(payload, profile, revision)
    details = adapter.details(document)
    _atomic_write_json(output, document.as_dict())
    result = {
        "schema": "retrolarium.game-extract-result",
        "schema_version": 1,
        "game": profile.game_id,
        "revision": revision.revision_id,
        "resource": adapter.resource_id,
        "output": str(output.resolve()),
    }
    result.update(details)
    return result


def validate_game(
    path: Path, document_path: Path, *, custom_profile: Path | None = None
) -> dict[str, object]:
    payload, profile, revision = _read_image(path, custom_profile)
    value = _load_json(document_path)
    adapter = _adapter_for_document(value, profile, revision)
    document = adapter.parse(value, profile, revision)
    _output, report = adapter.build(payload, document, profile, revision)
    return {
        "schema": "retrolarium.game-validation-result",
        "schema_version": 1,
        "status": "valid",
        "game": profile.game_id,
        "revision": revision.revision_id,
        "document": str(document_path.resolve()),
        "build": report,
    }


def _check_build_paths(
    path: Path, document_paths: Sequence[Path], output: Path, report_path: Path | None
) -> None:
    """Refuse a build that would write over what it reads, or write its two
    outputs to one file, before anything is written."""
    inputs = {path.resolve(): "the source image"}
    for document in document_paths:
        inputs.setdefault(document.resolve(), f"the document {document}")
    for target, name in ((output, "output"), (report_path, "report")):
        if target is not None and target.resolve() in inputs:
            raise AuthoringError(f"game build {name} must not overwrite {inputs[target.resolve()]}")
    if report_path is not None and report_path.resolve() == output.resolve():
        raise AuthoringError("game build report must not overwrite the built image")


def build_game(
    path: Path,
    document_path: Path,
    output: Path,
    *,
    custom_profile: Path | None = None,
    report_path: Path | None = None,
) -> dict[str, object]:
    _check_build_paths(path, [document_path], output, report_path)
    payload, profile, revision = _read_image(path, custom_profile)
    value = _load_json(document_path)
    adapter = _adapter_for_document(value, profile, revision)
    document = adapter.parse(value, profile, revision)
    rebuilt, report = adapter.build(payload, document, profile, revision)
    _atomic_write(output, rebuilt, durable=False)
    result = {
        **report,
        "output": str(output.resolve()),
        "document": str(document_path.resolve()),
    }
    if report_path is not None:
        _atomic_write_json(report_path, result)
        result["report"] = str(report_path.resolve())
    return result


def build_game_documents(
    path: Path,
    document_paths: Sequence[Path],
    output: Path,
    *,
    custom_profile: Path | None = None,
    report_path: Path | None = None,
) -> dict[str, object]:
    """Build several typed documents against one source and merge their edits."""

    _check_build_paths(path, document_paths, output, report_path)
    if not document_paths:
        raise AuthoringError("composed game build needs at least one document")
    payload, profile, revision = _read_image(path, custom_profile)
    merged = bytearray(payload)
    owners: dict[int, tuple[Path, int]] = {}
    builds: list[dict[str, object]] = []
    schemas: set[str] = set()
    for document_path in document_paths:
        value = _load_json(document_path)
        schema = value.get("schema")
        if not isinstance(schema, str) or schema in schemas:
            raise AuthoringError(
                f"composed game build requires unique document schemas; got {schema!r}"
            )
        schemas.add(schema)
        adapter = _adapter_for_document(value, profile, revision)
        document = adapter.parse(value, profile, revision)
        rebuilt, report = adapter.build(payload, document, profile, revision)
        if len(rebuilt) != len(payload):
            raise AuthoringError(
                f"authoring document {document_path} changed the game image size"
            )
        for offset, (before, after) in enumerate(zip(payload, rebuilt)):
            if before == after:
                continue
            previous = owners.get(offset)
            if previous is not None and previous[1] != after:
                raise AuthoringError(
                    "authoring documents conflict at file offset "
                    f"0x{offset:X}: {previous[0]} and {document_path}"
                )
            owners[offset] = (document_path, after)
            merged[offset] = after
        builds.append({
            "document": str(document_path.resolve()),
            "resource": adapter.resource_id,
            "build": report,
        })
    _atomic_write(output, bytes(merged), durable=False)
    result: dict[str, object] = {
        "schema": "retrolarium.composed-game-build",
        "schema_version": 1,
        "game": profile.game_id,
        "revision": revision.revision_id,
        "changed_bytes": len(owners),
        "builds": builds,
        "output": str(output.resolve()),
    }
    if report_path is not None:
        _atomic_write_json(report_path, result)
        result["report"] = str(report_path.resolve())
    return result


def _validate_built_image(
    artifact: Path, profile: GameProfile, revision: RevisionSpec
) -> None:
    if profile.platform != "nes":
        raise AuthoringError(
            f"no direct authoring launch validator for {profile.platform}"
        )
    image = load_ines(artifact)
    if artifact.stat().st_size != revision.size or image.mapper != revision.mapper:
        raise AuthoringError(
            "edited image container, size or mapper differs from its base revision"
        )


def game_launch_plan(
    source: Path,
    artifact: Path,
    *,
    mame: Path,
    machine: str | None = None,
    attach_window: str | None = None,
    custom_profile: Path | None = None,
) -> dict[str, object]:
    _payload, profile, revision = _read_image(source, custom_profile)
    _validate_built_image(artifact, profile, revision)
    plan = launch_image_plan(
        artifact,
        platform=profile.platform,
        mame=mame,
        machine=machine or vs_machine(profile.game_id, revision.revision_id),
        attach_window=attach_window,
    )
    plan["game"] = profile.game_id
    plan["revision"] = revision.revision_id
    plan["source_sha256"] = revision.sha256
    return plan


def launch_game_smoke(
    source: Path,
    artifact: Path,
    *,
    mame: Path,
    output: Path,
    machine: str | None = None,
    seconds: int = 2,
    timeout: int = 60,
    custom_profile: Path | None = None,
) -> dict[str, object]:
    _payload, profile, revision = _read_image(source, custom_profile)
    _validate_built_image(artifact, profile, revision)
    result = launch_image_smoke(
        artifact,
        platform=profile.platform,
        mame=mame,
        output=output,
        machine=machine or vs_machine(profile.game_id, revision.revision_id),
        seconds=seconds,
        timeout=timeout,
    )
    result["game"] = profile.game_id
    result["revision"] = revision.revision_id
    result["source_sha256"] = revision.sha256
    run_directory = Path(str(result["run_directory"]))
    _atomic_write_json(run_directory / "run-result.json", result)
    return result
