"""Launch and playtest plans: the MAME process specs the Studio runs to play a
build from a chosen point (an SMB area, a Castle Excellent room, a Solomon's Key room, a
Doraemon block or part, a Flicky or Pac-Man round), Flicky sound previews and
smoke launches."""

from __future__ import annotations

from collections.abc import Mapping
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any

from retrolarium_kit.core.game_profiles import game_profiles
from retrolarium_kit.games.flicky.content import (
    WORKSPACE_FILES as FLICKY_WORKSPACE_FILES,
    validate_flicky_source_snapshot,
    FlickyContentError,
)
from retrolarium_core.fds import FdsError, parse_fds_side
from retrolarium_kit.core.project import launch_image_plan, launch_image_smoke, sha256_file, vs_machine
from retrolarium_kit.studio.project import (
    SOLOMON_REVISIONS,
    StudioProjectError,
    _mapping,
    _require_declared_operation,
    load_studio_project,
    verify_studio_project,
)
# Revisions made for 50 Hz consoles run on MAME's PAL machine.
PAL_REVISIONS = frozenset({("smb1", "pal"), ("solomons-key", "europe")})
# SMB cartridge revisions whose program runs the point playtest script:
# Japan/USA, its PlayChoice-10 copy (the same program) and Europe (the same
# RAM and hooks).
SMB_SCRIPTED_REVISIONS = frozenset({"ju", "pc10", "pal"})
# The point playtest also runs the Disk System versions. Super Mario Bros. 2
# and All Night Nippon are on the later Disk System engine: the timer digits
# sit at $07EC, the running game is OperMode_Task 4 (5 in All Night Nippon,
# which adds a task), and worlds 5-8 (file $20) and the hard / extended
# worlds (file $40) load from the disk while the game runs; the playtest puts
# that file in RAM the way the game's loader does and records it in the file
# list number ($07F7).
SMB_POINT_PLAYTEST_REVISIONS = SMB_SCRIPTED_REVISIONS | {"fds_smb", "smb2_jp_fds", "ann_fds"}
SMB_LATE_FDS_RUNNING_TASKS = {"smb2_jp_fds": 4, "ann_fds": 5}
SMB_LATE_FDS_HARD_WORLDS = frozenset({"world-hard", "world-extended"})


def _fds_program_at(image_path: Path, address: int) -> tuple[bytes, int]:
    """The program file the console loads at boot over ``address`` (the first
    on the disk; overlays loaded later come after it), and the address's
    offset in it."""
    side = image_path.read_bytes()[-65500:]
    try:
        records = parse_fds_side(side)
    except FdsError as exc:
        raise StudioProjectError(f"{image_path.name}: {exc}") from exc
    for record in records:
        if record.file_type == 0 and record.load_address <= address < record.load_address + record.size:
            return side[record.data_offset:record.data_offset + record.size], address - record.load_address
    raise StudioProjectError(f"{image_path.name} has no program file at ${address:04X}")


def _fds_file_blob(image_path: Path, file_id: int) -> bytes:
    """A program file of a built disk image: its load address, then its data."""
    image = image_path.read_bytes()
    try:
        records = parse_fds_side(image[-65500:])
    except FdsError as exc:
        raise StudioProjectError(f"{image_path.name}: {exc}") from exc
    side = image[-65500:]
    for record in records:
        if record.file_id == file_id and record.file_type == 0:
            return record.load_address.to_bytes(2, "little") \
                + side[record.data_offset:record.data_offset + record.size]
    raise StudioProjectError(f"{image_path.name} has no program file ${file_id:02X}")


def _source_revision(inspection: Mapping[str, Any]):
    """The profile and revision a project's source is, if the kit knows it."""
    registry = game_profiles()
    for identifier in registry.identifiers():
        profile = registry.get(identifier)
        if f"{profile.game_id}.v{profile.profile_version}" != inspection["project"]["profile"]:
            continue
        for revision in profile.revisions:
            if revision.sha256 == inspection["source"].get("sha256"):
                return profile, revision
    return None, None


def _revision_machine(inspection: Mapping[str, Any]) -> str | None:
    profile, revision = _source_revision(inspection)
    if profile is None:
        return None
    if (profile.game_id, revision.revision_id) in PAL_REVISIONS:
        return "nespal"
    return vs_machine(profile.game_id, revision.revision_id)


def studio_launch_plan(
    path: Path,
    *,
    mame: Path,
    machine: str | None = None,
    attach_window: str | None = None,
) -> dict[str, Any]:
    inspection = load_studio_project(path)
    _require_declared_operation(inspection, "launch")
    verification = verify_studio_project(path)
    if verification["status"] != "current":
        raise StudioProjectError("Studio artifact is stale; build it again before launch")
    plan = launch_image_plan(
        Path(verification["artifact"]["absolute_path"]),
        platform=inspection["project"]["platform"],
        mame=mame,
        machine=machine or _revision_machine(inspection),
        attach_window=attach_window,
    )
    plan["operation"] = "retrolarium.studio.launch"
    plan["project_file"] = inspection["project_file"]
    return plan


