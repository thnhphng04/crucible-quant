"""Typed GP operators for engine C-gp (arch §3.1.11, D20, P2-11).

Every operator maps genomes to a genome of the same grammar, so a child is scale-invariant,
declares ≤ 6 TUNABLE and passes gate ①a by construction.

- ``param``     — one TUNABLE value moves within its bounds. The code is unchanged, so the child
                  keeps its parent's ``strategy_hash``: gate ④ counts it as a variant of the same
                  code. At most ``param_only_max`` of the offspring may be parameter-only (D20).
- ``point``     — flip a comparison / direction / and-or, or swap an indicator for one of the
                  same kind (sma ↔ ema, rsi ↔ zscore).
- ``subtree``   — replace one clause with a freshly sampled one.
- ``crossover`` — replace (or add) a clause with one taken from a second parent.

A child is never an optimizer step: each evaluated child is one trial (§3.3.1).
"""

from __future__ import annotations

import dataclasses
from typing import Any, Literal

import numpy as np

from quantcrucible.agent.grammar import (
    OSC_OPS,
    PRICE_OPS,
    Breakout,
    Clause,
    Cmp,
    Combine,
    Compare,
    Cross,
    CrossLevel,
    Distance,
    Genome,
    GrammarConfig,
    Indicator,
    Param,
    Slope,
    Threshold,
    sample_clause,
    sample_level,
)

MutationKind = Literal["param", "point", "subtree", "crossover"]
STRUCTURAL: tuple[MutationKind, ...] = ("point", "subtree", "crossover")
MAX_CLAUSES = 3
PERIOD_STEP = 0.2  # a period moves by N(0, 20%) of its value (at least 1)
LEVEL_STEP = 0.1  # a level or multiplier moves by N(0, 10%) of its range


class OperatorFailed(RuntimeError):
    """No valid child from this operator (e.g. the TUNABLE budget would be exceeded)."""


def choose_kind(
    rng: np.random.Generator,
    children: int,
    param_only: int,
    param_only_max: float,
    p_param: float = 0.3,
) -> MutationKind:
    """The next child's operator. Parameter-only is chosen with ``p_param`` but never if it
    would push the parameter-only share of the offspring above ``param_only_max`` (INV-66)."""
    if rng.random() < p_param and param_only + 1 <= param_only_max * (children + 1):
        return "param"
    return STRUCTURAL[int(rng.integers(len(STRUCTURAL)))]


# ── helpers ─────────────────────────────────────────────────────────────────────────────────
def _unique_params(g: Genome) -> list[Param]:
    seen: dict[int, Param] = {}
    for p in g.params():
        seen.setdefault(id(p), p)
    return list(seen.values())


def _swap(node: Any, old: object, new: object) -> Any:
    """``node`` with every field that *is* ``old`` (identity) replaced by ``new``, recursively."""
    if node is old:
        return new
    if isinstance(node, tuple):
        return tuple(_swap(x, old, new) for x in node)
    if dataclasses.is_dataclass(node) and not isinstance(node, type):
        changes = {}
        for f in dataclasses.fields(node):
            value = getattr(node, f.name)
            swapped = _swap(value, old, new)
            if swapped is not value:
                changes[f.name] = swapped
        return dataclasses.replace(node, **changes) if changes else node
    return node


def _with_clauses(g: Genome, clauses: tuple[Clause, ...]) -> Genome:
    if len(clauses) == 1:
        return dataclasses.replace(g, entry=clauses[0])
    op = g.entry.op if isinstance(g.entry, Combine) else "and"
    return dataclasses.replace(g, entry=Combine(op, clauses))


def _fits(g: Genome, config: GrammarConfig) -> bool:
    return len(_unique_params(g)) <= config.max_params


# ── operators ───────────────────────────────────────────────────────────────────────────────
def mutate_param(g: Genome, rng: np.random.Generator) -> Genome:
    params = _unique_params(g)
    old = params[int(rng.integers(len(params)))]
    if old.kind == "period":
        step = max(1.0, abs(float(old.value)) * PERIOD_STEP)
        value: float | int = int(
            np.clip(round(old.value + rng.normal(0.0, step)), old.low, old.high)
        )
        if value == old.value:
            value = int(np.clip(old.value + (1 if rng.random() < 0.5 else -1), old.low, old.high))
    else:
        step = (old.high - old.low) * LEVEL_STEP
        value = round(float(np.clip(old.value + rng.normal(0.0, step), old.low, old.high)), 4)
    if value == old.value:
        raise OperatorFailed("the parameter could not move within its bounds")
    child: Genome = _swap(g, old, dataclasses.replace(old, value=value))
    return child


