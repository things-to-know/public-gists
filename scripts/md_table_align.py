#!/usr/bin/env python3
r"""Align the pipe tables of a Markdown document as `markdownlint` MD060 (`aligned`) wants them.

Usage:
    scripts/md_table_align.py < doc.md | diff -u doc.md -
    printf '%s\n' '| a | b |' '|-|-|' '| long cell | c |' | scripts/md_table_align.py

Reads Markdown on stdin and writes it to stdout with every table that MD060 would report padded
so that its pipes line up. It opens no file, so it cannot write one: compare the output with the
original and apply the difference with the editing tool. Tables it leaves alone for a reason the
author should know about are reported on stderr with their line number.

It exists because markdownlint-cli reports MD060 for the `aligned` style and cannot fix it.
It needs Python 3.10 or later and nothing outside the standard library.

Rules:

    - A table is a line starting with `|` (header) and a delimiter row (`|---|:--:|`, its
        leading `|` optional) with the same number of cells, as GFM requires, and the lines
        starting with `|` after them. As in micromark, a delimiter row holds a `|` or a `:`:
        under paragraph text, `--` alone underlines a heading.
        It may interrupt paragraph text: of `| a |`, `| b |`, `|---|` the table is the last two
        lines. Other lines starting with `|` are paragraph text, left as they are
    - A table is aligned when MD060 would report it: when a row after the header has a pipe off
        the header's pipes, on a line where markdownlint's comments leave MD060 enabled. It is
        then aligned whole, rows on lines where MD060 is disabled included
    - Those comments are read as markdownlint reads them: on every line after the front matter,
        in code too, naming MD060 as `MD060`, `table-column-style` or `table` in any case, or
        naming no rule at all (`<!-- markdownlint-disable -->`). `markdownlint-configure-file`
        comments and markdownlint's configuration files are not read: MD060's `aligned` style
        is assumed
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
    - Widths are display columns per grapheme cluster, counted as the `string-width` package
        MD060 measures with counts them: wide (CJK) characters and emoji count 2, an Indic
        conjunct or a decomposed Hangul syllable counts as one character, and combining marks
        and controls count 0. The emoji and cluster tables below were generated with Node
        22.23.4, whose ICU 78.3 implements Unicode 17; the Unicode version changes with Node's
        ICU. Everything else comes from this Python's Unicode data (15.1 on Python 3.13, 16.0
        on 3.14, 17.0 on 3.15), so a character it does not know yet, or whose width a later
        version changed, can count differently: on Python 3.13 a Unicode 16 emoji counts 1
        instead of 2, the trigrams at U+2630 (wide since Unicode 16) count 1, and a combining
        mark added later (U+0897) counts 1, not 0
    - Line endings (LF, CRLF), a byte order mark and input bytes that are not UTF-8 pass
        through; a byte sequence that is not UTF-8 counts as the one U+FFFD Node reads it as
"""
import argparse
import itertools
import re
import sys
import unicodedata
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
INLINE_CONFIG = re.compile(  # markdownlint's comments; what they name runs to the next `-->`
    r"(<!--\s*markdownlint-(disable|enable|capture|restore|disable-file|enable-file|disable-line"
    r"|disable-next-line|configure-file))(?:\s|-->)",
    re.I,
)
MD060_NAMES = frozenset({"MD060", "TABLE-COLUMN-STYLE", "TABLE"})  # its name, alias and tag

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


def code_points(ranges: str) -> frozenset[int]:
    points: set[int] = set()
    for item in ranges.split():
        first, _, last = item.partition("-")
        points.update(range(int(first, 16), int(last or first, 16) + 1))
    return frozenset(points)