def studio_flicky_round_playtest_plan(
    path: Path, *, mame: Path, round: int,
) -> dict[str, Any]:
    """Run one authored Flicky round through the game's regular mode loader."""

    inspection = load_studio_project(path)
    _require_declared_operation(inspection, "launch")
    project = inspection["project"]
    if project["source_mode"] != "flicky-content" \
            or project["platform"] != "megadrive" \
            or project["profile"] != "flicky-ue.v1":
        raise StudioProjectError(
            "Flicky round playtest requires the canonical flicky-ue.v1 source profile"
        )
    matching_tabs = [
        tab for tab in inspection["workspace"]["tabs"]
        if tab["enabled"] and tab["codec"] == "flicky.levels.v1"
        and "playtest" in tab["capabilities"]
    ]
    if len(matching_tabs) != 1:
        raise StudioProjectError(
            "Studio project must expose one enabled playtest-capable Flicky Levels tab"
        )
    round = _bounded_integer(round, "round", 1, 48)
    verification = verify_studio_project(path)
    if verification["status"] != "current":
        raise StudioProjectError(
            "Studio artifact is stale; build it again before round playtest"
        )
    lua = Path(__file__).with_name("runtime") / "flicky" / "playtest.lua"
    if not lua.is_file():
        raise StudioProjectError(f"packaged Flicky round playtest script is missing: {lua}")
    artifact = Path(verification["artifact"]["absolute_path"])
    plan = launch_image_plan(
        artifact, platform="megadrive", mame=mame, machine="genesis",
    )
    plan.update({
        "operation": "retrolarium.studio.flicky-round-playtest",
        "backend": "mame-lua",
        "arguments": [
            *plan["arguments"], "-autoboot_delay", "0",
            "-autoboot_script", str(lua.resolve()),
        ],
        "environment": {"FLICKY_PLAYTEST_ROUND": str(round)},
        "project_file": inspection["project_file"],
        "script": {"path": str(lua.resolve()), "sha256": sha256_file(lua)},
        "point": {"round": round},
    })
    return plan


