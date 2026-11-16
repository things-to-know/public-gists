#!/usr/bin/env python3
"""
Tests for the Markdown table aligner.

Run from the repository root, with no third-party dependency (the virtualenv here has no
pytest, and these need none):

    python3 -m unittest discover --start-directory scripts/tests

The module under test is `scripts/md_table_align.py`, loaded by path, so that it does not
depend on `scripts/` being importable.

Every fixture is synthetic, so nothing here names a company, customer or account.
"""
import contextlib
import importlib.util
import io
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "scripts" / "md_table_align.py"

spec = importlib.util.spec_from_file_location("md_table_align", MODULE_PATH)
assert spec is not None and spec.loader is not None
md_table_align = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = md_table_align
spec.loader.exec_module(md_table_align)

align = md_table_align.align_document


def align_with_warnings(text: str) -> tuple[str, str]:
    """The aligned text and what the script reported on stderr."""
    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr):
        out = align(text)
    return out, stderr.getvalue()


class AlignTest(unittest.TestCase):
    def test_pads_every_cell_to_the_widest_one_in_its_column(self):
        text = (
            "| name | value |\n"
            "|--|--|\n"
            "| `lorem-ipsum` | 1 |\n"
        )
        expected = (
            "| name          | value |\n"
            "|---------------|-------|\n"
            "| `lorem-ipsum` | 1     |\n"
        )
        self.assertEqual(align(text), expected)

    def test_keeps_alignment_markers(self):
        text = "| a | b | c |\n|:--|--:|:-:|\n| lorem | ipsum | dolor |\n"
        expected = (
            "| a     | b     | c     |\n"
            "|:------|------:|:-----:|\n"
            "| lorem | ipsum | dolor |\n"
        )
        self.assertEqual(align(text), expected)

    def test_a_header_only_table_gets_the_minimum_width(self):
        text = "| a |\n|-|\n"
        self.assertEqual(align(text), "| a   |\n|-----|\n")

    def test_is_idempotent(self):
        text = (
            "| name | value |\n"
            "|--|--|\n"
            "| `lorem-ipsum` | 1 |\n"
        )
        once = align(text)
        self.assertEqual(align(once), once)

    def test_keeps_the_indentation_of_a_table_nested_in_a_list(self):
        text = (
            "- item:\n"
            "\n"
            "  | a | b |\n"
            "  |-|-|\n"
            "  | lorem | 1 |\n"
        )
        expected = (
            "- item:\n"
            "\n"
            "  | a     | b   |\n"
            "  |-------|-----|\n"
            "  | lorem | 1   |\n"
        )
        self.assertEqual(align(text), expected)

    def test_an_escaped_pipe_stays_inside_its_cell(self):
        text = "| command | note |\n|-|-|\n| `a \\| b` | ok |\n"
        expected = (
            "| command  | note |\n"
            "|----------|------|\n"
            "| `a \\| b` | ok   |\n"
        )
        self.assertEqual(align(text), expected)

    def test_pads_a_short_row_with_empty_cells(self):
        text = "| a | b |\n|-|-|\n| lorem |\n"
        expected = (
            "| a     | b   |\n"
            "|-------|-----|\n"
            "| lorem |     |\n"
        )
        self.assertEqual(align(text), expected)

    def test_aligns_a_delimiter_row_without_a_leading_pipe(self):
        text = "| a | b |\n--- | :-:\n| lorem | 1 |\n"
        expected = (
            "| a     | b   |\n"
            "|-------|:---:|\n"
            "| lorem | 1   |\n"
        )
        self.assertEqual(align_with_warnings(text), (expected, ""))


