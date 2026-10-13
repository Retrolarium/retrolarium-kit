"""Opt-in Solomon title/demo document and ROM gate against neighbor source."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from retrolarium_kit.core.game_profiles import repository_profile
from retrolarium_kit.games.solomon.presentation import (
    build_presentation, decode_presentation, parse_presentation_document,
)


def check(rom: Path, revision_id: str, oracle: object, profile) -> None:
    revision = next(
        item for item in profile.revisions if item.revision_id == revision_id
    )
    source = rom.read_bytes()
    oracle_profile = {
        "id": revision_id,
        "rom": {"sha256": hashlib.sha256(source).hexdigest()},
    }
    reference = oracle.export_document(source, oracle_profile)
    document = decode_presentation(source, profile, revision).as_dict()
    for section in ("layout", "streams", "demo_steps"):
        if document[section] != reference[section]:
            raise AssertionError(f"{revision_id}: {section} differs from oracle")
    if build_presentation(
        source, parse_presentation_document(document, profile, revision),
        profile, revision,
    )[0] != source:
        raise AssertionError(f"{revision_id}: zero-edit ROM differs")
    title = next(
        token for token in document["streams"][0]["tokens"]
        if token["kind"] == "literal"
    )
    title["tiles"][0] ^= 1
    reference_title = next(
        token for token in reference["streams"][0]["tokens"]
        if token["kind"] == "literal"
    )
    reference_title["tiles"][0] ^= 1
    document["demo_steps"][1]["duration"] ^= 1
    reference["demo_steps"][1]["duration"] ^= 1
    for edited in (document, reference):
        buttons = edited["demo_steps"][1]["buttons"]
        if "RIGHT" in buttons:
            buttons.remove("RIGHT")
        else:
            buttons.append("RIGHT")
    output, report = build_presentation(
        source, parse_presentation_document(document, profile, revision),
        profile, revision,
    )
    expected = oracle.build_presentation_image(reference, source, oracle_profile)
    oracle.validate_rebuilt_document(reference, output, oracle_profile)
    if output != expected or report["changed_bytes"] != 3:
        raise AssertionError(f"{revision_id}: edited presentation ROM differs")
    print(f"Solomon {revision_id}: title/demo document and three ROM edits exact")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--neighbor", type=Path, required=True)
    parser.add_argument("--usa", type=Path, required=True)
    parser.add_argument("--europe", type=Path, required=True)
    args = parser.parse_args()
    neighbor = args.neighbor.resolve(strict=True)
    profile = repository_profile(neighbor)
    sys.path.insert(0, str(neighbor))
    from scripts.authoring import presentation_editor

    check(args.usa.resolve(strict=True), "usa", presentation_editor, profile)
    check(args.europe.resolve(strict=True), "europe", presentation_editor, profile)


if __name__ == "__main__":
    main()
