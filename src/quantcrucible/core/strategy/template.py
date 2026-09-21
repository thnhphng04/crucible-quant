"""Strategy template: fixed region + evolvable region (Architecture §3.3.1).

A strategy source file is the canonical template (``strategy_template.txt``) with the body of its
``EVOLVE-BLOCK`` replaced. Everything outside the editable blocks — including the marker lines —
is the *fixed region*; its hash is locked per campaign and checked at gate ①a (rule 1).

Markers (one per line, any indentation)::

    # ═══ EVOLVE-BLOCK-START ═══            one block, named "joint"
    # ═══ EVOLVE-BLOCK-START: entry ═══     named blocks for module-wise evolution
    # ═══ EVOLVE-BLOCK-END ═══              (the END marker may repeat the name)

``evolve_scope`` selects which blocks are editable: ``joint`` = all of them; ``entry`` / ``exit``
/ ``regime`` = only that named block, the others count as fixed.
"""

from __future__ import annotations

import hashlib
import re
import types
from dataclasses import dataclass
from importlib.resources import files

from quantcrucible.core.strategy.base import Strategy
from quantcrucible.core.strategy.tunable import Tunable, parse_tunables

JOINT = "joint"
BLOCK_NAMES = frozenset({"entry", "exit", "regime", JOINT})
STRATEGY_CLASS = "GeneratedStrategy"

_START = re.compile(r"^\s*# ═══ EVOLVE-BLOCK-START(?:: (?P<name>\w+))? ═══\s*$")
_END = re.compile(r"^\s*# ═══ EVOLVE-BLOCK-END(?:: (?P<name>\w+))? ═══\s*$")
_ANY_MARKER = re.compile(r"EVOLVE-BLOCK")


class TemplateError(ValueError):
    """The source does not follow the template structure."""


@dataclass(frozen=True, slots=True)
class Block:
    name: str
    body: str  # text strictly between the START and END marker lines


@dataclass(frozen=True, slots=True)
class ParsedStrategy:
    fixed: tuple[str, ...]  # fixed text around the blocks: len(fixed) == len(blocks) + 1
    blocks: tuple[Block, ...]
    tunables: tuple[Tunable, ...]

    def block(self, name: str) -> Block:
        for b in self.blocks:
            if b.name == name:
                return b
        raise TemplateError(f"no EVOLVE-BLOCK named {name!r}")

    def editable(self, evolve_scope: str) -> frozenset[str]:
        names = {b.name for b in self.blocks}
        if evolve_scope == JOINT:
            return frozenset(names)
        if evolve_scope not in names:
            raise TemplateError(
                f"evolve_scope {evolve_scope!r} but the template has {sorted(names)}"
            )
        return frozenset({evolve_scope})


def parse(source: str) -> ParsedStrategy:
    lines = source.splitlines(keepends=True)
    fixed: list[str] = []
    blocks: list[Block] = []
    current: list[str] = []
    open_name: str | None = None
    for lineno, line in enumerate(lines, start=1):
        start, end = _START.match(line), _END.match(line)
        if start:
            if open_name is not None:
                raise TemplateError(f"line {lineno}: nested EVOLVE-BLOCK-START")
            current.append(line)
            fixed.append("".join(current))
            current = []
            open_name = start["name"] or JOINT
            if open_name not in BLOCK_NAMES:
                raise TemplateError(f"line {lineno}: unknown block name {open_name!r}")
        elif end:
            if open_name is None:
                raise TemplateError(f"line {lineno}: EVOLVE-BLOCK-END without START")
            if end["name"] and end["name"] != open_name:
                raise TemplateError(f"line {lineno}: END {end['name']!r} closes {open_name!r}")
            blocks.append(Block(open_name, "".join(current)))
            current = [line]
            open_name = None
        elif _ANY_MARKER.search(line) and line.lstrip().startswith("#"):
            raise TemplateError(f"line {lineno}: malformed EVOLVE-BLOCK marker")
        else:
            current.append(line)
    if open_name is not None:
        raise TemplateError(f"EVOLVE-BLOCK {open_name!r} is never closed")
    fixed.append("".join(current))
    if not blocks:
        raise TemplateError("no EVOLVE-BLOCK found — a whole-file replacement is not allowed")
    names = [b.name for b in blocks]
    if len(set(names)) != len(names):
        raise TemplateError(f"duplicate EVOLVE-BLOCK names: {names}")
    tunables = parse_tunables("".join(b.body for b in blocks))
    return ParsedStrategy(tuple(fixed), tuple(blocks), tuple(tunables))


def fixed_region_hash(parsed: ParsedStrategy, evolve_scope: str) -> str:
    """SHA256 over everything the agent may not edit under ``evolve_scope`` (rule 1)."""
    editable = parsed.editable(evolve_scope)
    h = hashlib.sha256()
    for i, block in enumerate(parsed.blocks):
        h.update(parsed.fixed[i].encode("utf-8"))
        h.update(b"\x00<editable>\x00" if block.name in editable else block.body.encode("utf-8"))
    h.update(parsed.fixed[-1].encode("utf-8"))
    return h.hexdigest()


def render(template: str, bodies: dict[str, str]) -> str:
    """The template with the given blocks' bodies replaced (others kept)."""
    parsed = parse(template)
    unknown = set(bodies) - {b.name for b in parsed.blocks}
    if unknown:
        raise TemplateError(f"template has no block(s) {sorted(unknown)}")
    out: list[str] = []
    for i, block in enumerate(parsed.blocks):
        out.append(parsed.fixed[i])
        body = bodies.get(block.name, block.body)
        out.append(body if body.endswith("\n") or not body else body + "\n")
    out.append(parsed.fixed[-1])
    return "".join(out)


def canonical_template() -> str:
    return files("quantcrucible.core.strategy").joinpath("strategy_template.txt").read_text("utf-8")


def template_hash(evolve_scope: str) -> str:
    """The fixed-region hash every candidate must match — recorded in the campaign lock."""
    return fixed_region_hash(parse(canonical_template()), evolve_scope)


def load_strategy_class(source: str, module_name: str = "candidate_strategy") -> type[Strategy]:
    """Execute ``source`` and return its ``GeneratedStrategy`` class.

    ⚠️ Executes code. Outside the sandbox (§3.3.3) call it ONLY on trusted, hand-written source
    (the zoo, tests); generated code is loaded exclusively by the sandbox runner.
    """
    module = types.ModuleType(module_name)
    exec(compile(source, f"<{module_name}>", "exec"), module.__dict__)
    cls = module.__dict__.get(STRATEGY_CLASS)
    if not (isinstance(cls, type) and issubclass(cls, Strategy)):
        raise TemplateError(f"source must define class {STRATEGY_CLASS}(Strategy)")
    return cls
