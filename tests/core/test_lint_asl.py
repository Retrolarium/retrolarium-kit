"""The AS house style (core.lint.asl), as the Mega Drive games keep it."""

import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401  (src/ on sys.path)
from retrolarium_kit.core.lint import asl

STYLE = asl.AslStyle()
COMMENT_COLUMN = STYLE.comment_column


def normalize_line(line: str, depth: int, column: int | None = None) -> str:
    return asl._normalize_line(line, depth, column, STYLE)


def normalize_comment(comment: str) -> str:
    return asl._normalize_comment(comment)


def label_columns(lines: list[str], minimum: int) -> list[int]:
    return asl._label_columns(lines, asl.AslStyle(mnemonic_column=minimum))


def normalize_file(text: str) -> str:
    return asl.format_text(text, STYLE)


def check_lines(path: Path, lines: list[str], issues: list) -> None:
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / path.name
        source.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        issues.extend(asl.lint_file(source, STYLE))


class NormalizeLine(unittest.TestCase):
    def test_a_tab_inside_a_string_is_data_and_stays(self):
        formatted = normalize_line('\tdc.b\t"A\tB"\t; one\ttwo', 0)
        self.assertTrue(formatted.startswith(" " * 16 + 'dc.b    "A\tB"'))
        self.assertTrue(formatted.endswith("; one two"))
        self.assertEqual(1, formatted.count("\t"))

    def test_instruction_reaches_the_operand_column(self):
        self.assertEqual(
            normalize_line("  move.w  #$B000,d2", 0),
            " " * 16 + "move.w  #$B000,d2",
        )

    def test_short_mnemonic_is_padded_to_the_operand_field(self):
        self.assertEqual(
            normalize_line("                dc.w 3", 0),
            " " * 16 + "dc.w    3",
        )

    def test_a_long_mnemonic_keeps_one_space(self):
        # movea.l fills the field, so padding it would push the operand out of
        # line with everything else.
        self.assertEqual(
            normalize_line("        movea.l   d1,a6", 0),
            " " * 16 + "movea.l d1,a6",
        )

    def test_label_with_code_uses_the_given_column(self):
        self.assertEqual(
            normalize_line("Checksum: dc.w $B7E0", 0, 16),
            "Checksum:".ljust(16) + "dc.w    $B7E0",
        )

    def test_a_label_that_fills_its_column_still_gets_a_space(self):
        line = normalize_line("Sys_VectorTable: dc.l Foo", 0, 16)
        self.assertEqual(line, "Sys_VectorTable: dc.l    Foo")

    def test_without_a_column_a_label_takes_the_next_tab_stop(self):
        # 17 characters, so the next stop past the minimum of 16 is 20.
        line = normalize_line("Nem_BitMaskTable: dc.w 1", 0)
        self.assertEqual(line, "Nem_BitMaskTable:".ljust(20) + "dc.w    1")

    def test_equ_keeps_its_name_in_the_label_field(self):
        self.assertEqual(
            normalize_line("IO_CT1_CTRL equ $A10008", 0, 16),
            "IO_CT1_CTRL".ljust(16) + "equ     $A10008",
        )

    def test_macro_definition_keeps_its_name_in_the_label_field(self):
        self.assertEqual(
            normalize_line("cnop macro offset,alignment", 0, 16),
            "cnop".ljust(16) + "macro   offset,alignment",
        )

    def test_quoted_strings_are_never_reflowed(self):
        # Collapsing whitespace inside an operand once rewrote the ROM header
        # and moved 48,105 bytes. The operand must be copied verbatim.
        source = 'Name:   dc.b    "FLICKY     GM 00001022-00"'
        self.assertIn('"FLICKY     GM 00001022-00"', normalize_line(source, 0))

    def test_a_semicolon_inside_a_string_is_not_a_comment(self):
        # This line is real: bonus_objects.s holds `dc.b "; 250 PTS.=      PTS.",0`.
        source = 'a250PtsPts:     dc.b    "; 250 PTS.=      PTS.",0'
        self.assertEqual(normalize_line(source, 0, 16), source)

    def test_tabs_become_spaces(self):
        self.assertNotIn("\t", normalize_line('Art:\tbinclude\t"a.bin"', 0))


