"""The kit's import rules, checked on the source.

core < platform < games < studio < cli: a module imports its own layer or a
lower one. The kit stands without the disassembler. Platforms and games stay
apart: the Disk System is NES hardware, so platform.nes may use platform.fds
(not the other way round), and a game never imports another game, except the
two dispatchers that exist to reach them all (games.catalog, games.nes_codecs).
"""

from __future__ import annotations

import ast
from pathlib import Path
import unittest

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "retrolarium_kit"
LAYERS = {"core": 0, "platform": 1, "games": 2, "studio": 3, "cli": 4}
DISPATCHERS = {("games", "catalog"), ("games", "nes_codecs")}


def imports() -> list[tuple[tuple[str, ...], tuple[str, ...], int]]:
    """(importing module path, imported module path, line) inside the kit, lazy imports included."""
    found = []
    for path in sorted(PACKAGE.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        module = path.relative_to(PACKAGE).with_suffix("").parts
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
                if node.module == "retrolarium_kit" or node.module.count(".") == 1:
                    # from retrolarium_kit.games import catalog: the submodule.
                    names += [f"{node.module}.{alias.name}" for alias in node.names]
            elif isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            for name in names:
                found.append((module, tuple(name.split(".")), node.lineno))
    return found


class LayerTests(unittest.TestCase):
    def test_the_kit_does_not_import_the_disassembler(self) -> None:
        offenders = [
            f"{'/'.join(module)}:{line}"
            for module, target, line in imports()
            if target[0] == "retrolarium_disassembler"
        ]
        self.assertEqual([], offenders)

    def test_layers_import_downwards(self) -> None:
        offenders = []
        for module, target, line in imports():
            if target[0] != "retrolarium_kit" or len(target) < 2:
                continue
            source, imported = module[0], target[1]
            if source in LAYERS and imported in LAYERS and LAYERS[imported] > LAYERS[source]:
                offenders.append(f"{'.'.join(module)}:{line} imports {'.'.join(target)}")
        self.assertEqual([], offenders)

    def test_platforms_stay_apart(self) -> None:
        offenders = []
        for module, target, line in imports():
            if module[0] != "platform" or target[:2] != ("retrolarium_kit", "platform") or len(target) < 3:
                continue
            if module[1] != target[2] and not (module[1] == "nes" and target[2] == "fds"):
                offenders.append(f"{'.'.join(module)}:{line} imports {'.'.join(target)}")
        self.assertEqual([], offenders)

    def test_games_stay_apart(self) -> None:
        offenders = []
        for module, target, line in imports():
            if module[0] != "games" or module[:2] in DISPATCHERS:
                continue
            if target[:2] != ("retrolarium_kit", "games") or len(target) < 3:
                continue
            if ("games", target[2]) in DISPATCHERS or target[2] != module[1]:
                offenders.append(f"{'.'.join(module)}:{line} imports {'.'.join(target)}")
        self.assertEqual([], offenders)

    def test_every_game_registers_its_nes_codecs_through_the_provider(self) -> None:
        provider = (PACKAGE / "games" / "nes_codecs.py").read_text(encoding="utf-8")
        for codecs in sorted(PACKAGE.glob("games/*/codecs.py")):
            self.assertIn(f"retrolarium_kit.games.{codecs.parent.name} import codecs", provider)


if __name__ == "__main__":
    unittest.main()
