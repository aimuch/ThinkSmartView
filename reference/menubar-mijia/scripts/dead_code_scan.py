#!/usr/bin/env python3
"""Find low-reference Swift declarations and record repo-wide evidence.

This is a candidate scanner, not a deletion oracle. Swift protocol witnesses,
framework callbacks, reflection, string lookup, and public API consumers can
all evade token search. Candidates therefore require manual classification
before deletion.
"""

from __future__ import annotations

import argparse
import collections
import pathlib
import re


DECLARATION = re.compile(
    r"^\s*(?P<prefix>[^\n{}]*?)\b(?P<kind>func|class|struct|enum|protocol|actor|typealias)\s+"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)",
    re.MULTILINE,
)
TOKEN = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")


def read_files(root: pathlib.Path) -> dict[pathlib.Path, str]:
    paths = [*root.glob("Sources/**/*.swift"), *root.glob("Tests/**/*.swift")]
    return {path: path.read_text(encoding="utf-8", errors="ignore") for path in paths}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=pathlib.Path, default=pathlib.Path(__file__).parents[1])
    parser.add_argument("--max-refs", type=int, default=2)
    args = parser.parse_args()
    texts = read_files(args.root)
    source_paths = {path for path in texts if "Sources" in path.parts}
    test_paths = {path for path in texts if "Tests" in path.parts}
    file_counts = {path: collections.Counter(TOKEN.findall(text)) for path, text in texts.items()}

    rows: list[tuple[int, int, pathlib.Path, int, str, str, int]] = []
    for path, text in texts.items():
        if path not in source_paths:
            continue
        for match in DECLARATION.finditer(text):
            name = match.group("name")
            source_refs = sum(file_counts[p][name] for p in source_paths)
            test_refs = sum(file_counts[p][name] for p in test_paths)
            # The declaration itself is one product reference. Keep low-count declarations only.
            if source_refs <= args.max_refs:
                line = text[: match.start()].count("\n") + 1
                rows.append((source_refs, test_refs, path, line, match.group("kind"), name, source_refs + test_refs))

    print("source_refs\ttest_refs\tdeclaration\ttotal_repo_tokens")
    for source_refs, test_refs, path, line, kind, name, total in sorted(rows, key=lambda row: (row[0], row[1], str(row[2]), row[3])):
        print(f"{source_refs}\t{test_refs}\t{path}:{line} {kind} {name}\t{total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