class LeaveAloneTest(unittest.TestCase):
    def test_leaves_a_table_inside_a_fence_untouched(self):
        text = "```markdown\n| a | b |\n|-|-|\n| lorem | 1 |\n```\n"
        self.assertEqual(align(text), text)

    def test_a_longer_fence_is_not_closed_by_a_shorter_one(self):
        text = (
            "````markdown\n"
            "```text\n"
            "| a | b |\n"
            "|-|-|\n"
            "```\n"
            "| c | d |\n"
            "|-|-|\n"
            "````\n"
        )
        self.assertEqual(align(text), text)

    def test_a_table_after_a_fence_is_aligned(self):
        text = "```text\nx\n```\n\n| a | b |\n|-|-|\n| lorem | 1 |\n"
        expected = (
            "```text\nx\n```\n\n"
            "| a     | b   |\n"
            "|-------|-----|\n"
            "| lorem | 1   |\n"
        )
        self.assertEqual(align(text), expected)

    def test_leaves_a_run_without_a_delimiter_row_untouched(self):
        text = "| not | a table |\n| just | lines |\n"
        self.assertEqual(align(text), text)

    def test_a_row_of_dashes_alone_underlines_a_heading(self):
        for text in ("| a |\n--\n", "a |\n--\n", "> Title\n> ---\n"):
            with self.subTest(text=text):
                self.assertEqual(align_with_warnings(text), (text, ""))

    def test_leaves_prose_and_the_final_newline_untouched(self):
        self.assertEqual(align("lorem ipsum\n\ndolor\n"), "lorem ipsum\n\ndolor\n")
        self.assertEqual(align("lorem ipsum"), "lorem ipsum")


# The classes below follow the rules of the script's docstring, one class per rule. Each case
# checks the output and stderr together, so that a stray warning fails it too.

TABLE = "| a | b |\n|-|-|\n| long | x |\n"
ALIGNED = "| a    | b   |\n|------|-----|\n| long | x   |\n"


class Case(unittest.TestCase):
    def check(self, text: str, expected: str | None = None, warnings: str = "") -> None:
        """`text` aligns to `expected` (unchanged if None), reporting `warnings`."""
        expected = text if expected is None else expected
        self.assertEqual(align_with_warnings(text), (expected, warnings))


class TableTest(Case):
    """A header, a delimiter row with as many cells, and the rows starting with `|` after them."""

    def test_a_table_may_interrupt_paragraph_text(self):
        self.check("text\n" + TABLE, "text\n" + ALIGNED)

    def test_of_several_pipe_lines_the_table_is_the_last_two(self):
        self.check("| a |\n| long |\n|-|\n", "| a |\n| long |\n|------|\n")

    def test_a_second_delimiter_row_is_a_body_row(self):
        self.check(
            TABLE + "| c | d |\n|-|-|\n",
            ALIGNED + "| c    | d   |\n| -    | -   |\n",
        )

    def test_spaces_around_delimiter_cells_are_dropped(self):
        self.check("| a | b |\n| - | - |\n| long | x |\n", ALIGNED)

    def test_a_space_inside_a_delimiter_cell_makes_no_table(self):
        self.check("| a | b |\n| -- - | - |\n| long | x |\n")

    def test_a_header_and_a_delimiter_row_of_different_widths_make_no_table(self):
        self.check(
            "| a | b |\n|-|\n| long | x |\n",
            warnings="stdin:1: header has 2 cells and delimiter row 1; GFM renders this as a "
            "paragraph, left as it is\n",
        )

    def test_a_table_whose_pipes_already_line_up_is_left_alone(self):
        self.check("| a  | b |\n|----|---|\n| xx | y |\n")


