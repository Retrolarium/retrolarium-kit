"""Routine candidates retain operands, target positions and platform context."""

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

import helpers  # noqa: F401
from retrolarium_kit.cli import main
from retrolarium_kit.core.assembly_similarity import AssemblySimilarityError, scan


NES = """.proc {name}
 LDA #{value}
 STA $20
 LDX #3
@loop:
 DEX
 BNE @loop
 RTS
.endproc
"""
M68K = """{name}:
 moveq #1,d0
 move.w d0,d1
{label}:
 subq.w #1,d1
 bne.s {label}
 add.w d1,d0
 rts
; End of function {name}
"""


class SimilarityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "src").mkdir()

    def write(self, name, text):
        path = self.root / "src" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def scan(self, platform="nes", **settings):
        return scan(self.root, [], platform=platform, **settings)

    def test_nes_renamed_local_branch_targets_are_the_same_source(self):
        self.write("a.asm", NES.format(name="First", value=1))
        self.write("b.asm", NES.format(name="Second", value="$01").replace("@loop", "@again"))
        report = self.scan()
        self.assertEqual(report["counts"]["routines"], 2)
        self.assertEqual(report["matches"][0]["kind"], "same-normalized-source")
        self.assertEqual(report["matches"][0]["operand_score"], 1)
        self.assertFalse(report["byte_identity_verified"])

    def test_different_constants_and_external_calls_remain_distinct(self):
        for name, value in (("a", 1), ("b", 2)):
            body = NES.format(name=name, value=value).replace("STA $20", f"JSR Helper_{name}")
            self.write(name + ".asm", body)
        report = self.scan()
        self.assertEqual(report["matches"][0]["kind"], "similar-instructions")
        self.assertLess(report["matches"][0]["operand_score"], 1)

    def test_genesis_named_and_anonymous_branch_targets_match(self):
        self.write("s3.s", M68K.format(name="First", label="NamedLoop"))
        self.write("sk.s", M68K.format(name="Second", label="-"))
        report = self.scan("genesis")
        self.assertEqual(report["counts"]["routines"], 2)
        self.assertEqual(report["matches"][0]["kind"], "same-normalized-source")
        self.assertEqual(report["matches"][0]["left"]["boundary"], "explicit")

    def test_a_different_branch_target_is_not_an_exact_match(self):
        self.write("a.s", M68K.format(name="First", label="Again"))
        self.write("b.s", M68K.format(name="Second", label="Again").replace("bne.s Again", "bne.s Second"))
        report = self.scan("genesis")
        self.assertTrue(all(row["kind"] != "same-normalized-source" for row in report["matches"]))

    def test_snes_long_calls_return_and_declared_widths(self):
        source = """.a16
.i16
.proc {name}
 LDA #$1234
 PHA
 JSL RenderFrame
 PLA
 STA $7e1000
 RTL
.endproc
"""
        self.write("a.asm", source.format(name="First"))
        self.write("b.asm", source.format(name="Second"))
        report = self.scan("snes")
        self.assertEqual(report["matches"][0]["kind"], "same-normalized-source")
        self.write("b.asm", source.format(name="Second").replace(".a16", ".a8"))
        report = self.scan("snes")
        self.assertTrue(all(row["kind"] != "same-normalized-source" for row in report["matches"]))

    def test_rep_sep_width_state_is_part_of_snes_instruction_context(self):
        body = "REP #$30\nLDA #1\nPHA\nPLA\nSTA $2000\nRTL\n"
        self.write("a.asm", body)
        self.write("b.asm", body.replace("#$30", "#$10"))
        report = self.scan("snes", operand_threshold=0)
        self.assertTrue(all(row["kind"] != "same-normalized-source" for row in report["matches"]))

    def test_macro_bodies_data_and_other_cpus_do_not_form_routines(self):
        body = NES.format(name="First", value=1)
        self.write("macro.asm", ".macro Hidden\n" + body + ".endmacro\n")
        self.write("data.asm", "LDA #1\nSTA $20\nLDX #3\n.byte 1,2\nDEX\nNOP\nRTS\n")
        report = self.scan()
        self.assertEqual(report["counts"]["routines"], 0)
        self.assertEqual(report["diagnostics"]["skipped_macro_or_repeat_blocks"], 1)
        self.write("z80.s", "cpu Z80\n" + M68K.format(name="WrongCpu", label="Loop"))
        self.assertEqual(self.scan("genesis")["counts"]["routines"], 0)

    def test_conditions_are_separate_inferred_blocks(self):
        body = NES.format(name="First", value=1)
        self.write("a.asm", ".if FIRST\n" + body + ".else\n" + body.replace("First", "Second") + ".endif\n")
        report = self.scan()
        self.assertEqual(report["counts"]["routines"], 2)
        self.assertFalse(report["conditional_assembly_evaluated"])
        self.assertTrue(all(row["left"]["boundary"].endswith("-conditional") for row in report["matches"]))

    def test_source_filters_and_deterministic_report(self):
        for path, name in (("s3/a.s", "First"), ("sk/a.s", "Second"), ("shared/a.s", "Third")):
            self.write(path, M68K.format(name=name, label="Loop"))
        settings = {"left": "src/s3/*", "right": "src/sk/*"}
        report = self.scan("genesis", **settings)
        self.assertEqual(report["counts"]["matches"], 1)
        self.assertEqual(report, self.scan("genesis", **settings))

    def test_inline_end_marker_keeps_the_terminal_instruction(self):
        source = M68K.format(name="First", label="Again").replace(
            " rts\n; End of function First", " rts ; End of function First"
        )
        self.write("a.s", source)
        self.write("b.s", M68K.format(name="Second", label="OtherLoop"))
        report = self.scan("genesis")
        self.assertEqual(report["counts"]["routines"], 2)
        self.assertEqual(report["matches"][0]["left"]["instructions"], 6)
        self.assertEqual(report["matches"][0]["kind"], "same-normalized-source")

    def test_scan_from_root_excludes_generated_and_vendor_sources(self):
        self.write("a.asm", NES.format(name="First", value=1))
        self.write("b.asm", NES.format(name="Second", value=1))
        for directory in ("build", "vendor", "src/build"):
            path = self.root / directory / "generated.asm"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(NES.format(name="Generated", value=1), encoding="utf-8")
        report = scan(self.root, ["."], platform="nes")
        self.assertEqual(report["counts"]["files"], 2)
        self.assertEqual(report["counts"]["matches"], 1)

    def test_macro_spellings_and_unmatched_side_patterns(self):
        for name, first, last in (("a", "macro Hidden()", "endmacro"), ("b", ".rept 2", ".endr")):
            self.write(name + ".asm", first + "\n" + NES.format(name="Hidden", value=1) + last + "\n")
        self.assertEqual(self.scan()["counts"]["routines"], 0)
        with self.assertRaisesRegex(AssemblySimilarityError, "side pattern"):
            self.scan(left="missing/*", right="src/*")

    def test_two_instruction_similarity_uses_short_seeds(self):
        self.write("a.asm", "First:\n LDA #1\n RTS\n")
        self.write("b.asm", "Second:\n LDA #2\n RTS\n")
        report = self.scan(minimum=2)
        self.assertEqual(report["counts"]["matches"], 1)
        self.assertEqual(report["matches"][0]["kind"], "similar-instructions")

    def test_invalid_paths_settings_and_candidate_explosion_fail(self):
        self.write("a.asm", NES.format(name="First", value=1))
        for settings in ({"minimum": 1}, {"threshold": 2}, {"left": "src/*"}, {"platform": "unknown"}):
            with self.subTest(settings=settings), self.assertRaises(AssemblySimilarityError):
                scan(self.root, [], **({"platform": "nes"} | settings))
        with self.assertRaises(AssemblySimilarityError):
            scan(self.root, ["../missing"], platform="nes")
        for i in range(66):
            self.write(f"{i:02}.asm", NES.format(name=f"Routine{i}", value=1))
        with self.assertRaisesRegex(AssemblySimilarityError, "limit exceeded"):
            self.scan(max_candidates=10)

    def test_cli_writes_a_structured_report_and_handles_errors(self):
        self.write("a.asm", NES.format(name="First", value=1))
        self.write("b.asm", NES.format(name="Second", value=1))
        output = self.root / "matches.json"
        with contextlib.redirect_stdout(io.StringIO()):
            status = main(["asm", "similar", "--root", str(self.root), "--platform", "nes", "--json", str(output)])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output.read_text())["counts"]["matches"], 1)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(
                main(["asm", "similar", "--root", str(self.root), "--platform", "nes", "--min-instructions", "1"]), 1
            )
