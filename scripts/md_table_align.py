#!/usr/bin/env python3
"""Align the pipe tables of a Markdown document, as `markdownlint` MD060 wants them.

Usage:
    scripts/md_table_align.py < in.md > /path/outside/the/repo/out.md
    printf '%s\n' '| a | b |' '|-|-|' '| long cell | c |' | scripts/md_table_align.py

Reads Markdown on stdin and writes it to stdout with every table padded so that its pipes
line up. It opens no file, so it cannot write one: redirect the output outside the repository,
compare it with the original, and apply the difference with the editing tool.

It exists because markdownlint-cli v0.49.1 reports MD060 and cannot fix it.

Rules:

    - A table is a run of lines starting with `|` whose 2nd line is a delimiter row
        (`|---|:--:|`). Any other run is left as it is
    - Tables inside a fenced code block are left as they are
    - The indentation of the first row (a table nested in a list) is kept for every row
    - Cells are split on every `|` unless it is escaped as `\\|`, also inside backticks,
        which is how GitHub renders a table
    - Column alignment markers (`:---`, `---:`, `:---:`) are kept
    - A row with fewer cells than the widest one is padded with empty cells
    - Widths are counted in characters, which is exact for ASCII and not for wide ones
"""
import argparse
import re
import sys

CELL_SPLIT = re.compile(r"(?<!\\)\|")
DELIMITER_CELL = re.compile(r"^:?-+:?$")
FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
MIN_WIDTH = 3


def split_row(line: str) -> list[str]:
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|") and not body.endswith("\\|"):
        body = body[:-1]
    return [cell.strip() for cell in CELL_SPLIT.split(body)]


def is_delimiter_row(cells: list[str]) -> bool:
    return bool(cells) and all(DELIMITER_CELL.match(cell) for cell in cells)


def is_table(block: list[str]) -> bool:
    return len(block) >= 2 and is_delimiter_row(split_row(block[1]))


def delimiter_cell(spec: str, width: int) -> str:
    left = spec.startswith(":")
    right = spec.endswith(":")
    dashes = width + 2 - left - right
    return (":" if left else "") + "-" * dashes + (":" if right else "")


def align_table(block: list[str]) -> list[str]:
    indent = block[0][: len(block[0]) - len(block[0].lstrip())]
    rows = [split_row(line) for line in block]
    columns = max(len(row) for row in rows)
    rows = [row + [""] * (columns - len(row)) for row in rows]
    delimiter = rows.pop(1)
    widths = [
        max(MIN_WIDTH, *(len(row[column]) for row in rows)) for column in range(columns)
    ]

    def render(row: list[str]) -> str:
        cells = (cell.ljust(widths[column]) for column, cell in enumerate(row))
        return indent + "| " + " | ".join(cells) + " |"

    delimiter_line = (
        indent
        + "|"
        + "|".join(delimiter_cell(spec, widths[column]) for column, spec in enumerate(delimiter))
        + "|"
    )
    return [render(rows[0]), delimiter_line, *(render(row) for row in rows[1:])]


def align_document(text: str) -> str:
    lines = text.split("\n")
    out: list[str] = []
    fence: tuple[str, int] | None = None
    i = 0
    while i < len(lines):
        line = lines[i]
        match = FENCE.match(line)
        if fence is not None:
            closes = (
                match is not None
                and match.group(1)[0] == fence[0]
                and len(match.group(1)) >= fence[1]
                and line.strip() == match.group(1)
            )
            if closes:
                fence = None
            out.append(line)
            i += 1
        elif match is not None:
            fence = (match.group(1)[0], len(match.group(1)))
            out.append(line)
            i += 1
        elif line.lstrip().startswith("|"):
            end = i
            while end < len(lines) and lines[end].lstrip().startswith("|"):
                end += 1
            block = lines[i:end]
            out.extend(align_table(block) if is_table(block) else block)
            i = end
        else:
            out.append(line)
            i += 1
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.parse_args()
    sys.stdout.write(align_document(sys.stdin.read()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
