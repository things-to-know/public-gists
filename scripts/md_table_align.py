#!/usr/bin/env python3
r"""Align the pipe tables of a Markdown document as `markdownlint` MD060 (`aligned`) wants them.

Usage:
    scripts/md_table_align.py < doc.md | diff -u doc.md -
    printf '%s\n' '| a | b |' '|-|-|' '| long cell | c |' | scripts/md_table_align.py

Reads Markdown on stdin and writes it to stdout with every misaligned table padded so that its
pipes line up. It opens no file, so it cannot write one: compare the output with the original
and apply the difference with the editing tool. Tables it leaves alone for a reason the author
should know about are reported on stderr with their line number.

It exists because markdownlint-cli reports MD060 for the `aligned` style and cannot fix it.
It needs Python 3.10 or later and nothing outside the standard library.

Rules:

    - A table is a line starting with `|` (header) and a delimiter row (`|---|:--:|`, its
        leading `|` optional) with the same number of cells, as GFM requires, and the lines
        starting with `|` after them. As in micromark, a delimiter row holds a `|` or a `:`:
        under paragraph text, `--` alone underlines a heading.
        It may interrupt paragraph text: of `| a |`, `| b |`, `|---|` the table is the last two
        lines. Other lines starting with `|` are paragraph text, left as they are
    - A table whose pipes already line up (as MD060 measures them) is left as it is
    - Front matter (as markdownlint finds it), fenced code blocks, HTML blocks and indented
        code blocks are left as they are; indentation is measured from the enclosing list
        item, also when a fence or an HTML block starts on the item's marker line
    - The indentation of the first row is kept for every row. A table that starts on a list
        item's marker line (`- | a |`) keeps the marker, and its other rows are indented under it
    - Block quotes and list items are read as micromark's document tokenizer reads them, one
        line at a time. A list item ends at a line indented less than its content, and fenced
        code or an HTML block in it ends with it; an item whose first line is empty ends at a
        blank line
    - Rows must stay in the table's list item: a less indented line ends the table, and so does
        one indented 4 or more columns further (code). A line that lazily continues a paragraph
        in a list item or block quote is paragraph text, never a table row, and so is a less
        indented line right after a table in a list item (micromark reads it as lazy)
    - As in micromark, a list item numbered other than 1, or an empty one, cannot interrupt
        paragraph text or indented code: `2. | a |` there is paragraph text, and a `-` under
        paragraph text underlines a heading. Neither can the second item of a line that
        interrupts: in `- 2. x` under paragraph text, `2. x` is the text of the `-` item.
        A block quote whose last line is in fenced code, an HTML block or indented code takes
        no lazy line, so a table can start right after it. A lone HTML tag on a lazy line
        starts an HTML block in the paragraph's containers, which a blank or a lazy line ends;
        indented code that starts on a lazy line ends with it
    - A lazy line can head a table whose delimiter row is back in the list item: its pipes
        cannot line up unless its header or its rows change containers, so it is reported
    - Left as they are, and reported: tables in a block quote, tables whose header has no
        leading `|` or is a lazy line, and body rows without a leading `|` (GFM continues a
        table onto any line that starts no other block) together with the lines after them
    - Cells are split on every `|` unless it is escaped by a backslash (`\|`), also inside
        backticks; `\\` is an escaped backslash, so `\\|` does split. This is how micromark
        (markdownlint's parser) and pandoc's GFM reader read a row
    - Cells are trimmed of spaces and tabs only, as Markdown trims them: U+00A0 or U+3000
        are content
    - Column alignment markers (`:---`, `---:`, `:---:`) are kept
    - The delimiter row sets the number of columns. A shorter row is padded with empty cells;
        the extra cells of a longer row, which GFM does not render, are kept as they are
        after the aligned ones and reported
    - Widths are counted in characters, which is exact for ASCII and not for wide ones
    - Line endings (LF, CRLF), a byte order mark and input bytes that are not UTF-8 pass
        through
"""
import argparse
import itertools
import re
import sys
from dataclasses import dataclass

