"""Attract-demo plans: MAME runs that play a game's recorded demo inputs, edited
in the Studio, from the title screen."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from retrolarium_kit.core.game_profiles import game_profiles
from retrolarium_kit.core.project import launch_image_plan, sha256_file
from retrolarium_kit.studio.project import (
    StudioProjectError,
    _require_declared_operation,
    load_studio_project,
    verify_studio_project,
)
from retrolarium_kit.studio.playtest import (
    _bounded_integer,
    _fds_program_at,
)


def studio_flicky_demo_plan(
    path: Path, *, mame: Path, demo: int, record: Path | None = None,
) -> dict[str, Any]:
    """Loop one attract demo (1-4) of the current Flicky build in MAME, or
    record a new one from the player's controller into ``record``."""

    inspection = load_studio_project(path)
    _require_declared_operation(inspection, "launch")
    project = inspection["project"]
    if project["source_mode"] != "flicky-content"             or project["platform"] != "megadrive"             or project["profile"] != "flicky-ue.v1":
        raise StudioProjectError(
            "Flicky demo playtest requires the canonical flicky-ue.v1 source profile"
        )
    demo = _bounded_integer(demo, "demo", 1, 4)
    executable = mame.resolve()
    if not executable.is_file():
        raise StudioProjectError(f"MAME executable does not exist: {executable}")
    verification = verify_studio_project(path)
    if verification["status"] != "current":
        raise StudioProjectError("Studio artifact is stale; build it again before the demo")
    lua = Path(__file__).with_name("runtime") / "flicky" / "demo.lua"
    if not lua.is_file():
        raise StudioProjectError(f"packaged Flicky demo script is missing: {lua}")
    artifact = Path(verification["artifact"]["absolute_path"])
    plan = launch_image_plan(
        artifact, platform="megadrive", mame=executable, machine="genesis",
    )
    recording = "" if record is None else str(record.resolve())
    plan.update({
        "operation": "retrolarium.studio.flicky-demo",
        "backend": "mame-lua",
        "arguments": [
            *plan["arguments"], "-autoboot_delay", "0",
            "-autoboot_script", str(lua.resolve()),
        ],
        "environment": {
            "FLICKY_DEMO_INDEX": str(demo - 1),
            "FLICKY_DEMO_RECORD": recording,
        },
        "project_file": inspection["project_file"],
        "script": {"path": str(lua.resolve()), "sha256": sha256_file(lua)},
        "demo": {"index": demo, "record": recording or None},
    })
    return plan


# Attract-demo hooks per revision: the demo input thread's entry and the
# post-game thread's entry.
SOLOMON_DEMO_HOOKS = {
    "usa": {"run": 0xCB2B, "post_game": 0xCA6E},
    "europe": {"run": 0xCABB, "post_game": 0xC9FE},
    "japan": {"run": 0xCBDC, "post_game": 0xCB6E},
}
SOLOMON_DEMO_RUN_GUARD = bytes.fromhex("a90085228581")

# SMB JU: the title demo's exit to the title screen and the RAM the demo
# script reads and writes (the tables and the engine come from the profile).
SMB_DEMO_HOOKS = {
    "saved_joypad": 0x06FC, "demo_action": 0x0717, "action_timer": 0x0718,
    "oper_mode": 0x0770,
}
# Where each version's title goes back to the start when its demo is over;
# the RAM above is the same in all of them.
SMB_DEMO_RESET_TITLE = {
    "ju": 0x82C9, "pc10": 0x82C9, "pal": 0x82C9,
    "fds_smb": 0x62CF, "ann_fds": 0xC497, "smb2_jp_fds": 0xC4DD,
}


