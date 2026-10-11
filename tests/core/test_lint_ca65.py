"""The ca65 house style (core.lint.ca65), as the NES games keep it."""

import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401  (src/ on sys.path)
from retrolarium_kit.core.lint import ca65


def lint(source: str) -> set[str]:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory, "source.asm")
        path.write_bytes(source.encode("utf-8"))
        return {issue.code for issue in ca65.lint_file(path)}


class Ca65StyleTests(unittest.TestCase):
    def test_nested_source_passes(self) -> None:
        source = (
            ".if ENABLE_DEMO\n"
            "handler_demo_entry:\n"
            "    LDA #$01  ; Select demo mode\n"
            "    .repeat 2\n"
            "        NOP\n"
            "    .endrepeat\n"
            ".endif\n"
        )
        self.assertEqual(lint(source), set())

    def test_common_violations_are_reported(self) -> None:
        codes = lint(";Bad heading\nBad: lda #$00 ;bad comment\n\n\n")
        self.assertTrue({"blank-lines", "comment-space", "final-newline",
                         "inline-comment-gap", "label-line"} <= codes)

    def test_non_ascii_text_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "unicode.asm")
            path.write_bytes("; Привет\n".encode("utf-8"))
            issues = ca65.lint_file(path)
        self.assertTrue(any(issue.code == "ascii-only" and "U+041F" in issue.message for issue in issues))

    def test_crlf_tabs_and_trailing_spaces_are_reported(self) -> None:
        codes = lint("label:\r\n\tNOP \r\n")
        self.assertTrue({"line-ending", "tab", "trailing-whitespace"} <= codes)

    def test_leading_blank_line_and_terminal_period_are_reported(self) -> None:
        self.assertIn("file-start", lint("\n; Header\n"))
        self.assertIn("comment-period", lint("; First sentence. Second sentence.\n"))

    def test_an_address_dump_comment_is_refused(self) -> None:
        self.assertIn("machine-code-comment", lint("    NOP  ; $C000 EA\n"))

    def test_case_and_indentation_are_checked(self) -> None:
        codes = lint("  label:\n  nop\n.BYTE 1\n  value = 2\n.proc a\n.endproc\n.scope\n")
        self.assertTrue({"label-indent", "mnemonic-case", "instruction-indent", "directive-case",
                         "directive-indent", "assignment-indent", "block-depth"} <= codes)

    def test_the_formatter_produces_the_style(self) -> None:
        source = "Start: lda #$01 ;load.\n  .BYTE 1\n\n\n    ; note\n    nop ; $C000 EA\n"
        formatted = ca65.format_text(source)
        self.assertEqual(formatted, "Start:\n    LDA #$01  ; load\n    .byte 1\n\n; note\n    NOP\n")
        self.assertEqual(ca65.format_text(formatted), formatted)
        self.assertEqual(lint(formatted), set())

    def test_a_label_comment_moves_above_the_label(self) -> None:
        self.assertEqual(ca65.format_text("Entry:  ; starts here\n    RTS\n"),
                         "; starts here\nEntry:\n    RTS\n")

    def test_a_semicolon_inside_a_string_stays_data(self) -> None:
        self.assertEqual(ca65.split_comment('    .byte "a;b"  ; text'), ('    .byte "a;b"  ', " text"))


if __name__ == "__main__":
    unittest.main()
