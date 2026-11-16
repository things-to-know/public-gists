"""Check md_table_align.py against markdownlint itself, in the markdownlint-cli2 image.

Usage, from the repository root:
    python3 -I scripts/tests/md_table_align/oracle.py [--script SCRIPT.py]... SOURCE [--show N]

Each `--script` is a version of md_table_align.py to check, by default scripts/md_table_align.py;
an earlier one comes from `git show <commit>:scripts/md_table_align.py`. SOURCE is one of:
    --tests                 the documents of the unit tests, except those with a markdownlint
                            comment that names no rule (it would disable table-lines.cjs too)
    --fuzz COUNT SEED       documents from fuzz.py's generator
    --comments COUNT SEED   the same, with markdownlint's disable comments among the lines
    --tables COUNT SEED     the same, with more table rows among the lines
    --cases FILE...         the minimal documents in fuzz.py's outputs

markdownlint lints each document and each script's output for it, in batches, in a container
with no network, no mounts and a memory cap. A custom rule (table-lines.cjs) reports the
tables micromark finds; MD060 runs with the `aligned` style. A script's result is a problem
when:
    - it changes a line outside a table, or in a table MD060 does not report
    - MD060 still reports a line of its output, outside the tables it reports as left alone
    - its warnings about block quotes, missing leading pipes and continued tables differ from
        what micromark's tables call for, or a warning about cells or tabs is off a table
    - the tables' rows move
"""
import ast
import io
import json
import re
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent))  # for fuzz.py, under `python3 -I`
import fuzz  # noqa: E402

IMAGE = "davidanson/markdownlint-cli2:v0.23.3"
HERE = Path(__file__).resolve().parent
TESTS = HERE.parent
SCRIPT = TESTS.parent / "md_table_align.py"
CONFIG = {
    "config": {"default": False, "MD060": {"style": "aligned"}, "table-lines": True},
    "customRules": ["./table-lines.cjs"],
    "noBanner": True,
    "noProgress": True,
}
BATCH = 2000
RESULT = re.compile(r"^(d\d+)\.md:(\d+)(?::\d+)? (?:(?:error|warning) )?(\S+) (.*)$")
WARNING = re.compile(r"^stdin:(\d+): (?:lines (\d+)-(\d+): )?(.*)$", re.M)
COMMENTS = [
    "<!-- markdownlint-disable MD060 -->", "<!-- markdownlint-enable MD060 -->",
    "<!-- markdownlint-disable-next-line MD060 -->", "<!-- markdownlint-capture -->",
    "<!-- markdownlint-restore -->", "<!-- markdownlint-disable table-column-style -->",
    "<!-- markdownlint-disable-file TABLE -->", "<!-- markdownlint-enable-file md060 -->",
    "| c <!-- markdownlint-disable-line MD060 --> | d |",
    "| a <!-- markdownlint-disable-next-line MD060 --> | b |",
]
TABLE_LINES = ["| a | b |", "| long | x |", "|-|-|", "|:-|-:|", "| c | d |", "a | b", "--|--"]
NAMELESS = re.compile(r"markdownlint-\S+\s*-->")  # it would disable table-lines.cjs too


class Table(NamedTuple):
    quoted: bool
    rows: list[tuple[int, bool]]  # each row's line, and whether it starts with `|`

    @property
    def lines(self) -> range:
        return range(self.rows[0][0], self.rows[-1][0] + 1)


class Lint(NamedTuple):
    tables: list[Table]
    md060: set[int]  # the lines MD060 reports


def lint(texts: list[str]) -> list[Lint]:
    """What markdownlint finds in each of `texts`."""
    results = []
    for first in range(0, len(texts), BATCH):
        batch = texts[first : first + BATCH]
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as tar:
            files = {".markdownlint-cli2.jsonc": json.dumps(CONFIG).encode()}
            files["table-lines.cjs"] = (HERE / "table-lines.cjs").read_bytes()
            for n, text in enumerate(batch):
                files[f"d{n:05}.md"] = text.encode("utf-8", "surrogateescape")
            for name, data in files.items():
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        run = subprocess.run(
            ["docker", "run", "--rm", "-i", "--network", "none", "--memory", "1g",
             "--memory-swap", "1g", "--entrypoint", "sh", IMAGE, "-c",
             'mkdir /tmp/w && cd /tmp/w && tar -x && markdownlint-cli2 "*.md"'],
            input=buffer.getvalue(), capture_output=True, check=False,
        )
        if run.returncode not in (0, 1):
            raise RuntimeError(run.stderr.decode(errors="replace"))
        found = [Lint([], set()) for _ in batch]
        for line in (run.stdout + run.stderr).decode(errors="replace").splitlines():
            match = RESULT.match(line)
            if match is None:
                continue
            result = found[int(match[1][1:])]
            if match[3] == "table-lines":
                detail = json.loads(re.search(r"\[(\{.*\})\]$", match[4])[1])
                result.tables.append(Table(detail["quoted"], [tuple(r) for r in detail["rows"]]))
            elif match[3].startswith("MD060"):
                result.md060.add(int(match[2]))
            else:
                raise RuntimeError(f"unexpected result: {line}")
        results.extend(found)
    return results


