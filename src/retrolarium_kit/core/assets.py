"""A game's private assets: which ROM images it reads and what it cuts out of
them (``retrolarium.assets`` v1, normally ``assets/manifest.json``).

A manifest names each image the game builds from or checks against by its
file, size and SHA-1, and each asset by the image it comes from, its offset
in that file, its size and SHA-1::

    {
      "schema": "retrolarium.assets",
      "schema_version": 1,
      "output": "assets/generated",
      "images": [
        {"id": "japan-v10", "file": "Pac-Man (J) (V1.0) [!].nes", "size": 24592, "sha1": "..."}
      ],
      "assets": [
        {"path": "chr/pacman.chr", "image": "japan-v10", "offset": 16400, "size": 8192, "sha1": "..."}
      ]
    }

``split`` cuts the assets out of every image that is present (or the images
asked for), ``check`` says whether they are in place, and ``verify`` compares a
built file with an image byte for byte. Paths are relative to the repository
root; nothing here knows any game.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Iterable, Mapping

SCHEMA = "retrolarium.assets"


class AssetsError(ValueError):
    """A manifest, an image or an asset is not what it should be."""


@dataclass(frozen=True)
class Image:
    id: str
    file: str
    size: int
    sha1: str


@dataclass(frozen=True)
class Asset:
    path: str
    image: str
    offset: int
    size: int
    sha1: str


@dataclass(frozen=True)
class Manifest:
    root: Path
    output: str
    images: tuple[Image, ...]
    assets: tuple[Asset, ...]

    def image(self, image_id: str) -> Image:
        for image in self.images:
            if image.id == image_id:
                return image
        raise AssetsError(f"the manifest names no image {image_id!r}")

    def assets_of(self, image_id: str) -> tuple[Asset, ...]:
        return tuple(asset for asset in self.assets if asset.image == image_id)

    def output_path(self, asset: Asset) -> Path:
        return self.root / self.output / asset.path


def sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def _relative(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise AssetsError(f"{field} must be a path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "\\" in value:
        raise AssetsError(f"{field} must stay inside the repository: {value}")
    return value


def _number(value: object, field: str) -> int:
    if isinstance(value, bool):
        raise AssetsError(f"{field} must be a number")
    if isinstance(value, int):
        number = value
    elif isinstance(value, str):
        try:
            number = int(value, 0)
        except ValueError as exc:
            raise AssetsError(f"{field} must be a number") from exc
    else:
        raise AssetsError(f"{field} must be a number")
    if number < 0:
        raise AssetsError(f"{field} must not be negative")
    return number


def _hash(value: object, field: str) -> str:
    if not isinstance(value, str) or len(value) != 40 or any(c not in "0123456789abcdef" for c in value.lower()):
        raise AssetsError(f"{field} must be a SHA-1")
    return value.lower()


def load(path: Path, root: Path | None = None) -> Manifest:
    """Read a manifest; root defaults to the folder above the manifest's own
    (``assets/manifest.json`` sits in the repository root's ``assets/``)."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AssetsError(f"cannot read {path}: {exc}") from exc
    if not isinstance(raw, Mapping) or raw.get("schema") != SCHEMA or raw.get("schema_version") != 1:
        raise AssetsError(f"{path.name} is not a {SCHEMA} v1 manifest")
    images = []
    for index, item in enumerate(raw.get("images") or ()):
        if not isinstance(item, Mapping):
            raise AssetsError(f"images[{index}] must be an object")
        images.append(Image(str(item.get("id") or ""), _relative(item.get("file"), f"images[{index}].file"),
                            _number(item.get("size"), f"images[{index}].size"),
                            _hash(item.get("sha1"), f"images[{index}].sha1")))
    ids = [image.id for image in images]
    if not images or "" in ids or len(set(ids)) != len(ids):
        raise AssetsError("images must have unique, non-empty ids")
    by_id = {image.id: image for image in images}
    assets = []
    for index, item in enumerate(raw.get("assets") or ()):
        if not isinstance(item, Mapping):
            raise AssetsError(f"assets[{index}] must be an object")
        asset = Asset(_relative(item.get("path"), f"assets[{index}].path"), str(item.get("image") or ""),
                      _number(item.get("offset"), f"assets[{index}].offset"),
                      _number(item.get("size"), f"assets[{index}].size"),
                      _hash(item.get("sha1"), f"assets[{index}].sha1"))
        image = by_id.get(asset.image)
        if image is None:
            raise AssetsError(f"assets[{index}] ({asset.path}) names no image of the manifest")
        if asset.size == 0 or asset.offset + asset.size > image.size:
            raise AssetsError(f"{asset.path} lies outside {image.id} ({image.size} bytes)")
        assets.append(asset)
    paths = [asset.path for asset in assets]
    duplicates = sorted({path for path in paths if paths.count(path) > 1})
    if duplicates:
        raise AssetsError(f"more than one asset writes {', '.join(duplicates)}")
    output = _relative(raw.get("output", "assets/generated"), "output")
    return Manifest((root or path.resolve().parent.parent), output, tuple(images), tuple(assets))


def read_image(manifest: Manifest, image: Image, override: Path | None = None) -> bytes:
    """The image's bytes, refused unless its size and SHA-1 are the manifest's."""
    path = override or manifest.root / image.file
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise AssetsError(f"{image.id}: cannot read {path}: {exc.strerror or exc}") from exc
    if len(data) != image.size or sha1(data) != image.sha1:
        raise AssetsError(f"{path.name} is not {image.id}: expected {image.size} bytes with SHA-1 {image.sha1}, "
                          f"found {len(data)} bytes with SHA-1 {sha1(data)}")
    return data


def present(manifest: Manifest, image: Image) -> bool:
    return (manifest.root / image.file).is_file()


def split(manifest: Manifest, image_ids: Iterable[str] = (),
          overrides: Mapping[str, Path] | None = None) -> list[str]:
    """Cut the assets out of the images asked for (all whose file is present
    when none is), writing only files whose bytes change; return the report."""
    overrides = dict(overrides or {})
    wanted = list(image_ids)
    for image_id in wanted:
        manifest.image(image_id)
    report: list[str] = []
    for image in manifest.images:
        if wanted and image.id not in wanted:
            continue
        if not wanted and image.id not in overrides and not present(manifest, image):
            if manifest.assets_of(image.id):
                report.append(f"[SKIP] {image.id}: {image.file} is not here")
            continue
        data = read_image(manifest, image, overrides.get(image.id))
        written = kept = 0
        for asset in manifest.assets_of(image.id):
            payload = data[asset.offset:asset.offset + asset.size]
            if sha1(payload) != asset.sha1:
                raise AssetsError(f"{asset.path}: bytes {asset.offset:#x}+{asset.size:#x} of {image.id} "
                                  f"have SHA-1 {sha1(payload)}, the manifest says {asset.sha1}")
            target = manifest.output_path(asset)
            if target.is_file() and target.read_bytes() == payload:
                kept += 1
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + ".tmp")
            temporary.write_bytes(payload)
            temporary.replace(target)
            written += 1
        report.append(f"[OK] {image.id}: {written} asset(s) written, {kept} unchanged")
    return report