class OtherBlocksTest(Case):
    """Front matter, fenced code, HTML blocks and indented code are left as they are."""

    def test_front_matter(self):
        self.check("---\ntitle: x\n---\n" + TABLE, "---\ntitle: x\n---\n" + ALIGNED)

    def test_unclosed_front_matter_is_a_thematic_break(self):
        self.check("---\n" + TABLE, "---\n" + ALIGNED)

    def test_html_blocks(self):
        for text in (
            "<div>\n" + TABLE + "</div>\n",  # type 6, which ends at a blank line
            "<pre>\n" + TABLE + "</pre>\n",  # type 1, which ends at its closing tag
            "<!--\n" + TABLE + "-->\n",  # type 2
        ):
            with self.subTest(text=text):
                self.check(text)

    def test_a_table_right_after_an_html_block_that_closed(self):
        for html in ("<!-- c -->\n", "<pre>\nx\n</pre>\n", "<!--\n| a |\n|-|\n-->\n"):
            with self.subTest(html=html):
                self.check(html + TABLE, html + ALIGNED)

    def test_a_lone_html_tag_ends_a_table(self):
        self.check(TABLE + "<span>\n", ALIGNED + "<span>\n")

    def test_indented_code(self):
        self.check("    code\n" + TABLE, "    code\n" + ALIGNED)

    def test_indented_code_in_a_list_item(self):
        self.check("- x\n\n      | a | b |\n      |-|-|\n      | long | x |\n")

    def test_fenced_code_in_a_list_item(self):
        fence = "- x\n  ```\n  | a | b |\n  |-|-|\n  ```\n\n"
        self.check(fence + TABLE, fence + ALIGNED)

    def test_fenced_code_on_a_list_items_marker_line(self):
        self.check("- ```\n  | a | b |\n  |-|-|\n  | long | x |\n  ```\n")


class ListItemTest(Case):
    """Indentation is measured from the list item, and the rows stay in it."""

    def test_a_table_on_a_marker_line_keeps_the_marker(self):
        for marker in ("-", "1.", "- -", "-  "):
            indent = " " * (len(marker) + 1)
            text = f"{marker} | a | b |\n{indent}|-|-|\n{indent}| long | x |\n"
            expected = f"{marker} | a    | b   |\n{indent}|------|-----|\n{indent}| long | x   |\n"
            with self.subTest(marker=marker):
                self.check(text, expected)

    def test_a_table_under_an_empty_items_marker_line(self):
        self.check(
            "-\n  | a | b |\n  |-|-|\n  | long | x |\n",
            "-\n  | a    | b   |\n  |------|-----|\n  | long | x   |\n",
        )

    def test_a_table_after_a_list_at_the_top_level(self):
        self.check("- item\n\n" + TABLE, "- item\n\n" + ALIGNED)

    def test_a_list_item_holding_only_a_thematic_break_ends_at_the_next_line(self):
        for line in ("- ---\n", "* * *\n"):  # the second is a thematic break, not a list
            with self.subTest(line=line):
                self.check(line + TABLE, line + ALIGNED)

    def test_a_row_indented_4_columns_more_is_code_and_ends_the_table(self):
        self.check(
            "- | a | b |\n  |-|-|\n      | long | x |\n",
            "- | a   | b   |\n  |-----|-----|\n      | long | x |\n",
        )

    def test_the_next_list_item_ends_the_table(self):
        self.check(
            "- | a | b |\n  |-|-|\n  | long | x |\n- next\n",
            "- | a    | b   |\n  |------|-----|\n  | long | x   |\n- next\n",
        )

    def test_a_less_indented_line_right_after_a_table_in_an_item_starts_no_table(self):
        self.check("- | a |\n  |---|\n| b |\n|---|\n| c |\n")

    def test_but_the_line_after_it_may(self):
        self.check(
            "- | a |\n  |---|\nfoo\n| b |\n|-|\n",
            "- | a |\n  |---|\nfoo\n| b   |\n|-----|\n",
        )

    def test_a_less_indented_line_ends_the_item_and_its_rows_are_outside_it(self):
        self.check(
            "- x\n\n | a | b |\n|-|-|\n| long | x |\n",
            "- x\n\n | a    | b   |\n |------|-----|\n | long | x   |\n",
        )

    def test_an_item_whose_first_line_is_empty_ends_at_a_blank_line(self):
        self.check(
            "-\n\n  | a | b |\n|-|-|\n| long | x |\n",
            "-\n\n  | a    | b   |\n  |------|-----|\n  | long | x   |\n",
        )

    def test_fenced_code_in_an_item_ends_with_the_item(self):
        self.check("1. ```\n | a | b |\n|-|-|\n", "1. ```\n | a   | b   |\n |-----|-----|\n")


