"""Exercise imported Flicky source: exact zero edit, edit, and restore.

Requires the user's local canonical ROM and source checkout.  All compilation
and generated output stays in a disposable directory under this repository's
ignored build/ directory; the neighboring source checkout is read-only.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile

from retrolarium_kit.flicky_content import (
    CANONICAL_SHA256,
    FlickyContentError,
    SOURCE_DIRECTORY,
    WORKSPACE_FILES,
    import_flicky_source,
)
from retrolarium_kit.studio_project import (
    build_studio_project,
    create_flicky_studio_project,
    validate_studio_project,
    verify_studio_project,
)


def mapping_bytes(mapping: dict) -> bytes:
    """Independently encode the canonical Genesis six-byte sprite pieces."""
    output = bytearray((len(mapping["pieces"]) - 1, mapping["render_flags"]))
    for piece in mapping["pieces"]:
        size = ((piece["width"] - 1) << 2) | (piece["height"] - 1)
        attributes = piece["tile"] | (piece["palette"] << 13)
        attributes |= 0x8000 if piece["priority"] else 0
        attributes |= 0x0800 if piece["hflip"] else 0
        attributes |= 0x1000 if piece["vflip"] else 0
        output.extend((piece["y"] & 0xFF, size))
        output.extend(attributes.to_bytes(2, "big"))
        output.extend((piece["x"] & 0xFF, piece["flipped_x"] & 0xFF))
    return bytes(output)


def timer_animation_address(rom: bytes, delay: int) -> tuple[int, list[int]]:
    """Resolve UI_TimerAnim through canonical data-table words and ROM pointers."""
    tables = (
        (0x633B, 0x6205, 0x633C, 0x633D, 0x6205, 0x633E, 0x633F, 0x6205, 0x6340),
        (0x632B, 0x632C, 0x632D, 0x632E, 0x632F, 0x6330, 0x6331, 0x6332, 0x6333),
        (0x6334, 0x6335, 0x6336, 0x6337, 0x6335, 0x6338, 0x6339, 0x6335, 0x633A),
    )
    addresses = []
    for words in tables:
        payload = b"".join(word.to_bytes(2, "big") for word in words)
        assert rom.count(payload) == 1, "timer data table is not unique in ROM"
        addresses.append(rom.find(payload))
    header = bytes((4, delay)) + b"".join(address.to_bytes(4, "big")
        for address in (addresses[0], addresses[1], addresses[2], addresses[0]))
    assert rom.count(header) == 1, "UI_TimerAnim header is not unique in ROM"
    return rom.find(header), addresses


def sound_source_model(source: Path):
    """Load the imported source oracle without importing the sibling checkout."""
    path = source / "scripts/authoring/sound_studio_model.py"
    spec = importlib.util.spec_from_file_location("flicky_sound_source_oracle", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--rom", type=Path, required=True)
    parser.add_argument("--native-level-editor", type=Path,
                        help="exercise the Qt editor's save path before ROM build")
    parser.add_argument("--native-graphics-editor", type=Path,
                        help="exercise 1bpp Qt pixel edit and independent ROM readback")
    parser.add_argument("--native-semantics-editor", type=Path,
                        help="exercise Qt text/palette edits and independent ROM readback")
    parser.add_argument("--native-sequences-editor", type=Path,
                        help="exercise Qt sprite mapping/animation edits and ROM readback")
    parser.add_argument("--native-sound-editor", type=Path,
                        help="exercise Qt note/FM voice/tempo edits and ROM readback")
    args = parser.parse_args()

    scratch = Path(__file__).resolve().parents[2] / "build"
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="flicky-content-gate-", dir=scratch) as name:
        project = Path(name)
        imported = import_flicky_source(args.source_root, args.rom, project)
        assert imported["tracked_files"] > 150
        inspection = create_flicky_studio_project(project)
        assert len(inspection["workspace"]["tabs"]) == 5
        project_file = project / "flicky-ue.rlj"
        assert validate_studio_project(project_file)["status"] == "valid"
        zero = build_studio_project(project_file)
        assert zero["artifact"]["sha256"] == CANONICAL_SHA256, "zero edit must be exact"
        assert verify_studio_project(project_file)["status"] == "current"

        level_path = project / SOURCE_DIRECTORY / WORKSPACE_FILES["level_layouts"]
        original = level_path.read_bytes()
        document = json.loads(original)
        cell = document["layouts"][0]["collision"][4][0]
        if args.native_level_editor:
            subprocess.run([
                str(args.native_level_editor.resolve()), "--edit",
                str(level_path), "0", "0", "4", str(1 - cell),
            ], check=True)
            saved = json.loads(level_path.read_text(encoding="utf-8"))
            assert saved["layouts"][0]["collision"][4][0] == 1 - cell
        else:
            document["layouts"][0]["collision"][4][0] = 1 - cell
            level_path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        assert verify_studio_project(project_file)["status"] == "mismatch"
        edited = build_studio_project(project_file)
        assert edited["artifact"]["sha256"] != zero["artifact"]["sha256"], "level edit must affect ROM"
        assert edited["artifact"]["size"] == zero["artifact"]["size"]
        assert verify_studio_project(project_file)["status"] == "current"
        edited_bytes = (project / edited["artifact"]["path"]).read_bytes()
        original_bytes = args.rom.read_bytes()
        changed = [index for index, pair in enumerate(zip(edited_bytes, original_bytes))
                   if pair[0] != pair[1]]
        assert changed and min(changed) >= 0x400, "edit touched no level data"

        level_path.write_bytes(original)
        restored = build_studio_project(project_file)
        assert restored["artifact"]["sha256"] == CANONICAL_SHA256, "restore must be exact"

        level_document = json.loads(original)
        layout_zero = level_document["layouts"][0]
        level_payload = bytes(layout_zero["baseline_bytes"])
        special_payload = bytes(layout_zero["baseline_special_bytes"])
        assert original_bytes.count(level_payload) == 1, \
            "round 1 level payload has no unique ROM address"
        assert original_bytes.count(special_payload) == 1, \
            "round 1 special payload has no unique ROM address"
        player_address = (original_bytes.find(level_payload)
                          + len(layout_zero["baseline_collision_stream"]))
        special_address = original_bytes.find(special_payload) + 1
        for field, coordinate, address in (
            ("player", layout_zero["player"], player_address),
            ("flag_7", layout_zero["flag_7"][0], special_address),
        ):
            changed_x = coordinate[0] + 1
            assert changed_x < 32
            if args.native_level_editor:
                subprocess.run([
                    str(args.native_level_editor.resolve()), "--edit-object",
                    str(level_path), "0", field, "0", str(changed_x),
                    str(coordinate[1]),
                ], check=True)
            else:
                copy = json.loads(level_path.read_text(encoding="utf-8"))
                if field == "player":
                    copy["layouts"][0][field][0] = changed_x
                else:
                    copy["layouts"][0][field][0][0] = changed_x
                level_path.write_text(json.dumps(copy, indent=2) + "\n",
                                      encoding="utf-8")
            saved_level = json.loads(level_path.read_text(encoding="utf-8"))
            saved_coordinate = saved_level["layouts"][0][field]
            if field != "player":
                saved_coordinate = saved_coordinate[0]
            assert saved_coordinate == [changed_x, coordinate[1]]
            assert verify_studio_project(project_file)["status"] == "mismatch"
            object_build = build_studio_project(project_file)
            assert verify_studio_project(project_file)["status"] == "current"
            object_rom = (project / object_build["artifact"]["path"]).read_bytes()
            object_differences = [index for index, pair in enumerate(
                zip(object_rom, original_bytes)) if pair[0] != pair[1]]
            assert object_differences == [address], \
                f"{field} placement changed unexpected ROM offsets: {object_differences[:16]}"
            assert object_rom[address] == changed_x, \
                f"{field} placement did not read back from ROM"
            level_path.write_bytes(original)
            assert build_studio_project(project_file)["artifact"]["sha256"] \
                == CANONICAL_SHA256, f"{field} placement restore must be exact"

        graphics_path = project / SOURCE_DIRECTORY / WORKSPACE_FILES["graphics_assets"]
        graphics_original = graphics_path.read_bytes()
        graphics_document = json.loads(graphics_original)
        font = next(asset for asset in graphics_document["assets"]
                    if asset["id"] == "japanese_font")
        original_pixel = font["tiles"][0][0][0]
        edited_pixel = 1 - original_pixel
        if args.native_graphics_editor:
            subprocess.run([
                str(args.native_graphics_editor.resolve()), "--edit",
                str(graphics_path), "japanese_font", "0", "0", "0",
                str(edited_pixel),
            ], check=True)
            saved_graphics = json.loads(graphics_path.read_text(encoding="utf-8"))
            saved_font = next(asset for asset in saved_graphics["assets"]
                              if asset["id"] == "japanese_font")
            assert saved_font["tiles"][0][0][0] == edited_pixel
        else:
            font["tiles"][0][0][0] = edited_pixel
            graphics_path.write_text(json.dumps(graphics_document, indent=2) + "\n",
                                     encoding="utf-8")
        assert verify_studio_project(project_file)["status"] == "mismatch"
        graphics_build = build_studio_project(project_file)
        assert verify_studio_project(project_file)["status"] == "current"
        graphics_rom = (project / graphics_build["artifact"]["path"]).read_bytes()
        font_address = 0x2372  # assets/manifest.json: Jap1BPPTiles, tile 0 row 0
        assert graphics_rom[font_address] ^ original_bytes[font_address] == 0x80, \
            "edited font pixel did not read back as the high bit of its ROM byte"
        assert (graphics_rom[font_address] >> 7) & 1 == edited_pixel
        graphics_differences = [index for index, pair in enumerate(
            zip(graphics_rom, original_bytes)) if pair[0] != pair[1]]
        assert graphics_differences == [font_address], \
            f"1bpp pixel edit changed unrelated ROM offsets: {graphics_differences[:16]}"
        graphics_path.write_bytes(graphics_original)
        graphics_restored = build_studio_project(project_file)
        assert graphics_restored["artifact"]["sha256"] == CANONICAL_SHA256, \
            "graphics restore must be exact"

        semantics_path = project / SOURCE_DIRECTORY / WORKSPACE_FILES["graphics_semantics"]
        semantics_original = semantics_path.read_bytes()
        semantics_document = json.loads(semantics_original)
        text = next(record for record in semantics_document["texts"]
                    if record["id"] == "Ending_CongratulationsText")
        old_text = text["value"]
        new_text = "D" + old_text[1:]
        old_text_bytes = old_text.encode("ascii")
        text_address = original_bytes.find(old_text_bytes)
        assert text_address >= 0 and original_bytes.find(
            old_text_bytes, text_address + 1) < 0, "text ROM address is not unique"
        if args.native_semantics_editor:
            subprocess.run([
                str(args.native_semantics_editor.resolve()), "--edit-text",
                str(semantics_path), text["id"], new_text,
            ], check=True)
        else:
            text["value"] = new_text
            semantics_path.write_text(json.dumps(semantics_document, indent=2) + "\n",
                                      encoding="utf-8")
        assert json.loads(semantics_path.read_text(encoding="utf-8"))["texts"][
            semantics_document["texts"].index(text)]["value"] == new_text
        assert verify_studio_project(project_file)["status"] == "mismatch"
        text_build = build_studio_project(project_file)
        assert verify_studio_project(project_file)["status"] == "current"
        text_rom = (project / text_build["artifact"]["path"]).read_bytes()
        assert text_rom[text_address:text_address + len(old_text_bytes)] == new_text.encode("ascii"), \
            "GUI text did not read back from ROM"
        text_differences = [index for index, pair in enumerate(
            zip(text_rom, original_bytes)) if pair[0] != pair[1]]
        assert text_differences == [text_address], \
            f"text edit changed unrelated ROM offsets: {text_differences[:16]}"
        semantics_path.write_bytes(semantics_original)
        assert build_studio_project(project_file)["artifact"]["sha256"] == CANONICAL_SHA256, \
            "text restore must be exact"

        semantics_document = json.loads(semantics_original)
        palette = next(record for record in semantics_document["palettes"]
                       if record["id"] == "Level_Palette0")
        colours = palette["colours"]
        old_palette_bytes = b"".join(colour.to_bytes(2, "big") for colour in colours)
        palette_address = original_bytes.find(old_palette_bytes)
        assert palette_address >= 0 and original_bytes.find(
            old_palette_bytes, palette_address + 1) < 0, "palette ROM address is not unique"
        new_colour = 8 if colours[1] != 8 else 6
        if args.native_semantics_editor:
            subprocess.run([
                str(args.native_semantics_editor.resolve()), "--edit-colour",
                str(semantics_path), palette["id"], "1", str(new_colour),
            ], check=True)
        else:
            colours[1] = new_colour
            semantics_path.write_text(json.dumps(semantics_document, indent=2) + "\n",
                                      encoding="utf-8")
        saved_palette = next(record for record in json.loads(
            semantics_path.read_text(encoding="utf-8"))["palettes"]
            if record["id"] == palette["id"])
        assert saved_palette["colours"][1] == new_colour
        assert verify_studio_project(project_file)["status"] == "mismatch"
        palette_build = build_studio_project(project_file)
        assert verify_studio_project(project_file)["status"] == "current"
        palette_rom = (project / palette_build["artifact"]["path"]).read_bytes()
        palette_word = palette_address + 2
        assert palette_rom[palette_word:palette_word + 2] == new_colour.to_bytes(2, "big"), \
            "GUI CRAM colour did not read back from ROM"
        palette_differences = [index for index, pair in enumerate(
            zip(palette_rom, original_bytes)) if pair[0] != pair[1]]
        assert palette_differences == [palette_word + 1], \
            f"palette edit changed unrelated ROM offsets: {palette_differences[:16]}"
        semantics_path.write_bytes(semantics_original)
        assert build_studio_project(project_file)["artifact"]["sha256"] == CANONICAL_SHA256, \
            "palette restore must be exact"

        sequences_path = project / SOURCE_DIRECTORY / WORKSPACE_FILES["graphics_sequences"]
        sequences_original = sequences_path.read_bytes()
        sequences_document = json.loads(sequences_original)
        mapping = next(record for record in sequences_document["mappings"]
                       if record["id"] == "Obj_TitleCursorData")
        original_mapping = mapping_bytes(mapping)
        assert original_bytes.count(original_mapping) == 1, \
            "source mapping has no unique ROM address"
        mapping_address = original_bytes.find(original_mapping)
        old_x = mapping["pieces"][0]["x"]
        new_x = old_x + 1 if old_x < 127 else old_x - 1
        if args.native_sequences_editor:
            subprocess.run([
                str(args.native_sequences_editor.resolve()), "--edit-mapping-x",
                str(sequences_path), mapping["id"], "0", str(new_x),
            ], check=True)
        else:
            mapping["pieces"][0]["x"] = new_x
            sequences_path.write_text(json.dumps(sequences_document, indent=2) + "\n",
                                      encoding="utf-8")
        saved_mapping = next(record for record in json.loads(
            sequences_path.read_text(encoding="utf-8"))["mappings"]
            if record["id"] == mapping["id"])
        assert saved_mapping["pieces"][0]["x"] == new_x
        assert verify_studio_project(project_file)["status"] == "mismatch"
        mapping_build = build_studio_project(project_file)
        assert verify_studio_project(project_file)["status"] == "current"
        mapping_rom = (project / mapping_build["artifact"]["path"]).read_bytes()
        mapping_x_address = mapping_address + 2 + 4
        assert mapping_rom[mapping_x_address] == new_x & 0xFF, \
            "GUI sprite x did not read back from ROM"
        mapping_differences = [index for index, pair in enumerate(
            zip(mapping_rom, original_bytes)) if pair[0] != pair[1]]
        assert mapping_differences == [mapping_x_address], \
            f"mapping edit changed unrelated ROM offsets: {mapping_differences[:16]}"
        sequences_path.write_bytes(sequences_original)
        assert build_studio_project(project_file)["artifact"]["sha256"] == CANONICAL_SHA256, \
            "mapping restore must be exact"

        sequences_document = json.loads(sequences_original)
        animation = next(record for record in sequences_document["animations"]
                         if record["id"] == "UI_TimerAnim")
        assert animation["kind"] == "tilemap" and animation["frames"] == [
            "UI_TimerData0", "UI_TimerData1", "UI_TimerData2", "UI_TimerData0"]
        animation_address, timer_addresses = timer_animation_address(
            original_bytes, animation["delay"])
        new_delay = animation["delay"] - 1
        if args.native_sequences_editor:
            subprocess.run([
                str(args.native_sequences_editor.resolve()), "--edit-animation-delay",
                str(sequences_path), animation["id"], str(new_delay),
            ], check=True)
        else:
            animation["delay"] = new_delay
            sequences_path.write_text(json.dumps(sequences_document, indent=2) + "\n",
                                      encoding="utf-8")
        saved_animation = next(record for record in json.loads(
            sequences_path.read_text(encoding="utf-8"))["animations"]
            if record["id"] == animation["id"])
        assert saved_animation["delay"] == new_delay
        assert verify_studio_project(project_file)["status"] == "mismatch"
        animation_build = build_studio_project(project_file)
        assert verify_studio_project(project_file)["status"] == "current"
        animation_rom = (project / animation_build["artifact"]["path"]).read_bytes()
        assert animation_rom[animation_address + 1] == new_delay, \
            "GUI animation delay did not read back from ROM"
        animation_differences = [index for index, pair in enumerate(
            zip(animation_rom, original_bytes)) if pair[0] != pair[1]]
        assert animation_differences == [animation_address + 1], \
            f"animation edit changed unrelated ROM offsets: {animation_differences[:16]}"
        sequences_path.write_bytes(sequences_original)
        assert build_studio_project(project_file)["artifact"]["sha256"] == CANONICAL_SHA256, \
            "animation restore must be exact"

        sequences_document = json.loads(sequences_original)
        animation = next(record for record in sequences_document["animations"]
                         if record["id"] == "UI_TimerAnim")
        new_frame = "UI_TimerData1"
        if args.native_sequences_editor:
            subprocess.run([
                str(args.native_sequences_editor.resolve()), "--edit-animation-frame",
                str(sequences_path), animation["id"], "0", new_frame,
            ], check=True)
        else:
            animation["frames"][0] = new_frame
            sequences_path.write_text(json.dumps(sequences_document, indent=2) + "\n",
                                      encoding="utf-8")
        saved_animation = next(record for record in json.loads(
            sequences_path.read_text(encoding="utf-8"))["animations"]
            if record["id"] == animation["id"])
        assert saved_animation["frames"][0] == new_frame
        assert verify_studio_project(project_file)["status"] == "mismatch"
        frame_build = build_studio_project(project_file)
        assert verify_studio_project(project_file)["status"] == "current"
        frame_rom = (project / frame_build["artifact"]["path"]).read_bytes()
        pointer_address = animation_address + 2
        new_pointer = timer_addresses[1].to_bytes(4, "big")
        assert frame_rom[pointer_address:pointer_address + 4] == new_pointer, \
            "GUI animation frame pointer did not read back from ROM"
        frame_differences = [index for index, pair in enumerate(
            zip(frame_rom, original_bytes)) if pair[0] != pair[1]]
        expected_pointer_differences = [pointer_address + index
            for index, (before, after) in enumerate(zip(
                timer_addresses[0].to_bytes(4, "big"), new_pointer))
            if before != after]
        assert frame_differences == expected_pointer_differences, \
            f"animation frame edit changed unrelated ROM offsets: {frame_differences[:16]}"
        sequences_path.write_bytes(sequences_original)
        assert build_studio_project(project_file)["artifact"]["sha256"] == CANONICAL_SHA256, \
            "animation frame restore must be exact"

        source_copy = project / SOURCE_DIRECTORY
        sound_path = source_copy / WORKSPACE_FILES["z80_sound_banks"]
        sound_original = sound_path.read_bytes()
        sound_model = sound_source_model(source_copy)
        baseline_sound = source_copy / "src/sound/z80/data.asm"
        sound_document = sound_model.export_document(sound_path)
        sequence = next(record for record in sound_document["sequences"]
                        if record["id"] == "zSFX90Sequence")
        original_sequence = bytes(sequence["bytes"])
        assert original_bytes.count(original_sequence) == 1, \
            "SFX stream has no unique ROM address"
        sequence_address = original_bytes.find(original_sequence)
        note = sound_model.piano_roll_notes(sequence["bytes"])[0]
        new_note = note["note"] + 1
        if args.native_sound_editor:
            subprocess.run([
                str(args.native_sound_editor.resolve()), "--edit-note",
                str(sound_path), sequence["id"], "0", str(new_note),
            ], check=True)
        else:
            sequence["bytes"][note["offset"]] = 0x81 + new_note
            sound_model.apply_document(sound_document, baseline_sound, sound_path)
        saved_sequence = next(record for record in sound_model.export_document(
            sound_path)["sequences"] if record["id"] == sequence["id"])
        assert saved_sequence["bytes"][note["offset"]] == 0x81 + new_note
        assert verify_studio_project(project_file)["status"] == "mismatch"
        note_build = build_studio_project(project_file)
        assert verify_studio_project(project_file)["status"] == "current"
        note_rom = (project / note_build["artifact"]["path"]).read_bytes()
        note_address = sequence_address + note["offset"]
        assert note_rom[note_address] == 0x81 + new_note, \
            "GUI SFX note did not read back from ROM"
        note_differences = [index for index, pair in enumerate(
            zip(note_rom, original_bytes)) if pair[0] != pair[1]]
        assert note_differences == [note_address], \
            f"SFX note changed unrelated ROM offsets: {note_differences[:16]}"
        sound_path.write_bytes(sound_original)
        assert build_studio_project(project_file)["artifact"]["sha256"] == CANONICAL_SHA256, \
            "sound note restore must be exact"

        sound_document = sound_model.export_document(sound_path)
        voice_bank = next(record for record in sound_document["voice_banks"]
                          if record["id"] == "zSFX90Voices")
        original_voice = bytes(voice_bank["voices"][0])
        assert original_bytes.count(original_voice) == 1, \
            "FM voice has no unique ROM address"
        voice_address = original_bytes.find(original_voice)
        new_algorithm = (original_voice[0] & 7) + 1
        assert new_algorithm <= 7
        if args.native_sound_editor:
            subprocess.run([
                str(args.native_sound_editor.resolve()), "--edit-voice-algorithm",
                str(sound_path), voice_bank["id"], "0", str(new_algorithm),
            ], check=True)
        else:
            voice_bank["voices"][0][0] = (original_voice[0] & 0xF8) | new_algorithm
            sound_model.apply_document(sound_document, baseline_sound, sound_path)
        saved_voice = next(record for record in sound_model.export_document(
            sound_path)["voice_banks"] if record["id"] == voice_bank["id"])
        assert saved_voice["voices"][0][0] == (original_voice[0] & 0xF8) | new_algorithm
        assert verify_studio_project(project_file)["status"] == "mismatch"
        voice_build = build_studio_project(project_file)
        assert verify_studio_project(project_file)["status"] == "current"
        voice_rom = (project / voice_build["artifact"]["path"]).read_bytes()
        assert voice_rom[voice_address] == saved_voice["voices"][0][0], \
            "GUI FM algorithm did not read back from ROM"
        voice_differences = [index for index, pair in enumerate(
            zip(voice_rom, original_bytes)) if pair[0] != pair[1]]
        assert voice_differences == [voice_address], \
            f"FM voice edit changed unrelated ROM offsets: {voice_differences[:16]}"
        sound_path.write_bytes(sound_original)
        assert build_studio_project(project_file)["artifact"]["sha256"] == CANONICAL_SHA256, \
            "FM voice restore must be exact"

        sound_document = sound_model.export_document(sound_path)
        music = next(record for record in sound_document["headers"]
                     if record["id"] == "zMusic81Header")
        music_voices = next(record for record in sound_document["voice_banks"]
                            if record["id"] == music["voices"])
        music_voice_bytes = bytes(sum(music_voices["voices"], [])
                                  + music_voices["tail"])
        assert original_bytes.count(music_voice_bytes) == 1, \
            "music voice bank has no unique ROM address"
        music_voice_address = original_bytes.find(music_voice_bytes)
        # Canonical source copies 0x1C8 bytes of the SFX bank from ROM $101E0;
        # the music bank starts immediately after at Z80 $1200.
        music_rom_base = 0x101E0 + 0x1C8
        music_voice_z80 = 0x1200 + music_voice_address - music_rom_base
        fm_count = sum(track["type"] == "fm" for track in music["tracks"])
        psg_count = sum(track["type"] == "psg" for track in music["tracks"])
        music_header = music_voice_z80.to_bytes(2, "little") + bytes((
            fm_count, psg_count, music["scale"], music["tempo"]))
        assert original_bytes.count(music_header) == 1, \
            "music header has no unique ROM address"
        tempo_address = original_bytes.find(music_header) + 5
        new_tempo = music["tempo"] + 1
        if args.native_sound_editor:
            subprocess.run([
                str(args.native_sound_editor.resolve()), "--edit-tempo",
                str(sound_path), music["id"], str(new_tempo),
            ], check=True)
        else:
            music["tempo"] = new_tempo
            sound_model.apply_document(sound_document, baseline_sound, sound_path)
        saved_music = next(record for record in sound_model.export_document(
            sound_path)["headers"] if record["id"] == music["id"])
        assert saved_music["tempo"] == new_tempo
        assert verify_studio_project(project_file)["status"] == "mismatch"
        tempo_build = build_studio_project(project_file)
        assert verify_studio_project(project_file)["status"] == "current"
        tempo_rom = (project / tempo_build["artifact"]["path"]).read_bytes()
        assert tempo_rom[tempo_address] == new_tempo, \
            "GUI music tempo did not read back from ROM"
        tempo_differences = [index for index, pair in enumerate(
            zip(tempo_rom, original_bytes)) if pair[0] != pair[1]]
        assert tempo_differences == [tempo_address], \
            f"music tempo changed unrelated ROM offsets: {tempo_differences[:16]}"
        sound_path.write_bytes(sound_original)
        assert build_studio_project(project_file)["artifact"]["sha256"] == CANONICAL_SHA256, \
            "music tempo restore must be exact"

        relocated_project = project / "relocated"
        relocated_project.mkdir()
        (project / SOURCE_DIRECTORY).rename(relocated_project / SOURCE_DIRECTORY)
        project_file.rename(relocated_project / project_file.name)
        relocated_file = relocated_project / project_file.name
        relocated = build_studio_project(relocated_file)
        assert relocated["artifact"]["sha256"] == CANONICAL_SHA256, "relocated build must be exact"
        source_file = relocated_project / SOURCE_DIRECTORY / "scripts/authoring/build_content.py"
        source_file.write_bytes(source_file.read_bytes() + b"\n")
        stale_source = verify_studio_project(relocated_file)
        assert stale_source["status"] == "mismatch"
        assert "source-snapshot" in stale_source["differences"]
        try:
            build_studio_project(relocated_file)
        except FlickyContentError as exc:
            assert "snapshot changed" in str(exc)
        except ValueError as exc:
            assert "snapshot changed" in str(exc)
        else:
            raise AssertionError("source drift was not rejected")
    level_editor = "Qt GUI" if args.native_level_editor else "document"
    graphics_editor = "Qt GUI" if args.native_graphics_editor else "document"
    semantics_editor = "Qt GUI" if args.native_semantics_editor else "document"
    sequences_editor = "Qt GUI" if args.native_sequences_editor else "document"
    sound_editor = "Qt GUI" if args.native_sound_editor else "document"
    print(f"Flicky isolated content build: zero edit, {level_editor} collision/player/special edit, "
          f"{graphics_editor} 1bpp edit/ROM readback, "
          f"{semantics_editor} text/CRAM edit/ROM readback, "
          f"{sequences_editor} mapping/animation delay/frame edit/ROM readback, "
          f"{sound_editor} SFX note/FM voice/music tempo edit/ROM readback, "
          "restore, relocate, source drift")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