def _flip(c: Clause, rng: np.random.Generator) -> Clause:
    if isinstance(c, Compare):
        return dataclasses.replace(c, op="<" if c.op == ">" else ">")
    if isinstance(c, Distance):
        return dataclasses.replace(c, op="<" if c.op == ">" else ">")
    if isinstance(c, Threshold):
        op: Cmp = "<" if c.op == ">" else ">"
        return Threshold(c.osc, op, sample_level(rng, c.osc.op, op))
    if isinstance(c, CrossLevel):
        up = not c.up
        return CrossLevel(c.osc, up, sample_level(rng, c.osc.op, ">" if up else "<"))
    if isinstance(c, Cross | Slope | Breakout):
        return dataclasses.replace(c, up=not c.up)
    raise OperatorFailed(f"no point mutation for {type(c).__name__}")


def _swap_indicator(c: Clause, rng: np.random.Generator) -> Clause:
    indicators = [
        v for v in (getattr(c, f.name) for f in dataclasses.fields(c)) if isinstance(v, Indicator)
    ]
    if not indicators:
        raise OperatorFailed("no indicator to swap")
    old = indicators[int(rng.integers(len(indicators)))]
    family = PRICE_OPS if old.op in PRICE_OPS else OSC_OPS
    others = [op for op in family if op != old.op]
    if not others:
        raise OperatorFailed("no other indicator of the same kind")
    new = dataclasses.replace(old, op=others[int(rng.integers(len(others)))])
    child: Clause = _swap(c, old, new)
    if isinstance(child, Threshold | CrossLevel):  # a level belongs to its oscillator's scale
        op: Cmp = child.op if isinstance(child, Threshold) else (">" if child.up else "<")
        child = dataclasses.replace(child, level=sample_level(rng, new.op, op))
    return child


def mutate_point(g: Genome, rng: np.random.Generator) -> Genome:
    clauses = g.clauses()
    choice = rng.random()
    if isinstance(g.entry, Combine) and choice < 0.2:
        return dataclasses.replace(
            g, entry=Combine("or" if g.entry.op == "and" else "and", g.entry.items)
        )
    i = int(rng.integers(len(clauses)))
    new = _flip(clauses[i], rng) if choice < 0.6 else _swap_indicator(clauses[i], rng)
    return _with_clauses(g, (*clauses[:i], new, *clauses[i + 1 :]))


def mutate_subtree(g: Genome, rng: np.random.Generator, config: GrammarConfig) -> Genome:
    clauses = g.clauses()
    for _ in range(20):
        i = int(rng.integers(len(clauses)))
        kind = config.clause_types[int(rng.integers(len(config.clause_types)))]
        child = _with_clauses(g, (*clauses[:i], sample_clause(rng, kind), *clauses[i + 1 :]))
        if _fits(child, config):
            return child
    raise OperatorFailed("no subtree fits the TUNABLE budget")


def crossover(a: Genome, b: Genome, rng: np.random.Generator, config: GrammarConfig) -> Genome:
    donors = b.clauses()
    for _ in range(20):
        clause = donors[int(rng.integers(len(donors)))]
        clauses = a.clauses()
        if len(clauses) < MAX_CLAUSES and rng.random() < 0.5:
            child = _with_clauses(a, (*clauses, clause))
        else:
            i = int(rng.integers(len(clauses)))
            child = _with_clauses(a, (*clauses[:i], clause, *clauses[i + 1 :]))
        if rng.random() < 0.5:
            child = dataclasses.replace(child, stop=b.stop)
        if _fits(child, config) and child != a:
            return child
    raise OperatorFailed("no crossover child fits the TUNABLE budget")


def breed(
    kind: MutationKind,
    parent: Genome,
    rng: np.random.Generator,
    config: GrammarConfig,
    other: Genome | None = None,
) -> Genome:
    if kind == "param":
        return mutate_param(parent, rng)
    if kind == "point":
        return mutate_point(parent, rng)
    if kind == "subtree":
        return mutate_subtree(parent, rng, config)
    if other is None:
        raise OperatorFailed("crossover needs a second parent")
    return crossover(parent, other, rng, config)