def check(manifest: Manifest, image_ids: Iterable[str] = (), exact: bool = False) -> list[str]:
    """The assets that are missing (of the images asked for, or all); with
    exact, also those that differ from the image. A build takes the assets as
    they are, so edited ones only fail an exact check."""
    wanted = list(image_ids)
    for image_id in wanted:
        manifest.image(image_id)
    problems = []
    for asset in manifest.assets:
        if wanted and asset.image not in wanted:
            continue
        target = manifest.output_path(asset)
        if not target.is_file():
            problems.append(f"{manifest.output}/{asset.path} is missing")
        elif exact and sha1(target.read_bytes()) != asset.sha1:
            problems.append(f"{manifest.output}/{asset.path} differs from {asset.image}")
    return problems


def verify(manifest: Manifest, image_id: str, built: Path) -> str:
    """Compare a built file with an image byte for byte; return the report or
    raise with the first difference."""
    image = manifest.image(image_id)
    try:
        data = built.read_bytes()
    except OSError as exc:
        raise AssetsError(f"cannot read {built}: {exc.strerror or exc}") from exc
    if len(data) == image.size and sha1(data) == image.sha1:
        return f"[OK] {built.name} is {image.id} byte for byte ({image.size} bytes, SHA-1 {image.sha1})"
    message = f"{built.name} differs from {image.id}: {len(data)} bytes with SHA-1 {sha1(data)}, " \
              f"expected {image.size} bytes with SHA-1 {image.sha1}"
    original = manifest.root / image.file
    if original.is_file():
        reference = original.read_bytes()
        first = next((i for i in range(min(len(data), len(reference))) if data[i] != reference[i]),
                     min(len(data), len(reference)))
        message += f"; first difference at file offset {first:#x}"
    raise AssetsError(message)