def studio_smb_demo_plan(path: Path, *, mame: Path, record: Path | None = None) -> dict[str, Any]:
    """Loop the SMB title demo of the current build in MAME, or record a new
    one from the player's controller into ``record``."""

    inspection = load_studio_project(path)
    _require_declared_operation(inspection, "launch")
    if inspection["project"]["platform"] != "nes" or inspection["project"]["profile"] != "smb1.v1":
        raise StudioProjectError("SMB demo playtest requires the smb1.v1 NES profile")
    profile = game_profiles().get("smb1")
    revision = next(
        (item for item in profile.revisions if item.sha256 == inspection["source"]["sha256"]), None
    )
    if revision is None or revision.revision_id not in SMB_DEMO_RESET_TITLE:
        raise StudioProjectError("The demo does not play in MAME in this version.")
    demo = next(item for item in profile.resources_for(revision) if item.codec == "smb.demo-input.v1")
    tables = demo.parameters
    executable = mame.resolve()
    if not executable.is_file():
        raise StudioProjectError(f"MAME executable does not exist: {executable}")
    verification = verify_studio_project(path)
    if verification["status"] != "current":
        raise StudioProjectError("Studio artifact is stale; build it again before the demo")
    artifact = Path(verification["artifact"]["absolute_path"]).resolve()
    if artifact.suffix.lower() == ".fds":
        source, engine = _fds_program_at(artifact, tables["engine_cpu"])
    else:
        source = artifact.read_bytes()
        engine = 16 + tables["engine_cpu"] - 0x8000
    expected = (tables["durations_cpu"] - 1).to_bytes(2, "little")
    if engine < 0 or source[engine:engine + 3] != b"\xae\x17\x07" \
            or source[engine + 14:engine + 16] != expected:
        raise StudioProjectError("SMB build does not match the guarded demo layout")
    lua = Path(__file__).with_name("runtime") / "smb" / "demo.lua"
    if not lua.is_file():
        raise StudioProjectError(f"packaged SMB demo script is missing: {lua}")
    plan = launch_image_plan(artifact, platform="nes", mame=executable,
                             machine="nespal" if revision.revision_id == "pal" else "nes")
    recording = "" if record is None else str(record.resolve())
    plan.update({
        "operation": "retrolarium.studio.smb-demo",
        "backend": "mame-lua",
        "arguments": [
            *plan["arguments"], "-autoboot_delay", "0",
            "-autoboot_script", str(lua.resolve()),
        ],
        "environment": {
            "SMB_DEMO_ENGINE": str(tables["engine_cpu"]),
            "SMB_DEMO_RESET_TITLE": str(SMB_DEMO_RESET_TITLE[revision.revision_id]),
            "SMB_DEMO_ACTIONS": str(tables["actions_cpu"]),
            "SMB_DEMO_DURATIONS": str(tables["durations_cpu"]),
            "SMB_DEMO_CAPACITY": str(tables["capacity"]),
            "SMB_DEMO_SAVED_JOYPAD": str(SMB_DEMO_HOOKS["saved_joypad"]),
            "SMB_DEMO_ACTION": str(SMB_DEMO_HOOKS["demo_action"]),
            "SMB_DEMO_ACTION_TIMER": str(SMB_DEMO_HOOKS["action_timer"]),
            "SMB_DEMO_OPER_MODE": str(SMB_DEMO_HOOKS["oper_mode"]),
            "SMB_DEMO_RECORD": recording,
        },
        "project_file": inspection["project_file"],
        "script": {"path": str(lua.resolve()), "sha256": sha256_file(lua)},
        "demo": {"revision": revision.revision_id, "record": recording or None},
    })
    return plan


# Castle Excellent: the RAM flag that plays the demo in place of controller 1.
CASTLE_EXCELLENT_DEMO_ACTIVE = 0x07C7