# UAX #29 GB9c, Indic conjuncts: consonant, linker (a virama), consonant are one cluster.
INCB_CONSONANTS = code_points(
    "915-939 958-95f 978-97f 995-9a8 9aa-9b0 9b2 9b6-9b9 9dc-9dd 9df 9f0-9f1 a95-aa8 aaa-ab0 "
    "ab2-ab3 ab5-ab9 af9 b15-b28 b2a-b30 b32-b33 b35-b39 b5c-b5d b5f b71 c15-c28 c2a-c39 "
    "c58-c5a d15-d3a 1000-102a 103f 1050-1055 105a-105d 1061 1065-1066 106e-1070 1075-1081 "
    "108e 1780-17b3 1a20-1a54 1b0b-1b0c 1b13-1b33 1b45-1b4c 1b83-1ba0 1bae-1baf 1bbb-1bbd "
    "a989-a98b a98f-a9b2 a9e0-a9e4 a9e7-a9ef a9fa-a9fe aa60-aa6f aa71-aa73 aa7a aa7e-aa7f "
    "aae0-aaea abc0-abda 10a00 10a10-10a13 10a15-10a17 10a19-10a35 11103-11126 11144 11147 "
    "11380-11389 1138b 1138e 11390-113b5 11900-11906 11909 1190c-11913 11915-11916 "
    "11918-1192f 11a00 11a0b-11a32 11a50 11a5c-11a83 11f04-11f10 11f12-11f33"
)
# Linkers (viramas) of GB9c.
INCB_LINKERS = code_points(
    "94d 9cd acd b4d c4d d4d 1039 17d2 1a60 1b44 1bab a9c0 aaf6 10a3f 11133 113d0 1193e 11a47 "
    "11a99 11f42"
)
# Spacing marks that extend a cluster as nonspacing marks do (Grapheme_Extend, U+09BE).
INCB_SPACING_EXTEND = code_points(
    "9be 9d7 b3e b57 bbe bd7 cc0 cc2 cc7-cc8 cca-ccb cd5-cd6 d3e d57 dcf ddf 1715 1734 1b35 "
    "1b3b 1b3d 1b43-1b44 1baa 1bf2-1bf3 302e-302f a953 a9c0 111c0 11235 1133e 1134d 11357 "
    "113b8 113c2 113c5 113c7-113c9 113cf 114b0 114bd 115af 116b6 11930 1193d 11f41 "
    "16ff0-16ff1 1d165-1d166 1d16d-1d172"
)
# UAX #29 GB9b: characters that join the one after them (U+0D4E MALAYALAM LETTER DOT REPH).
PREPEND = code_points(
    "600-605 6dd 70f 890-891 8e2 d4e 110bd 110cd 111c2-111c3 113d1 1193f 11941 11a84-11a89 "
    "11d46 11f02"
)
# Extended_Pictographic: what a ZWJ joins, and what makes a ZWJ sequence 2 columns wide.
EXTENDED_PICTOGRAPHIC = code_points(
    "a9 ae 203c 2049 2122 2139 2194-2199 21a9-21aa 231a-231b 2328 23cf 23e9-23f3 23f8-23fa "
    "24c2 25aa-25ab 25b6 25c0 25fb-25fe 2600-2604 260e 2611 2614-2615 2618 261d 2620 "
    "2622-2623 2626 262a 262e-262f 2638-263a 2640 2642 2648-2653 265f-2660 2663 2665-2666 "
    "2668 267b 267e-267f 2692-2697 2699 269b-269c 26a0-26a1 26a7 26aa-26ab 26b0-26b1 "
    "26bd-26be 26c4-26c5 26c8 26ce-26cf 26d1 26d3-26d4 26e9-26ea 26f0-26f5 26f7-26fa 26fd "
    "2702 2705 2708-270d 270f 2712 2714 2716 271d 2721 2728 2733-2734 2744 2747 274c 274e "
    "2753-2755 2757 2763-2764 2795-2797 27a1 27b0 27bf 2934-2935 2b05-2b07 2b1b-2b1c 2b50 "
    "2b55 3030 303d 3297 3299 1f004 1f02c-1f02f 1f094-1f09f 1f0af-1f0b0 1f0c0 1f0cf-1f0d0 "
    "1f0f6-1f0ff 1f170-1f171 1f17e-1f17f 1f18e 1f191-1f19a 1f1ae-1f1e5 1f201-1f20f 1f21a "
    "1f22f 1f232-1f23a 1f23c-1f23f 1f249-1f25f 1f266-1f321 1f324-1f393 1f396-1f397 "
    "1f399-1f39b 1f39e-1f3f0 1f3f3-1f3f5 1f3f7-1f3fa 1f400-1f4fd 1f4ff-1f53d 1f549-1f54e "
    "1f550-1f567 1f56f-1f570 1f573-1f57a 1f587 1f58a-1f58d 1f590 1f595-1f596 1f5a4-1f5a5 "
    "1f5a8 1f5b1-1f5b2 1f5bc 1f5c2-1f5c4 1f5d1-1f5d3 1f5dc-1f5de 1f5e1 1f5e3 1f5e8 1f5ef "
    "1f5f3 1f5fa-1f64f 1f680-1f6c5 1f6cb-1f6d2 1f6d5-1f6e5 1f6e9 1f6eb-1f6f0 1f6f3-1f6ff "
    "1f7da-1f7ff 1f80c-1f80f 1f848-1f84f 1f85a-1f85f 1f888-1f88f 1f8ae-1f8af 1f8bc-1f8bf "
    "1f8c2-1f8cf 1f8d9-1f8ff 1f90c-1f93a 1f93c-1f945 1f947-1f9ff 1fa58-1fa5f 1fa6e-1faff "
    "1fc00-1fffd"
)
# Characters 1 column wide alone and 2 with U+FE0F after them, as RGI emoji (U+26A0).
EMOJI_WITH_VS16 = code_points(
    "a9 ae 203c 2049 2122 2139 2194-2199 21a9-21aa 2328 23cf 23ed-23ef 23f1-23f2 23f8-23fa "
    "24c2 25aa-25ab 25b6 25c0 25fb-25fc 2600-2604 260e 2611 2618 261d 2620 2622-2623 2626 "
    "262a 262e-262f 2638-263a 2640 2642 265f-2660 2663 2665-2666 2668 267b 267e 2692 "
    "2694-2697 2699 269b-269c 26a0 26a7 26b0-26b1 26c8 26cf 26d1 26d3 26e9 26f0-26f1 26f4 "
    "26f7-26f9 2702 2708-2709 270c-270d 270f 2712 2714 2716 271d 2721 2733-2734 2744 2747 "
    "2763-2764 27a1 2934-2935 2b05-2b07 1f170-1f171 1f17e-1f17f 1f321 1f324-1f32c 1f336 1f37d "
    "1f396-1f397 1f399-1f39b 1f39e-1f39f 1f3cb-1f3ce 1f3d4-1f3df 1f3f3 1f3f5 1f3f7 1f43f "
    "1f441 1f4fd 1f549-1f54a 1f56f-1f570 1f573-1f579 1f587 1f58a-1f58d 1f590 1f5a5 1f5a8 "
    "1f5b1-1f5b2 1f5bc 1f5c2-1f5c4 1f5d1-1f5d3 1f5dc-1f5de 1f5e1 1f5e3 1f5e8 1f5ef 1f5f3 "
    "1f5fa 1f6cb 1f6cd-1f6cf 1f6e0-1f6e5 1f6e9 1f6f0 1f6f3"
)
# Characters 1 column wide alone and 2 with a skin tone after them (U+261D).
SKIN_TONE_BASES = code_points(
    "261d 26f9 270c-270d 1f3cb-1f3cc 1f574-1f575 1f590"
)
# Regional indicator pairs that are RGI flags, 2 columns; any other pair is 1.
FLAGS = frozenset(
    (
        "AC AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN "
        "BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CP CQ CR CU CV CW CX CY "
        "CZ DE DG DJ DK DM DO DZ EA EC EE EG EH ER ES ET EU FI FJ FK FM FO FR GA GB GD GE GF GG "
        "GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU IC ID IE IL IM IN IO IQ IR IS "
        "IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC "
        "MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO "
        "NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD "
        "SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TA TC TD TF TG TH TJ TK TL TM TN TO "
        "TR TT TV TW TZ UA UG UM UN US UY UZ VA VC VE VG VI VN VU WF WS XK YE YT ZA ZM ZW"
    ).split()
)
ZERO_WIDTH_CATEGORIES = frozenset({"Mn", "Me", "Mc", "Cc", "Cf", "Cs"})
ZERO_WIDTH_LETTERS = frozenset({0x115F, 0x1160, 0x3164, 0xFFA0})  # Hangul fillers
ZWJ = 0x200D
VS16 = 0xFE0F
KEYCAP = 0x20E3
SKIN_TONES = range(0x1F3FB, 0x1F400)
HANGUL_JOINS = {
    ("L", "L"), ("L", "V"), ("L", "LV"), ("L", "LVT"),
    ("LV", "V"), ("LV", "T"), ("V", "V"), ("V", "T"), ("LVT", "T"), ("T", "T"),
}


