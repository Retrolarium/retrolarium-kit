#!/usr/bin/env python3
"""Compare a range of canonical Flicky bonus VDP frames in one MAME run."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import tempfile

from retrolarium_kit.games.flicky.content import import_flicky_source
from retrolarium_kit.studio.project import build_studio_project
from retrolarium_kit.studio.create import create_flicky_studio_project
from retrolarium_kit.studio.playtest import studio_flicky_round_playtest_plan

from run_round_playtest_source_oracle import (
    launch,
    render_native_bonus_phase,
)


VIEWPORT = (256, 224)
DAC = {0: 0, 36: 52, 72: 87, 108: 116, 144: 144, 180: 172, 216: 206, 252: 255}
DAC_TRANSLATION = bytes(DAC.get(value, value) for value in range(256))


def matching_pixels(native_path: Path, mame_path: Path) -> int:
    from PIL import Image

    with Image.open(native_path) as native_image, Image.open(mame_path) as mame_image:
        assert native_image.size == mame_image.size == VIEWPORT, (
            f"unexpected bonus VDP dimensions: {native_image.size}, {mame_image.size}"
        )
        native = native_image.convert("RGB").tobytes().translate(DAC_TRANSLATION)
        mame = mame_image.convert("RGB").tobytes()
    return sum(native[offset : offset + 3] == mame[offset : offset + 3] for offset in range(0, len(native), 3))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--rom", type=Path, required=True)
    parser.add_argument("--native-writer", type=Path, required=True)
    parser.add_argument("--native-renderer", type=Path, required=True)
    parser.add_argument("--mame", type=Path, required=True)
    parser.add_argument("--round", type=int, default=3)
    parser.add_argument("--start-phase", type=int, required=True)
    parser.add_argument("--end-phase", type=int, required=True)
    parser.add_argument("--report", type=Path, help="retain per-phase matching-pixel counts as JSON")
    parser.add_argument("--diagnostics-dir", type=Path, help="retain native/MAME PNG pairs for differing phases")
    parser.add_argument("--require-exact", action="store_true", help="fail if any phase differs from MAME")
    args = parser.parse_args()
    if not 1 <= args.round <= 48 or args.round % 4 != 3:
        parser.error("--round must select one of the 12 bonus rounds")
    if not 1 <= args.start_phase <= args.end_phase <= 600:
        parser.error("phase range must satisfy 1 <= start <= end <= 600")
    if args.diagnostics_dir is not None:
        args.diagnostics_dir.mkdir(parents=True, exist_ok=True)

    os.environ["RETROLARIUM_FLICKY_LEVEL_WRITER"] = str(args.native_writer.resolve())
    scratch = Path(__file__).resolve().parents[3] / "build"
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="flicky-bonus-sweep-", dir=scratch) as name:
        root = Path(name)
        import_flicky_source(args.source_root, args.rom, root)
        create_flicky_studio_project(root)
        project = root / "flicky-ue.rlj"
        build_studio_project(project)
        source = root / "flicky-source"
        prefix = root / "bonus-sweep"
        final_snapshot = root / f"bonus-sweep-{args.end_phase:03d}.png"
        earlier = tuple(range(args.start_phase, args.end_phase))
        extra_environment = (
            {
                "FLICKY_PLAYTEST_SNAPSHOT_PHASES": ",".join(map(str, earlier)),
                "FLICKY_PLAYTEST_SNAPSHOT_PREFIX": str(prefix.resolve()),
            }
            if earlier
            else None
        )
        launch(
            studio_flicky_round_playtest_plan(project, mame=args.mame, round=args.round),
            args.round,
            root,
            final_snapshot,
            snapshot_delay=args.end_phase,
            extra_environment=extra_environment,
        )

        results = []
        total = VIEWPORT[0] * VIEWPORT[1]
        for phase in range(args.start_phase, args.end_phase + 1):
            native = root / f"native-{phase:03d}.png"
            mame = root / f"bonus-sweep-{phase:03d}.png"
            assert mame.is_file(), f"MAME missed bonus snapshot +{phase}"
            render_native_bonus_phase(args.native_renderer, source, args.round, phase, native)
            matches = matching_pixels(native, mame)
            results.append({"phase": phase, "matching_pixels": matches, "mismatching_pixels": total - matches})
            if matches != total and args.diagnostics_dir is not None:
                stem = f"round-{args.round}-phase-{phase:03d}"
                shutil.copyfile(native, args.diagnostics_dir / f"{stem}-native.png")
                shutil.copyfile(mame, args.diagnostics_dir / f"{stem}-mame.png")

    mismatches = [item for item in results if item["mismatching_pixels"]]
    worst = max(results, key=lambda item: item["mismatching_pixels"])
    mean_agreement = sum(item["matching_pixels"] for item in results) / (len(results) * total)
    print(
        f"Flicky bonus round {args.round} phase sweep "
        f"+{args.start_phase}..+{args.end_phase}: "
        f"{len(results) - len(mismatches)}/{len(results)} exact frames; "
        f"worst +{worst['phase']}={worst['matching_pixels']}/{total} "
        f"({worst['matching_pixels'] / total:.2%}); "
        f"mean pixel agreement {mean_agreement:.2%}"
    )
    if mismatches:
        sample = mismatches[:20]
        print(
            "Non-exact phases: "
            + ", ".join(f"+{item['phase']} ({item['mismatching_pixels']} pixels)" for item in sample)
            + (f", … and {len(mismatches) - len(sample)} more" if len(mismatches) > len(sample) else "")
        )
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(
                {
                    "round": args.round,
                    "viewport": list(VIEWPORT),
                    "summary": {
                        "exact_frames": len(results) - len(mismatches),
                        "total_frames": len(results),
                        "worst_phase": worst["phase"],
                        "minimum_pixel_agreement": worst["matching_pixels"] / total,
                        "mean_pixel_agreement": mean_agreement,
                    },
                    "phases": results,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    if args.require_exact:
        assert not mismatches, f"{len(mismatches)} bonus phases differ from MAME"
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