def studio_flicky_sound_preview(
    path: Path, *, header: str, seconds: int = 30,
    enabled_channels: tuple[int, ...] | None = None,
    document_override: Path | None = None,
) -> dict[str, Any]:
    """Render saved or transient Flicky Sound data through the pinned source oracle."""

    inspection = load_studio_project(path)
    project = inspection["project"]
    if project["source_mode"] != "flicky-content" \
            or project["platform"] != "megadrive" \
            or project["profile"] != "flicky-ue.v1":
        raise StudioProjectError(
            "Flicky sound preview requires the canonical flicky-ue.v1 source profile"
        )
    matching_tabs = [
        tab for tab in inspection["workspace"]["tabs"]
        if tab["enabled"] and tab["codec"] == "flicky.sound.v1"
        and "preview" in tab["capabilities"]
    ]
    if len(matching_tabs) != 1:
        raise StudioProjectError(
            "Studio project must expose one enabled preview-capable Flicky Sound tab"
        )
    seconds = _bounded_integer(seconds, "seconds", 1, 30)
    root = Path(inspection["project_root"]).resolve()
    try:
        validate_flicky_source_snapshot(root)
    except FlickyContentError as exc:
        raise StudioProjectError(str(exc)) from exc
    source = root / "flicky-source"
    document = Path(matching_tabs[0]["document_path"]).resolve()
    expected_document = source / FLICKY_WORKSPACE_FILES["z80_sound_banks"]
    if document != expected_document.resolve():
        raise StudioProjectError("Flicky Sound tab does not own the source workspace")
    output_dir = root / "build/sound-preview"
    if not output_dir.resolve().is_relative_to(root):
        raise StudioProjectError(
            "Flicky Sound preview output must remain inside the project"
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    document_origin = "saved"
    if document_override is not None:
        candidate = document_override.resolve()
        if candidate.parent != output_dir.resolve() \
                or not candidate.name.startswith(".flicky-unsaved-") \
                or candidate.suffix != ".asm" or not candidate.is_file():
            raise StudioProjectError(
                "transient Flicky Sound document must be an ASM file in "
                "this project's build/sound-preview directory"
            )
        document = candidate
        document_origin = "unsaved"
    from retrolarium_kit.games.flicky.workspace import sound as flicky_sound
    from retrolarium_kit.games.flicky.workspace.preview import render_preview

    try:
        inventory = flicky_sound.export_document(document)
    except (OSError, ValueError, KeyError) as exc:
        raise StudioProjectError(f"cannot read Flicky Sound inventory: {exc}") from exc
    headers = {item["id"]: item for item in inventory["headers"]}
    if header not in headers:
        raise StudioProjectError(f"unknown Flicky Sound header: {header!r}")
    track_count = len(headers[header]["tracks"])
    if enabled_channels is not None:
        if not isinstance(enabled_channels, tuple) or not enabled_channels \
                or any(not isinstance(index, int) or isinstance(index, bool)
                       or not 0 <= index < track_count
                       for index in enabled_channels) \
                or len(set(enabled_channels)) != len(enabled_channels):
            raise StudioProjectError(
                f"enabled channels must be unique indexes in 0..{track_count - 1}"
            )
        enabled_channels = tuple(sorted(enabled_channels))
    renderer = ymfm_renderer()
    channel_key = "all" if enabled_channels is None else "-".join(
        str(index) for index in enabled_channels)
    stem = f"{header}-{sha256_file(document)[:12]}-s{seconds}-c{channel_key}"
    vgm = output_dir / f"{stem}.vgm"
    wav = output_dir / f"{stem}.wav"
    with tempfile.TemporaryDirectory(prefix=".flicky-sound-", dir=output_dir) as name:
        temporary = Path(name)
        try:
            render_preview(source, document, header, temporary / "preview.vgm", temporary / "preview.wav",
                           seconds=seconds, renderer=renderer,
                           enabled_channels=None if enabled_channels is None else set(enabled_channels))
        except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
            raise StudioProjectError(f"Flicky Sound preview failed: {exc}") from exc
        temporary_vgm = temporary / "preview.vgm"
        temporary_wav = temporary / "preview.wav"
        try:
            if temporary_vgm.read_bytes()[:4] != b"Vgm " \
                    or temporary_wav.read_bytes()[:4] != b"RIFF":
                raise StudioProjectError(
                    "Flicky Sound preview produced invalid audio files"
                )
            os.replace(temporary_vgm, vgm)
            os.replace(temporary_wav, wav)
        except OSError as exc:
            raise StudioProjectError(
                f"Flicky Sound preview cannot publish audio: {exc}"
            ) from exc
    return {
        "schema": "retrolarium.flicky-sound-preview",
        "schema_version": 1,
        "status": "rendered",
        "project_file": inspection["project_file"],
        "document_origin": document_origin,
        "header": header,
        "seconds": seconds,
        "enabled_channels": list(range(track_count)) if enabled_channels is None
            else list(enabled_channels),
        "document_sha256": sha256_file(document),
        "renderer_sha256": sha256_file(renderer),
        "vgm": {"path": str(vgm), "size": vgm.stat().st_size,
                "sha256": sha256_file(vgm)},
        "wav": {"path": str(wav), "size": wav.stat().st_size,
                "sha256": sha256_file(wav)},
    }


def ymfm_renderer() -> Path:
    """The studio's YM2612 renderer (retrolarium-ymfm-renderer): from
    RETROLARIUM_YMFM_RENDERER, which the studio sets, or the PATH."""
    found = os.environ.get("RETROLARIUM_YMFM_RENDERER") or shutil.which("retrolarium-ymfm-renderer")
    if not found or not Path(found).is_file():
        raise StudioProjectError(
            "the YMFM renderer is missing: set RETROLARIUM_YMFM_RENDERER to the studio's "
            "retrolarium-ymfm-renderer or put it on the PATH"
        )
    return Path(found).resolve()


def _smb_area_pointer(name: str) -> int:
    area_types = ("water", "ground", "underground", "castle")
    area_type, separator, index_text = name.rpartition("_")
    if not separator or area_type not in area_types:
        raise StudioProjectError(f"unsupported SMB area name: {name!r}")
    try:
        area_index = int(index_text) - 1
    except ValueError as exc:
        raise StudioProjectError(f"unsupported SMB area name: {name!r}") from exc
    if not 0 <= area_index <= 31:
        raise StudioProjectError(f"SMB area index is outside 1..32: {name!r}")
    return (area_types.index(area_type) << 5) | area_index


def _smb_object_pages(area: Mapping[str, Any]) -> set[int]:
    """The pages an area object starts on, as the game's area parser counts
    them: the page bit moves on one page, a row-13 page control (d6 clear)
    jumps to a page, and page controls are not objects themselves."""
    pages: set[int] = set()
    page = 0
    for item in area.get("objects") or []:
        if not isinstance(item, Mapping):
            continue
        if item.get("page_advance"):
            page += 1
        control = item.get("object_control")
        if item.get("row") == 13 and isinstance(control, int) and not control & 0x40:
            page = control & 0x1F
            continue
        pages.add(page)
    return pages


def _bounded_integer(value: object, name: str, minimum: int, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) \
            or not minimum <= value <= maximum:
        raise StudioProjectError(f"{name} must be in range {minimum}..{maximum}")
    return value


def studio_smb_point_playtest_plan(
    path: Path,
    *,
    mame: Path,
    course_bank: str,
    area: str,
    world: int,
    course: int,
    column: int,
    row: int,
    theme: str,
    infinite_time: bool = True,
    invincible: bool = True,
    wrap_pits: bool = True,
    sfx_channel: str = "none",
    sfx_value: int = 0,
) -> dict[str, Any]:
    """Emit the fixed MAME/Lua process contract for an SMB JU editor point."""

    inspection = load_studio_project(path)
    _require_declared_operation(inspection, "launch")
    if inspection["project"]["platform"] != "nes" \
            or inspection["project"]["profile"] != "smb1.v1":
        raise StudioProjectError(
            "SMB point playtest currently supports only the smb1.v1 NES profile"
        )
    matching_tabs = [
        tab for tab in inspection["workspace"]["tabs"]
        if tab["enabled"] and tab["codec"] == "smb.levels.v1"
        and "playtest" in tab["capabilities"]
    ]
    if len(matching_tabs) != 1:
        raise StudioProjectError(
            "Studio project must expose one enabled playtest-capable SMB Levels tab"
        )

    verification = verify_studio_project(path)
    if verification["status"] != "current":
        raise StudioProjectError(
            "Studio artifact is stale; build it again before point playtest"
        )
    executable = mame.resolve()
    if not executable.is_file():
        raise StudioProjectError(f"MAME executable does not exist: {executable}")

    document_path = Path(matching_tabs[0]["document_path"])
    try:
        document = _mapping(
            json.loads(document_path.read_text(encoding="utf-8")),
            "SMB resource document",
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StudioProjectError(f"cannot read SMB resource document: {exc}") from exc
    if document.get("schema") != "retrolarium.nes-resource-bundle" \
            or document.get("game") != "smb1":
        raise StudioProjectError("SMB point playtest requires an smb1 resource document")
    if document.get("revision") not in SMB_POINT_PLAYTEST_REVISIONS:
        raise StudioProjectError(
            "Starting inside a level does not work in this version; play it from power-on."
        )
    resources = document.get("resources")
    if not isinstance(resources, list):
        raise StudioProjectError("SMB resource document resources must be an array")
    level_resources = [
        item for item in resources if isinstance(item, Mapping)
        and item.get("id") == course_bank and item.get("codec") == "smb.levels.v1"
    ]
    if len(level_resources) != 1:
        raise StudioProjectError(f"unknown SMB course bank: {course_bank!r}")
    level_resource = level_resources[0]
    areas = level_resource.get("areas")
    selected_areas = [
        item for item in areas if isinstance(item, Mapping) and item.get("name") == area
    ] if isinstance(areas, list) else []
    if not selected_areas:
        raise StudioProjectError(
            f"SMB area {area!r} does not belong to course bank {course_bank!r}"
        )

    world = _bounded_integer(world, "world", 1, 8)
    course = _bounded_integer(course, "course", 1, 8)
    column = _bounded_integer(column, "column", 0, 511)
    row = _bounded_integer(row, "row", 1, 12)
    if theme not in {"day", "night"}:
        raise StudioProjectError("theme must be 'day' or 'night'")
    if sfx_channel not in {"none", "square1", "square2", "noise"}:
        raise StudioProjectError(
            "sfx_channel must be 'none', 'square1', 'square2', or 'noise'"
        )
    sfx_value = _bounded_integer(sfx_value, "sfx_value", 0, 255)
    if (sfx_channel == "none") != (sfx_value == 0):
        raise StudioProjectError(
            "sfx_channel 'none' requires value 0 and an SFX channel requires a non-zero value"
        )
    for name, value in (
        ("infinite_time", infinite_time),
        ("invincible", invincible),
        ("wrap_pits", wrap_pits),
    ):
        if not isinstance(value, bool):
            raise StudioProjectError(f"{name} must be a boolean")

    declared_world_resource = level_resource.get("world_resource")
    if declared_world_resource is not None and not isinstance(
        declared_world_resource, str
    ):
        raise StudioProjectError(
            f"SMB course bank {course_bank!r} has an invalid world resource"
        )
    world_resources = [
        item
        for item in resources
        if isinstance(item, Mapping)
        and item.get("codec") == "smb.world.v1"
        and (
            declared_world_resource is None
            or item.get("id") == declared_world_resource
        )
    ]
    if len(world_resources) != 1:
        if declared_world_resource is None:
            raise StudioProjectError("SMB point playtest requires one world resource")
        raise StudioProjectError(
            f"SMB course bank {course_bank!r} refers to an unknown world resource: "
            f"{declared_world_resource!r}"
        )
    worlds = world_resources[0].get("worlds")
    if not isinstance(worlds, list) or world > len(worlds):
        raise StudioProjectError(f"world {world} is not present in the SMB document")
    route = _mapping(worlds[world - 1], f"SMB world {world}").get("areas")
    if not isinstance(route, list) or course > len(route):
        raise StudioProjectError(
            f"course {course} is not present in SMB world {world}"
        )

    palette_resources = [
        item for item in resources if isinstance(item, Mapping)
        and item.get("codec") == "nes.ppu-palette-packets.v1"
    ]
    if len(palette_resources) != 1:
        raise StudioProjectError("SMB point playtest requires one area palette resource")
    day_color = _bounded_integer(
        palette_resources[0].get("day_background"), "day_background", 0, 63
    )
    night_color = _bounded_integer(
        palette_resources[0].get("night_background"), "night_background", 0, 63
    )

    pointer = _smb_area_pointer(area)
    selected_route = _mapping(
        route[course - 1], f"SMB world {world} course {course}"
    )
    route_pointer = (
        _bounded_integer(selected_route.get("area_type"), "area_type", 0, 3) << 5
    ) | _bounded_integer(selected_route.get("area_index"), "area_index", 0, 31)
    artifact = Path(verification["artifact"]["absolute_path"]).resolve()
    lua = Path(__file__).with_name("runtime") / "smb" / "playtest.lua"
    if not lua.is_file():
        raise StudioProjectError(f"packaged SMB point playtest script is missing: {lua}")
    environment = {
        "SMB1_PLAYTEST_AREA": str(pointer),
        "SMB1_PLAYTEST_WORLD": str(world - 1),
        "SMB1_PLAYTEST_LEVEL": str(course - 1),
        "SMB1_PLAYTEST_PAGE": str(column // 16),
        "SMB1_PLAYTEST_X": str((column % 16) * 16),
        "SMB1_PLAYTEST_Y": str((row + 1) * 16),
        "SMB1_PLAYTEST_THEME": theme,
        "SMB1_PLAYTEST_DAY_COLOR": str(day_color),
        "SMB1_PLAYTEST_NIGHT_COLOR": str(night_color),
        "SMB1_PLAYTEST_INFINITE_TIME": "1" if infinite_time else "0",
        "SMB1_PLAYTEST_INVINCIBLE": "1" if invincible else "0",
        "SMB1_PLAYTEST_WRAP_PITS": "1" if wrap_pits else "0",
        "SMB1_PLAYTEST_SFX_CHANNEL": sfx_channel,
        "SMB1_PLAYTEST_SFX_VALUE": str(sfx_value),
    }
    # Entering on a later page, the game's area parser looks for an object
    # that starts on that page and spins forever when there is none (a page
    # control can skip pages); the script then stops it looking.
    if column // 16 and column // 16 not in _smb_object_pages(selected_areas[0]):
        environment["SMB1_PLAYTEST_EMPTY_PAGE"] = "1"
    if document.get("revision") in SMB_LATE_FDS_RUNNING_TASKS:
        environment["SMB1_PLAYTEST_TIMER"] = str(0x07EC)
        environment["SMB1_PLAYTEST_RUNNING_TASK"] = str(SMB_LATE_FDS_RUNNING_TASKS[document["revision"]])
        hard = world_resources[0].get("id") in SMB_LATE_FDS_HARD_WORLDS
        if hard or world >= 5:
            file_id, file_list = (0x40, 3) if hard else (0x20, 1)
            blob = artifact.parent / "playtest-disk-file.bin"
            blob.write_bytes(_fds_file_blob(artifact, file_id))
            environment["SMB1_PLAYTEST_DISK_FILE"] = str(blob)
            environment["SMB1_PLAYTEST_DISK_LIST"] = str(file_list)
            if hard:
                environment["SMB1_PLAYTEST_HARD"] = "1"
    plan = launch_image_plan(
        artifact, platform="nes", mame=executable,
        machine="nespal" if document.get("revision") == "pal" else "nes",
    )
    plan.update({
        "operation": "retrolarium.studio.smb-point-playtest",
        "backend": "mame-lua",
        "arguments": [
            *plan["arguments"],
            "-autoboot_delay", "0",
            "-autoboot_script", str(lua.resolve()),
        ],
        "environment": environment,
        "project_file": inspection["project_file"],
        "script": {"path": str(lua.resolve()), "sha256": sha256_file(lua)},
        "point": {
            "course_bank": course_bank,
            "area": area,
            "area_pointer": pointer,
            "world": world,
            "course": course,
            "route_matches_area": route_pointer == pointer,
            "column": column,
            "row": row,
            "theme": theme,
            "sfx": {"channel": sfx_channel, "value": sfx_value},
        },
    })
    return plan


def studio_castle_excellent_point_playtest_plan(
    path: Path, *, mame: Path, room: int, x: int, y: int,
) -> dict[str, Any]:
    """Prepare an isolated Castle Excellent ROM that boots through its normal room loader."""

    inspection = load_studio_project(path)
    _require_declared_operation(inspection, "launch")
    if inspection["project"]["platform"] != "nes" \
            or inspection["project"]["profile"] != "castle-excellent.v1":
        raise StudioProjectError(
            "Castle Excellent point playtest requires the castle-excellent.v1 NES profile"
        )
    matching_tabs = [
        tab for tab in inspection["workspace"]["tabs"]
        if tab["enabled"] and tab["codec"] == "castle-excellent.prefix-rooms@1"
        and "playtest" in tab["capabilities"]
    ]
    if len(matching_tabs) != 1:
        raise StudioProjectError(
            "Studio project must expose one enabled playtest-capable Castle Excellent Rooms tab"
        )
    verification = verify_studio_project(path)
    if verification["status"] != "current":
        raise StudioProjectError(
            "Studio artifact is stale; build it again before point playtest"
        )
    executable = mame.resolve()
    if not executable.is_file():
        raise StudioProjectError(f"MAME executable does not exist: {executable}")

    document_path = Path(matching_tabs[0]["document_path"])
    try:
        document = _mapping(
            json.loads(document_path.read_text(encoding="utf-8")),
            "Castle Excellent rooms document",
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StudioProjectError(f"cannot read Castle Excellent rooms document: {exc}") from exc
    if document.get("schema") != "retrolarium.castle-excellent-levels" \
            or document.get("schema_version") != 1 \
            or document.get("game") != "castle-excellent" \
            or document.get("revision") not in {
                "castle-excellent-japan-hsp-05", "castlequest-usa"
            }:
        raise StudioProjectError("Castle Excellent rooms document has an unsupported identity")
    rooms = document.get("rooms")
    if not isinstance(rooms, list) or len(rooms) != 100:
        raise StudioProjectError("Castle Excellent point playtest requires exactly 100 rooms")
    room = _bounded_integer(room, "room", 1, 100)
    x = _bounded_integer(x, "x", 0, 319)
    y = _bounded_integer(y, "y", 0, 159)

    artifact = Path(verification["artifact"]["absolute_path"]).resolve()
    source = artifact.read_bytes()
    # iNES header + PRG offset $22B1: LDA #$47; STA $37. Both supported
    # revisions share this sequence. Only the immediate operand is changed.
    if len(source) != 65552 or source[:4] != b"NES\x1a" \
            or source[0x22c1] != 0xa9 or source[0x22c3:0x22c5] != b"\x85\x37":
        raise StudioProjectError(
            "Castle Excellent build does not match the guarded initial-room layout"
        )
    preview = bytearray(source)
    preview[0x22c2] = room
    project_root = Path(inspection["project_root"]).resolve()
    preview_directory = project_root / "build" / "point-playtest"
    preview_directory.mkdir(parents=True, exist_ok=True)
    preview_path = preview_directory / (
        f"castle-excellent-{sha256_file(artifact)[:16]}-room-{room:03d}.nes"
    )
    if not preview_path.is_file() or preview_path.read_bytes() != preview:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".castle-excellent-point-", suffix=".tmp", dir=preview_directory
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(preview)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, preview_path)
        finally:
            temporary.unlink(missing_ok=True)

    lua = Path(__file__).with_name("runtime") / "castle_excellent" / "playtest.lua"
    if not lua.is_file():
        raise StudioProjectError(f"packaged Castle Excellent point playtest script is missing: {lua}")
    plan = launch_image_plan(preview_path, platform="nes", mame=executable)
    plan.update({
        "operation": "retrolarium.studio.castle-excellent-point-playtest",
        "backend": "mame-lua",
        "arguments": [
            *plan["arguments"], "-autoboot_delay", "0",
            "-autoboot_script", str(lua.resolve()),
        ],
        "environment": {
            "CASTLE_EXCELLENT_PLAYTEST_ROOM": str(room),
            "CASTLE_EXCELLENT_PLAYTEST_X": str(x),
            "CASTLE_EXCELLENT_PLAYTEST_Y": str(y),
        },
        "project_file": inspection["project_file"],
        "script": {"path": str(lua.resolve()), "sha256": sha256_file(lua)},
        "point": {"room": room, "x": x, "y": y},
        "base_rom": {"path": str(artifact), "sha256": sha256_file(artifact)},
    })
    return plan


def studio_solomon_point_playtest_plan(
    path: Path, *, mame: Path, room: int,
) -> dict[str, Any]:
    """Enter a selected Solomon room in MAME without patching the content ROM."""

    inspection = load_studio_project(path)
    _require_declared_operation(inspection, "launch")
    if inspection["project"]["platform"] != "nes" \
            or inspection["project"]["profile"] != "solomons-key.v1":
        raise StudioProjectError(
            "Solomon point playtest requires the solomons-key.v1 NES profile"
        )
    matching_tabs = [
        tab for tab in inspection["workspace"]["tabs"]
        if tab["enabled"] and tab["codec"] == "solomon.rooms@3"
        and "playtest" in tab["capabilities"]
    ]
    if len(matching_tabs) != 1:
        raise StudioProjectError(
            "Studio project must expose one enabled playtest-capable Solomon Rooms tab"
        )
    document_path = Path(matching_tabs[0]["document_path"])
    try:
        document = _mapping(
            json.loads(document_path.read_text(encoding="utf-8")),
            "Solomon rooms document",
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StudioProjectError(f"cannot read Solomon rooms document: {exc}") from exc
    revision_id = document.get("revision")
    if document.get("schema") != "retrolarium.solomon-rooms" \
            or document.get("schema_version") != 3 \
            or document.get("game") != "solomons-key" \
            or revision_id not in SOLOMON_REVISIONS \
            or not isinstance(document.get("rooms"), list) \
            or len(document["rooms"]) != 53:
        raise StudioProjectError("Solomon rooms document has an unsupported identity")
    room = _bounded_integer(room, "room", 0, 52)
    profile = game_profiles().get("solomons-key")
    revision = next(
        item for item in profile.revisions if item.revision_id == revision_id
    )
    hooks = revision.playtest
    if hooks is None:
        raise StudioProjectError(
            f"Solomon revision {revision_id} has no point-playtest contract"
        )
    executable = mame.resolve()
    if not executable.is_file():
        raise StudioProjectError(f"MAME executable does not exist: {executable}")
    verification = verify_studio_project(path)
    if verification["status"] != "current":
        raise StudioProjectError(
            "Studio artifact is stale; build it again before point playtest"
        )
    artifact = Path(verification["artifact"]["absolute_path"]).resolve()
    source = artifact.read_bytes()
    hook_offset = 16 + hooks["room_load_address"] - 0x8000
    # An expanded build (solomon_expansion) runs the game from its second
    # 32 KiB PRG bank.
    expanded = len(source) == 16 + 0x10000 + 0x8000 \
        and ((source[6] >> 4) | (source[7] & 0xF0)) == 66
    if expanded:
        hook_offset += 0x8000
    if (len(source) != revision.size and not expanded) or source[:4] != b"NES\x1a" \
            or source[hook_offset:hook_offset + 6] != b"\xa9\xfd\x25\x7c\x85\x7c":
        raise StudioProjectError(
            "Solomon build does not match the guarded room-loader layout"
        )
    lua = Path(__file__).with_name("runtime") / "solomon" / "playtest.lua"
    if not lua.is_file():
        raise StudioProjectError(f"packaged Solomon point-playtest script is missing: {lua}")
    plan = launch_image_plan(
        artifact, platform="nes", mame=executable,
        machine="nespal" if revision_id == "europe" else "nes",
    )
    plan.update({
        "operation": "retrolarium.studio.solomon-point-playtest",
        "backend": "mame-lua",
        "arguments": [
            *plan["arguments"], "-autoboot_delay", "0",
            "-autoboot_script", str(lua.resolve()),
        ],
        "environment": {
            "SOLOMON_PLAYTEST_ROOM": str(room),
            "SOLOMON_PLAYTEST_ROOM_LOAD": str(hooks["room_load_address"]),
            "SOLOMON_PLAYTEST_GAMEPLAY": str(hooks["gameplay_address"]),
            "SOLOMON_PLAYTEST_CURRENT_ROOM": str(hooks["current_room_address"]),
            "SOLOMON_PLAYTEST_START_FRAME": str(hooks["start_frame"]),
            "SOLOMON_PLAYTEST_READY_FRAMES": str(hooks["ready_frames"]),
        },
        "project_file": inspection["project_file"],
        "script": {"path": str(lua.resolve()), "sha256": sha256_file(lua)},
        "point": {"room": room, "revision": revision_id},
    })
    return plan


def studio_pacman_round_playtest_plan(path: Path, *, mame: Path, round: int) -> dict[str, Any]:
    """Run the current Pac-Man build from power-on into a one-player game
    that starts at ``round`` (1-23, where the game stops counting)."""

    inspection = load_studio_project(path)
    _require_declared_operation(inspection, "launch")
    if inspection["project"]["platform"] != "nes"             or inspection["project"]["profile"] != "pacman.v1":
        raise StudioProjectError("Pac-Man round playtest requires the pacman.v1 NES profile")
    round = _bounded_integer(round, "round", 1, 23)
    executable = mame.resolve()
    if not executable.is_file():
        raise StudioProjectError(f"MAME executable does not exist: {executable}")
    verification = verify_studio_project(path)
    if verification["status"] != "current":
        raise StudioProjectError("Studio artifact is stale; build it again before round playtest")
    lua = Path(__file__).with_name("runtime") / "pacman" / "playtest.lua"
    if not lua.is_file():
        raise StudioProjectError(f"packaged Pac-Man round playtest script is missing: {lua}")
    artifact = Path(verification["artifact"]["absolute_path"]).resolve()
    plan = launch_image_plan(artifact, platform="nes", mame=executable, machine="nes")
    plan.update({
        "operation": "retrolarium.studio.pacman-round-playtest",
        "backend": "mame-lua",
        "arguments": [
            *plan["arguments"], "-autoboot_delay", "0",
            "-autoboot_script", str(lua.resolve()),
        ],
        "environment": {"PACMAN_PLAYTEST_ROUND": str(round)},
        "project_file": inspection["project_file"],
        "script": {"path": str(lua.resolve()), "sha256": sha256_file(lua)},
        "point": {"round": round},
    })
    return plan


# The shell's reads when Start begins a game (bank 3 CPU addresses).
DORAEMON_START_BUTTONS_READ = 0x83EE
DORAEMON_START_CHAPTER_READ = 0x83FD
# The world start-up stores a playtest point answers (CPU addresses in the
# world's bank): World1_InitializeDemoState's camera, player position and
# area palette ($29),
# Bank1_World2Main's stage index, Bank2_World3Main's two room clears and
# World 3's respawn loader's store of the respawn room (a respawn returns
# to wherever the game shuffled the respawn ring).
DORAEMON_WORLD1_START_WRITES = (0x831C, 0x8320, 0x8324, 0x8328, 0x8342)
# World 1's underground: World1_TryEnterManhole's reads of the buttons and
# the manhole flag, World1_EnterManhole's store of the manhole's object id
# (the room + 8), then the room profile's player X/Y, camera X and axis
# position stores.
DORAEMON_UNDERGROUND_WRITES = (0xD237, 0xD23D, 0xD268, 0xCDF3, 0xCDF8, 0xCE07, 0xCE16)
DORAEMON_UNDERGROUND_GUARDS = {
    0xD237: (0xA5, 0x21), 0xD23D: (0xA5, 0x81), 0xD268: (0x85, 0x81), 0xCDF3: (0x85, 0x75),
    0xCDF8: (0x85, 0x76), 0xCE07: (0x85, 0x5B), 0xCE16: (0x85, 0x89),
}
# World1_UndergroundRoomCameraProfiles ($D1EF: camera X, camera Y, axis
# limit, axis start) and World1_UndergroundRoomEntryProfiles ($D213: player
# X, Y and the two city returns), four bytes a room.
DORAEMON_UNDERGROUND_CAMERAS = 0xD1EF
DORAEMON_UNDERGROUND_ROOMS = 9
DORAEMON_WORLD2_START_WRITES = (0x88B5,)
DORAEMON_WORLD3_START_WRITES = (0x830C, 0x8326, 0x8557)


def _doraemon_underground_rooms(prg_bank0: bytes) -> list[tuple[int, int, int, int]]:
    """Each room's (camera X, camera Y, axis limit, axis start) in cells."""
    base = DORAEMON_UNDERGROUND_CAMERAS - 0x8000
    return [tuple(prg_bank0[base + room * 4:base + room * 4 + 4])
            for room in range(DORAEMON_UNDERGROUND_ROOMS)]


def _doraemon_underground_point(
    block: tuple[int, int], rooms: list[tuple[int, int, int, int]],
) -> tuple[str, dict[str, Any]]:
    x, y = block
    if not (0 <= x < 64 and 0 <= y < 25):
        raise StudioProjectError("World 1 underground blocks are 0-63 across and 0-24 down")
    # The underground atlas is 64x25 blocks of 32 pixels. A room scrolls
    # across from its axis origin (camera X - axis start) by its limit, a
    # screen of 32x30 cells at its fixed camera Y; duplicate entrances
    # (rooms 5/6 and 7/8) share one region, the first one is used.
    cx, cy = x * 4 + 2, y * 4 + 2
    for room, (camera_x, camera_y, limit, start) in enumerate(rooms):
        origin = camera_x - start
        if origin <= cx < origin + limit + 32 and camera_y <= cy < camera_y + 30:
            break
    else:
        raise StudioProjectError("that underground block is not in a room a manhole leads to")
    coarse = max(0, min(limit, cx - origin - 16))
    left = origin + coarse
    sx = max(0x10, min(0xF0, x * 32 + 16 - left * 8))
    sy = max(0x10, min(0xE0, y * 32 + 16 - camera_y * 8))
    return (f"underground {room} {sx} {sy} {left} {coarse}",
            {"world": 1, "underground": [x, y], "room": room, "camera": [left, camera_y],
             "axis": coarse, "player": [sx, sy]})


def _doraemon_playtest_point(
    world: int, block: tuple[int, int] | None, stage: int | None, room: int | None,
    underground: tuple[int, int] | None = None,
    underground_rooms: list[tuple[int, int, int, int]] | None = None,
) -> tuple[str, tuple[int, ...], dict[str, Any] | None]:
    """The Lua point, the store PCs it answers and the plan's description."""
    if world == 1 and underground is not None:
        if block is not None or underground_rooms is None:
            raise StudioProjectError("pick a city block or an underground block, not both")
        point, described = _doraemon_underground_point(underground, underground_rooms)
        return point, DORAEMON_UNDERGROUND_WRITES, described
    if underground is not None:
        raise StudioProjectError("an underground block is for World 1")
    if world == 1 and block is not None:
        x, y = block
        if not (0 <= x < 64 and 0 <= y < 64):
            raise StudioProjectError("World 1 city blocks are 0-63 on each axis")
        # The city is 64x64 blocks of 32 pixels; the camera counts 8-pixel
        # cells (0-$E0 across, 0-$E2 down) and the player is on the screen.
        px, py = x * 32 + 16, y * 32 + 16
        cx = max(0, min(0xE0, (px - 0x78) // 8))
        cy = max(0, min(0xE2, (py - 0x80) // 8))
        sx = max(8, min(0xF0, px - cx * 8))
        sy = max(16, min(0xE0, py - cy * 8))
        # Above camera row $40 is the mountain, reached from the underground,
        # which then sets the area palette $29 to 1.
        area = 1 if cy < 0x40 else 0
        return (f"camera {cx} {cy} {sx} {sy} {area}", DORAEMON_WORLD1_START_WRITES,
                {"world": 1, "block": [x, y], "camera": [cx, cy], "player": [sx, sy], "area": area})
    if world == 2 and stage is not None:
        if stage not in (0, 1, 2):
            raise StudioProjectError("World 2 stages are 0-2")
        return f"stage {stage}", DORAEMON_WORLD2_START_WRITES, {"world": 2, "stage": stage}
    if world == 3 and room is not None:
        if not 1 <= room <= 63:
            raise StudioProjectError("World 3 rooms are 1-63 (room 0 is the start)")
        return f"room {room}", DORAEMON_WORLD3_START_WRITES, {"world": 3, "room": room}
    if block is not None or stage is not None or room is not None:
        raise StudioProjectError("a city block is for World 1, a stage for World 2, a room for World 3")
    return "", (), None


def studio_doraemon_playtest_plan(
    path: Path, *, mame: Path, world: int, block: tuple[int, int] | None = None,
    stage: int | None = None, room: int | None = None, underground: tuple[int, int] | None = None,
) -> dict[str, Any]:
    """Start the current Doraemon build in one world in MAME, through the
    title's chapter select (A and B held with Start), optionally at a city
    block (World 1), a stage (World 2) or a room (World 3)."""

    if world not in (1, 2, 3):
        raise StudioProjectError("Doraemon playtest world must be 1, 2 or 3")
    inspection = load_studio_project(path)
    _require_declared_operation(inspection, "launch")
    if inspection["project"]["platform"] != "nes" or inspection["project"]["profile"] != "doraemon.v1":
        raise StudioProjectError("Doraemon playtest requires the doraemon.v1 NES profile")
    executable = mame.resolve()
    if not executable.is_file():
        raise StudioProjectError(f"MAME executable does not exist: {executable}")
    verification = verify_studio_project(path)
    if verification["status"] != "current":
        raise StudioProjectError("Studio artifact is stale; build it again before the playtest")
    artifact = Path(verification["artifact"]["absolute_path"]).resolve()
    source = artifact.read_bytes()
    # Bank 3's game start: LDA $21 / AND #$C0 / CMP #$C0, then LDX $3C.
    shell = 16 + 3 * 0x8000 - 0x8000
    if source[shell + DORAEMON_START_BUTTONS_READ:shell + DORAEMON_START_BUTTONS_READ + 6] \
            != bytes((0xA5, 0x21, 0x29, 0xC0, 0xC9, 0xC0)) \
            or source[shell + DORAEMON_START_CHAPTER_READ:shell + DORAEMON_START_CHAPTER_READ + 2] != bytes((0xA6, 0x3C)):
        raise StudioProjectError("Doraemon build does not match the guarded chapter start")
    bank = 16 + (world - 1) * 0x8000 - 0x8000
    point, writes, described = _doraemon_playtest_point(
        world, block, stage, room, underground,
        _doraemon_underground_rooms(source[16:16 + 0x8000]) if underground is not None else None)
    if writes == DORAEMON_UNDERGROUND_WRITES:
        for pc, expected in DORAEMON_UNDERGROUND_GUARDS.items():
            if source[bank + pc:bank + pc + 2] != bytes(expected):
                raise StudioProjectError("Doraemon build does not match the guarded manhole entry")
        writes_checked: tuple[int, ...] = ()
    else:
        writes_checked = writes
    # The stores the point answers: LDA #imm / STA zp in the world's bank.
    for pc in writes_checked:
        target = {0x831C: 0x5B, 0x8320: 0x5C, 0x8324: 0x75, 0x8328: 0x76, 0x8342: 0x29,
                  0x88B5: 0xA9, 0x830C: 0xDF, 0x8326: 0xDF, 0x8557: 0xDF}[pc]
        if source[bank + pc:bank + pc + 2] != bytes((0x85, target)):
            raise StudioProjectError("Doraemon build does not match the guarded world start")
    lua = Path(__file__).with_name("runtime") / "doraemon" / "playtest.lua"
    if not lua.is_file():
        raise StudioProjectError(f"packaged Doraemon playtest script is missing: {lua}")
    plan = launch_image_plan(artifact, platform="nes", mame=executable, machine="nes")
    plan.update({
        "operation": "retrolarium.studio.doraemon-playtest",
        "backend": "mame-lua",
        "arguments": [
            *plan["arguments"], "-autoboot_delay", "0",
            "-autoboot_script", str(lua.resolve()),
        ],
        "environment": {
            "DORAEMON_PLAYTEST_WORLD": str(world),
            "DORAEMON_PLAYTEST_BUTTONS_READ": str(DORAEMON_START_BUTTONS_READ),
            "DORAEMON_PLAYTEST_CHAPTER_READ": str(DORAEMON_START_CHAPTER_READ),
            "DORAEMON_PLAYTEST_POINT": point,
            "DORAEMON_PLAYTEST_WRITES": ",".join(str(pc) for pc in writes),
        },
        "project_file": inspection["project_file"],
        "script": {"path": str(lua.resolve()), "sha256": sha256_file(lua)},
        "point": described or {"world": world},
    })
    return plan


def launch_studio_smoke(
    path: Path,
    *,
    mame: Path,
    output: Path | None = None,
    machine: str | None = None,
    seconds: int = 2,
    timeout: int = 60,
) -> dict[str, Any]:
    inspection = load_studio_project(path)
    _require_declared_operation(inspection, "launch")
    verification = verify_studio_project(path)
    if verification["status"] != "current":
        raise StudioProjectError("Studio artifact is stale; build it again before launch")
    root = Path(inspection["project_root"])
    return launch_image_smoke(
        Path(verification["artifact"]["absolute_path"]),
        platform=inspection["project"]["platform"],
        mame=mame,
        output=(output or root / "build" / "runs"),
        machine=machine,
        seconds=seconds,
        timeout=timeout,
    )