def category(code: int) -> str:
    return unicodedata.category(chr(code))


def zero_width(code: int) -> bool:
    return code in ZERO_WIDTH_LETTERS or category(code) in ZERO_WIDTH_CATEGORIES


def east_asian_width(code: int) -> int:
    return 2 if unicodedata.east_asian_width(chr(code)) in ("W", "F") else 1


def hangul(code: int) -> str:
    if 0x1100 <= code <= 0x115F or 0xA960 <= code <= 0xA97C:
        return "L"
    if 0x1160 <= code <= 0x11A7 or 0xD7B0 <= code <= 0xD7C6:
        return "V"
    if 0x11A8 <= code <= 0x11FF or 0xD7CB <= code <= 0xD7FB:
        return "T"
    if 0xAC00 <= code <= 0xD7A3:
        return "LV" if (code - 0xAC00) % 28 == 0 else "LVT"
    return ""


def grapheme_extend(code: int) -> bool:
    """Grapheme_Cluster_Break Extend: marks, the spacing marks that extend (U+09BE), ZWNJ,
    skin tones and tags. It keeps an emoji ZWJ sequence or (but for ZWNJ) a conjunct going."""
    return (
        category(code) in ("Mn", "Me")
        or code in INCB_SPACING_EXTEND
        or code in (0x200C, 0xFF9E, 0xFF9F)
        or code in SKIN_TONES
        or 0xE0020 <= code <= 0xE007F
    )