def problems(text: str, out: str, err: str, before: Lint, after: Lint) -> list[str]:
    """How a script's `out` and `err` for `text` disagree with what markdownlint finds."""
    found = []
    lines, aligned = text.splitlines(), out.splitlines()
    if len(lines) != len(aligned):
        return ["the line count changed"]
    headers = {table.rows[0][0]: table for table in before.tables}
    rows = {line: table for table in before.tables for line, _ in table.rows}
    expected, allowed = set(), set()
    for table in before.tables:
        header, last = table.rows[0][0], table.rows[-1][0]
        if table.quoted:
            expected.add((header, "a table in a block quote", header))
            allowed.update(table.lines)
        elif not table.rows[0][1]:
            expected.add((header, "a table without a leading `|`", last))
            allowed.update(table.lines)
        else:
            unpiped = [line for line, piped in table.rows[2:] if not piped]
            if unpiped:
                expected.add((unpiped[0], "the table above continues", last))
                allowed.update(range(unpiped[0], last + 1))
    got = set()
    for match in WARNING.finditer(err):
        line, last, message = int(match[1]), int(match[3] or match[1]), match[4]
        kind = next((k for k in ("a table in a block quote", "a table without a leading `|`",
                                 "the table above continues") if message.startswith(k)), None)
        if kind is not None:
            got.add((line, kind, last))
        elif message.startswith(("a tab ", "a table whose header is less indented")):
            if line in headers:
                allowed.update(headers[line].lines)
            else:
                found.append(f"tab warning off a table header, line {line}")
        elif message.startswith("header has"):
            if line in headers:
                found.append(f"cell count warning on a table, line {line}")
        elif "cells for" in message:
            if line not in rows:
                found.append(f"extra cells warning off a table row, line {line}")
        else:
            found.append(f"unknown warning: {message}")
    if got - expected:
        found.append(f"unexpected warnings {sorted(got - expected)}")
    if expected - got:
        found.append(f"missing warnings {sorted(expected - got)}")
    for number, (a, b) in enumerate(zip(lines, aligned, strict=True), start=1):
        if a == b:
            continue
        if number not in rows:
            found.append(f"line {number} changed outside a table")
        elif not before.md060.intersection(rows[number].lines):
            found.append(f"line {number} changed in a table MD060 does not report")
    if after.md060 - allowed:
        found.append(f"MD060 still reports lines {sorted(after.md060 - allowed)}")
    if [[r[0] for r in t.rows] for t in before.tables] != [
        [r[0] for r in t.rows] for t in after.tables
    ]:
        found.append("the tables' rows moved")
    return found


def cases(paths: list[str]) -> list[str]:
    docs = []
    for path in paths:
        text = Path(path).read_text(encoding="utf-8", errors="surrogateescape")
        docs.extend(ast.literal_eval(doc) for doc in re.findall(r"^=== (.*)$", text, re.M))
    return list(dict.fromkeys(docs))


def fixtures() -> list[str]:
    """The documents the unit tests align."""
    sys.path.insert(0, str(TESTS))
    import unittest

    import test_md_table_align as tests

    seen = []
    original = tests.align

    def record(text):
        seen.append(text)
        return original(text)

    tests.align = record  # align_with_warnings calls it too
    suite = unittest.defaultTestLoader.loadTestsFromModule(tests)
    unittest.TextTestRunner(stream=io.StringIO()).run(suite)
    return [text for text in dict.fromkeys(seen) if not NAMELESS.search(text)]


def main() -> int:
    args = sys.argv[1:]
    scripts, show = [], 10
    while "--script" in args:
        at = args.index("--script")
        scripts.append(args[at + 1])
        del args[at : at + 2]
    if "--show" in args:
        at = args.index("--show")
        show = int(args[at + 1])
        del args[at : at + 2]
    scripts = scripts or [str(SCRIPT)]
    if args[0] == "--cases":
        texts = cases(args[1:])
    elif args[0] == "--tests":
        texts = fixtures()
    else:
        count, seed = int(args[1]), int(args[2])
        contents = {"--comments": fuzz.CONTENTS + COMMENTS * 3,
                    "--tables": fuzz.CONTENTS + TABLE_LINES * 6}.get(args[0], fuzz.CONTENTS)
        texts = ["".join(f"{x}\n" for x in doc) for doc in fuzz.documents(count, seed, contents)]
    modules = [fuzz.load(path, f"script{n}") for n, path in enumerate(scripts)]
    runs = [[fuzz.run(module, text) for text in texts] for module in modules]
    linted = lint(texts + [out for run in runs for out, _ in run])
    before = linted[: len(texts)]
    print(f"{len(texts)} documents, {sum(len(x.tables) for x in before)} tables, "
          f"{sum(bool(x.md060) for x in before)} with MD060 reports")
    for n, (path, run) in enumerate(zip(scripts, runs, strict=True)):
        after = linted[len(texts) * (n + 1) : len(texts) * (n + 2)]
        bad = [(text, out, err, issues) for text, (out, err), b, a in zip(
            texts, run, before, after, strict=True) if (issues := problems(text, out, err, b, a))]
        print(f"--- {path}: {len(bad)} documents with problems")
        for text, out, err, issues in sorted(bad, key=lambda x: len(x[0]))[:show]:
            print(f"=== {text!r}\n  out: {out!r}\n  err: {err!r}")
            for issue in issues:
                print(f"  - {issue}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
