"""Differential fuzz: two versions of md_table_align.py on random documents.

Usage, from the repository root:
    git show <commit>:scripts/md_table_align.py > <old.py>
    python3 -I scripts/tests/md_table_align/fuzz.py <old.py> scripts/md_table_align.py \
        [COUNT] [SEED]

Prints each minimal document on which the two differ in output or stderr, once, and how many
documents differed; oracle.py --cases tells which of the two markdownlint agrees with.
oracle.py also imports `documents` to generate its own.
"""
import contextlib
import importlib.util
import io
import random
import sys

PREFIXES = [
    "", "", "", "", "- ", "1. ", "2. ", "> ", " ", "  ", "   ", "    ", "\t", "-\t", ">",
    "-", "10. ", "* ", "+ ", "  - ", "1) ", "-    ", "> > ", ">\t",
]
CONTENTS = [
    "| a | b |", "| long | x |", "|-|-|", "--- | ---", "a | b", "--|--", "|-|", "| c |",
    "text", "---", "--", "===", "```", "~~~", "<div>", "</div>", "<span>", "<!-- c -->",
    "# h", "code", "", "", ":--", "| a |", "2. x", "- x", "* * *", "| long |", "-",
    "| a | b | c |", "|:-|-:|", "<pre>", "</pre>",
]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def documents(count, seed, contents=CONTENTS):
    """`count` random documents, as lists of lines, the same for the same seed."""
    rng = random.Random(seed)

    def line():
        prefix = "".join(rng.choice(PREFIXES) for _ in range(rng.choice((0, 1, 1, 2))))
        return prefix + rng.choice(contents)

    for _ in range(count):
        yield [line() for _ in range(rng.randint(2, 7))]


def run(module, text):
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        try:
            out = module.align_document(text)
        except Exception as error:  # noqa: BLE001
            out = f"EXCEPTION {type(error).__name__}: {error}"
    return out, err.getvalue()


def main():
    old = load(sys.argv[1], "old")
    new = load(sys.argv[2], "new")
    count = int(sys.argv[3]) if len(sys.argv) > 3 else 100_000
    seed = int(sys.argv[4]) if len(sys.argv) > 4 else 1

    def differs(lines):
        text = "".join(f"{x}\n" for x in lines)
        return run(old, text) != run(new, text)

    def minimize(lines):
        changed = True
        while changed:
            changed = False
            for k in range(len(lines)):
                shorter = lines[:k] + lines[k + 1 :]
                if shorter and differs(shorter):
                    lines = shorter
                    changed = True
                    break
        return lines

    seen = set()
    found = 0
    for doc in documents(count, seed):
        if differs(doc):
            found += 1
            small = tuple(minimize(doc))
            if small in seen:
                continue
            seen.add(small)
            text = "".join(f"{x}\n" for x in small)
            print(f"=== {text!r}")
            print(f"  old: {run(old, text)!r}")
            print(f"  new: {run(new, text)!r}")
    print(f"--- {found} differing documents, {len(seen)} minimal cases, of {count}")


if __name__ == "__main__":
    main()
