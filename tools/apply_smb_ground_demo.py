#!/usr/bin/env python3
"""Apply the bundled CC0 loop as the complete SMB ground-music schedule."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile

from retrolarium_kit.studio.audio import import_smb_midi_into_document


DEFAULT_MIDI = Path(__file__).resolve().parents[1] / "assets" / "midi" / "smb-ground-demo.mid"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("document", type=Path)
    parser.add_argument("--midi", type=Path, default=DEFAULT_MIDI)
    arguments = parser.parse_args()
    document = arguments.document.resolve()
    descriptor, temporary_name = tempfile.mkstemp(dir=document.parent, prefix=f".{document.name}.", suffix=".tmp")
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        report = import_smb_midi_into_document(
            document,
            arguments.midi,
            temporary,
            resource_id="music",
            selector="area_ground",
            target_channel="all",
        )
        root = json.loads(temporary.read_text(encoding="utf-8"))
        music = next(resource for resource in root["resources"] if resource.get("id") == "music")
        selector = next(item for item in music["selectors"] if item.get("name") == "area_ground")
        header = selector["header"]
        music["ground_schedule"] = [header] * len(music["ground_schedule"])
        temporary.write_text(json.dumps(root, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, document)
        print(
            json.dumps(
                {
                    **report,
                    "document": str(document),
                    "ground_schedule_header": header,
                    "ground_schedule_entries": len(music["ground_schedule"]),
                },
                indent=2,
                sort_keys=True,
            )
        )
    finally:
        temporary.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
