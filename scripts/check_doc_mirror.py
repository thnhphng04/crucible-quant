"""Check that the English docs mirror the Vietnamese originals and that no doc link is broken.

research_docs_vi/ is the source of truth; research_docs/ is a 1:1 translation (same line count,
same heading structure). The raw source reports 90–93 are Vietnamese only and are skipped.
README.md is checked for heading structure only: the English one carries an extra edition note.

Usage:  uv run python scripts/check_doc_mirror.py      (exit 1 on any problem)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VI = ROOT / "research_docs_vi"
EN = ROOT / "research_docs"
LINK_SCAN = [VI, EN, ROOT / "implement_docs", ROOT / "README.md", ROOT / "CLAUDE.md"]

LINE_COUNT_EXEMPT = {"README.md"}

FENCE = re.compile(r"^\s*(```|~~~)")
HEADING = re.compile(r"^(#{1,6})\s")
MD_LINK = re.compile(r"\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
WIKI_LINK = re.compile(r"\[\[([^\]|#]+)(?:[#|][^\]]*)?\]\]")
PREFIX = re.compile(r"^(\d{2})-")


def _prose_lines(text: str) -> list[str]:
    """Lines outside fenced code blocks (code may contain '# comment' or '](…)')."""
    out: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence:
            out.append(line)
    return out


def _heading_levels(text: str) -> list[int]:
    return [len(m.group(1)) for line in _prose_lines(text) if (m := HEADING.match(line))]


def _pairs() -> list[tuple[Path, Path]]:
    def key(p: Path) -> str:
        m = PREFIX.match(p.name)
        return m.group(1) if m else p.name

    vi = {key(p): p for p in VI.glob("*.md")}
    en = {key(p): p for p in EN.glob("*.md")}
    return [(vi[k], en[k]) for k in sorted(en) if k in vi] + [
        (Path("<missing>"), en[k]) for k in sorted(en) if k not in vi
    ]


def check_mirror() -> list[str]:
    problems: list[str] = []
    for vi_path, en_path in _pairs():
        if not vi_path.exists():
            problems.append(f"{en_path.name}: no Vietnamese original")
            continue
        vi_text = vi_path.read_text(encoding="utf-8")
        en_text = en_path.read_text(encoding="utf-8")
        vi_n, en_n = len(vi_text.splitlines()), len(en_text.splitlines())
        if vi_n != en_n and en_path.name not in LINE_COUNT_EXEMPT:
            problems.append(f"{en_path.name}: {en_n} lines vs {vi_n} in {vi_path.name}")
        vi_h, en_h = _heading_levels(vi_text), _heading_levels(en_text)
        if vi_h != en_h:
            problems.append(
                f"{en_path.name}: heading structure differs from {vi_path.name} "
                f"({len(en_h)} vs {len(vi_h)} headings)"
            )
    return problems


def _md_files() -> list[Path]:
    files: list[Path] = []
    for target in LINK_SCAN:
        if target.is_dir():
            files.extend(sorted(target.rglob("*.md")))
        elif target.exists():
            files.append(target)
    return files


def check_links() -> list[str]:
    problems: list[str] = []
    for path in _md_files():
        rel = path.relative_to(ROOT).as_posix()
        for line in _prose_lines(path.read_text(encoding="utf-8")):
            for m in MD_LINK.finditer(line):
                target = m.group(1).strip("<>").split("#", 1)[0]
                if not target or re.match(r"^[a-z]+:", target):
                    continue
                if not (path.parent / target).exists():
                    problems.append(f"{rel}: broken link -> {m.group(1)}")
            for m in WIKI_LINK.finditer(line):
                name = m.group(1).strip()
                if not (path.parent / f"{name}.md").exists():
                    problems.append(f"{rel}: broken wiki-link -> [[{name}]]")
    return problems


def main() -> int:
    problems = check_mirror() + check_links()
    for p in problems:
        print(f"FAIL  {p}")
    if not problems:
        n_pairs, n_files = len(_pairs()), len(_md_files())
        print(f"OK    {n_pairs} EN/VI pairs mirror 1:1; links resolve in {n_files} files")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
