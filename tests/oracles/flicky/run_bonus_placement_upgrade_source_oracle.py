"""Upgrade an edited legacy Flicky .rlj without reimporting or losing edits."""

from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from retrolarium_kit.games.flicky.bonus_placement import export_bonus_placements
from retrolarium_kit.games.flicky.content import (
    SOURCE_DIRECTORY, WORKSPACE_FILES, import_flicky_source,
)
from retrolarium_kit.studio.project import (
    StudioProjectError,
    build_studio_project,
    verify_studio_project,
)
from retrolarium_kit.studio.create import (
    create_flicky_studio_project,
    upgrade_flicky_studio_project,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--rom", type=Path, required=True)
    parser.add_argument("--native-writer", type=Path, required=True)
    parser.add_argument("--native-editor", type=Path,
                        help="also prove the migrated Qt controls reach ROM")
    args = parser.parse_args()
    os.environ["RETROLARIUM_FLICKY_LEVEL_WRITER"] = str(
        args.native_writer.resolve())
    scratch = Path(__file__).resolve().parents[3] / "build"
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="flicky-bonus-upgrade-", dir=scratch) as name:
        root = Path(name)
        import_flicky_source(args.source_root, args.rom, root)
        create_flicky_studio_project(root)
        project = root / "flicky-ue.rlj"
        source = root / SOURCE_DIRECTORY
        levels_path = source / WORKSPACE_FILES["level_layouts"]
        levels = json.loads(levels_path.read_text(encoding="utf-8"))
        assert levels.pop("bonus_placements") == export_bonus_placements(source)
        cell = levels["layouts"][0]["collision"][4][0]
        levels["layouts"][0]["collision"][4][0] = 1 - cell
        player_x = levels["layouts"][0]["player"][0]
        assert player_x < 31
        levels["layouts"][0]["player"][0] = player_x + 1
        legacy = copy.deepcopy(levels)
        levels_path.write_text(json.dumps(levels, indent=2) + "\n",
                               encoding="utf-8", newline="\n")
        project_text = project.read_text(encoding="utf-8")
        assert project_text.count('"preview", "playtest",') == 1
        project_text = project_text.replace('"preview", "playtest",',
                                            '"preview",', 1)
        sound_start = project_text.index('id = "sound"')
        project_text = (project_text[:sound_start]
                        + project_text[sound_start:].replace(
                            '"edit", "preview",', '"edit",', 1))
        project.write_text(project_text + "# user note preserved by upgrade\n",
                           encoding="utf-8", newline="\n")
        before = build_studio_project(project)
        before_sha = before["artifact"]["sha256"]
        before_rom = (root / before["artifact"]["path"]).read_bytes()
        legacy_bytes = levels_path.read_bytes()
        project_bytes = project.read_bytes()

        source_code = source / "src/game/bonus/objects.s"
        source_bytes = source_code.read_bytes()
        assert source_bytes.count(b"#$140,$30(a0)") == 1
        source_code.write_bytes(source_bytes.replace(
            b"#$140,$30(a0)", b"#$141,$30(a0)", 1))
        try:
            upgrade_flicky_studio_project(project)
        except StudioProjectError as error:
            assert "snapshot" in str(error)
        else:
            raise AssertionError("upgrade accepted changed source code")
        assert levels_path.read_bytes() == legacy_bytes
        assert project.read_bytes() == project_bytes
        source_code.write_bytes(source_bytes)

        result = upgrade_flicky_studio_project(project)
        assert result["status"] == "upgraded"
        assert result["upgraded_documents"] == ["levels"]
        assert result["skipped_documents"] == []
        assert "# user note preserved by upgrade" in project.read_text(
            encoding="utf-8")
        tabs = {tab["id"]: tab for tab in result["inspection"]["workspace"]["tabs"]}
        assert "playtest" in tabs["levels"]["capabilities"]
        assert "preview" in tabs["sound"]["capabilities"]
        migrated = json.loads(levels_path.read_text(encoding="utf-8"))
        positions = migrated.pop("bonus_placements")
        assert positions == export_bonus_placements(source)
        assert migrated == legacy, "upgrade changed existing level edits"
        migrated_bytes = levels_path.read_bytes()
        upgraded_project = project.read_bytes()
        assert upgrade_flicky_studio_project(project)["status"] == "current"
        assert levels_path.read_bytes() == migrated_bytes
        assert project.read_bytes() == upgraded_project
        assert verify_studio_project(project)["status"] == "mismatch"
        rebuilt = build_studio_project(project)
        assert rebuilt["artifact"]["sha256"] == before_sha, \
            "adding baseline-only bonus metadata changed the edited ROM"
        assert verify_studio_project(project)["status"] == "current"

        if args.native_editor:
            subprocess.run([
                str(args.native_editor.resolve()), "--edit-bonus-placement",
                str(levels_path), "seesaw", str(0x148), str(0xC0), str(0x150),
            ], check=True)
            edited = build_studio_project(project)
            image = (root / edited["artifact"]["path"]).read_bytes()
            signature = bytes.fromhex("01400030")
            assert before_rom.count(signature) == 1
            address = before_rom.find(signature) + 1
            changed = {index for index, (old, new) in enumerate(
                zip(before_rom, image)) if old != new}
            assert changed - {0x18E, 0x18F} == {address}, changed
            assert image[address] == 0x48
            levels_path.write_bytes(migrated_bytes)
            restored = build_studio_project(project)
            assert restored["artifact"]["sha256"] == before_sha
        document_only = json.loads(levels_path.read_text(encoding="utf-8"))
        document_only.pop("bonus_placements")
        levels_path.write_text(json.dumps(document_only, indent=2) + "\n",
                               encoding="utf-8", newline="\n")
        project_before_document_only = project.read_bytes()
        document_result = upgrade_flicky_studio_project(project)
        assert document_result["status"] == "upgraded"
        assert document_result["upgraded_documents"] == ["levels"]
        assert document_result["skipped_documents"] == []
        assert project.read_bytes() == project_before_document_only, \
            "document-only upgrade rewrote an already current .rlj"
        assert json.loads(levels_path.read_text(encoding="utf-8"))[
            "bonus_placements"] == positions
        relocated_root = root / "relocated"
        shutil.copytree(source, relocated_root / SOURCE_DIRECTORY)
        shutil.copy2(project, relocated_root / "flicky-ue.rlj")
        relocated = relocated_root / "flicky-ue.rlj"
        assert build_studio_project(relocated)["artifact"]["sha256"] == before_sha
        assert verify_studio_project(relocated)["status"] == "current"
    print("Flicky legacy upgrade: edited levels preserved, stale source rejected, "
          "capabilities and bonus positions added, ROM unchanged, idempotent, "
          "relocatable"
          + (", migrated Qt→ROM edit/restore passed" if args.native_editor else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
