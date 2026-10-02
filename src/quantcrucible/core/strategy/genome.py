"""Genome types, rendering and serialization (arch §3.1.11, §3.3.3; P3-32, ADR-0038).

A genome is engine C's strategy as **data**: a small typed tree — the entry rule built from
clauses over price series and oscillators, plus a stop — that ``render_genome`` turns into the
template's evolvable block. The types live in ``core`` so the layers below the agent can read a
genome without importing it: the kernel engine interprets a genome instead of running its source.
Sampling, breeding and the behavioural descriptors stay in ``quantcrucible.agent.grammar``.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal

from quantcrucible.core.strategy.base import ScopeDirection
from quantcrucible.core.strategy.template import canonical_template, render

ParamKind = Literal["period", "level", "mult"]
Cmp = Literal[">", "<"]


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
    stop_kind: Literal["atr", "bollinger"] = "atr"
    # The stop's period (ADR-0041): ATR(n_stop) for an ATR stop, the band period for a Bollinger
    # one. ``None`` is the fixed ATR(14) / Bollinger(8) of a lock without ``stop_period``.
    stop_period: Param | None = None

    def clauses(self) -> tuple[Clause, ...]:
        return self.entry.items if isinstance(self.entry, Combine) else (self.entry,)

    def params(self) -> list[Param]:
        """Every number in traversal order — the order its TUNABLE names are assigned in."""
        out: list[Param] = []
        for clause in self.clauses():
            out.extend(_clause_params(clause))
        out.append(self.stop)
        if self.stop_period is not None:
            out.append(self.stop_period)
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


# ── rendering ───────────────────────────────────────────────────────────────────────────────
PREFIX = {"period": "n", "level": "lv", "mult": "k"}


def _fmt(v: float | int, is_int: bool) -> str:
    return str(int(v)) if is_int else repr(float(v))


class _Renderer:
    def __init__(
        self, genome: Genome, direction: ScopeDirection = "long", tp_sl_ratio: float | None = None
    ) -> None:
        if direction not in ("long", "short"):
            raise ValueError(f"direction must be long or short, got {direction!r}")
        self.genome = genome
        self.direction = direction
        self.tp_sl_ratio = tp_sl_ratio
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
            elif p is genome.stop_period:
                self.names[id(p)] = "n_stop"
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

    def tail(self) -> list[tuple[str, str]]:
        """The fixed features after the entry's: the stop's own ATR when it has a period gene,
        then the ATR(14) yardstick of the guard and of ``Distance`` (ADR-0041)."""
        period = self.genome.stop_period
        out: list[tuple[str, str]] = []
        if period is not None and self.genome.stop_kind == "atr":
            out.append(("atr_stop", f"ind.atr(bars, {self.p(period)})"))
        out.append(("atr", "ind.atr(bars, 14)"))
        return out

    def body(self) -> str:
        entry = self.entry()  # registers features first
        period = self.genome.stop_period
        if self.genome.stop_kind == "bollinger":
            close = self.feature("bars.close")
            band_name = "boll_lower" if self.direction == "long" else "boll_upper"
            n = "8" if period is None else self.p(period)
            band = self.feature(f"ind.{band_name}(bars.close, {n})")
            distance = f"({close} - {band})" if self.direction == "long" else f"({band} - {close})"
        else:
            distance = "atr" if period is None else 'x["atr_stop"]'
        tunables = "".join(
            f"    # TUNABLE: {self.names[id(p)]} = {_fmt(p.value, p.is_int)}, "
            f"bounds=({_fmt(p.low, p.is_int)}, {_fmt(p.high, p.is_int)})\n"
            for p in self.unique
        )
        feats = "".join(f'            "{n}": {e},\n' for n, e in (*self.features, *self.tail()))
        tp = ""
        if self.tp_sl_ratio is not None:
            tp = f", {self.tp_sl_ratio!r} * stop"
        elif self.genome.take_profit is not None:
            tp = f", {self.p(self.genome.take_profit)} * atr"
        return (
            tunables
            + "\n    def indicators(self, bars: Bars) -> Features:\n"
            + "        return {\n"
            + feats
            + "        }\n\n"
            + "    def signal(self, x: FeatureView) -> Signal:\n"
            + '        atr = x["atr"] + 0\n'
            + f"        stop = {self.p(self.genome.stop)} * {distance}\n"
            + "        ready = atr > 0 and atr - atr == 0 and stop > 0 and stop - stop == 0\n"
            + f"        if ready and ({entry}):\n"
            + f'            return Signal("{self.direction}", 1.0, stop{tp})\n'
            + '        return Signal("flat", 0.0, 0.0)\n\n'
        )

    def params(self) -> dict[str, float | int]:
        return {self.names[id(p)]: p.value for p in self.unique}


def feature_specs(
    genome: Genome, direction: ScopeDirection = "long", tp_sl_ratio: float | None = None
) -> list[tuple[str, str]]:
    """``(name, expression)`` of every feature the render's ``indicators()`` returns, in order,
    ``"atr"`` last — e.g. ``("f1", "ind.ema(bars.close, self.p.n1)")``."""
    r = _Renderer(genome, direction, tp_sl_ratio)
    r.body()
    return [*r.features, *r.tail()]


def named_params(genome: Genome) -> list[tuple[str, Param]]:
    """Each distinct parameter with the TUNABLE name its render gives it, in declaration order.
    A parameter object shared by two nodes is one name (the renderer's rule)."""
    r = _Renderer(genome)
    return [(r.names[id(p)], p) for p in r.unique]


def render_genome(
    genome: Genome, direction: ScopeDirection = "long", tp_sl_ratio: float | None = None
) -> tuple[str, dict[str, float | int]]:
    """(full strategy source, parameter values) — the source's TUNABLE defaults are the values.

    ``direction`` is the scope's side (P3-04): the rendered ``signal`` emits that side or
    ``flat``, never the other one (INV-91). Strength is always 1.0 — ADR-0031 forbids it as a
    second risk lever. The two renderings of one genome are different strategies with different
    hashes, so a long and a short scope never share a trial.
    """
    r = _Renderer(genome, direction, tp_sl_ratio)
    body = r.body()
    return render(canonical_template(), {"joint": body}), r.params()


# ── serialization: a genome is recorded with its submission (the ledger is the only state) ──
_NODES: dict[str, type] = {
    t.__name__: t for t in (Param, Close, Indicator, *CLAUSE_TYPES, Combine, Genome)
}


def genome_to_dict(node: object) -> object:
    """JSON-safe form of a genome (or any node of one); parameter objects shared by two nodes
    are written twice and read back as two equal parameters — rendering is unchanged. A genome
    without a stop-period gene omits the field, so its JSON is what it was before ADR-0041."""
    if isinstance(node, tuple):
        return [genome_to_dict(x) for x in node]
    if type(node).__name__ in _NODES and not isinstance(node, type):
        fields = {f: genome_to_dict(getattr(node, f)) for f in node.__dataclass_fields__}  # type: ignore[attr-defined]
        if isinstance(node, Genome) and node.stop_period is None:
            del fields["stop_period"]
        return {"t": type(node).__name__, **fields}
    return node


def genome_from_dict(data: object) -> object:
    if isinstance(data, list):
        return tuple(genome_from_dict(x) for x in data)
    if isinstance(data, dict) and "t" in data:
        cls = _NODES[str(data["t"])]
        return cls(**{k: genome_from_dict(v) for k, v in data.items() if k != "t"})
    return data


def load_genome(data: object) -> Genome:
    g = genome_from_dict(data)
    if not isinstance(g, Genome):
        raise ValueError("not a serialized genome")
    return g