if sys.version_info < (3, 10):  # noqa: UP036 - a clear error instead of a TypeError later
    sys.exit("md_table_align.py needs Python 3.10 or later")

SPACE = " \t"  # the only characters Markdown trims or indents with
TAB_STOP = 4
MIN_WIDTH = 3
DELIMITER_CELL = re.compile(r":?-+:?")
FENCE = re.compile(r"(`{3,}(?=[^`]*$)|~{3,})")  # a backtick fence has no backtick after it
LIST_MARKER = re.compile(r"(?:[-+*]|\d{1,9}[.)])(?=[ \t]|$)")
LINE = re.compile(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+\Z")
ATX_HEADING = re.compile(r"#{1,6}(?:[ \t]|$)")
SETEXT_UNDERLINE = re.compile(r"(?:=+|-+)[ \t]*")  # under paragraph text
THEMATIC_BREAK = re.compile(r"(?:(?:\*[ \t]*){3,}|(?:-[ \t]*){3,}|(?:_[ \t]*){3,})$")
FRONT_MATTER = {"---": ("---",), "+++": ("+++", "..."), "{": ("}",)}  # markdownlint's
BOM = "\N{ZERO WIDTH NO-BREAK SPACE}"

HTML_BLOCK_NAMES = (
    "address|article|aside|base|basefont|blockquote|body|caption|center|col|colgroup|dd|"
    "details|dialog|dir|div|dl|dt|fieldset|figcaption|figure|footer|form|frame|frameset|h[1-6]|"
    "head|header|hr|html|iframe|legend|li|link|main|menu|menuitem|nav|noframes|ol|optgroup|"
    "option|p|param|search|section|summary|table|tbody|td|tfoot|th|thead|title|tr|track|ul"
)
ATTRIBUTE = (
    r"""[ \t]+[A-Za-z_:][A-Za-z0-9_.:-]*"""
    r"""(?:[ \t]*=[ \t]*(?:[^ \t"'=<>`]+|'[^']*'|"[^"]*"))?"""
)
RAW_TAGS = r"(?:pre|script|style|textarea)"
HTML_BLOCKS = (  # CommonMark HTML blocks 1-6: how one starts, and what ends it (None: a blank line)
    (re.compile(rf"<{RAW_TAGS}(?:[ \t>]|$)", re.I), re.compile(rf"</{RAW_TAGS}>", re.I)),
    (re.compile(r"<!(?=--)"), re.compile(r"-->")),  # `<!-->` and `<!--->` close themselves
    (re.compile(r"<(?=\?)"), re.compile(r"\?>")),  # `<?>` closes itself
    (re.compile(r"<![A-Za-z]"), re.compile(r">")),
    (re.compile(r"<!\[CDATA\["), re.compile(r"\]\]>")),
    (re.compile(rf"</?(?:{HTML_BLOCK_NAMES})(?:[ \t>]|/>|$)", re.I), None),
)
HTML_TAG_LINE = re.compile(  # HTML block 7: a lone tag; it cannot interrupt a paragraph
    rf"(?:<(?!{RAW_TAGS}(?![A-Za-z0-9-]))[A-Za-z][A-Za-z0-9-]*(?:{ATTRIBUTE})*[ \t]*/?>"
    r"|</[A-Za-z][A-Za-z0-9-]*[ \t]*>)[ \t]*$",
    re.I,
)


def display_width(text: str) -> int:
    """Columns `text` takes, counted as characters: exact for ASCII, not for wide ones."""
    return len(text)


def columns(prefix: str) -> int:
    return len(prefix.expandtabs(TAB_STOP))


def unescaped_pipes(line: str) -> list[int]:
    """Indexes of the `|` that separate cells: a backslash escapes the character after it."""
    pipes = []
    i = 0
    while i < len(line):
        if line[i] == "\\":
            i += 2
            continue
        if line[i] == "|":
            pipes.append(i)
        i += 1
    return pipes


def split_row(line: str) -> list[str]:
    body = line.strip(SPACE)
    bounds = [-1, *unescaped_pipes(body), len(body)]
    cells = [body[start + 1 : end].strip(SPACE) for start, end in itertools.pairwise(bounds)]
    if body.startswith("|"):
        cells = cells[1:]
    if len(bounds) > 2 and bounds[-2] == len(body) - 1 and cells:
        cells = cells[:-1]
    return cells


def is_delimiter_row(row: str) -> bool:
    """Whether `row` delimits a table: as micromark wants, with a `|` or a `:` somewhere, since
    a row of dashes alone is a heading underline or a thematic break."""
    cells = split_row(row)
    return (
        bool(cells)
        and ("|" in row or ":" in row)
        and all(DELIMITER_CELL.fullmatch(cell) for cell in cells)
    )


def pipe_columns(line: str) -> list[int]:
    """Display column of each cell-separating `|`, adding up the pieces between them."""
    result = []
    column = previous = 0
    for i in unescaped_pipes(line):
        column += display_width(line[previous:i])
        result.append(column)
        previous = i
    return result


def is_aligned(block: list[str]) -> bool:
    """MD060's `aligned` check: every pipe of a row sits on a column of a header pipe."""
    header = set(pipe_columns(block[0]))
    for line in block[1:]:
        remaining = set(header)
        for column in pipe_columns(line):
            if remaining and column not in remaining:
                return False
            remaining.discard(column)
    return True


def delimiter_cell(spec: str, width: int) -> str:
    left = spec.startswith(":")
    right = spec.endswith(":")
    dashes = width + 2 - left - right
    return (":" if left else "") + "-" * dashes + (":" if right else "")


def pad(cell: str, width: int) -> str:
    return cell + " " * (width - display_width(cell))


def align_table(rows: list[str], first_prefix: str, indent: str) -> list[str]:
    header, delimiter, *body = map(split_row, rows)
    count = len(delimiter)
    widths = [
        max(MIN_WIDTH, *(display_width(row[i]) for row in (header, *body) if i < len(row)))
        for i in range(count)
    ]

    def render(prefix: str, row: list[str]) -> str:
        cells = row[:count] + [""] * (count - len(row))
        line = prefix + "| " + " | ".join(map(pad, cells, widths)) + " |"
        extra = row[count:]
        return line + (" " + " | ".join(extra) + " |" if extra else "")

    delimiter_line = indent + "|" + "|".join(map(delimiter_cell, delimiter, widths)) + "|"
    return [render(first_prefix, header), delimiter_line, *(render(indent, row) for row in body)]


def is_table(rows: list[str]) -> bool:
    if len(rows) < 2:
        return False
    return is_delimiter_row(rows[1]) and len(split_row(rows[0])) == len(split_row(rows[1]))


def interrupts(content: str, paragraph: bool = True) -> bool:
    """Whether `content` starts a block that ends a paragraph or (`paragraph` false) a table:
    a lone HTML tag ends a table, not a paragraph."""
    return bool(
        ATX_HEADING.match(content)
        or THEMATIC_BREAK.match(content)
        or content.startswith(">")
        or LIST_MARKER.match(content)
        or block_start(content, paragraph)
    )


def can_interrupt(line: str, marker: re.Match[str]) -> bool:
    """Whether the list item `marker` starts may interrupt a paragraph (in micromark, also
    indented code): a bullet or `1.`, with something on its first line."""
    return marker[0] in ("-", "+", "*", "1.", "1)") and bool(line[marker.end() :].strip(SPACE))


@dataclass
class Container:
    """An open block quote or list item, as micromark's document tokenizer keeps them."""

    quote: bool
    size: int = 0  # a list item's content column, relative to the content column around it
    marker: str = ""  # its list's `-`, `+` or `*`, or an ordered list's `.` or `)`
    initial_blank: bool = False  # the item's first line is blank
    further_blank: bool = False  # and a blank line followed it, so the item takes no more lines


def content_start(line: str, index: int) -> int:
    """Index of the first character after the spaces and tabs at `index`."""
    return len(line) - len(line[index:].lstrip(SPACE))


def quote_content(line: str, start: int) -> tuple[int, int]:
    """Index and column of a block quote's content, its `>` at `start`: one space after the `>`,
    or one column of a tab, belongs to the marker."""
    index = start + 1
    column = columns(line[:index])
    if line[index : index + 1] in (" ", "\t"):
        column += 1
        index += line[index] == " "  # the other columns of a tab indent the content
    return index, column


def list_item(
    line: str, index: int, column: int, interrupt: bool, sibling: Container | None = None
) -> tuple[Container, int, int] | None:
    """The list item that starts after `index` (`column`), with the index and column of its
    content, as micromark starts one: after up to 3 columns of indentation, with the marker of
    the list a `sibling` is in, and when it would `interrupt` a block, only as `can_interrupt`
    allows."""
    start = content_start(line, index)
    marker = LIST_MARKER.match(line, start)
    if marker is None or columns(line[:start]) - column > 3 or THEMATIC_BREAK.match(line, start):
        return None
    symbol = marker[0][-1]
    if (sibling is not None and symbol != sibling.marker) or (
        interrupt and not can_interrupt(line, marker)
    ):
        return None
    after = columns(line[: marker.end()])
    content = content_start(line, marker.end())
    if content == len(line):  # an empty first line
        return Container(False, after + 1 - column, symbol, True), content, after + 1
    if columns(line[:content]) - after > 4:  # one column is the marker's, the rest is code
        index = marker.end() + (line[marker.end()] == " ")
        return Container(False, after + 1 - column, symbol), index, after + 1
    width = columns(line[:content])
    return Container(False, width - column, symbol), content, width


def new_container(
    line: str, index: int, column: int, interrupt: bool
) -> tuple[Container, int, int] | None:
    """The block quote or list item that starts after `index`, as `list_item` reads one."""
    start = content_start(line, index)
    if line.startswith(">", start) and columns(line[:start]) - column <= 3:
        return Container(True), *quote_content(line, start)
    return list_item(line, index, column, interrupt)


def continuation(line: str, stack: list[Container], update: bool = False) -> tuple[int, int, int]:
    """How many containers of `stack` `line` continues, and the index and column its content
    starts at inside them. A blank line continues a list item, unless it began with one; with
    `update`, the items note the blank lines."""
    index = column = 0
    for depth, container in enumerate(stack):
        start = content_start(line, index)
        indent = columns(line[:start]) - column
        if container.quote:
            if indent > 3 or not line.startswith(">", start):
                return depth, index, column
            index, column = quote_content(line, start)
        elif start == len(line):
            if update:
                container.further_blank = container.further_blank or container.initial_blank
        else:
            further = container.further_blank
            if update:
                container.initial_blank = container.further_blank = False
            if further or indent < container.size:
                return depth, index, column
            column += container.size
    return len(stack), index, column


def leaf_start(content: str, interrupting: bool):
    """How the block that `content` starts ends, if it is a heading, a thematic break, fenced
    code or HTML: when `interrupting` a paragraph, a lone HTML tag starts none, and a setext
    underline makes the paragraph a heading."""
    if (
        ATX_HEADING.match(content)
        or THEMATIC_BREAK.match(content)
        or (interrupting and SETEXT_UNDERLINE.fullmatch(content))
    ):
        return ends_on_its_first_line
    return block_start(content, interrupting)


def format_block(block: list[str], start: int, first_line: int) -> list[str]:
    """Align `block` if it is a table; its first row starts at index `start` of `block[0]`."""
    if len(block) < 2:
        return block
    rows = [block[0][start:], *block[1:]]
    if not is_delimiter_row(rows[1]):
        return block
    header, delimiter = split_row(rows[0]), split_row(rows[1])
    if len(header) != len(delimiter):
        warn(first_line, f"header has {len(header)} cells and delimiter row {len(delimiter)}; "
             "GFM renders this as a paragraph, left as it is")
        return block
    for offset, row in enumerate(rows[2:], start=2):
        cells = len(split_row(row))
        if cells > len(delimiter):
            warn(first_line + offset, f"{cells} cells for {len(delimiter)} columns; "
                 "GFM does not render the extra ones (an unescaped `|`?), kept as they are")
    if is_aligned(block):
        return block
    prefix = block[0][:start]
    if not prefix.strip(SPACE):
        return align_table(rows, prefix, prefix)
    if "\t" in prefix:
        marker = len(prefix) - len(prefix.lstrip(SPACE))
        where = "in the indentation before" if "\t" in prefix[:marker] else "after"
        warn(first_line, f"a tab {where} the list marker; left as it is")
        return block
    return align_table(rows, prefix, " " * len(prefix))


def line_span(first: int, last: int) -> str:
    """A prefix for a warning that covers more than its first line."""
    return "" if first == last else f"lines {first}-{last}: "


def warn(line_number: int, message: str) -> None:
    print(f"stdin:{line_number}: {message}", file=sys.stderr)


def front_matter_end(lines: list[str]) -> int:
    """Index of the first line after the front matter, as markdownlint finds it, or 0."""
    closers = FRONT_MATTER.get(lines[0].rstrip()) if lines else None
    if closers:
        for i in range(1, len(lines)):
            if lines[i].rstrip() in closers:
                return i + 1
    return 0


def ends_at_blank(content: str, relative: int) -> bool:
    return not content


def ends_on_its_first_line(content: str, relative: int) -> bool:
    return True


def ends_with(end: re.Pattern):
    def closes(content: str, relative: int) -> bool:
        return end.search(content) is not None

    return closes


def ends_with_fence(char: str, length: int):
    def closes(content: str, relative: int) -> bool:
        match = FENCE.match(content)
        return (
            relative < 4
            and match is not None
            and match.group(1)[0] == char
            and len(match.group(1)) >= length
            and content.rstrip(SPACE) == match.group(1)
        )

    return closes


def block_start(content: str, paragraph: bool):
    """How the fenced code or HTML block starting with `content` ends; None if none starts."""
    match = FENCE.match(content)
    if match:
        return ends_with_fence(match.group(1)[0], len(match.group(1)))
    for start, end in HTML_BLOCKS:
        match = start.match(content)
        if match is None:
            continue
        if end is None:
            return ends_at_blank
        return ends_on_its_first_line if end.search(content, match.end()) else ends_with(end)
    if not paragraph and HTML_TAG_LINE.match(content):
        return ends_at_blank
    return None


PARAGRAPH, CODE, TABLE = "paragraph", "code", "table"  # the open blocks, besides fences and HTML


def align_document(text: str) -> str:
    """Align the tables of `text`. It follows micromark's document tokenizer line by line: the
    open block quotes and list items on a stack, and the open block (`leaf`) inside them."""
    bom = BOM if text.startswith(BOM) else ""
    lines = LINE.findall(text[len(bom) :])
    bodies = [line.rstrip("\r\n") for line in lines]
    endings = [line[len(body) :] for line, body in zip(lines, bodies, strict=True)]
    out = list(bodies)

    def delimiter(i: int, stack: list[Container]) -> str:
        """What line `i + 1` holds, if it can be a delimiter row under line `i`: in all the
        containers of `stack`, starting none (it would interrupt the table), indented less
        than 4 columns. Otherwise, ""."""
        if i + 1 == len(bodies):
            return ""
        line = bodies[i + 1]
        matched, index, column = continuation(line, stack)
        at = content_start(line, index)
        if (
            matched < len(stack)
            or columns(line[:at]) - column >= 4
            or new_container(line, index, column, True) is not None
        ):
            return ""
        return line[at:]

    def table_end(i: int, start: int, stack: list[Container], lazy: bool) -> int:
        """Where the table whose header starts at index `start` of line `i` ends, once aligned
        or reported; `stack` holds the containers of its rows, and of its header unless that is
        a `lazy` line."""
        header = bodies[i][start:]
        quoted = any(container.quote for container in stack)
        end = i + 2
        piped = None  # the first row without a leading `|`
        while end < len(bodies):  # nothing is open after a row: any container ends the table
            matched, index, column = continuation(bodies[end], stack)
            at = content_start(bodies[end], index)
            row = bodies[end][at:]
            if (
                matched < len(stack)
                or not row
                or columns(bodies[end][:at]) - column >= 4
                or interrupts(row, False)
            ):
                break
            if piped is None and not row.startswith("|"):
                piped = end
            end += 1
        piped = end if piped is None else piped
        if quoted:
            warn(i + 1, "a table in a block quote; left as it is")
        elif not header.startswith("|"):
            warn(i + 1, f"{line_span(i + 1, end)}a table without a leading `|`; left as it is")
        elif lazy:  # its pipes cannot line up unless its header or its rows change containers
            warn(i + 1, f"{line_span(i + 1, end)}a table whose header is less indented than its "
                 "list item, unlike its rows; left as it is")
        else:
            out[i:piped] = format_block(bodies[i:piped], start, i + 1)
            if end > piped:
                warn(piped + 1, f"{line_span(piped + 1, end)}the table above continues without "
                     "a leading `|`; not aligned")
        return end

    stack: list[Container] = []
    leaf = None  # PARAGRAPH, CODE, TABLE (after its last row), or how fenced code or HTML ends
    i = front_matter_end(bodies)
    while i < len(bodies):
        line = bodies[i]
        matched, index, column = continuation(line, stack, update=True)
        if matched < len(stack) and not stack[matched].quote:  # perhaps the list's next item
            item = list_item(line, index, column, False, stack[matched])
            if item is not None:
                stack[matched:] = [item[0]]
                index, column = item[1], item[2]
                matched, leaf = len(stack), None  # micromark closes the open block
        if matched == len(stack) and callable(leaf):  # fenced code or HTML takes the line
            start = content_start(line, index)
            if leaf(line[start:], columns(line[:start]) - column):
                leaf = None
            i += 1
            continue
        # A paragraph or indented code still open in the same containers is interrupted
        interrupt = matched == len(stack) and leaf in (PARAGRAPH, CODE)
        if (found := new_container(line, index, column, interrupt)) is not None:
            del stack[matched:]
            leaf = None
            while found is not None:
                container, index, column = found
                stack.append(container)
                found = new_container(line, index, column, interrupt)
            matched = len(stack)
        lazy = matched < len(stack)  # it misses containers, and starts none
        start = content_start(line, index)
        content = line[start:]
        code = columns(line[:start]) - column >= 4
        paragraph = leaf == PARAGRAPH
        if leaf == CODE and not lazy and (code or not content):
            i += 1
            continue
        ends = leaf_start(content, paragraph and not lazy) if content and not code else None
        # A lone HTML tag on a lazy line starts HTML in the paragraph's containers, as does a
        # lazy line that heads a table whose delimiter row is back in them
        tag = paragraph and lazy and ends is ends_at_blank and block_start(content, True) is None
        within = stack if paragraph else stack[:matched]
        table = False
        if content and not code and ends is None and not (lazy and leaf == TABLE):
            below = delimiter(i, within)  # no table starts on a lazy line right after one
            table = is_table([content, below])
            if not table and content.startswith("|") and not any(c.quote for c in within):
                format_block([content, below], 0, i + 1)  # for its warning, if any
            elif table and paragraph and not lazy and (fresh := block_start(content, False)):
                ends, table = fresh, False  # a table ends the paragraph; a lone HTML tag starts
        if paragraph and content and (code or (ends is None and not table)):
            i += 1  # more of the paragraph, also on a lazy line
            continue
        if not (paragraph and (table or tag)):
            del stack[matched:]  # a lazy line that starts a block ends the containers it misses
        if table:
            leaf, i = TABLE, table_end(i, start, within, paragraph and lazy)
            continue
        if not content:
            leaf = None
        elif code:  # micromark ends code that starts on a lazy line at its end
            leaf = None if lazy else CODE
        elif ends is not None:
            leaf = None if ends is ends_on_its_first_line else ends
        else:
            leaf = PARAGRAPH
        i += 1
    return bom + "".join(body + ending for body, ending in zip(out, endings, strict=True))


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.parse_args()
    text = sys.stdin.buffer.read().decode("utf-8", "surrogateescape")
    sys.stdout.buffer.write(align_document(text).encode("utf-8", "surrogateescape"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