class InterruptTest(Case):
    """Lazy lines, and which list items may interrupt paragraph text, as micromark reads them."""

    def test_a_lazy_line_in_a_list_item_is_paragraph_text(self):
        self.check("- item\n" + TABLE)

    def test_an_item_numbered_other_than_1_cannot_interrupt_a_paragraph(self):
        for marker in ("2.", "10."):
            indent = " " * (len(marker) + 1)
            text = f"para\n{marker} | a | b |\n{indent}|-|-|\n{indent}| long | x |\n"
            with self.subTest(marker=marker):
                self.check(text)

    def test_an_item_numbered_1_can(self):
        self.check(
            "para\n1. | a | b |\n   |-|-|\n   | long | x |\n",
            "para\n1. | a    | b   |\n   |------|-----|\n   | long | x   |\n",
        )

    def test_a_lone_dash_under_a_paragraph_underlines_a_heading(self):
        self.check("para\n-\n" + TABLE, "para\n-\n" + ALIGNED)

    def test_a_table_right_after_a_heading(self):
        for heading in ("# h\n", "para\n===\n"):
            with self.subTest(heading=heading):
                self.check(heading + TABLE, heading + ALIGNED)

    def test_every_item_a_line_starts_must_be_able_to_interrupt(self):
        # micromark keeps interrupting for the second marker: `2.` starts no list in the `-`
        self.check(
            "text\n- 2. x\n  | long | x |\n    |-|-|\n",
            "text\n- 2. x\n  | long | x   |\n  |------|-----|\n",
        )

    def test_a_lazy_line_indented_4_columns_continues_the_paragraph(self):
        self.check("> text\n    - x\n" + TABLE)

    def test_a_lone_html_tag_on_a_lazy_line_is_one_line_of_html(self):
        self.check("- x\n<span>\n" + TABLE, "- x\n<span>\n" + ALIGNED)

    def test_a_lone_html_tag_a_table_would_interrupt_a_paragraph_at_starts_html(self):
        # The table ends the paragraph; read again, the line starts an HTML block first
        self.check("text\n<span>\n|-|\n| long |\n")

    def test_a_lazy_line_may_head_a_table_whose_rows_are_in_the_containers(self):
        self.check(
            "- x\n| a | b |\n  |-|-|\n  | long | x |\n",
            warnings="stdin:2: lines 2-4: a table whose header is less indented than its list "
            "item, unlike its rows; left as it is\n",
        )
        self.check(
            "> x\n| a | b |\n> |-|-|\n",
            warnings="stdin:2: a table in a block quote; left as it is\n",
        )

    def test_a_heading_or_a_thematic_break_is_no_table_header(self):
        for text in ("# h\n|-|\n", "---\n:--\n", ">\n> |-|\n"):
            with self.subTest(text=text):
                self.check(text)


class BlockQuoteTest(Case):
    """Tables in a block quote are reported, not aligned."""

    def test_a_table_in_a_block_quote_is_reported(self):
        for text in ("> | a | b |\n> |-|-|\n> | long | x |\n", "- > | a | b |\n  > |-|-|\n"):
            with self.subTest(text=text):
                self.check(text, warnings="stdin:1: a table in a block quote; left as it is\n")

    def test_a_lazy_line_after_a_quoted_paragraph_is_paragraph_text(self):
        self.check("> q\n" + TABLE)

    def test_a_quote_ending_in_code_takes_no_lazy_line(self):
        for quote in ("> ```\n> x\n> ```\n", ">     code\n"):
            with self.subTest(quote=quote):
                self.check(quote + TABLE, quote + ALIGNED)


class ReportedTest(Case):
    """Tables left as they are, and reported, for the author to fix."""

    def test_a_table_without_a_leading_pipe(self):
        self.check(
            "a | b\n--|--\nlong | x\n",
            warnings="stdin:1: lines 1-3: a table without a leading `|`; left as it is\n",
        )
        self.check(
            "a |\n:--\n",
            warnings="stdin:1: lines 1-2: a table without a leading `|`; left as it is\n",
        )

    def test_body_rows_without_a_leading_pipe(self):
        self.check(
            TABLE + "c | d\n",
            ALIGNED + "c | d\n",
            "stdin:4: the table above continues without a leading `|`; not aligned\n",
        )

    def test_a_tab_on_the_marker_line_says_where_it_is(self):
        before = "2: a tab in the indentation before"
        for text, warning in (
            ("-\t| a | b |\n\t|-|-|\n\t| long | x |\n", "1: a tab after"),
            ("- x\n  -\t| a | b |\n  \t|-|-|\n  \t| long | x |\n", "2: a tab after"),
            ("- x\n\t- | a | b |\n\t  |-|-|\n\t  | long | x |\n", before),
        ):
            with self.subTest(text=text):
                self.check(text, warnings=f"stdin:{warning} the list marker; left as it is\n")