class Comments(unittest.TestCase):
    def test_an_inline_comment_lands_on_the_shared_column(self):
        line = normalize_line("                rts      ; done", 0)
        self.assertEqual(line.index(";"), COMMENT_COLUMN)

    def test_a_long_line_falls_back_to_two_spaces(self):
        code = " " * 16 + "move.l  (a0,d0.w),(Ram_LizardJumpVelocityXValue).w"
        line = normalize_line(code + " ; note", 0)
        self.assertGreater(len(code), COMMENT_COLUMN - 2)
        self.assertEqual(line, code + "  ; note")

    def test_one_space_after_the_semicolon(self):
        self.assertEqual(normalize_comment(";no space"), "; no space")
        self.assertEqual(normalize_comment(";    wide"), "; wide")

    def test_the_trailing_period_goes(self):
        self.assertEqual(normalize_comment("; a sentence."), "; a sentence")

    def test_a_banner_comment_is_left_alone(self):
        # ";;;" and ";---" are rules, not prose; normalizing them would eat them.
        for banner in (";;;;;;;;", ";--------", ";========"):
            self.assertEqual(normalize_comment(banner), banner)

    def test_a_whole_line_comment_is_indented_in_multiples_of_four(self):
        self.assertEqual(normalize_line("   ; note", 0), "    ; note")
        self.assertEqual(normalize_line("; note", 0), "; note")


class LabelColumns(unittest.TestCase):
    def test_a_run_of_labels_shares_one_column(self):
        # The longest member decides the column, and it steps to a tab stop
        # rather than stopping wherever that name happens to end.
        lines = ["Short: equ 1", "AnEvenMuchLongerName: equ 2", "Mid: equ 3"]
        columns = label_columns(lines, 16)
        self.assertEqual(len(set(columns)), 1)
        self.assertEqual(columns[0], 24)  # 20 characters, so the stop after 20

    def test_a_bare_label_does_not_break_a_run(self):
        # art.s alternates `Name: binclude ...` with `Name_End:`; treating the
        # end markers as separators would leave every include on its own.
        lines = ['A: binclude "a"', "A_End:", 'LongerName: binclude "b"', "LongerName_End:"]
        columns = label_columns(lines, 16)
        self.assertEqual(columns[0], columns[2])

    def test_an_instruction_separates_two_runs(self):
        lines = ["A: dc.w 1", "        rts", "AMuchLongerLabel: dc.w 2"]
        columns = label_columns(lines, 16)
        self.assertNotEqual(columns[0], columns[2])
        self.assertEqual(columns[0], 16)

    def test_a_blank_line_separates_two_runs(self):
        lines = ["A: dc.w 1", "", "AMuchLongerLabel: dc.w 2"]
        columns = label_columns(lines, 16)
        self.assertNotEqual(columns[0], columns[2])


class NormalizeFile(unittest.TestCase):
    def test_blank_runs_collapse_and_file_ends_with_one_newline(self):
        result = normalize_file("A:\n\n\n\nB:\n\n\n")
        self.assertEqual(result, "A:\n\nB:\n")

    def test_normalization_is_idempotent(self):
        once = normalize_file("  move.w  #1,d0\nLabel:  rts\n")
        self.assertEqual(normalize_file(once), once)

    def test_the_line_count_never_changes(self):
        # Formatting is whitespace and comment text only. Adding or dropping a
        # line would mean it is rewriting the program.
        source = "; header.\nLabel:  dc.w 1  ; was: word_1\nLabel_End:\n        rts   ; done.\n"
        self.assertEqual(
            len(normalize_file(source).splitlines()),
            len(source.splitlines()),
        )


class Checks(unittest.TestCase):
    def codes(self, lines: list[str]) -> set[str]:
        issues: list = []
        check_lines(Path("x.s"), lines, issues)
        return {issue.code for issue in issues}

    def test_tab_and_trailing_space_are_reported(self):
        found = self.codes(["\tmove.w  #1,d0   "])
        self.assertIn("tab", found)
        self.assertIn("trailing-space", found)

    def test_non_ascii_is_reported_rather_than_rewritten(self):
        self.assertIn("charset", self.codes(["                rts  ; \u043e\u043a"]))

    def test_a_trailing_period_is_reported(self):
        self.assertIn("comment-period", self.codes(["; a sentence."]))

    def test_a_misaligned_inline_comment_is_reported(self):
        self.assertIn("comment-column", self.codes([" " * 16 + "rts  ; done"]))

    def test_a_comment_indented_off_the_grid_is_reported(self):
        self.assertIn("comment-indent", self.codes(["   ; note"]))

    def test_a_correct_line_reports_nothing(self):
        line = " " * 16 + "rts" + " " * (COMMENT_COLUMN - 19) + "; done"
        self.assertEqual(self.codes([line]), set())


if __name__ == "__main__":
    unittest.main()
