"""What the Alien Soldier playtest script needs, read from the source: the
RAM it watches (by name, from the source's equates) and each stage's
password.

`Password_StageCodeTable` holds two four-byte codes for each stage the
password screen opens (the easy one first), ended by $FFFF $FFFF; the
password screen sets StageTableIndex to twice the stage's place in it.
"""

from __future__ import annotations

import json
from pathlib import Path

from retrolarium_kit.games.alien_soldier import workspace
from retrolarium_kit.platform.megadrive.asl import SourceTree


PASSWORDS = "Password_StageCodeTable"
RAM = (
    "GameModeIndex",
    "GameSubstateIndex",
    "StageTableIndex",
    "DifficultyMode",
    "PasswordDigits",
    "PasswordCursorOffset",
    "PasswordCursorMoveFlag",
    "PlayerObjectType",
    "PaletteFadeMaskStatus",
    "ScenePaletteFadeOffset",
    "DataLoaderControl",
    "PaletteFadeColorOffset",
    "PaletteFadeMode",
    "SetupTransitionIndex",
    "WeaponSetupVScrollTarget",
    "SecondaryCameraYPos",
    "PlayerHealth",
    "PlayerMaxHealth",
    "PlayerYPosition",
)


class PlaytestError(ValueError):
    """A stage the password screen cannot open, or a source without what the
    script needs."""


def passwords(tree: SourceTree) -> list[int]:
    """The easy code of each stage the password screen opens, in order."""
    words: list[int] = []
    for label in tree.order[tree.order.index(PASSWORDS) :]:
        block = tree.block(label)
        if block.kind != "dc.w":
            break
        words += block.values()
        if words[-2:] == [0xFFFF, 0xFFFF]:
            break
    if words[-2:] != [0xFFFF, 0xFFFF] or (len(words) - 2) % 4:
        raise PlaytestError(f"{PASSWORDS} is not two codes a stage, ended by $FFFF $FFFF")
    return [(words[at] << 16) | words[at + 1] for at in range(0, len(words) - 2, 4)]


DEMO_RAM = (
    "GameModeIndex",
    "FrameCounter",
    "DemoRotationIndex",
    "DemoPlaybackActive",
    "DemoPlaybackInputWord",
    "DemoInputRunFrames",
    "ControllerHeldState",
    "ControllerPressedState",
    "PaletteFadeMaskStatus",
    "ScenePaletteFadeOffset",
    "DataLoaderControl",
)


def ram_environment(tree: SourceTree, names: tuple[str, ...]) -> dict[str, str]:
    """ALIEN_RAM_<name> for each name, from the source's equates."""
    settings = {}
    for name in names:
        if name not in tree.equates:
            raise PlaytestError(f"the source does not name {name}")
        settings[f"ALIEN_RAM_{name}"] = f"{tree.equates[name] & 0xFFFFFF:06X}"
    return settings


def _runtime(snapshot: Path, revision: str) -> dict:
    # The plan verifies source integrity before reaching this function. Keep
    # only immutable source-derived RAM constants and passwords across runs.
    from retrolarium_kit.games.alien_soldier import content

    key = content._cache_key(snapshot)
    path = snapshot.parent / "build/alien-soldier-runtime.json"
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
        if cached.get("key") == key and cached.get("revision") == revision:
            return cached
    except (OSError, ValueError):
        pass
    tree = workspace.tree(snapshot, workspace.source_profile(snapshot), revision)
    cached = {
        "key": key,
        "revision": revision,
        "passwords": passwords(tree),
        "ram": ram_environment(tree, tuple(dict.fromkeys(RAM + DEMO_RAM))),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic publication also keeps simultaneous preview requests independent.
    import os
    import tempfile

    descriptor, temporary = tempfile.mkstemp(prefix=".runtime-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(cached, stream)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return cached


def environment(snapshot: Path, revision: str, stage: int) -> dict[str, str]:
    """The playtest script's settings for stage slot `stage` (0-based)."""
    cached = _runtime(snapshot, revision)
    codes = cached["passwords"]
    if not 0 <= stage < len(codes):
        raise PlaytestError(f"the password screen opens stages 1..{len(codes)}, not {stage + 1}")
    return {
        "ALIEN_PLAYTEST_STAGE_INDEX": str(2 * stage),
        "ALIEN_PLAYTEST_PASSWORD": f"{codes[stage]:08X}",
        "ALIEN_PLAYTEST_INVINCIBLE": "1",
        **{f"ALIEN_RAM_{name}": cached["ram"][f"ALIEN_RAM_{name}"] for name in RAM},
    }


def demo_environment(snapshot: Path, revision: str, demo: int) -> dict[str, str]:
    """The demo script's settings for demo `demo` (0..3)."""
    if not 0 <= demo <= 3:
        raise PlaytestError("the game has demos 1..4")
    cached = _runtime(snapshot, revision)
    return {
        "ALIEN_DEMO_INDEX": str(demo),
        **{f"ALIEN_RAM_{name}": cached["ram"][f"ALIEN_RAM_{name}"] for name in DEMO_RAM},
    }