class CellTest(Case):
    """How a row splits into cells, and how wide a cell is."""

    def test_an_escaped_backslash_does_not_escape_the_pipe_after_it(self):
        self.check(
            "| a | b |\n|-|-|\n| x \\\\| y |\n",
            "| a    | b   |\n|------|-----|\n| x \\\\ | y   |\n",
        )

    def test_an_escaped_pipe_in_the_header(self):
        self.check("| a \\| b |\n|-|\n| long |\n", "| a \\| b |\n|--------|\n| long   |\n")

    def test_a_row_of_a_lone_pipe_is_one_empty_cell(self):
        self.check("| a |\n|-|\n|\n", "| a   |\n|-----|\n|     |\n")

    def test_extra_cells_are_kept_after_the_aligned_ones_and_reported(self):
        warning = (
            "stdin:3: 3 cells for 2 columns; GFM does not render the extra ones (an unescaped "
            "`|`?), kept as they are\n"
        )
        head = "| a   | b   |\n|-----|-----|\n"
        for row, expected in (
            ("| c | d | e |\n", "| c   | d   | e |\n"),
            ("| c | d | |\n", "| c   | d   |  |\n"),
            ("| c | d | e\n", "| c   | d   | e |\n"),
        ):
            with self.subTest(row=row):
                self.check("| a | b |\n|-|-|\n" + row, head + expected, warning)

    def test_widths(self):
        head = "| a | b |\n|-|-|\n"
        for cell, expected in (
            ("日本語", "| a      | b   |\n|--------|-----|\n| 日本語 | x   |\n"),  # wide: 2 each
            ("x\ty", "| a   | b   |\n|-----|-----|\n| x\ty  | x   |\n"),  # a control: 0
            ("\xa0x", "| a   | b   |\n|-----|-----|\n| \xa0x  | x   |\n"),  # U+00A0 is content
        ):
            with self.subTest(cell=cell):
                self.check(head + f"| {cell} | x |\n", expected)

    def test_a_byte_sequence_that_is_not_utf_8_counts_as_one_replacement_character(self):
        self.check(  # E2 82, a truncated sequence, as `surrogateescape` decodes it
            "| a | b |\n|-|-|\n| \udce2\udc82 | y |\n",
            "| a   | b   |\n|-----|-----|\n| \udce2\udc82   | y   |\n",
        )


class LineEndingTest(Case):
    """Line endings and a byte order mark pass through."""

    def test_line_endings(self):
        for ending in ("\r\n", "\r"):
            with self.subTest(ending=ending):
                self.check(TABLE.replace("\n", ending), ALIGNED.replace("\n", ending))

    def test_a_byte_order_mark(self):
        self.check("\ufeff" + TABLE, "\ufeff" + ALIGNED)

    def test_no_final_newline(self):
        self.check(TABLE.rstrip("\n"), ALIGNED.rstrip("\n"))

    def test_bytes_that_are_not_utf_8_pass_through_main(self):
        text = b"caf\xe9\n\n| a | b |\n|-|-|\n| \xe2\x82 | y |\n"
        expected = b"caf\xe9\n\n| a   | b   |\n|-----|-----|\n| \xe2\x82   | y   |\n"
        result = subprocess.run(
            [sys.executable, "-I", str(MODULE_PATH)], input=text, capture_output=True, check=True
        )
        self.assertEqual((result.stdout, result.stderr), (expected, b""))


if __name__ == "__main__":
    unittest.main()
