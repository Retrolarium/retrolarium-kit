#!/usr/bin/env python3
"""Compare native full-level/special bytes with source and built ROM readback."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from retrolarium_kit.flicky_content import (
    CANONICAL_SHA256, WORKSPACE_FILES, import_flicky_source,
)
from retrolarium_kit.studio_project import build_studio_project, create_flicky_studio_project


def native_bytes(executable: Path, document: Path) -> tuple[list[bytes], list[bytes], tuple[int, int]]:
    result = subprocess.run(
        [str(executable.resolve()), "--encode", str(document)],
        capture_output=True, text=True, check=True,
    )
    levels = [b""] * 36
    specials = [b""] * 36
    usage = None
    for line in result.stdout.splitlines():
        kind, index, value = line.split("\t")
        if kind == "usage":
            usage = (int(index), int(value))
        elif kind == "level":
            levels[int(index)] = bytes.fromhex(value)
        elif kind == "special":
            specials[int(index)] = bytes.fromhex(value)
        else:
            raise AssertionError(f"unknown native codec output: {line}")
    assert all(levels) and all(specials) and usage is not None
    return levels, specials, usage


def compare(executable: Path, writer: Path, document: Path,
            source: Path, model) -> tuple[list[bytes], list[bytes]]:
    data = json.loads(document.read_text(encoding="utf-8"))
    expected_levels, expected_specials = model.validate_document(data, source)
    actual_levels, actual_specials, usage = native_bytes(executable, document)
    for index in range(36):
        assert actual_levels[index] == bytes(expected_levels[index]), \
            f"native level {index} differs from source encoder"
        assert actual_specials[index] == bytes(expected_specials[index]), \
            f"native special {index} differs from source encoder"
    expected_usage = (96 + sum(map(len, expected_levels)),
                      96 + sum(map(len, expected_specials)))
    assert usage == expected_usage, f"native capacity {usage} != {expected_usage}"
    with tempfile.TemporaryDirectory(prefix="flicky-level-stage-", dir=source) as name:
        staged = Path(name)
        native = staged / "native"
        oracle = staged / "oracle"
        for root in (native, oracle):
            (root / "data").mkdir(parents=True)
            for filename in ("tables.s", "level_layout.s"):
                shutil.copyfile(source / "src/data" / filename,
                                root / "data" / filename)
        written = subprocess.run(
            [str(writer.resolve()), str(document), str(native)],
            capture_output=True, text=True,
        )
        assert written.returncode == 0, \
            f"native writer exit {written.returncode}: {written.stdout}{written.stderr}"
        model.apply_document(data, oracle, source)
        for filename in ("tables.s", "level_layout.s"):
            staged_native_bytes = (native / "data" / filename).read_bytes()
            staged_oracle_bytes = (oracle / "data" / filename).read_bytes()
            if staged_native_bytes != staged_oracle_bytes:
                offset = next((index for index, pair in enumerate(zip(
                    staged_native_bytes, staged_oracle_bytes)) if pair[0] != pair[1]),
                    min(len(staged_native_bytes), len(staged_oracle_bytes)))
                raise AssertionError(
                    f"native staged {filename} differs at {offset}: "
                    f"{staged_native_bytes[offset:offset + 90]!r} != "
                    f"{staged_oracle_bytes[offset:offset + 90]!r}"
                )
    return actual_levels, actual_specials


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--rom", type=Path, required=True)
    parser.add_argument("--native-codec", type=Path, required=True)
    parser.add_argument("--native-writer", type=Path, required=True)
    parser.add_argument("--native-editor", type=Path, required=True)
    args = parser.parse_args()
    scratch = Path(__file__).resolve().parents[2] / "build"
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="flicky-native-codec-", dir=scratch) as name:
        project = Path(name)
        import_flicky_source(args.source_root, args.rom, project)
        create_flicky_studio_project(project)
        source = project / "flicky-source"
        document = source / WORKSPACE_FILES["level_layouts"]
        sys.path.insert(0, str(source / "scripts"))
        from authoring import level_studio_model as model  # noqa: E402

        original = document.read_bytes()
        baseline_levels, baseline_specials = compare(
            args.native_codec, args.native_writer, document, source, model)
        with tempfile.TemporaryDirectory(prefix="flicky-writer-gates-", dir=source) as name:
            gates = Path(name)
            staged = gates / "stage" / "data"
            staged.mkdir(parents=True)
            for filename in ("tables.s", "level_layout.s"):
                shutil.copyfile(source / "src/data" / filename, staged / filename)
            original_table = (staged / "tables.s").read_bytes()
            overflow = json.loads(original)
            overflow["layouts"][0]["flag_7"].append([3, 4])
            overflow_path = gates / "overflow.json"
            overflow_path.write_text(json.dumps(overflow), encoding="utf-8")
            rejected = subprocess.run([
                str(args.native_writer.resolve()), str(overflow_path),
                str(staged.parent),
            ], capture_output=True, text=True)
            assert rejected.returncode != 0 and "capacity" in rejected.stderr.lower(), \
                "native writer accepted an overflowing special-tile section"
            assert (staged / "tables.s").read_bytes() == original_table, \
                "overflow changed staged source before rejection"
            (staged / "tables.s").write_bytes(
                original_table.replace(b"Unused_Block2:", b"Missing_Block2:"))
            rejected = subprocess.run([
                str(args.native_writer.resolve()), str(document),
                str(staged.parent),
            ], capture_output=True, text=True)
            assert rejected.returncode != 0 and "range" in rejected.stderr.lower(), \
                "native writer accepted a missing source boundary"
            (staged / "tables.s").write_bytes(original_table)
            special_original = (staged / "level_layout.s").read_bytes()
            (staged / "level_layout.s").write_bytes(
                special_original.replace(
                    b"Tiger_JumpArcTable:", b"Missing_JumpArcTable:"))
            rejected = subprocess.run([
                str(args.native_writer.resolve()), str(document),
                str(staged.parent),
            ], capture_output=True, text=True)
            assert rejected.returncode != 0 and "range" in rejected.stderr.lower(), \
                "native writer accepted a missing second source boundary"
            assert (staged / "tables.s").read_bytes() == original_table, \
                "second-section conflict changed first staged source"
        cli_environment = os.environ.copy()
        cli_environment.pop("RETROLARIUM_FLICKY_LEVEL_WRITER", None)
        no_writer_environment = cli_environment.copy()
        no_writer_environment["PATH"] = str(Path(sys.executable).parent)
        missing_writer = subprocess.run([
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "rltool.py"),
            "studio", "build", str(project / "flicky-ue.rlj"),
        ], cwd=project, env=no_writer_environment,
            capture_output=True, text=True)
        assert missing_writer.returncode != 0 \
            and "native Flicky level writer is required" in missing_writer.stderr, \
            "Kit silently fell back to the Python level writer"
        zero_cli = subprocess.run([
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "rltool.py"),
            "studio", "build", str(project / "flicky-ue.rlj"),
            "--flicky-level-writer", str(args.native_writer.resolve()),
        ], cwd=project, env=cli_environment, capture_output=True, text=True,
            check=True)
        zero = json.loads(zero_cli.stdout)
        assert zero["artifact"]["sha256"] == CANONICAL_SHA256, \
            "native-writer CLI zero edit must build the exact canonical ROM"
        os.environ["RETROLARIUM_FLICKY_LEVEL_WRITER"] = str(args.native_writer.resolve())
        original_rom = args.rom.read_bytes()
        level_address = original_rom.find(baseline_levels[0])
        special_address = original_rom.find(baseline_specials[0])
        assert level_address >= 0 and original_rom.find(
            baseline_levels[0], level_address + 1) < 0
        assert special_address >= 0 and original_rom.find(
            baseline_specials[0], special_address + 1) < 0

        data = json.loads(original)
        first = data["layouts"][data["round_layouts"][0]]
        cell = first["collision"][4][0]
        subprocess.run([
            str(args.native_editor.resolve()), "--edit", str(document),
            "0", "0", "4", str(1 - cell),
        ], check=True)
        compare(args.native_codec, args.native_writer, document, source, model)

        player = first["player"]
        player_x = player[0] + 1 if player[0] < 31 else player[0] - 1
        subprocess.run([
            str(args.native_editor.resolve()), "--edit-object", str(document),
            "0", "player", "0", str(player_x), str(player[1]),
        ], check=True)
        compare(args.native_codec, args.native_writer, document, source, model)

        special_layout = data["layouts"][data["round_special_layouts"][0]]
        flag = special_layout["flag_7"][0]
        flag_x = flag[0] + 1 if flag[0] < 31 else flag[0] - 1
        subprocess.run([
            str(args.native_editor.resolve()), "--edit-object", str(document),
            "0", "flag_7", "0", str(flag_x), str(flag[1]),
        ], check=True)
        expanded = json.loads(document.read_text(encoding="utf-8"))
        level_group = expanded["layouts"][expanded["round_layouts"][0]]["chicks_a"]
        assert len(level_group) < 16
        level_group.append([3, 4])
        special_group = expanded["layouts"][expanded["round_special_layouts"][0]]
        special_group["flag_7"].pop(0)
        special_group["flag_5"].append([6, 7])
        document.write_text(json.dumps(expanded, indent=2) + "\n", encoding="utf-8")
        levels, specials = compare(args.native_codec, args.native_writer,
                                   document, source, model)
        built = build_studio_project(project / "flicky-ue.rlj")
        image = (project / built["artifact"]["path"]).read_bytes()
        assert image[level_address:level_address + len(levels[0])] == levels[0], \
            "native level bytes did not read back from ROM"
        assert image[special_address:special_address + len(specials[0])] == specials[0], \
            "native special bytes did not read back from ROM"
        document.write_bytes(original)
        restored_levels, restored_specials = compare(
            args.native_codec, args.native_writer, document, source, model)
        assert restored_levels == baseline_levels and restored_specials == baseline_specials
        print("Flicky native level writer: 36+36 blocks, staged source parity, "
              "exact zero-edit, overflow/conflict refusal and ROM readback verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
