"""Typed strategy grammar for engine C (arch §3.1.11, P2-05).

A genome is a small typed tree — the entry rule built from clauses over price series and
oscillators, plus an ATR stop — rendered into the template's evolvable block. The grammar only
builds scale-invariant rules (price series are compared with price series, oscillators with
levels, price distances are divided by ATR), declares every number as a TUNABLE (≤ 6), and
always emits the readiness guard on ATR and the stop, so a sampled strategy passes gate ①a by
construction. C-random samples genomes i.i.d.; C-gp (P2-11) breeds them with the same types.

Only the ``joint`` evolve scope is supported (named blocks: O13). The genome types, rendering and
serialization live in :mod:`quantcrucible.core.strategy.genome`; this module samples and
describes genomes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np

# The genome types, their rendering and serialization live in core (P3-32, ADR-0038) so the
# layers below the agent can read a genome; they are re-exported here for the engines.
from quantcrucible.core.strategy.base import ScopeDirection
from quantcrucible.core.strategy.genome import CLAUSE_TYPES as CLAUSE_TYPES
from quantcrucible.core.strategy.genome import Breakout as Breakout
from quantcrucible.core.strategy.genome import Clause as Clause
from quantcrucible.core.strategy.genome import Close as Close
from quantcrucible.core.strategy.genome import Cmp as Cmp
from quantcrucible.core.strategy.genome import Combine as Combine
from quantcrucible.core.strategy.genome import Compare as Compare
from quantcrucible.core.strategy.genome import Cross as Cross
from quantcrucible.core.strategy.genome import CrossLevel as CrossLevel
from quantcrucible.core.strategy.genome import Distance as Distance
from quantcrucible.core.strategy.genome import Entry as Entry
from quantcrucible.core.strategy.genome import Genome as Genome
from quantcrucible.core.strategy.genome import Indicator as Indicator
from quantcrucible.core.strategy.genome import Param as Param
from quantcrucible.core.strategy.genome import ParamKind as ParamKind
from quantcrucible.core.strategy.genome import Series as Series
from quantcrucible.core.strategy.genome import Slope as Slope
from quantcrucible.core.strategy.genome import Threshold as Threshold
from quantcrucible.core.strategy.genome import genome_from_dict as genome_from_dict
from quantcrucible.core.strategy.genome import genome_to_dict as genome_to_dict
from quantcrucible.core.strategy.genome import load_genome as load_genome
from quantcrucible.core.strategy.genome import render_genome as render_genome
from quantcrucible.core.strategy.registry import OPS
from quantcrucible.core.strategy.tunable import LEGACY_MAX_TUNABLES

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
STOP_PERIOD_RANGE = (5, 50)  # n_stop: ATR period or band period of the stop (ADR-0041, provisional)


# ── sampling ────────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class GrammarConfig:
    """Sampling distribution of the grammar (C-random, and C-gp's initial population)."""

    clause_count_weights: tuple[float, ...] = (0.5, 0.35, 0.15)  # 1, 2, 3 clauses
    or_probability: float = 0.3  # combine with `or` instead of `and`
    # 0 until execution places take-profit orders: the bridge ignores Signal.take_profit (O19),
    # so a k_tp would be a TUNABLE that changes nothing
    take_profit_probability: float = 0.0
    boll_stop_probability: float = 0.0
    tp_sl_ratio: float | None = None
    # the scope's side (P3-04): rendered sources emit it or flat, never the other side (INV-91)
    direction: ScopeDirection = "long"
    # a stop_period: tunable_v1 campaign (ADR-0041): every genome carries n_stop, cap 7
    stop_period: bool = False
    max_params: int = LEGACY_MAX_TUNABLES
    clause_types: tuple[type, ...] = field(default=CLAUSE_TYPES)
    # the campaign's grammar version (``derived.grammar_version``; 1 for older locks)
    version: int = 1
    # v2 (ADR-0043): an island seeded with a category samples clauses of that category only
    category: str | None = None


def _uniform(rng: np.random.Generator, kind: ParamKind, lo: float, hi: float) -> Param:
    if kind == "period":
        return Param(kind, lo, hi, int(rng.integers(int(lo), int(hi) + 1)))
    return Param(kind, lo, hi, round(float(rng.uniform(lo, hi)), 4))


# v3 (ADR-0044): a long RSI hugs 50 and never reaches its levels; a z-score of n values never
# exceeds (n − 1)/√n — 1.79 at n = 5, 2.85 at n = 10 — so short ones never reach theirs (P3-54)
PERIOD_CAPS_V3: dict[str, tuple[int, int]] = {"rsi": (2, 30), "zscore": (10, 300)}


def period_range(op: str, version: int = 1) -> tuple[int, int]:
    low, high = OPS[op].period or PERIOD_RANGE
    low, high = max(low, PERIOD_RANGE[0]), min(high, PERIOD_RANGE[1])
    if version >= 3 and op in PERIOD_CAPS_V3:
        cap_low, cap_high = PERIOD_CAPS_V3[op]
        low, high = max(low, cap_low), min(high, cap_high)
    return low, high


def _log_uniform_period(rng: np.random.Generator, lo: int, hi: int) -> Param:
    """An integer period, uniform in log(period): as many draws in [2, 20] as in [30, 300]."""
    value = round(float(np.exp(rng.uniform(np.log(lo), np.log(hi)))))
    return Param("period", lo, hi, min(max(value, lo), hi))


def sample_period(rng: np.random.Generator, op: str, version: int = 1) -> Param:
    lo, hi = period_range(op, version)
    if version >= 3:
        return _log_uniform_period(rng, lo, hi)
    return _uniform(rng, "period", lo, hi)


def sample_level(rng: np.random.Generator, op: str, cmp: Cmp) -> Param:
    lo, hi = LEVEL_RANGES[(op, cmp)]
    return _uniform(rng, "level", lo, hi)


def sample_price_series(rng: np.random.Generator, version: int = 1) -> Series:
    if rng.random() < 0.3:
        return Close()
    op = PRICE_OPS[int(rng.integers(len(PRICE_OPS)))]
    return Indicator(op, sample_period(rng, op, version))


def _two_price_series(rng: np.random.Generator, version: int) -> tuple[Series, Series]:
    while True:
        a, b = sample_price_series(rng, version), sample_price_series(rng, version)
        if a != b and not (isinstance(a, Close) and isinstance(b, Close)):
            return a, b


def sample_oscillator(rng: np.random.Generator, version: int = 1) -> Indicator:
    op = OSC_OPS[int(rng.integers(len(OSC_OPS)))]
    return Indicator(op, sample_period(rng, op, version))


def _cmp(rng: np.random.Generator) -> Cmp:
    return ">" if rng.random() < 0.5 else "<"


def sample_clause(rng: np.random.Generator, kind: type, version: int = 1) -> Clause:
    """A clause of ``kind``; ``version`` is the campaign's grammar version (periods from v3)."""
    v = version
    if kind is Compare:
        a, b = _two_price_series(rng, v)
        return Compare(a, _cmp(rng), b)
    if kind is Cross:
        a, b = _two_price_series(rng, v)
        return Cross(a, bool(rng.random() < 0.5), b)
    if kind is Threshold:
        osc, cmp = sample_oscillator(rng, v), _cmp(rng)
        return Threshold(osc, cmp, sample_level(rng, osc.op, cmp))
    if kind is CrossLevel:
        osc, up = sample_oscillator(rng, v), bool(rng.random() < 0.5)
        return CrossLevel(osc, up, sample_level(rng, osc.op, ">" if up else "<"))
    if kind is Distance:
        a, b = _two_price_series(rng, v)
        return Distance(a, b, _cmp(rng), _uniform(rng, "level", *DISTANCE_RANGE))
    if kind is Breakout:
        up = bool(rng.random() < 0.5)
        return Breakout(up, sample_period(rng, "rolling_max" if up else "rolling_min", v))
    if kind is Slope:
        use_price = rng.random() < 0.6
        series: Series = sample_price_series(rng, v) if use_price else sample_oscillator(rng, v)
        if isinstance(series, Close):
            series = Indicator("ema", sample_period(rng, "ema", v))
        return Slope(series, bool(rng.random() < 0.5))
    raise ValueError(f"unknown clause type {kind!r}")


def sample_genome(rng: np.random.Generator, config: GrammarConfig | None = None) -> Genome:
    """One genome from the grammar's distribution, within the TUNABLE budget."""
    cfg = config or GrammarConfig()
    weights = np.asarray(cfg.clause_count_weights, dtype=float)
    while True:
        n = 1 + int(rng.choice(len(weights), p=weights / weights.sum()))
        kinds = [cfg.clause_types[int(rng.integers(len(cfg.clause_types)))] for _ in range(n)]
        if cfg.version >= 2 and cfg.category is not None:
            clauses = tuple(sample_clause_in_category(rng, k, cfg) for k in kinds)
        else:  # no island category: the version's own stream (v1: what older campaigns drew)
            clauses = tuple(sample_clause(rng, k, cfg.version) for k in kinds)
        entry: Entry = clauses[0]
        if n > 1:
            entry = Combine("or" if rng.random() < cfg.or_probability else "and", clauses)
        stop = _uniform(rng, "mult", *STOP_RANGE)
        tp = (
            _uniform(rng, "mult", *TP_RANGE) if rng.random() < cfg.take_profit_probability else None
        )
        kind: Literal["atr", "bollinger"] = (
            "bollinger" if rng.random() < cfg.boll_stop_probability else "atr"
        )
        # drawn last, and only when enabled: a fixed-stop campaign samples the stream it always did
        period = _uniform(rng, "period", *STOP_PERIOD_RANGE) if cfg.stop_period else None
        genome = Genome(entry, stop, tp, kind, period)
        if len(genome.params()) <= cfg.max_params:
            return genome


# ── behavioural category (feature-map dimension, arch §3.1.3) ─────────────────────────────
CATEGORIES = ("trend", "momentum", "mean_reversion", "breakout")
# clause types able to express each category — an island seeded with a category samples these
CATEGORY_CLAUSES: dict[str, tuple[type, ...]] = {
    "trend": (Compare, Cross, Distance, Slope),
    "momentum": (Threshold, CrossLevel, Slope),
    "mean_reversion": (Threshold, CrossLevel, Distance, Breakout),
    "breakout": (Breakout,),
}
# v2: a family's clause types, and every type for mean reversion — any clause can fade a move
CATEGORY_CLAUSES_V2: dict[str, tuple[type, ...]] = {
    **CATEGORY_CLAUSES,
    "mean_reversion": CLAUSE_TYPES,
}
MAX_CATEGORY_DRAWS = 50


def _speed(s: Series) -> tuple[int, int]:
    """Smaller is faster: the close, then shorter periods; at one period, ema before sma."""
    if isinstance(s, Close):
        return (0, 0)
    return (int(s.period.value), 0 if s.op == "ema" else 1)


def _family(c: Clause) -> str:
    if isinstance(c, Threshold | CrossLevel):
        return "momentum"
    if isinstance(c, Slope):
        return "momentum" if isinstance(c.series, Indicator) and c.series.op in OSC_OPS else "trend"
    if isinstance(c, Breakout):
        return "breakout"
    return "trend"


def polarity(c: Clause) -> int:
    """+1 when the clause holds as price rises, −1 when it holds as price falls. A clause on two
    price series is read fast − slow, so ``a > b`` and ``b < a`` get the same polarity."""
    if isinstance(c, Compare | Cross | Distance):
        rising = c.up if isinstance(c, Cross) else c.op == ">"
        fast_left = _speed(c.left) <= _speed(c.right)
        return 1 if rising == fast_left else -1
    if isinstance(c, Threshold):
        return 1 if c.op == ">" else -1
    return 1 if c.up else -1


def clause_category(c: Clause, direction: ScopeDirection = "long", version: int = 1) -> str:
    """The clause's strategy category. v2 (ADR-0043): its family when it trades with the move on
    this side — polarity × side > 0 — mean reversion otherwise. v1: the clause alone."""
    if version >= 2:
        side = 1 if direction == "long" else -1
        return _family(c) if polarity(c) * side > 0 else "mean_reversion"
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


def categories(
    genome: Genome, direction: ScopeDirection = "long", version: int = 1
) -> tuple[str, ...]:
    """The strategy categories a genome's clauses belong to, in ``CATEGORIES`` order."""
    found = {clause_category(c, direction, version) for c in genome.clauses()}
    return tuple(c for c in CATEGORIES if c in found)


def sample_clause_in_category(rng: np.random.Generator, kind: type, cfg: GrammarConfig) -> Clause:
    """A clause of ``kind`` in the config's category on its side, drawn again until it is —
    up to ``MAX_CATEGORY_DRAWS``, after which the last draw stands (a ``kind`` that cannot
    express the category, such as a price ``Slope`` on the momentum island, only costs draws)."""
    clause = sample_clause(rng, kind, cfg.version)
    for _ in range(MAX_CATEGORY_DRAWS - 1):
        if clause_category(clause, cfg.direction, cfg.version) == cfg.category:
            break
        clause = sample_clause(rng, kind, cfg.version)
    return clause


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
    clauses = tuple(sorted(_clause_sig(c) for c in genome.clauses()))
    return ("stop:bollinger", *clauses) if genome.stop_kind == "bollinger" else clauses
