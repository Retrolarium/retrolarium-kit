"""What the Alien Soldier playtest script needs, read from the source: the
RAM it watches (by name, from the source's equates) and each stage's
password.

`Password_StageCodeTable` holds two four-byte codes for each stage the
password screen opens (the easy one first), ended by $FFFF $FFFF; the
password screen sets StageTableIndex to twice the stage's place in it.
"""

from __future__ import annotations

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
    "DemoCurrentInputWord",
    "DemoInputRunFrames",
    "ControllerHeldState",
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


def environment(snapshot: Path, revision: str, stage: int) -> dict[str, str]:
    """The playtest script's settings for stage slot `stage` (0-based)."""
    tree = workspace.tree(snapshot, workspace.source_profile(snapshot), revision)
    codes = passwords(tree)
    if not 0 <= stage < len(codes):
        raise PlaytestError(f"the password screen opens stages 1..{len(codes)}, not {stage + 1}")
    return {
        "ALIEN_PLAYTEST_STAGE_INDEX": str(2 * stage),
        "ALIEN_PLAYTEST_PASSWORD": f"{codes[stage]:08X}",
        **ram_environment(tree, RAM),
    }


def demo_environment(snapshot: Path, revision: str, demo: int) -> dict[str, str]:
    """The demo script's settings for demo `demo` (0..3)."""
    if not 0 <= demo <= 3:
        raise PlaytestError("the game has demos 1..4")
    tree = workspace.tree(snapshot, workspace.source_profile(snapshot), revision)
    return {"ALIEN_DEMO_INDEX": str(demo), **ram_environment(tree, DEMO_RAM)}
