#!/usr/bin/env python3
"""Report Windows TypeScript reachability and low-reference exports.

The report is deliberately conservative. It follows relative imports from the
Vite entry point and counts token references across source and tests. TypeScript
declaration companions and runtime string lookups still require manual review.
"""

from __future__ import annotations

import collections
import pathlib
import re


ROOT = pathlib.Path(__file__).parents[1] / "windows"
SOURCE = ROOT / "src"
IMPORT = re.compile(r"(?:from|import)\s*\(?\s*[\"'](\.?\.?/[^\"']+)[\"']")
EXPORT = re.compile(
    r"^\s*export\s+(?:async\s+)?(?:function|class|interface|type|const|let|var|enum)\s+"
    r"([A-Za-z_$][\w$]*)",
    re.MULTILINE,
)
TOKEN = re.compile(r"\b[A-Za-z_$][\w$]*\b")


def key(path: pathlib.Path) -> str:
    return path.relative_to(SOURCE).with_suffix("").as_posix()


def resolve_import(path: pathlib.Path, spec: str, files: dict[str, pathlib.Path]) -> str | None:
    base = (path.parent / spec.removesuffix(".js")).resolve()
    for candidate in (base, pathlib.Path(f"{base}.ts"), pathlib.Path(f"{base}.tsx"), base / "index.ts"):
        candidate_key = key(candidate) if candidate.exists() else None
        if candidate_key in files:
            return candidate_key
    return None


def main() -> int:
    files = {key(path): path for path in SOURCE.rglob("*.ts") if path.name != "vite-env.d.ts"}
    edges: dict[str, set[str]] = {name: set() for name in files}
    for name, path in files.items():
        for spec in IMPORT.findall(path.read_text(encoding="utf-8", errors="ignore")):
            target = resolve_import(path, spec, files)
            if target:
                edges[name].add(target)

    reachable: set[str] = set()
    pending = ["ui/app"]
    while pending:
        name = pending.pop()
        if name in reachable:
            continue
        reachable.add(name)
        pending.extend(edges[name])

    texts = {path: path.read_text(encoding="utf-8", errors="ignore") for path in files.values()}
    counts = collections.Counter(TOKEN.findall("\n".join(texts.values())))
    print(f"reachable_from_ui_app={len(reachable)}/{len(files)}")
    for name in sorted(files):
        if name not in reachable:
            importers = sorted(source for source, targets in edges.items() if name in targets)
            print(f"UNREACHABLE_MODULE\t{name}\timporters={','.join(importers) or '-'}")
    for name, path in sorted(files.items()):
        if name.startswith("vendor/"):
            continue
        text = texts[path]
        for match in EXPORT.finditer(text):
            symbol = match.group(1)
            if counts[symbol] <= 1:
                line = text[: match.start()].count("\n") + 1
                print(f"LOW_REFERENCE_EXPORT\t{path}:{line}\t{symbol}\ttotal_tokens={counts[symbol]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