def extends(code: int) -> bool:
    """Never starts a cluster: Grapheme_Cluster_Break Extend, ZWJ or SpacingMark."""
    spacing_mark = category(code) == "Mc" or code in (0x0E33, 0x0EB3)
    return grapheme_extend(code) or code == ZWJ or spacing_mark


def control(code: int) -> bool:
    """Grapheme_Cluster_Break Control, approximately: nothing joins after it."""
    kind = category(code)
    if kind == "Cf":
        return not extends(code) and code not in PREPEND
    return kind in ("Cc", "Zl", "Zp")


def regional(code: int) -> bool:
    return 0x1F1E6 <= code <= 0x1F1FF


def clusters(text: str):
    """Extended grapheme clusters (UAX #29) of `text`, as lists of code points, approximately."""
    cluster: list[int] = []
    regionals = 0  # regional indicators at the end of the cluster
    conjunct = 0  # 1 after an Indic consonant, 2 once a linker follows it
    pictograph = 0  # 1 after an Extended_Pictographic, 2 once a ZWJ follows it (rule GB11)
    for char in text:
        code = ord(char)
        if cluster:
            previous = cluster[-1]
            joins = not control(previous) and (
                extends(code)
                or (hangul(previous), hangul(code)) in HANGUL_JOINS
                or (previous in PREPEND and not control(code))
                or (pictograph == 2 and code in EXTENDED_PICTOGRAPHIC)
                or (conjunct == 2 and code in INCB_CONSONANTS)
                or (regional(code) and regionals % 2 == 1)
            )
            if not joins:
                yield cluster
                cluster, regionals, conjunct, pictograph = [], 0, 0, 0
        cluster.append(code)
        regionals = regionals + 1 if regional(code) else 0
        if code in EXTENDED_PICTOGRAPHIC:
            pictograph = 1
        elif code == ZWJ:
            pictograph = 2 if pictograph == 1 else 0
        elif not (pictograph == 1 and grapheme_extend(code)):
            pictograph = 0
        if code in INCB_CONSONANTS:
            conjunct = 1
        elif code in INCB_LINKERS and conjunct:
            conjunct = 2
        elif not (conjunct and (code == ZWJ or (grapheme_extend(code) and code != 0x200C))):
            conjunct = 0
    if cluster:
        yield cluster


def flag(cluster: list[int]) -> str:
    """The two letters a pair of regional indicators spells (U+1F1E8 U+1F1F1 is CL)."""
    return "".join(chr(code - 0x1F1E6 + ord("A")) for code in cluster)


def emoji(cluster: list[int]) -> bool:
    """Whether `string-width` counts a multi-code-point cluster as a 2-column emoji."""
    base = cluster[0]
    return len(cluster) > 1 and (
        (chr(base) in "0123456789#*" and cluster[1:] in ([VS16, KEYCAP], [KEYCAP]))
        or (cluster[1:] == [VS16] and base in EMOJI_WITH_VS16)
        or (len(cluster) == 2 and cluster[1] in SKIN_TONES and base in SKIN_TONE_BASES)
        or (len(cluster) == 2 and all(map(regional, cluster)) and flag(cluster) in FLAGS)
        or (ZWJ in cluster and sum(code in EXTENDED_PICTOGRAPHIC for code in cluster) >= 2)
    )


def jamo_width(codes: list[int]) -> int:
    """Hangul jamo: a leading consonant, a vowel and an optional trailing consonant take 2."""
    width = 0
    k = 0
    while k < len(codes):
        kind = hangul(codes[k])
        if kind not in ("L", "V", "T"):
            return width + sum(map(east_asian_width, codes[k:]))
        if kind == "L" and k + 1 < len(codes) and hangul(codes[k + 1]) == "V":
            width += 2
            k += 3 if k + 2 < len(codes) and hangul(codes[k + 2]) == "T" else 2
            continue
        width += east_asian_width(codes[k])
        k += 1
    return width


