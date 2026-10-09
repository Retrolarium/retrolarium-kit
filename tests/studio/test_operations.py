from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from retrolarium_kit.studio.project import (
    StudioProjectError,
    _fds_file_blob,
    _fds_program_at,
    _smb_object_pages,
    build_studio_project,
    studio_castle_point_playtest_plan,
    studio_pacman_round_playtest_plan,
    studio_solomon_point_playtest_plan,
    studio_smb_point_playtest_plan,
    validate_studio_project,
    verify_studio_project,
)


SHA256 = "a" * 64


def ines_image() -> bytes:
    return b"NES\x1a" + bytes([1, 1]) + bytes(10) + bytes(16_384 + 8_192)


def inspection(root: Path) -> dict[str, object]:
    return {
        "schema": "retrolarium.project-inspection",
        "schema_version": 1,
        "project_file": str((root / "fixture.rlj").resolve()),
        "project_root": str(root.resolve()),
        "project": {
            "id": "fixture",
            "name": "Fixture",
            "platform": "nes",
            "profile": "honkeypong.v1",
            "source_mode": "source-project",
        },
        "source": {
            "disassembly": "disassembly/manifest.json",
            "disassembly_path": str((root / "disassembly/manifest.json").resolve()),
            "sha256": SHA256,
            "schema": "retrolarium.nes.disassembly",
        },
        "workspace": {
            "layout": "auto",
            "startup_tab": "graphics",
            "tabs": [
                {
                    "id": "graphics",
                    "title": "Graphics",
                    "kind": "graphics.tile-set",
                    "group": "assets",
                    "document": "documents/resources.json",
                    "document_path": str((root / "documents/resources.json").resolve()),
                    "codec": "nes.chr-2bpp.v1",
                    "capabilities": ["pixel-paint"],
                    "depends_on": [],
                    "enabled": True,
                }
            ],
        },
        "operations": {
            "validate": "project.validate",
            "build": "project.build",
            "verify": "project.verify",
            "launch": "project.launch",
        },
    }


