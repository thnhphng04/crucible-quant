"""Check that translated docs mirror their originals 1:1, and that no doc link is broken.

Two mirrored pairs (original → translation):
  research_docs_vi/ → research_docs/     Vietnamese is the source of truth. The raw source
                                         reports 90–93 are Vietnamese only and have no mirror.
  implement_docs/   → implement_docs_vi/ English is the original (the coding agent reads it).

Files are paired by their number prefix (``03-…``, ``adr/0001-…``) or, without one, by name, so
each side may use its own language in filenames. A pair must have the same line count and the
same heading structure. README.md in research_docs/ is exempt from the line count only: the
English one carries an extra edition note.

Usage:  uv run python scripts/check_doc_mirror.py      (exit 1 on any problem)
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Mirror:
    original: Path
    translation: Path
    original_only: re.Pattern[str] | None = None  # original files that are never translated
    line_count_exempt: frozenset[str] = frozenset()  # keys checked for headings only


MIRRORS = [
    Mirror(
        original=ROOT / "research_docs_vi",
        translation=ROOT / "research_docs",
        original_only=re.compile(r"^9\d$"),
        line_count_exempt=frozenset({"README.md"}),
    ),
    Mirror(original=ROOT / "implement_docs", translation=ROOT / "implement_docs_vi"),
]
LINK_SCAN = [
    *(m.original for m in MIRRORS),
    *(m.translation for m in MIRRORS),
    ROOT / "README.md",
    ROOT / "CLAUDE.md",
]

FENCE = re.compile(r"^\s*(```|~~~)")
HEADING = re.compile(r"^(#{1,6})\s")
MD_LINK = re.compile(r"\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
WIKI_LINK = re.compile(r"\[\[([^\]|#]+)(?:[#|][^\]]*)?\]\]")
PREFIX = re.compile(r"^(\d{2,4})-")


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


def _keyed(folder: Path) -> dict[str, Path]:
    """{'03': …/03-X.md, 'adr/0001': …/adr/0001-y.md, 'README.md': …} for every .md under folder."""
    out: dict[str, Path] = {}
    for p in sorted(folder.rglob("*.md")):
        m = PREFIX.match(p.name)
        stem = m.group(1) if m else p.name
        parent = p.parent.relative_to(folder).as_posix()
        out[stem if parent == "." else f"{parent}/{stem}"] = p
    return out


def _rel(p: Path) -> str:
    return p.relative_to(ROOT).as_posix()


def check_mirror() -> tuple[list[str], int]:
    problems: list[str] = []
    n_pairs = 0
    for mirror in MIRRORS:
        orig, trans = _keyed(mirror.original), _keyed(mirror.translation)
        for key in sorted(orig.keys() | trans.keys()):
            if key not in trans:
                if not (mirror.original_only and mirror.original_only.match(key)):
                    where = _rel(mirror.translation)
                    problems.append(f"{_rel(orig[key])}: no translation in {where}/")
                continue
            if key not in orig:
                problems.append(f"{_rel(trans[key])}: no original in {_rel(mirror.original)}/")
                continue
            n_pairs += 1
            o_path, t_path = orig[key], trans[key]
            o_text = o_path.read_text(encoding="utf-8")
            t_text = t_path.read_text(encoding="utf-8")
            o_n, t_n = len(o_text.splitlines()), len(t_text.splitlines())
            if o_n != t_n and key not in mirror.line_count_exempt:
                problems.append(f"{_rel(t_path)}: {t_n} lines vs {o_n} in {_rel(o_path)}")
            o_h, t_h = _heading_levels(o_text), _heading_levels(t_text)
            if o_h != t_h:
                problems.append(
                    f"{_rel(t_path)}: heading structure differs from {_rel(o_path)} "
                    f"({len(t_h)} vs {len(o_h)} headings)"
                )
    return problems, n_pairs


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
        rel = _rel(path)
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
    mirror_problems, n_pairs = check_mirror()
    problems = mirror_problems + check_links()
    for p in problems:
        print(f"FAIL  {p}")
    if not problems:
        n_files = len(_md_files())
        print(f"OK    {n_pairs} doc pairs mirror 1:1; links resolve in {n_files} files")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