def cluster_width(cluster: list[int]) -> int:
    if all(map(zero_width, cluster)):
        return 0
    if emoji(cluster):
        return 2
    while zero_width(cluster[0]):
        cluster = cluster[1:]
    if hangul(cluster[0]) in ("L", "V", "T"):
        return jamo_width([code for code in cluster if not zero_width(code)])
    halfwidth = sum(east_asian_width(code) for code in cluster[1:] if 0xFF00 <= code <= 0xFFEF)
    return east_asian_width(cluster[0]) + halfwidth


def display_width(text: str) -> int:
    """Columns `text` takes in a monospaced editor, as `string-width` (MD060) counts them."""
    if text.isascii() and text.isprintable():
        return len(text)
    if any(0xDC80 <= ord(char) <= 0xDCFF for char in text):  # bytes that are not UTF-8
        text = text.encode("utf-8", "surrogateescape").decode("utf-8", "replace")
    return sum(map(cluster_width, clusters(text)))


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
    """Display column of each cell-separating `|`, adding up the pieces between them: no
    cluster crosses a `|` (marks after one join it, and they are zero width)."""
    result = []
    column = previous = 0
    for i in unescaped_pipes(line):
        column += display_width(line[previous:i])
        result.append(column)
        previous = i
    return result


def misaligned_rows(block: list[str]) -> list[int]:
    """MD060's `aligned` check: the offsets in `block` of the rows with a pipe off the columns
    of the header's pipes. MD060 reports each on its own line, and never the header."""
    header = set(pipe_columns(block[0]))
    offsets = []
    for offset, line in enumerate(block[1:], start=1):
        remaining = set(header)
        for column in pipe_columns(line):
            if remaining and column not in remaining:
                offsets.append(offset)
                break
            remaining.discard(column)
    return offsets


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


def format_block(block: list[str], start: int, first_line: int, enabled: list[bool]) -> list[str]:
    """Align `block` if it is a table that MD060 would report; its first row starts at index
    `start` of `block[0]`, and `enabled` tells, by line index, where MD060 is enabled."""
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
    if not any(enabled[first_line - 1 + offset] for offset in misaligned_rows(block)):
        return block  # aligned already, or MD060 is disabled on every line it would report
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


def inline_config(line: str):
    """The action and the names of each markdownlint comment on `line`, as markdownlint finds
    them: one with no `-->` after it on the line ends the search."""
    position = 0
    while match := INLINE_CONFIG.search(line, position):
        end = line.find("-->", match.end(1))
        if end == -1:
            return
        yield match[2].upper(), line[match.end(1) : end]
        position = match.end()


def names_md060(names: str) -> bool:
    """Whether a comment naming `names` reaches MD060: no name at all means every rule."""
    words = names.upper().split()
    return not words or not MD060_NAMES.isdisjoint(words)


def md060_enabled(lines: list[str], body: int) -> list[bool]:
    """For each line, whether markdownlint's comments leave MD060 enabled on it. Like
    markdownlint, this reads every line from index `body` on (after the front matter), in code
    too, in 3 passes."""
    comments = [
        [action for action, names in inline_config(line)
         if action in ("CAPTURE", "RESTORE") or names_md060(names)]  # those two take no names
        if number >= body else []
        for number, line in enumerate(lines)
    ]
    enabled = True
    for line in comments:  # the `-file` comments set the state the document starts in
        for action in line:
            if action in ("ENABLE-FILE", "DISABLE-FILE"):
                enabled = action == "ENABLE-FILE"
    captured = True  # so a `restore` before any `capture` undoes the `-file` comments
    states = []
    for line in comments:  # each comment takes effect on its own line
        for action in line:
            if action == "CAPTURE":
                captured = enabled
            elif action == "RESTORE":
                enabled = captured
            elif action in ("ENABLE", "DISABLE"):
                enabled = action == "ENABLE"
        states.append(enabled)
    for number, line in enumerate(comments):
        for action in line:
            target = number + (action == "DISABLE-NEXT-LINE")
            if action in ("DISABLE-LINE", "DISABLE-NEXT-LINE") and target < len(states):
                states[target] = False
    return states


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
    enabled = md060_enabled(bodies, front_matter_end(bodies))

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
            out[i:piped] = format_block(bodies[i:piped], start, i + 1, enabled)
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
                format_block([content, below], 0, i + 1, enabled)  # for its warning, if any
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