class StudioOperationTests(unittest.TestCase):
    def prepare(self, root: Path) -> dict[str, object]:
        (root / "documents").mkdir()
        (root / "disassembly/generated").mkdir(parents=True)
        (root / "fixture.rlj").write_text("fixture\n", encoding="utf-8")
        (root / "documents/resources.json").write_text(
            json.dumps(
                {
                    "schema": "retrolarium.nes-resource-bundle",
                    "schema_version": 1,
                    "source_sha256": SHA256,
                }
            ),
            encoding="utf-8",
        )
        (root / "disassembly/manifest.json").write_text("{}", encoding="utf-8")
        (root / "disassembly/generated/base.nes").write_bytes(ines_image())
        return inspection(root)

    def test_validate_routes_recognized_document_through_game_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = self.prepare(root)
            semantic = {
                "schema": "retrolarium.game-document-validation",
                "status": "valid",
            }
            with patch(
                "retrolarium_kit.studio.project.load_studio_project",
                return_value=state,
            ), patch(
                "retrolarium_kit.studio.project.validate_game_document_for_revision",
                return_value=semantic,
            ) as validate:
                result = validate_studio_project(root / "fixture.rlj")
            self.assertEqual([semantic], result["documents"])
            validate.assert_called_once_with(
                (root / "documents/resources.json").resolve(),
                profile_reference="honkeypong.v1",
                source_sha256=SHA256,
            )

    def test_build_and_verify_bind_artifact_to_current_documents(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = self.prepare(root)

            def authoring_build(source: Path, document: Path, output: Path):
                payload = bytearray(source.read_bytes())
                payload[-1] = 1
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(payload)
                return {"schema": "retrolarium.nes-resource-build", "status": "built"}

            patches = (
                patch(
                    "retrolarium_kit.studio.project.load_studio_project",
                    return_value=state,
                ),
                patch(
                    "retrolarium_kit.studio.project.validate_game_document_for_revision",
                    return_value={"status": "valid"},
                ),
                patch(
                    "retrolarium_kit.studio.project.build_project",
                    return_value={"artifact": {"path": "generated/base.nes"}},
                ),
                patch(
                    "retrolarium_kit.studio.project.verify_project",
                    return_value={"status": "exact"},
                ),
                patch(
                    "retrolarium_kit.studio.project.build_game",
                    side_effect=authoring_build,
                ),
                patch(
                    "retrolarium_kit.studio.project.load_disassembly_manifest",
                    return_value={"input": {"size": len(ines_image())}},
                ),
            )
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
                built = build_studio_project(root / "fixture.rlj")
                verified = verify_studio_project(root / "fixture.rlj")
                self.assertEqual("built", built["status"])
                self.assertEqual("current", verified["status"])
                (root / "documents/resources.json").write_text(
                    '{"schema":"retrolarium.nes-resource-bundle","changed":true}',
                    encoding="utf-8",
                )
                stale = verify_studio_project(root / "fixture.rlj")
            self.assertEqual("mismatch", stale["status"])
            self.assertIn("documents", stale["differences"])

    def test_build_rejects_document_without_registered_writer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = self.prepare(root)
            (root / "documents/resources.json").write_text(
                '{"schema":"unknown"}\n', encoding="utf-8"
            )
            with patch(
                "retrolarium_kit.studio.project.load_studio_project",
                return_value=state,
            ), patch(
                "retrolarium_kit.studio.project.validate_studio_project",
                return_value={"status": "valid"},
            ), patch(
                "retrolarium_kit.studio.project.build_project",
                return_value={"artifact": {"path": "generated/base.nes"}},
            ), patch(
                "retrolarium_kit.studio.project.verify_project",
                return_value={"status": "exact"},
            ):
                with self.assertRaisesRegex(StudioProjectError, "no registered writer"):
                    build_studio_project(root / "fixture.rlj")

    def test_build_composes_multiple_registered_documents(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = self.prepare(root)
            rooms = root / "documents/rooms.json"
            rooms.write_text(
                '{"schema":"retrolarium.castle-levels"}', encoding="utf-8"
            )
            state["workspace"]["tabs"].insert(0, {
                "id": "rooms",
                "title": "Rooms",
                "kind": "castle.rooms",
                "group": "content",
                "document": "documents/rooms.json",
                "document_path": str(rooms.resolve()),
                "codec": "castle.prefix-rooms@1",
                "capabilities": ["edit"],
                "depends_on": [],
                "enabled": True,
            })

            def composed(source: Path, documents: list[Path], output: Path):
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(source.read_bytes())
                return {
                    "schema": "retrolarium.composed-game-build",
                    "documents": len(documents),
                }

            with patch(
                "retrolarium_kit.studio.project.load_studio_project",
                return_value=state,
            ), patch(
                "retrolarium_kit.studio.project.validate_game_document_for_revision",
                return_value={"status": "valid"},
            ), patch(
                "retrolarium_kit.studio.project.build_project",
                return_value={"artifact": {"path": "generated/base.nes"}},
            ), patch(
                "retrolarium_kit.studio.project.verify_project",
                return_value={"status": "exact"},
            ), patch(
                "retrolarium_kit.studio.project.build_game_documents",
                side_effect=composed,
            ) as build_documents:
                result = build_studio_project(root / "fixture.rlj")
            self.assertEqual("retrolarium.composed-game-build", result["authoring"]["schema"])
            build_documents.assert_called_once_with(
                (root / "disassembly/generated/base.nes").resolve(),
                [rooms.resolve(), (root / "documents/resources.json").resolve()],
                (root / "build/fixture-edited.nes").resolve(),
            )

    def test_smb_point_playtest_plan_is_fixed_and_validated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = self.prepare(root)
            state["project"]["profile"] = "smb1.v1"
            tab = state["workspace"]["tabs"][0]
            tab["codec"] = "smb.levels.v1"
            tab["capabilities"] = ["playtest"]
            document = {
                "schema": "retrolarium.nes-resource-bundle",
                "schema_version": 1,
                "game": "smb1",
                "revision": "ju",
                "source_sha256": SHA256,
                "resources": [
                    {
                        "id": "levels",
                        "kind": "level",
                        "codec": "smb.levels.v1",
                        "world_resource": "world",
                        "areas": [{"name": "ground_1"}],
                    },
                    {
                        "id": "unused-world",
                        "kind": "world",
                        "codec": "smb.world.v1",
                        "worlds": [],
                    },
                    {
                        "id": "world",
                        "kind": "world",
                        "codec": "smb.world.v1",
                        "worlds": [{"world": 1, "areas": [{
                            "area_type": 1,
                            "area_index": 0,
                            "alternate_bit": False,
                        }]}],
                    },
                    {
                        "id": "palettes",
                        "kind": "palette",
                        "codec": "nes.ppu-palette-packets.v1",
                        "day_background": 0x22,
                        "night_background": 0x0f,
                    },
                ],
            }
            (root / "documents/resources.json").write_text(
                json.dumps(document), encoding="utf-8"
            )
            mame = root / "mame.exe"
            mame.write_bytes(b"fixture")
            artifact = root / "disassembly/generated/base.nes"
            verification = {
                "status": "current",
                "artifact": {"absolute_path": str(artifact)},
            }
            with patch(
                "retrolarium_kit.studio.project.load_studio_project",
                return_value=state,
            ), patch(
                "retrolarium_kit.studio.project.verify_studio_project",
                return_value=verification,
            ):
                plan = studio_smb_point_playtest_plan(
                    root / "fixture.rlj",
                    mame=mame,
                    course_bank="levels",
                    area="ground_1",
                    world=1,
                    course=1,
                    column=19,
                    row=8,
                    theme="night",
                    invincible=False,
                    sfx_channel="noise",
                    sfx_value=0x80,
                )
                self.assertEqual("mame-lua", plan["backend"])
                self.assertEqual(str(mame.resolve()), plan["program"])
                self.assertEqual("nes", plan["arguments"][0])
                self.assertEqual("-cart", plan["arguments"][1])
                self.assertEqual(str(artifact.resolve()), plan["arguments"][2])
                self.assertEqual("-autoboot_delay", plan["arguments"][-4])
                self.assertEqual("0", plan["arguments"][-3])
                self.assertEqual("-autoboot_script", plan["arguments"][-2])
                self.assertTrue(Path(plan["arguments"][-1]).is_file())
                self.assertEqual("32", plan["environment"]["SMB1_PLAYTEST_AREA"])
                self.assertEqual("1", plan["environment"]["SMB1_PLAYTEST_PAGE"])
                self.assertEqual("48", plan["environment"]["SMB1_PLAYTEST_X"])
                self.assertEqual("144", plan["environment"]["SMB1_PLAYTEST_Y"])
                self.assertEqual("0", plan["environment"]["SMB1_PLAYTEST_INVINCIBLE"])
                self.assertEqual("noise", plan["environment"]["SMB1_PLAYTEST_SFX_CHANNEL"])
                self.assertEqual("128", plan["environment"]["SMB1_PLAYTEST_SFX_VALUE"])
                self.assertEqual(
                    {"channel": "noise", "value": 0x80}, plan["point"]["sfx"]
                )
                self.assertTrue(plan["point"]["route_matches_area"])
                # The fixture area has no objects: page 1 has none to find.
                self.assertEqual("1", plan["environment"]["SMB1_PLAYTEST_EMPTY_PAGE"])
                self.assertNotIn("SMB1_PLAYTEST_TIMER", plan["environment"])
                first_page = studio_smb_point_playtest_plan(
                    root / "fixture.rlj", mame=mame, course_bank="levels",
                    area="ground_1", world=1, course=1, column=5, row=8, theme="day",
                )
                self.assertNotIn("SMB1_PLAYTEST_EMPTY_PAGE", first_page["environment"])
                # All Night Nippon: the later Disk System engine's timer and
                # running task; worlds 1-4 need no file from the disk.
                document["revision"] = "ann_fds"
                (root / "documents/resources.json").write_text(
                    json.dumps(document), encoding="utf-8"
                )
                late = studio_smb_point_playtest_plan(
                    root / "fixture.rlj", mame=mame, course_bank="levels",
                    area="ground_1", world=1, course=1, column=5, row=8, theme="day",
                )
                self.assertEqual(str(0x07EC), late["environment"]["SMB1_PLAYTEST_TIMER"])
                self.assertEqual("5", late["environment"]["SMB1_PLAYTEST_RUNNING_TASK"])
                self.assertNotIn("SMB1_PLAYTEST_DISK_FILE", late["environment"])
                document["revision"] = "vs_smb"
                (root / "documents/resources.json").write_text(
                    json.dumps(document), encoding="utf-8"
                )
                with self.assertRaisesRegex(StudioProjectError, "does not work in this version"):
                    studio_smb_point_playtest_plan(
                        root / "fixture.rlj", mame=mame, course_bank="levels",
                        area="ground_1", world=1, course=1, column=5, row=8, theme="day",
                    )
                document["revision"] = "ju"
                (root / "documents/resources.json").write_text(
                    json.dumps(document), encoding="utf-8"
                )
                with self.assertRaisesRegex(StudioProjectError, "column must be"):
                    studio_smb_point_playtest_plan(
                        root / "fixture.rlj",
                        mame=mame,
                        course_bank="levels",
                        area="ground_1",
                        world=1,
                        course=1,
                        column=512,
                        row=8,
                        theme="day",
                    )
                with self.assertRaisesRegex(StudioProjectError, "requires a non-zero"):
                    studio_smb_point_playtest_plan(
                        root / "fixture.rlj",
                        mame=mame,
                        course_bank="levels",
                        area="ground_1",
                        world=1,
                        course=1,
                        column=19,
                        row=8,
                        theme="day",
                        sfx_channel="square1",
                        sfx_value=0,
                    )

    def test_smb_object_pages_follow_the_area_parser(self) -> None:
        area = {"objects": [
            {"row": 15, "object_control": 0x26, "page_advance": False},
            {"row": 13, "object_control": 0x02, "page_advance": False},
            {"row": 8, "object_control": 0x72, "page_advance": False},
            {"row": 12, "object_control": 0x01, "page_advance": True},
            {"row": 13, "object_control": 0x41, "page_advance": False},
        ]}
        # A page control is not an object: page 1 is skipped over.
        self.assertEqual({0, 2, 3}, _smb_object_pages(area))
        self.assertEqual(set(), _smb_object_pages({}))

    def test_fds_program_files_are_found_by_id_and_address(self) -> None:
        # A main program at $C000 and an overlay loaded later over part of it.
        files = ((0x05, 0xC000, bytes(range(256)) * 2), (0x20, 0xC100, b"\xaa" * 16))
        side = bytearray(65_500)
        side[0] = 1
        side[56:58] = bytes((2, len(files)))
        position = 58
        for number, (identifier, address, payload) in enumerate(files):
            header = bytearray(16)
            header[0:3] = bytes((3, number, identifier))
            header[3:11] = f"FILE{number:04d}".encode("ascii")
            header[11:13] = address.to_bytes(2, "little")
            header[13:15] = len(payload).to_bytes(2, "little")
            side[position:position + 16] = header
            side[position + 16] = 4
            side[position + 17:position + 17 + len(payload)] = payload
            position += 17 + len(payload)
        with tempfile.TemporaryDirectory() as temporary:
            image = Path(temporary) / "game.fds"
            image.write_bytes(bytes(side))
            # The program loaded at boot, not the overlay over it.
            data, offset = _fds_program_at(image, 0xC150)
            self.assertEqual(files[0][2], data)
            self.assertEqual(0x150, offset)
            self.assertEqual(b"\x00\xc1" + b"\xaa" * 16, _fds_file_blob(image, 0x20))
            with self.assertRaisesRegex(StudioProjectError, "no program file at \\$8000"):
                _fds_program_at(image, 0x8000)
            with self.assertRaisesRegex(StudioProjectError, "no program file \\$40"):
                _fds_file_blob(image, 0x40)

    def test_castle_point_playtest_prepares_isolated_room_preview(self) -> None:
        for revision in ("castle-excellent-japan-hsp-05", "castlequest-usa"):
            with self.subTest(revision=revision), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                state = self.prepare(root)
                state["project"]["profile"] = "castle-excellent.v1"
                tab = state["workspace"]["tabs"][0]
                tab["codec"] = "castle.prefix-rooms@1"
                tab["capabilities"] = ["edit", "playtest"]
                (root / "documents/resources.json").write_text(json.dumps({
                    "schema": "retrolarium.castle-levels",
                    "schema_version": 1,
                    "game": "castle-excellent",
                    "revision": revision,
                    "rooms": [{} for _ in range(100)],
                }), encoding="utf-8")
                source = bytearray(65552)
                source[:16] = b"NES\x1a" + bytes([2, 4, 0x30]) + bytes(9)
                source[0x22c1:0x22c5] = b"\xa9\x47\x85\x37"
                artifact = root / "disassembly/generated/base.nes"
                artifact.write_bytes(source)
                mame = root / "mame.exe"
                mame.write_bytes(b"fixture")
                verification = {
                    "status": "current",
                    "artifact": {"absolute_path": str(artifact)},
                }
                with patch(
                    "retrolarium_kit.studio.project.load_studio_project",
                    return_value=state,
                ), patch(
                    "retrolarium_kit.studio.project.verify_studio_project",
                    return_value=verification,
                ):
                    plan = studio_castle_point_playtest_plan(
                        root / "fixture.rlj", mame=mame, room=42, x=312, y=156,
                    )
                    preview = Path(plan["arguments"][2])
                    expected = bytearray(source)
                    expected[0x22c2] = 42
                    self.assertEqual(bytes(expected), preview.read_bytes())
                    self.assertEqual(bytes(source), artifact.read_bytes())
                    self.assertEqual("42", plan["environment"]["CASTLE_PLAYTEST_ROOM"])
                    self.assertEqual("312", plan["environment"]["CASTLE_PLAYTEST_X"])
                    self.assertEqual("156", plan["environment"]["CASTLE_PLAYTEST_Y"])
                    self.assertEqual("retrolarium.studio.castle-point-playtest",
                                     plan["operation"])
                    self.assertTrue(Path(plan["arguments"][-1]).is_file())
                    with self.assertRaisesRegex(StudioProjectError, "room must be"):
                        studio_castle_point_playtest_plan(
                            root / "fixture.rlj", mame=mame, room=101, x=0, y=0,
                        )
                    with self.assertRaisesRegex(StudioProjectError, "x must be"):
                        studio_castle_point_playtest_plan(
                            root / "fixture.rlj", mame=mame, room=1, x=320, y=0,
                        )
                    source[0x22c3] = 0
                    artifact.write_bytes(source)
                    with self.assertRaisesRegex(StudioProjectError, "guarded"):
                        studio_castle_point_playtest_plan(
                            root / "fixture.rlj", mame=mame, room=1, x=0, y=0,
                        )

    def test_bundle_upgrade_adds_new_profile_resources_and_keeps_edits(self) -> None:
        from retrolarium_kit.studio import project as studio_project

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = self.prepare(root)
            state["project"]["profile"] = "doraemon.v1"
            bundle = root / "documents/resources.json"
            bundle.write_text(json.dumps({"schema": "x", "resources": [
                {"id": "a", "values": [9]}, {"id": "c", "values": [3]},
            ]}), encoding="utf-8")

            def extract(_source: Path, output: Path, resource: str | None = None) -> None:
                output.write_text(json.dumps({"schema": "x", "resources": [
                    {"id": "a", "values": [1]}, {"id": "b", "values": [2]}, {"id": "c", "values": [3]},
                ]}), encoding="utf-8")

            with patch("retrolarium_kit.studio.project.load_studio_project", return_value=state),                     patch("retrolarium_kit.studio.project._project_source", return_value=root / "base.nes"),                     patch("retrolarium_kit.games.catalog.extract_game", side_effect=extract):
                added = studio_project._add_bundle_resources(
                    root / "fixture.rlj", profile="doraemon.v1", codec="nes.chr-2bpp.v1")
                self.assertEqual(["b"], added)
                merged = json.loads(bundle.read_text(encoding="utf-8"))
                self.assertEqual([{"id": "a", "values": [9]}, {"id": "b", "values": [2]},
                                  {"id": "c", "values": [3]}], merged["resources"])
                self.assertEqual([], studio_project._add_bundle_resources(
                    root / "fixture.rlj", profile="doraemon.v1", codec="nes.chr-2bpp.v1"))

    def test_pacman_round_playtest_runs_the_build_with_a_round(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = self.prepare(root)
            state["project"]["profile"] = "pacman.v1"
            artifact = root / "disassembly/generated/base.nes"
            artifact.write_bytes(b"NES" + bytes((1, 1)) + bytes(10) + bytes(0x4000 + 0x2000))
            mame = root / "mame.exe"
            mame.write_bytes(b"fixture")
            verification = {"status": "current", "artifact": {"absolute_path": str(artifact)}}
            with patch(
                "retrolarium_kit.studio.project.load_studio_project", return_value=state,
            ), patch(
                "retrolarium_kit.studio.project.verify_studio_project", return_value=verification,
            ):
                plan = studio_pacman_round_playtest_plan(root / "fixture.rlj", mame=mame, round=7)
                self.assertEqual({"PACMAN_PLAYTEST_ROUND": "7"}, plan["environment"])
                self.assertEqual("retrolarium.studio.pacman-round-playtest", plan["operation"])
                self.assertIn(str(artifact.resolve()), plan["arguments"])
                self.assertTrue(Path(plan["arguments"][-1]).is_file())
                with self.assertRaisesRegex(StudioProjectError, "round must be"):
                    studio_pacman_round_playtest_plan(root / "fixture.rlj", mame=mame, round=24)
                state["project"]["profile"] = "castle-excellent.v1"
                with self.assertRaisesRegex(StudioProjectError, "pacman.v1"):
                    studio_pacman_round_playtest_plan(root / "fixture.rlj", mame=mame, round=1)

    def test_solomon_point_playtest_uses_profile_hooks_without_rom_patch(self) -> None:
        for revision, hook, machine in (
            ("usa", 0x9021, "nes"), ("europe", 0x9030, "nespal")
        ):
            with self.subTest(revision=revision), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                state = self.prepare(root)
                state["project"]["profile"] = "solomons-key.v1"
                tab = state["workspace"]["tabs"][0]
                tab["codec"] = "solomon.rooms@3"
                tab["capabilities"] = ["edit", "playtest"]
                (root / "documents/resources.json").write_text(json.dumps({
                    "schema": "retrolarium.solomon-rooms",
                    "schema_version": 3,
                    "game": "solomons-key",
                    "revision": revision,
                    "rooms": [{} for _ in range(53)],
                }), encoding="utf-8")
                source = bytearray(65552)
                source[:16] = b"NES\x1a" + bytes([2, 4, 0x30]) + bytes(9)
                source[16 + hook - 0x8000:22 + hook - 0x8000] = (
                    b"\xa9\xfd\x25\x7c\x85\x7c"
                )
                artifact = root / "disassembly/generated/base.nes"
                artifact.write_bytes(source)
                mame = root / "mame.exe"
                mame.write_bytes(b"fixture")
                verification = {
                    "status": "current",
                    "artifact": {"absolute_path": str(artifact)},
                }
                with patch(
                    "retrolarium_kit.studio.project.load_studio_project",
                    return_value=state,
                ), patch(
                    "retrolarium_kit.studio.project.verify_studio_project",
                    return_value=verification,
                ):
                    plan = studio_solomon_point_playtest_plan(
                        root / "fixture.rlj", mame=mame, room=29,
                    )
                    self.assertEqual(machine, plan["arguments"][0])
                    self.assertEqual(str(artifact.resolve()), plan["arguments"][2])
                    self.assertEqual("29", plan["environment"]["SOLOMON_PLAYTEST_ROOM"])
                    self.assertEqual(
                        str(hook), plan["environment"]["SOLOMON_PLAYTEST_ROOM_LOAD"]
                    )
                    self.assertEqual(bytes(source), artifact.read_bytes())
                    self.assertEqual("retrolarium.studio.solomon-point-playtest",
                                     plan["operation"])
                    with self.assertRaisesRegex(StudioProjectError, "room must be"):
                        studio_solomon_point_playtest_plan(
                            root / "fixture.rlj", mame=mame, room=53,
                        )
                    source[16 + hook - 0x8000] = 0
                    artifact.write_bytes(source)
                    with self.assertRaisesRegex(StudioProjectError, "guarded"):
                        studio_solomon_point_playtest_plan(
                            root / "fixture.rlj", mame=mame, room=0,
                        )


if __name__ == "__main__":
    unittest.main()