def studio_castle_excellent_demo_plan(path: Path, *, mame: Path, record: Path | None = None) -> dict[str, Any]:
    """Loop the Castle Excellent title demo of the current build in MAME, or
    record a new one from the player's controller into ``record``."""

    inspection = load_studio_project(path)
    _require_declared_operation(inspection, "launch")
    if inspection["project"]["platform"] != "nes" \
            or inspection["project"]["profile"] != "castle-excellent.v1":
        raise StudioProjectError("Castle Excellent demo playtest requires the castle-excellent.v1 NES profile")
    profile = game_profiles().get("castle-excellent")
    revision = next(
        (item for item in profile.revisions if item.sha256 == inspection["source"]["sha256"]), None
    )
    if revision is None:
        raise StudioProjectError("Castle Excellent demo playtest needs a known Castle Excellent source")
    demo = next(item for item in profile.resources_for(revision) if item.codec == "castle-excellent.title-demo.v1")
    layout = demo.parameters
    executable = mame.resolve()
    if not executable.is_file():
        raise StudioProjectError(f"MAME executable does not exist: {executable}")
    verification = verify_studio_project(path)
    if verification["status"] != "current":
        raise StudioProjectError("Studio artifact is stale; build it again before the demo")
    artifact = Path(verification["artifact"]["absolute_path"]).resolve()
    source = artifact.read_bytes()
    init = 16 + layout["init_cpu"] - 0x8000
    script = layout["script_cpu"]
    reader = 16 + layout["reader_cpu"] - 0x8000
    if source[init:init + 8] != bytes((0xA9, script & 0xFF, 0x85, 0xFD, 0xA9, script >> 8, 0x85, 0xFE)) \
            or source[reader + 10:reader + 12] != b"\xb1\xfd":
        raise StudioProjectError("Castle Excellent build does not match the guarded demo layout")
    lua = Path(__file__).with_name("runtime") / "castle_excellent" / "demo.lua"
    if not lua.is_file():
        raise StudioProjectError(f"packaged Castle Excellent demo script is missing: {lua}")
    plan = launch_image_plan(artifact, platform="nes", mame=executable,
                             machine="nespal" if revision.revision_id == "pal" else "nes")
    recording = "" if record is None else str(record.resolve())
    plan.update({
        "operation": "retrolarium.studio.castle-excellent-demo",
        "backend": "mame-lua",
        "arguments": [
            *plan["arguments"], "-autoboot_delay", "0",
            "-autoboot_script", str(lua.resolve()),
        ],
        "environment": {
            "CASTLE_EXCELLENT_DEMO_SCRIPT": str(script),
            "CASTLE_EXCELLENT_DEMO_READER": str(layout["reader_cpu"]),
            "CASTLE_EXCELLENT_DEMO_CAPACITY": str(layout["capacity"]),
            "CASTLE_EXCELLENT_DEMO_ACTIVE": str(CASTLE_EXCELLENT_DEMO_ACTIVE),
            "CASTLE_EXCELLENT_DEMO_RECORD": recording,
        },
        "project_file": inspection["project_file"],
        "script": {"path": str(lua.resolve()), "sha256": sha256_file(lua)},
        "demo": {"revision": revision.revision_id, "record": recording or None},
    })
    return plan


def studio_pacman_demo_plan(path: Path, *, mame: Path, record: Path | None = None) -> dict[str, Any]:
    """Loop the Pac-Man attract demo of the current build in MAME, or record
    a new one from the player's controller into ``record``."""

    inspection = load_studio_project(path)
    _require_declared_operation(inspection, "launch")
    if inspection["project"]["platform"] != "nes"             or inspection["project"]["profile"] != "pacman.v1":
        raise StudioProjectError("Pac-Man demo playtest requires the pacman.v1 NES profile")
    profile = game_profiles().get("pacman")
    revision = next(
        (item for item in profile.revisions if item.sha256 == inspection["source"]["sha256"]), None
    )
    if revision is None:
        raise StudioProjectError("Pac-Man demo playtest needs a known Pac-Man source")
    demo = next(item for item in profile.resources_for(revision) if item.codec == "pacman.attract-demo.v1")
    layout = demo.parameters
    executable = mame.resolve()
    if not executable.is_file():
        raise StudioProjectError(f"MAME executable does not exist: {executable}")
    verification = verify_studio_project(path)
    if verification["status"] != "current":
        raise StudioProjectError("Studio artifact is stale; build it again before the demo")
    artifact = Path(verification["artifact"]["absolute_path"]).resolve()
    source = artifact.read_bytes()
    script = 16 + layout["script_cpu"] - 0xC000
    table = layout["table_cpu"]
    if source[script + 12:script + 15] != bytes((0xB9, table & 0xFF, table >> 8)):
        raise StudioProjectError("Pac-Man build does not match the guarded demo layout")
    lua = Path(__file__).with_name("runtime") / "pacman" / "demo.lua"
    if not lua.is_file():
        raise StudioProjectError(f"packaged Pac-Man demo script is missing: {lua}")
    plan = launch_image_plan(artifact, platform="nes", mame=executable,
                             machine="nespal" if revision.revision_id == "pal" else "nes")
    recording = "" if record is None else str(record.resolve())
    plan.update({
        "operation": "retrolarium.studio.pacman-demo",
        "backend": "mame-lua",
        "arguments": [
            *plan["arguments"], "-autoboot_delay", "0",
            "-autoboot_script", str(lua.resolve()),
        ],
        "environment": {
            "PACMAN_DEMO_SCRIPT": str(layout["script_cpu"]),
            "PACMAN_DEMO_TABLE": str(table),
            "PACMAN_DEMO_PAIRS": str(layout["pairs"]),
            "PACMAN_DEMO_RECORD": recording,
        },
        "project_file": inspection["project_file"],
        "script": {"path": str(lua.resolve()), "sha256": sha256_file(lua)},
        "demo": {"revision": revision.revision_id, "record": recording or None},
    })
    return plan


