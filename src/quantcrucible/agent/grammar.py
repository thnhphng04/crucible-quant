"""Typed strategy grammar for engine C (arch §3.1.11, P2-05).

A genome is a small typed tree — the entry rule built from clauses over price series and
oscillators, plus an ATR stop — rendered into the template's evolvable block. The grammar only
builds scale-invariant rules (price series are compared with price series, oscillators with
levels, price distances are divided by ATR), declares every number as a TUNABLE (≤ 6), and
always emits the readiness guard on ATR and the stop, so a sampled strategy passes gate ①a by
construction. C-random samples genomes i.i.d.; C-gp (P2-11) breeds them with the same types.

Only the ``joint`` evolve scope is supported (named blocks: O13).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Literal

import numpy as np

from quantcrucible.core.strategy.registry import OPS
from quantcrucible.core.strategy.template import canonical_template, render
from quantcrucible.core.strategy.tunable import MAX_TUNABLES

ParamKind = Literal["period", "level", "mult"]
Cmp = Literal[">", "<"]
PRICE_OPS = ("sma", "ema")
OSC_OPS = ("rsi", "zscore")

# Default ranges for the numbers the grammar declares (provisional, tunable in one place).
PERIOD_RANGE = (2, 300)  # clipped to each operator's own period range
LEVEL_RANGES: dict[tuple[str, Cmp], tuple[float, float]] = {
    ("rsi", ">"): (50.0, 90.0),
    ("rsi", "<"): (10.0, 50.0),
    ("zscore", ">"): (0.0, 3.0),
    ("zscore", "<"): (-3.0, 0.0),
}
DISTANCE_RANGE = (-3.0, 3.0)  # (a - b) / ATR
STOP_RANGE = (0.5, 5.0)  # stop = k × ATR
TP_RANGE = (0.5, 10.0)  # take profit = k × ATR


@dataclass(frozen=True, slots=True)
class Param:
    kind: ParamKind
    low: float
    high: float
    value: float | int

    @property
    def is_int(self) -> bool:
        return self.kind == "period"


# ── series ──────────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class Close:
    """The close price (price units)."""


@dataclass(frozen=True, slots=True)
class Indicator:
    """``op`` over the close with a TUNABLE period. Price units for sma/ema/rolling_*, a
    dimensionless oscillator for rsi/zscore (``OPS[op].output``)."""

    op: str
    period: Param


Series = Close | Indicator


# ── clauses (each a truth value) ───────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class Compare:
    left: Series  # price
    op: Cmp
    right: Series  # price


@dataclass(frozen=True, slots=True)
class Cross:
    left: Series  # price
    up: bool
    right: Series  # price


@dataclass(frozen=True, slots=True)
class Threshold:
    osc: Indicator  # dimensionless
    op: Cmp
    level: Param


@dataclass(frozen=True, slots=True)
class CrossLevel:
    osc: Indicator
    up: bool
    level: Param


@dataclass(frozen=True, slots=True)
class Distance:
    """``(left - right) / ATR  op  k`` — a price distance measured in ATRs."""

    left: Series
    right: Series
    op: Cmp
    k: Param


@dataclass(frozen=True, slots=True)
class Breakout:
    """Close above the previous ``period``-bar high (up) or below the previous low (down)."""

    up: bool
    period: Param


@dataclass(frozen=True, slots=True)
class Slope:
    """A series rising (up) or falling against its previous bar."""

    series: Series
    up: bool


Clause = Compare | Cross | Threshold | CrossLevel | Distance | Breakout | Slope
CLAUSE_TYPES: tuple[type, ...] = (Compare, Cross, Threshold, CrossLevel, Distance, Breakout, Slope)


@dataclass(frozen=True, slots=True)
class Combine:
    """``and`` / ``or`` over two or more clauses."""

    op: Literal["and", "or"]
    items: tuple[Clause, ...]


Entry = Clause | Combine


@dataclass(frozen=True, slots=True)
class Genome:
    entry: Entry
    stop: Param
    take_profit: Param | None = None

    def clauses(self) -> tuple[Clause, ...]:
        return self.entry.items if isinstance(self.entry, Combine) else (self.entry,)

    def params(self) -> list[Param]:
        """Every number in traversal order — the order its TUNABLE names are assigned in."""
        out: list[Param] = []
        for clause in self.clauses():
            out.extend(_clause_params(clause))
        out.append(self.stop)
        if self.take_profit is not None:
            out.append(self.take_profit)
        return out


def _series_params(s: Series) -> Iterator[Param]:
    if isinstance(s, Indicator):
        yield s.period


def _clause_params(c: Clause) -> Iterator[Param]:
    if isinstance(c, Compare | Cross):
        yield from _series_params(c.left)
        yield from _series_params(c.right)
    elif isinstance(c, Threshold | CrossLevel):
        yield c.osc.period
        yield c.level
    elif isinstance(c, Distance):
        yield from _series_params(c.left)
        yield from _series_params(c.right)
        yield c.k
    elif isinstance(c, Breakout):
        yield c.period
    else:
        yield from _series_params(c.series)


# ── sampling ────────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class GrammarConfig:
    """Sampling distribution of the grammar (C-random, and C-gp's initial population)."""

    clause_count_weights: tuple[float, ...] = (0.5, 0.35, 0.15)  # 1, 2, 3 clauses
    or_probability: float = 0.3  # combine with `or` instead of `and`
    # 0 until execution places take-profit orders: the bridge ignores Signal.take_profit (O19),
    # so a k_tp would be a TUNABLE that changes nothing
    take_profit_probability: float = 0.0
    max_params: int = MAX_TUNABLES
    clause_types: tuple[type, ...] = field(default=CLAUSE_TYPES)


def _uniform(rng: np.random.Generator, kind: ParamKind, lo: float, hi: float) -> Param:
    if kind == "period":
        return Param(kind, lo, hi, int(rng.integers(int(lo), int(hi) + 1)))
    return Param(kind, lo, hi, round(float(rng.uniform(lo, hi)), 4))


def period_range(op: str) -> tuple[int, int]:
    low, high = OPS[op].period or PERIOD_RANGE
    return max(low, PERIOD_RANGE[0]), min(high, PERIOD_RANGE[1])


def sample_period(rng: np.random.Generator, op: str) -> Param:
    lo, hi = period_range(op)
    return _uniform(rng, "period", lo, hi)


def sample_level(rng: np.random.Generator, op: str, cmp: Cmp) -> Param:
    lo, hi = LEVEL_RANGES[(op, cmp)]
    return _uniform(rng, "level", lo, hi)


def sample_price_series(rng: np.random.Generator) -> Series:
    if rng.random() < 0.3:
        return Close()
    op = PRICE_OPS[int(rng.integers(len(PRICE_OPS)))]
    return Indicator(op, sample_period(rng, op))


def _two_price_series(rng: np.random.Generator) -> tuple[Series, Series]:
    while True:
        a, b = sample_price_series(rng), sample_price_series(rng)
        if a != b and not (isinstance(a, Close) and isinstance(b, Close)):
            return a, b


def sample_oscillator(rng: np.random.Generator) -> Indicator:
    op = OSC_OPS[int(rng.integers(len(OSC_OPS)))]
    return Indicator(op, sample_period(rng, op))


def _cmp(rng: np.random.Generator) -> Cmp:
    return ">" if rng.random() < 0.5 else "<"


def sample_clause(rng: np.random.Generator, kind: type) -> Clause:
    if kind is Compare:
        a, b = _two_price_series(rng)
        return Compare(a, _cmp(rng), b)
    if kind is Cross:
        a, b = _two_price_series(rng)
        return Cross(a, bool(rng.random() < 0.5), b)
    if kind is Threshold:
        osc, cmp = sample_oscillator(rng), _cmp(rng)
        return Threshold(osc, cmp, sample_level(rng, osc.op, cmp))
    if kind is CrossLevel:
        osc, up = sample_oscillator(rng), bool(rng.random() < 0.5)
        return CrossLevel(osc, up, sample_level(rng, osc.op, ">" if up else "<"))
    if kind is Distance:
        a, b = _two_price_series(rng)
        return Distance(a, b, _cmp(rng), _uniform(rng, "level", *DISTANCE_RANGE))
    if kind is Breakout:
        up = bool(rng.random() < 0.5)
        return Breakout(up, sample_period(rng, "rolling_max" if up else "rolling_min"))
    if kind is Slope:
        series: Series = sample_price_series(rng) if rng.random() < 0.6 else sample_oscillator(rng)
        if isinstance(series, Close):
            series = Indicator("ema", sample_period(rng, "ema"))
        return Slope(series, bool(rng.random() < 0.5))
    raise ValueError(f"unknown clause type {kind!r}")


def sample_genome(rng: np.random.Generator, config: GrammarConfig | None = None) -> Genome:
    """One genome from the grammar's distribution, within the TUNABLE budget."""
    cfg = config or GrammarConfig()
    weights = np.asarray(cfg.clause_count_weights, dtype=float)
    while True:
        n = 1 + int(rng.choice(len(weights), p=weights / weights.sum()))
        kinds = [cfg.clause_types[int(rng.integers(len(cfg.clause_types)))] for _ in range(n)]
        clauses = tuple(sample_clause(rng, k) for k in kinds)
        entry: Entry = clauses[0]
        if n > 1:
            entry = Combine("or" if rng.random() < cfg.or_probability else "and", clauses)
        stop = _uniform(rng, "mult", *STOP_RANGE)
        tp = (
            _uniform(rng, "mult", *TP_RANGE) if rng.random() < cfg.take_profit_probability else None
        )
        genome = Genome(entry, stop, tp)
        if len(genome.params()) <= cfg.max_params:
            return genome


# ── rendering ───────────────────────────────────────────────────────────────────────────────
PREFIX = {"period": "n", "level": "lv", "mult": "k"}


def _fmt(v: float | int, is_int: bool) -> str:
    return str(int(v)) if is_int else repr(float(v))


class _Renderer:
    def __init__(self, genome: Genome) -> None:
        self.genome = genome
        self.names: dict[int, str] = {}  # id(Param) -> TUNABLE name
        self.features: list[tuple[str, str]] = []  # (feature name, indicators() expression)
        counters: dict[str, int] = {}
        self.unique: list[Param] = []  # a Param object shared by two nodes is one TUNABLE
        for p in genome.params():
            if id(p) in self.names:
                continue
            self.unique.append(p)
            if p is genome.stop:
                self.names[id(p)] = "k_stop"
            elif p is genome.take_profit:
                self.names[id(p)] = "k_tp"
            else:
                prefix = PREFIX[p.kind]
                counters[prefix] = counters.get(prefix, 0) + 1
                self.names[id(p)] = f"{prefix}{counters[prefix]}"

    def p(self, param: Param) -> str:
        return f"self.p.{self.names[id(param)]}"

    def feature(self, expr: str) -> str:
        for name, e in self.features:
            if e == expr:
                return f'x["{name}"]'
        name = f"f{len(self.features) + 1}"
        self.features.append((name, expr))
        return f'x["{name}"]'

    def series(self, s: Series) -> str:
        if isinstance(s, Close):
            return self.feature("bars.close")
        return self.feature(f"ind.{s.op}(bars.close, {self.p(s.period)})")

    def clause(self, c: Clause) -> str:
        if isinstance(c, Compare):
            return f"{self.series(c.left)} {c.op} {self.series(c.right)}"
        if isinstance(c, Cross):
            fn = "cross_up" if c.up else "cross_down"
            return f"ind.{fn}({self.series(c.left)}, {self.series(c.right)})"
        if isinstance(c, Threshold):
            return f"{self.series(c.osc)} {c.op} {self.p(c.level)}"
        if isinstance(c, CrossLevel):
            fn = "cross_up" if c.up else "cross_down"
            return f"ind.{fn}({self.series(c.osc)}, {self.p(c.level)})"
        if isinstance(c, Distance):
            left, right = self.series(c.left), self.series(c.right)
            return f'({left} - {right}) / x["atr"] {c.op} {self.p(c.k)}'
        if isinstance(c, Breakout):
            op = "rolling_max" if c.up else "rolling_min"
            level = self.feature(f"ind.{op}(bars.close, {self.p(c.period)})")
            return f"{self.series(Close())} {'>' if c.up else '<'} {level}.ago(1)"
        rel = ">" if c.up else "<"
        s = self.series(c.series)
        return f"{s} {rel} {s}.ago(1)"

    def entry(self) -> str:
        e = self.genome.entry
        if isinstance(e, Combine):
            return f" {e.op} ".join(f"({self.clause(c)})" for c in e.items)
        return self.clause(e)

    def body(self) -> str:
        entry = self.entry()  # registers features first
        tunables = "".join(
            f"    # TUNABLE: {self.names[id(p)]} = {_fmt(p.value, p.is_int)}, "
            f"bounds=({_fmt(p.low, p.is_int)}, {_fmt(p.high, p.is_int)})\n"
            for p in self.unique
        )
        feats = "".join(f'            "{n}": {e},\n' for n, e in self.features)
        tp = ""
        if self.genome.take_profit is not None:
            tp = f", {self.p(self.genome.take_profit)} * atr"
        return (
            tunables
            + "\n    def indicators(self, bars: Bars) -> Features:\n"
            + "        return {\n"
            + feats
            + '            "atr": ind.atr(bars, 14),\n'
            + "        }\n\n"
            + "    def signal(self, x: FeatureView) -> Signal:\n"
            + '        atr = x["atr"] + 0\n'
            + f"        stop = {self.p(self.genome.stop)} * atr\n"
            + "        ready = atr > 0 and atr - atr == 0 and stop > 0 and stop - stop == 0\n"
            + f"        if ready and ({entry}):\n"
            + f'            return Signal("long", 1.0, stop{tp})\n'
            + '        return Signal("flat", 0.0, 0.0)\n\n'
        )

    def params(self) -> dict[str, float | int]:
        return {self.names[id(p)]: p.value for p in self.unique}


def render_genome(genome: Genome) -> tuple[str, dict[str, float | int]]:
    """(full strategy source, parameter values) — the source's TUNABLE defaults are the values."""
    r = _Renderer(genome)
    body = r.body()
    return render(canonical_template(), {"joint": body}), r.params()


# ── behavioural category (feature-map dimension, arch §3.1.3) ─────────────────────────────
CATEGORIES = ("trend", "momentum", "mean_reversion", "breakout")


def _clause_category(c: Clause) -> str:
    if isinstance(c, Compare | Cross):
        return "trend"
    if isinstance(c, Slope):
        return "momentum" if isinstance(c.series, Indicator) and c.series.op in OSC_OPS else "trend"
    if isinstance(c, Threshold):
        return "momentum" if c.op == ">" else "mean_reversion"
    if isinstance(c, CrossLevel):
        return "momentum" if c.up else "mean_reversion"
    if isinstance(c, Distance):
        return "trend" if c.op == ">" else "mean_reversion"
    return "breakout" if c.up else "mean_reversion"


def categories(genome: Genome) -> tuple[str, ...]:
    """The strategy categories a genome's clauses belong to, in ``CATEGORIES`` order."""
    found = {_clause_category(c) for c in genome.clauses()}
    return tuple(c for c in CATEGORIES if c in found)


# ── structural signature (novelty in the ranking, arch §3.1.6 #1) ─────────────────────────────
def _series_sig(s: Series) -> str:
    return "close" if isinstance(s, Close) else s.op


def _clause_sig(c: Clause) -> str:
    if isinstance(c, Compare):
        return f"cmp({_series_sig(c.left)}{c.op}{_series_sig(c.right)})"
    if isinstance(c, Cross):
        return f"cross({_series_sig(c.left)},{'up' if c.up else 'down'},{_series_sig(c.right)})"
    if isinstance(c, Threshold):
        return f"thr({c.osc.op}{c.op})"
    if isinstance(c, CrossLevel):
        return f"xlvl({c.osc.op},{'up' if c.up else 'down'})"
    if isinstance(c, Distance):
        return f"dist({_series_sig(c.left)}-{_series_sig(c.right)}{c.op})"
    if isinstance(c, Breakout):
        return f"brk({'up' if c.up else 'down'})"
    return f"slope({_series_sig(c.series)},{'up' if c.up else 'down'})"


def signature(genome: Genome) -> tuple[str, ...]:
    """The genome's clauses without their numbers, sorted — two strategies with the same
    signature differ only in parameter values or clause order."""
    return tuple(sorted(_clause_sig(c) for c in genome.clauses()))
