"""Opt-in independent Solomon sound decoder/encoder gate for USA and Europe."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from retrolarium_kit.game_profiles import packaged_game_profiles
from retrolarium_kit.ines import parse_ines
from retrolarium_kit.solomon_audio import (
    build_audio, decode_audio, parse_audio_document,
)


def check(rom: Path, revision_id: str, oracle: object) -> None:
    profile = packaged_game_profiles().get("solomons-key")
    revision = next(
        item for item in profile.revisions if item.revision_id == revision_id
    )
    source = rom.read_bytes()
    oracle_profile = {
        "id": revision_id,
        "rom": {"sha256": hashlib.sha256(source).hexdigest()},
    }
    reference = oracle.export_document(parse_ines(source).prg, oracle_profile)
    document = decode_audio(source, profile, revision).as_dict()
    sections = (
        "periods", "durations", "timing_tail", "envelopes",
        "effects", "commands", "trailing_bytes",
    )
    for section in sections:
        if document[section] != reference[section]:
            raise AssertionError(f"{revision_id}: {section} differs from oracle")
    parsed = parse_audio_document(document, profile, revision)
    if build_audio(source, parsed, profile, revision)[0] != source:
        raise AssertionError(f"{revision_id}: zero-edit audio ROM differs")

    document["periods"][0] ^= 1
    note = next(item for item in document["commands"] if item["kind"] == "note")
    note["value"] ^= 1
    rebuilt, _report = build_audio(
        source, parse_audio_document(document, profile, revision),
        profile, revision,
    )
    reference["periods"][0] ^= 1
    next(item for item in reference["commands"] if item["kind"] == "note")[
        "value"
    ] ^= 1
    expected = oracle.build_audio_image(reference, source, oracle_profile)
    oracle.validate_rebuilt_document(reference, rebuilt, oracle_profile)
    if rebuilt != expected:
        raise AssertionError(f"{revision_id}: edited ROM differs from oracle")
    print(f"Solomon {revision_id}: all sound sections and edited ROM match oracle")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--neighbor", type=Path, required=True)
    parser.add_argument("--usa", type=Path, required=True)
    parser.add_argument("--europe", type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.neighbor.resolve(strict=True)))
    from scripts.authoring import audio_editor

    check(args.usa.resolve(strict=True), "usa", audio_editor)
    check(args.europe.resolve(strict=True), "europe", audio_editor)


if __name__ == "__main__":
    main()
