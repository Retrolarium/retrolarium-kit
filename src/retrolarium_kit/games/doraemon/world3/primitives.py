"""Semantic material definitions and transactional native packing allocation."""

from __future__ import annotations
from collections.abc import Mapping, Sequence
from copy import deepcopy
from retrolarium_kit.core.authoring import AuthoringError

SHAPES = ("fill", "n", "e", "s", "w", "nw", "ne", "sw", "se")


def small_grid(hierarchy: Mapping, cells: Sequence[Sequence[int]]) -> list[list[int]]:
    grid = [[0] * 16 for _ in range(16)]
    for y, row in enumerate(cells):
        for x, block in enumerate(row):
            for offset, small in enumerate(hierarchy["big_blocks"][block]):
                grid[y * 2 + offset // 2][x * 2 + offset % 2] = small
    return grid


def validate_catalogue(catalogue: Mapping) -> None:
    if not isinstance(catalogue, Mapping) or catalogue.get("schema") != "doraemon.world3-primitives.v1":
        raise AuthoringError("expected a World 3 primitive catalogue")

    def ids(values):
        return isinstance(values, list) and bool(values) and all(type(i) is int and 0 <= i < 256 for i in values)

    families = catalogue.get("families")
    if not isinstance(families, Mapping) or not {"wet-rock", "dry-rock", "layered-rock"} <= families.keys():
        raise AuthoringError("the primitive catalogue needs wet, dry and layered rock families")
    for family in families.values():
        if (
            not isinstance(family, Mapping)
            or not isinstance(family.get("shapes"), Mapping)
            or set(family["shapes"]) != set(SHAPES)
        ):
            raise AuthoringError("a rock family needs fill, four edges and four convex corners")
        for values in family["shapes"].values():
            if not ids(values) or len(values) != 2:
                raise AuthoringError("each primitive shape needs two valid vertical phase IDs")
    backgrounds = catalogue.get("backgrounds")
    if (
        not isinstance(backgrounds, Mapping)
        or not {"sky", "water"} <= backgrounds.keys()
        or not ids(list(backgrounds.values()))
    ):
        raise AuthoringError("the primitive catalogue needs valid sky and water backgrounds")
    if not ids(catalogue.get("bricks")) or not ids(catalogue.get("supported_cover")):
        raise AuthoringError("the primitive catalogue needs valid brick and supported-cover IDs")
    if catalogue.get("protected_small") and not ids(catalogue["protected_small"]):
        raise AuthoringError("invalid protected primitive IDs")
    if not isinstance(catalogue.get("sky_rows", {}), Mapping) or any(
        type(value) is not int or not 0 <= value <= 16 for value in catalogue.get("sky_rows", {}).values()
    ):
        raise AuthoringError("sky bands need integer heights from 0 to 16 primitives")
    recipes = catalogue.get("derived_small", [])
    if not isinstance(recipes, list):
        raise AuthoringError("derived primitive recipes must be a list")
    for recipe in recipes:
        if (
            not isinstance(recipe, Mapping)
            or recipe.get("family") not in families
            or recipe.get("shape") not in SHAPES
            or type(recipe.get("phase")) is not int
            or recipe["phase"] not in (0, 1)
            or type(recipe.get("palette")) is not int
            or not 0 <= recipe["palette"] < 4
            or not ids([recipe.get(key) for key in ("source", "replace", "background")])
        ):
            raise AuthoringError("invalid derived primitive recipe")


class PrimitivePainter:
    """Allocate only unused definition slots from a caller-owned ROM."""

    def __init__(self, hierarchy: Mapping, catalogue: Mapping):
        validate_catalogue(catalogue)
        self.hierarchy = deepcopy(hierarchy)
        self.catalogue = deepcopy(catalogue)
        self.used = {block for item in hierarchy["maps"] for row in item["rows"] for block in row}
        used_small = {small for block in self.used for small in hierarchy["big_blocks"][block]}
        used_small.update(
            small
            for family in catalogue["families"].values()
            for values in family["shapes"].values()
            for small in values
        )
        used_small.update(catalogue.get("protected_small", []))
        used_small.update(catalogue["backgrounds"].values())
        used_small.update(catalogue["bricks"])
        used_small.update(catalogue["supported_cover"])
        used_small.update(recipe["source"] for recipe in catalogue.get("derived_small", []))
        # Palette and background substitutions reuse the user's CHR. They do
        # not copy pixel bytes into the catalogue or install new CHR tiles.
        for recipe in catalogue.get("derived_small", []):
            source = recipe["source"]
            tiles = [
                recipe["background"] if tile == recipe["replace"] else tile
                for tile in hierarchy["small_blocks"][source]
            ]
            attr = {**hierarchy["attributes"][source], "palette": recipe["palette"]}
            matches = [
                i
                for i in range(256)
                if self.hierarchy["small_blocks"][i] == tiles and self.hierarchy["attributes"][i] == attr
            ]
            target = matches[0] if matches else next((i for i in range(256) if i not in used_small), None)
            if target is None:
                raise AuthoringError("no unused small-block slots for primitive palette variants")
            self.hierarchy["small_blocks"][target] = tiles
            self.hierarchy["attributes"][target] = attr
            used_small.add(target)
            self.catalogue["families"][recipe["family"]]["shapes"][recipe["shape"]][recipe["phase"]] = target

    def _block(self, values: list[int]) -> int:
        matches = [index for index, item in enumerate(self.hierarchy["big_blocks"]) if item == values]
        index = matches[0] if matches else next((i for i in range(256) if i not in self.used), None)
        if index is None:
            raise AuthoringError("no unused big-block slots for semantic terrain edges")
        self.hierarchy["big_blocks"][index] = values
        self.used.add(index)
        return index


def audit_definitions(original: Mapping, candidate: Mapping) -> dict:
    """Prove that every original map's referenced art definitions survived."""
    big = {value for item in original["maps"] for row in item["rows"] for value in row}
    small = {value for index in big for value in original["big_blocks"][index]}
    if any(original["big_blocks"][i] != candidate["big_blocks"][i] for i in big):
        raise AuthoringError("a referenced original big-block definition changed")
    if any(original[key][i] != candidate[key][i] for i in small for key in ("small_blocks", "attributes")):
        raise AuthoringError("a referenced original small-block definition changed")
    return {
        key: [i for i in range(256) if original[key][i] != candidate[key][i]]
        for key in ("attributes", "small_blocks", "big_blocks")
    }