def studio_doraemon_demo_plan(path: Path, *, mame: Path, record: Path | None = None) -> dict[str, Any]:
    """Loop World 1's attract demo of the current Doraemon build in MAME, or
    record a new one from the player's controller into ``record``."""

    from retrolarium_kit.games.doraemon.demo import CODEC as DEMO_CODEC, capacity

    inspection = load_studio_project(path)
    _require_declared_operation(inspection, "launch")
    if inspection["project"]["platform"] != "nes" or inspection["project"]["profile"] != "doraemon.v1":
        raise StudioProjectError("Doraemon demo playtest requires the doraemon.v1 NES profile")
    profile = game_profiles().get("doraemon")
    revision = next(
        (item for item in profile.revisions if item.sha256 == inspection["source"]["sha256"]), None
    )
    if revision is None:
        raise StudioProjectError("Doraemon demo playtest needs a known Doraemon source")
    layout = next(item for item in profile.resources_for(revision) if item.codec == DEMO_CODEC).parameters
    executable = mame.resolve()
    if not executable.is_file():
        raise StudioProjectError(f"MAME executable does not exist: {executable}")
    verification = verify_studio_project(path)
    if verification["status"] != "current":
        raise StudioProjectError("Studio artifact is stale; build it again before the demo")
    artifact = Path(verification["artifact"]["absolute_path"]).resolve()
    source = artifact.read_bytes()
    stream = layout["stream_cpu"]
    entry = 16 + layout["bank"] * 0x8000 + layout["entry_cpu"] - 0x8000
    reader = 16 + layout["bank"] * 0x8000 + layout["reader_cpu"] + 46 - 0x8000
    if source[entry:entry + 8] != bytes((0xA9, stream & 0xFF, 0x85, 0xA6, 0xA9, stream >> 8, 0x85, 0xA7)) \
            or source[reader:reader + 2] != bytes((0x85, 0x21)):
        raise StudioProjectError("Doraemon build does not match the guarded demo layout")
    lua = Path(__file__).with_name("runtime") / "doraemon" / "demo.lua"
    if not lua.is_file():
        raise StudioProjectError(f"packaged Doraemon demo script is missing: {lua}")
    plan = launch_image_plan(artifact, platform="nes", mame=executable,
                             machine="nespal" if revision.revision_id == "pal" else "nes")
    recording = "" if record is None else str(record.resolve())
    plan.update({
        "operation": "retrolarium.studio.doraemon-demo",
        "backend": "mame-lua",
        "arguments": [
            *plan["arguments"], "-autoboot_delay", "0",
            "-autoboot_script", str(lua.resolve()),
        ],
        "environment": {
            "DORAEMON_DEMO_ENTRY": str(layout["entry_cpu"]),
            "DORAEMON_DEMO_READER": str(layout["reader_cpu"]),
            "DORAEMON_DEMO_STEPS": str(capacity(layout)),
            "DORAEMON_DEMO_RECORD": recording,
        },
        "project_file": inspection["project_file"],
        "script": {"path": str(lua.resolve()), "sha256": sha256_file(lua)},
        "demo": {"revision": revision.revision_id, "record": recording or None},
    })
    return plan


