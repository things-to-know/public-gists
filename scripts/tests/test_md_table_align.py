#!/usr/bin/env python3
"""
Tests for the Markdown table aligner.

Run from the repository root, with no third-party dependency (the virtualenv here has no
pytest, and these need none):

    python3 -m unittest discover --start-directory scripts/tests

The module under test is `scripts/md_table_align.py`, loaded by path like the other script
tests, so that it does not depend on `scripts/` being importable.

Every fixture is synthetic, so nothing here names a company, customer or account.
"""
import importlib.util
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

    def test_leaves_prose_and_the_final_newline_untouched(self):
        self.assertEqual(align("lorem ipsum\n\ndolor\n"), "lorem ipsum\n\ndolor\n")
        self.assertEqual(align("lorem ipsum"), "lorem ipsum")


if __name__ == "__main__":
    unittest.main()
