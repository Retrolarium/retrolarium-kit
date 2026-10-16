"""assets: cut a game's private assets out of its ROM images and compare
builds with them (``retrolarium.assets`` v1, normally assets/manifest.json).

Run from the repository root; ``make split`` and ``make verify`` in every game
call these.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


def _manifest(args: argparse.Namespace):
    from retrolarium_kit.core import assets

    return assets.load(args.manifest, Path(args.root) if args.root else Path.cwd())


def command_split(args: argparse.Namespace) -> None:
    from retrolarium_kit.core import assets

    overrides = {}
    for item in args.rom or ():
        image_id, separator, path = item.partition("=")
        if not separator or not image_id or not path:
            raise assets.AssetsError(f"--rom wants ID=PATH, not {item!r}")
        overrides[image_id] = Path(path)
    for line in assets.split(_manifest(args), args.image or (), overrides):
        print(line)


def command_check(args: argparse.Namespace) -> None:
    from retrolarium_kit.core import assets

    problems = assets.check(_manifest(args), args.image or (), exact=args.exact)
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        raise assets.AssetsError(f"{len(problems)} asset(s) missing or different; make split cuts them again")
    print("[OK] the assets are in place")


def command_verify(args: argparse.Namespace) -> None:
    from retrolarium_kit.core import assets

    print(assets.verify(_manifest(args), args.image, args.built))


def command_images(args: argparse.Namespace) -> None:
    from retrolarium_kit.core import assets

    manifest = _manifest(args)
    # Plain newlines: shell loops read these ids, and a Windows console's
    # carriage return would end up in each one.
    lines = "".join(f"{image.id}\n" for image in manifest.images
                    if not args.present or assets.present(manifest, image))
    sys.stdout.buffer.write(lines.encode("utf-8"))
    sys.stdout.flush()


def add_parsers(commands: argparse._SubParsersAction) -> None:
    group = commands.add_parser("assets", help="cut a game's assets out of its ROMs and verify builds")
    actions = group.add_subparsers(dest="assets_command", required=True)

    def common(action: argparse.ArgumentParser) -> None:
        action.add_argument("--manifest", type=Path, default=Path("assets/manifest.json"),
                            help="the assets manifest (default: assets/manifest.json)")
        action.add_argument("--root", help="the repository root (default: the current folder)")

    split = actions.add_parser("split", help="cut the assets out of the images that are here")
    common(split)
    split.add_argument("--image", action="append", help="only this image (repeatable); it must be here")
    split.add_argument("--rom", action="append", metavar="ID=PATH", help="read image ID from PATH")
    split.set_defaults(handler=command_split)

    check = actions.add_parser("check", help="say whether the assets are in place")
    common(check)
    check.add_argument("--image", action="append", help="only this image's assets (repeatable)")
    check.add_argument("--exact", action="store_true",
                       help="also compare each asset with its image (edited assets fail)")
    check.set_defaults(handler=command_check)

    images = actions.add_parser("images", help="list the manifest's images, one id per line")
    common(images)
    images.add_argument("--present", action="store_true", help="only those whose file is here")
    images.set_defaults(handler=command_images)

    verify = actions.add_parser("verify", help="compare a built file with an image byte for byte")
    common(verify)
    verify.add_argument("--image", required=True, help="the image the build must equal")
    verify.add_argument("--built", type=Path, required=True, help="the built file")
    verify.set_defaults(handler=command_verify)