def studio_solomon_demo_plan(
    path: Path, *, mame: Path, record: Path | None = None,
) -> dict[str, Any]:
    """Loop the attract demo of the current build in MAME, or record a new one
    from the player's controller into ``record``."""

    inspection = load_studio_project(path)
    _require_declared_operation(inspection, "launch")
    if inspection["project"]["platform"] != "nes" \
            or inspection["project"]["profile"] != "solomons-key.v1":
        raise StudioProjectError("Solomon demo playtest requires the solomons-key.v1 NES profile")
    profile = game_profiles().get("solomons-key")
    revision = next(
        (item for item in profile.revisions if item.sha256 == inspection["source"]["sha256"]), None
    )
    if revision is None or revision.revision_id not in SOLOMON_DEMO_HOOKS:
        raise StudioProjectError("Solomon demo playtest needs the USA or Europe source")
    hooks = SOLOMON_DEMO_HOOKS[revision.revision_id]
    executable = mame.resolve()
    if not executable.is_file():
        raise StudioProjectError(f"MAME executable does not exist: {executable}")
    verification = verify_studio_project(path)
    if verification["status"] != "current":
        raise StudioProjectError("Studio artifact is stale; build it again before the demo")
    artifact = Path(verification["artifact"]["absolute_path"]).resolve()
    source = artifact.read_bytes()
    offset = 16 + hooks["run"] - 0x8000
    # An expanded build (solomon_expansion) runs the game from its second bank.
    if len(source) == 16 + 0x10000 + 0x8000 and ((source[6] >> 4) | (source[7] & 0xF0)) == 66:
        offset += 0x8000
    if source[offset:offset + len(SOLOMON_DEMO_RUN_GUARD)] != SOLOMON_DEMO_RUN_GUARD             or source[offset + 0x1A] != 0xBD or source[offset + 0x21] != 0xE0:
        raise StudioProjectError("Solomon build does not match the guarded demo layout")
    # The demo's step tables as this build has them (LDA durations,X and
    # CPX #count in the playback loop).
    durations = source[offset + 0x1B] | (source[offset + 0x1C] << 8)
    steps = source[offset + 0x22]
    lua = Path(__file__).with_name("runtime") / "solomon" / "demo.lua"
    if not lua.is_file():
        raise StudioProjectError(f"packaged Solomon demo script is missing: {lua}")
    plan = launch_image_plan(
        artifact, platform="nes", mame=executable,
        machine="nespal" if revision.revision_id == "europe" else "nes",
    )
    recording = "" if record is None else str(record.resolve())
    plan.update({
        "operation": "retrolarium.studio.solomon-demo",
        "backend": "mame-lua",
        "arguments": [
            *plan["arguments"], "-autoboot_delay", "0",
            "-autoboot_script", str(lua.resolve()),
        ],
        "environment": {
            "SOLOMON_DEMO_RUN": str(hooks["run"]),
            "SOLOMON_DEMO_POST_GAME": str(hooks["post_game"]),
            "SOLOMON_DEMO_DURATIONS": str(durations),
            "SOLOMON_DEMO_STEPS": str(steps),
            "SOLOMON_DEMO_JOYPAD_RAW": str(0x0082),
            "SOLOMON_DEMO_INDEX": str(0x0081),
            "SOLOMON_DEMO_NMI_COUNTER": str(0x0022),
            "SOLOMON_DEMO_RECORD": recording,
        },
        "project_file": inspection["project_file"],
        "script": {"path": str(lua.resolve()), "sha256": sha256_file(lua)},
        "demo": {"revision": revision.revision_id, "record": recording or None},
    })
    return plan
