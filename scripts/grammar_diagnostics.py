"""What engine C's grammar looks like on in-sample bars (P3-54): indicator values only.

Measures, per symbol of an in-sample folder:

``fire``        how often each clause type is true, by its longest period: a clause true on
                almost no bar or on almost every bar is degenerate whatever its numbers are.
``grid``        the same on a fixed grid of each clause's numbers (period × level, fast × slow,
                period × k): where a range turns degenerate, per symbol.
``scenarios``   the sampled clauses again under P3-57's candidate changes — periods drawn
                log-uniform, the RSI period capped, ``Distance`` over ``stdev`` — and how many
                stay degenerate.
``distance``    how often ``Distance``'s ratio lies outside its ``k`` range ``[-3, 3]`` — on those
                bars the clause's value does not depend on ``k`` — for today's ``ATR(14)``
                denominator and for a ``stdev(close, n)`` one (P3-57).
``bollinger``   how often a Bollinger stop is ``<= 0`` (the ``ready`` guard then refuses the
                signal), unconditionally and on the bars a mean-reversion trigger fires.
``volatility``  quantiles of ``atr(n1) / atr(n2)`` and of the band width ``4·std / sma``, to set
                the ``k`` ranges of the volatility clauses (P3-58).
``corr``        Spearman correlation of dimensionless features, existing and candidate (P3-59).

Indicators are computed once over the full series (the registry's functions are causal), not on
the ``lookback`` window a backtest rebuilds every bar, so seeded recursions (ema, rsi, atr) differ
slightly from a backtest's in their first values. Nothing here computes a return, a fill or a
metric of a strategy: it is not a backtest and records no trial (P2). It reads only the folder it
is given and refuses any path under ``holdout`` (P6).

    uv run python scripts/grammar_diagnostics.py
    uv run python scripts/grammar_diagnostics.py --samples 400 --out reports/grammar
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Iterator
from dataclasses import fields, is_dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

from quantcrucible.agent.grammar import (
    CLAUSE_TYPES,
    LEVEL_RANGES,
    OSC_OPS,
    Breakout,
    Clause,
    Close,
    Compare,
    Cross,
    CrossLevel,
    Distance,
    Indicator,
    Param,
    Series,
    Slope,
    Threshold,
    period_range,
    sample_clause,
)
from quantcrucible.core.strategy import registry as R
from quantcrucible.core.strategy.base import Bars
from quantcrucible.data.store import read_bars

FloatArray = npt.NDArray[np.float64]
BoolArray = npt.NDArray[np.bool_]

K_RANGE = 3.0  # Distance's k bounds are [-3, 3] (grammar.DISTANCE_RANGE)
DISTANCE_SCALES = ("atr", "stdev", "atr_sqrt", "spread")  # Evaluator.distance_ratio
BANDS = ((2, 20), (21, 100), (101, 300))  # longest period of a clause
DEGENERATE = 0.01  # true on < 1% or > 99% of the bars
STOP_PERIODS = (5, 8, 14, 20, 50)  # 8 = the fixed band; the rest span n_stop's [5, 50]
VOL_RATIOS = ((5, 60), (14, 100), (20, 200), (50, 300))
BAND_WIDTHS = (10, 20, 50, 100)
QUANTILES = (0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99)


class DiagnosticsError(ValueError):
    """The input cannot be measured."""


# ── candidate indicators: diagnostics only, not in the ``ind`` whitelist ─────────────────────
def stdev(x: FloatArray, n: int) -> FloatArray:
    """Rolling population standard deviation; NaN where it is 0 (P3-57's candidate)."""
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        sd = sliding_window_view(np.asarray(x, dtype=np.float64), n).std(axis=1)
        out[n - 1 :] = np.where(sd > 0, sd, np.nan)
    return out


def _finite_stdev(x: FloatArray, n: int) -> FloatArray:
    """``stdev`` of a series with a NaN warm-up: NaN until n finite values are in the window."""
    out = np.full(len(x), np.nan)
    finite = np.isfinite(x)
    if finite.any():
        start = int(np.argmax(finite))
        out[start:] = stdev(x[start:], n)
    return out


def bandwidth(x: FloatArray, n: int) -> FloatArray:
    """(boll_upper − boll_lower) / sma = 4·std / sma; NaN where the sma is not positive."""
    mean = R.sma(x, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(mean > 0, (R.boll_upper(x, n) - R.boll_lower(x, n)) / mean, np.nan)


def efficiency_ratio(x: FloatArray, n: int) -> FloatArray:
    """Kaufman: |x − x₋ₙ| / Σ|Δx| over n bars, in [0, 1]; NaN on a flat window."""
    out = np.full(len(x), np.nan)
    if len(x) > n:
        net = np.abs(x[n:] - x[:-n])
        path = sliding_window_view(np.abs(np.diff(x)), n).sum(axis=1)
        with np.errstate(divide="ignore", invalid="ignore"):
            out[n:] = np.where(path > 0, net / path, np.nan)
    return out


def tsmom(x: FloatArray, n: int) -> FloatArray:
    """n-bar log return over its own volatility × √n — a t-statistic of time-series momentum."""
    out = np.full(len(x), np.nan)
    r = np.diff(np.log(x))
    if len(r) >= n:
        sd = sliding_window_view(r, n).std(axis=1)
        total = sliding_window_view(r, n).sum(axis=1)
        with np.errstate(divide="ignore", invalid="ignore"):
            out[n:] = np.where(sd > 0, total / (sd * np.sqrt(n)), np.nan)
    return out


def relative_volume(v: FloatArray, n: int) -> FloatArray:
    mean = R.sma(v, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(mean > 0, v / mean, np.nan)


def close_location(bars: Bars) -> FloatArray:
    """(close − low) / (high − low) in [0, 1]; NaN on a bar without range."""
    rng = bars.high - bars.low
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(rng > 0, (bars.close - bars.low) / rng, np.nan)


# ── clause evaluation, vectorized over bars (the rendered signal()'s semantics) ───────────────
def _prev(x: FloatArray) -> FloatArray:
    out = np.full(len(x), np.nan)
    out[1:] = x[:-1]
    return out


def _cmp(a: FloatArray, op: str, b: FloatArray | float) -> BoolArray:
    with np.errstate(invalid="ignore"):
        return np.greater(a, b) if op == ">" else np.less(a, b)  # NaN ⇒ False, as in Python


def _cross(a: FloatArray, b: FloatArray, up: bool) -> BoolArray:
    """``ind.cross_up``/``cross_down`` at every bar: False while any of the four is NaN."""
    a0, b0 = _prev(a), _prev(b)
    finite = np.isfinite(a0) & np.isfinite(a) & np.isfinite(b0) & np.isfinite(b)
    with np.errstate(invalid="ignore"):
        hit = (a0 <= b0) & (a > b) if up else (a0 >= b0) & (a < b)
    return finite & hit


class Evaluator:
    """Clause masks over one symbol's bars, each indicator computed once."""

    def __init__(self, bars: Bars) -> None:
        self.bars = bars
        self._cache: dict[tuple[str, int], FloatArray] = {}

    def ind(self, op: str, n: int) -> FloatArray:
        key = (op, n)
        if key not in self._cache:
            if op == "atr":
                self._cache[key] = R.atr(self.bars, n)
            elif op == "stdev":
                self._cache[key] = stdev(self.bars.close, n)
            else:
                self._cache[key] = getattr(R, op)(self.bars.close, n)
        return self._cache[key]

    def series(self, s: Series) -> FloatArray:
        if isinstance(s, Close):
            return self.bars.close
        return self.ind(s.op, int(s.period.value))

    def distance_ratio(self, c: Distance, scale: str = "atr") -> FloatArray:
        """``(a − b)`` over one of the candidate denominators, ``n`` being the period of b (of
        a when b is the close):

        ``atr``      ATR(14) — today's;
        ``stdev``    ``stdev(close, n)``;
        ``atr_sqrt`` ``ATR(14) · √n`` — a random walk's distance from its n-bar mean;
        ``spread``   ``stdev(a − b, n)`` — the spread's own dispersion.
        """
        ref = c.right if isinstance(c.right, Indicator) else c.left
        assert isinstance(ref, Indicator)  # two price series are never both the close
        n = int(ref.period.value)
        spread = self.series(c.left) - self.series(c.right)
        if scale == "atr":
            den = self.ind("atr", 14)
        elif scale == "stdev":
            den = self.ind("stdev", n)
        elif scale == "atr_sqrt":
            den = self.ind("atr", 14) * np.sqrt(n)
        elif scale == "spread":
            den = _finite_stdev(spread, n)
        else:
            raise DiagnosticsError(f"unknown Distance scale {scale!r}")
        with np.errstate(divide="ignore", invalid="ignore"):
            return spread / den

    def mask(self, c: Clause, scale: str = "atr") -> BoolArray:
        """The clause at every bar; ``scale`` is ``Distance``'s denominator."""
        if isinstance(c, Compare):
            return _cmp(self.series(c.left), c.op, self.series(c.right))
        if isinstance(c, Cross):
            return _cross(self.series(c.left), self.series(c.right), c.up)
        if isinstance(c, Threshold):
            return _cmp(self.series(c.osc), c.op, float(c.level.value))
        if isinstance(c, CrossLevel):
            osc = self.series(c.osc)
            return _cross(osc, np.full(len(osc), float(c.level.value)), c.up)
        if isinstance(c, Distance):
            own = "spread" if c.scale == "spread" else scale  # a v4 clause names its own
            return _cmp(self.distance_ratio(c, own), c.op, float(c.k.value))
        if isinstance(c, Breakout):
            level = self.ind("rolling_max" if c.up else "rolling_min", int(c.period.value))
            return _cmp(self.bars.close, ">" if c.up else "<", _prev(level))
        if isinstance(c, Slope):
            s = self.series(c.series)
            return _cmp(s, ">" if c.up else "<", _prev(s))
        raise DiagnosticsError(f"unknown clause {c!r}")


def periods(node: object) -> Iterator[int]:
    """Every period parameter under a clause."""
    if isinstance(node, Param):
        if node.kind == "period":
            yield int(node.value)
    elif is_dataclass(node) and not isinstance(node, type):
        for f in fields(node):
            yield from periods(getattr(node, f.name))


def band_of(c: Clause) -> str:
    longest = max(periods(c), default=0)
    for lo, hi in BANDS:
        if lo <= longest <= hi:
            return f"{lo}-{hi}"
    return "none"


# ── the measurements ──────────────────────────────────────────────────────────────────────────
def fire_rate(ev: Evaluator, c: Clause, scale: str = "atr") -> float:
    """Share of bars the clause is true on, after its longest warm-up (False while warming)."""
    m = ev.mask(c, scale)[max(periods(c), default=0) + 1 :]
    return float(m.mean()) if len(m) else float("nan")


def _flag(df: pd.DataFrame) -> pd.DataFrame:
    df["degenerate"] = (df["rate"] < DEGENERATE) | (df["rate"] > 1 - DEGENERATE)
    return df


def fire_rates(ev: Evaluator, clauses: list[Clause]) -> pd.DataFrame:
    rows = [
        {"clause": type(c).__name__, "band": band_of(c), "rate": fire_rate(ev, c)} for c in clauses
    ]
    return _flag(pd.DataFrame(rows))


# ── grid: one clause per parameter point, to see where a range turns degenerate ─────────────
PERIOD_GRID = (2, 3, 5, 8, 14, 20, 30, 50, 75, 100, 150, 200, 300)
K_GRID = (-3.0, -2.0, -1.0, 0.0, 1.0, 2.0, 3.0)
LEVELS_PER_RANGE = 5


def _period(n: int) -> Param:
    return Param("period", 2, 300, n)


def _number(v: float, low: float, high: float) -> Param:
    return Param("level", low, high, round(float(v), 4))


def grid_clauses() -> list[Clause]:
    """Every clause type that has a range worth bounding, on a fixed grid of its numbers.

    ``Distance`` only with ``>``: with ``<`` it is true on the other bars, so it is degenerate
    exactly when the ``>`` clause is. ``Compare`` and ``Slope`` are true about half the time
    whatever their periods (the sampled ``fire`` table), so they are left out.
    """
    out: list[Clause] = []
    for osc in OSC_OPS:
        lo_p, hi_p = period_range(osc)
        for n in (n for n in PERIOD_GRID if lo_p <= n <= hi_p):
            ind = Indicator(osc, _period(n))
            for cmp in (">", "<"):
                lo, hi = LEVEL_RANGES[(osc, cmp)]
                for v in np.linspace(lo, hi, LEVELS_PER_RANGE):
                    out.append(Threshold(ind, cmp, _number(v, lo, hi)))
                    out.append(CrossLevel(ind, cmp == ">", _number(v, lo, hi)))
    for i, n1 in enumerate(PERIOD_GRID):
        fast = Indicator("sma", _period(n1))
        out.append(Cross(Close(), True, fast))
        out.extend(Cross(fast, True, Indicator("sma", _period(n2))) for n2 in PERIOD_GRID[i + 1 :])
        for k in K_GRID:
            out.append(Distance(Close(), fast, ">", _number(k, -3, 3)))
            out.extend(
                Distance(fast, Indicator("sma", _period(n2)), ">", _number(k, -3, 3))
                for n2 in PERIOD_GRID[i + 1 :]
            )
        out.extend(Breakout(up, _period(n1)) for up in (True, False))
    return out


def describe(c: Clause) -> dict[str, object]:
    """The grid coordinates of a clause: its operator, periods and level or k."""
    row: dict[str, object] = {"clause": type(c).__name__, "osc": "", "op": "", "n1": 0, "n2": 0}
    if isinstance(c, Threshold | CrossLevel):
        op = c.op if isinstance(c, Threshold) else ("up" if c.up else "down")
        row.update(osc=c.osc.op, op=op, n1=int(c.osc.period.value), value=float(c.level.value))
    elif isinstance(c, Cross | Distance):
        ps = sorted(periods(c))
        row.update(n1=0 if len(ps) == 1 else ps[0], n2=ps[-1])
        row["value"] = float(c.k.value) if isinstance(c, Distance) else np.nan
    elif isinstance(c, Breakout):
        row.update(op="up" if c.up else "down", n1=int(c.period.value), value=np.nan)
    return row


def grid_rates(ev: Evaluator) -> pd.DataFrame:
    """``n1 = 0`` is the close. ``Distance`` once per denominator."""
    rows = []
    for c in grid_clauses():
        for scale in DISTANCE_SCALES if isinstance(c, Distance) else ("atr",):
            rows.append({**describe(c), "scale": scale, "rate": fire_rate(ev, c, scale)})
    return _flag(pd.DataFrame(rows))


# ── scenarios: the sampled clauses under the P3-57 candidate changes ──────────────────────────
def _log_uniform(rng: np.random.Generator, lo: float, hi: float) -> int:
    return int(np.clip(round(float(np.exp(rng.uniform(np.log(lo), np.log(hi))))), lo, hi))


def resample_periods(
    node: object, rng: np.random.Generator, caps: dict[str, tuple[int, int]]
) -> object:
    """The clause with every period drawn again, log-uniform within its bounds — an
    indicator named in ``caps`` within the capped bounds instead. Other numbers are kept."""
    if isinstance(node, Indicator):
        lo, hi = node.period.low, node.period.high
        if node.op in caps:
            lo, hi = max(lo, caps[node.op][0]), min(hi, caps[node.op][1])
        return Indicator(node.op, Param("period", lo, hi, _log_uniform(rng, lo, hi)))
    if isinstance(node, Param) and node.kind == "period":  # Breakout's own period
        return Param("period", node.low, node.high, _log_uniform(rng, node.low, node.high))
    if isinstance(node, tuple):
        return tuple(resample_periods(x, rng, caps) for x in node)
    if is_dataclass(node) and not isinstance(node, type):
        kwargs = {f.name: resample_periods(getattr(node, f.name), rng, caps) for f in fields(node)}
        return type(node)(**kwargs)
    return node


RSI_CAP = (2, 30)
ZSCORE_CAP = (10, 300)
# name → (period distribution, caps, Distance denominator)
SCENARIOS: dict[str, tuple[str, dict[str, tuple[int, int]], str]] = {
    "current": ("uniform", {}, "atr"),
    "log": ("log", {}, "atr"),
    "log+rsi30": ("log", {"rsi": RSI_CAP}, "atr"),
    **{f"log+rsi30+{s}": ("log", {"rsi": RSI_CAP}, s) for s in DISTANCE_SCALES[1:]},
    # a population z-score of n values never exceeds (n − 1)/√n: 1.79 at n = 5, 2.85 at n = 10
    "log+rsi30+spread+z10": ("log", {"rsi": RSI_CAP, "zscore": ZSCORE_CAP}, "spread"),
}


def scenario_clauses(clauses: list[Clause], seed: int) -> dict[str, list[Clause]]:
    """Each scenario's version of the sampled clauses, the same draw for every symbol."""
    out: dict[str, list[Clause]] = {}
    for name, (dist, caps, _) in SCENARIOS.items():
        rng = np.random.default_rng(seed)
        if dist == "uniform":
            out[name] = list(clauses)
        else:
            out[name] = [resample_periods(c, rng, caps) for c in clauses]  # type: ignore[misc]
    return out


def scenario_rates(ev: Evaluator, sets: dict[str, list[Clause]]) -> pd.DataFrame:
    rows = [
        {"scenario": name, "clause": type(c).__name__, "rate": fire_rate(ev, c, SCENARIOS[name][2])}
        for name, clauses in sets.items()
        for c in clauses
    ]
    return _flag(pd.DataFrame(rows))


def distance_saturation(ev: Evaluator, clauses: list[Clause]) -> pd.DataFrame:
    rows = []
    for c in clauses:
        if not isinstance(c, Distance):
            continue
        for scale in DISTANCE_SCALES:
            d = ev.distance_ratio(c, scale)
            d = d[np.isfinite(d)]
            if len(d):
                rows.append(
                    {
                        "band": band_of(c),
                        "scale": scale,
                        "outside_k": float(np.mean(np.abs(d) > K_RANGE)),
                        "abs_median": float(np.median(np.abs(d))),
                    }
                )
    return pd.DataFrame(rows)


def _mr_triggers(ev: Evaluator, side: str) -> dict[str, BoolArray]:
    """Canonical mean-reversion entries of one side."""
    long = side == "long"
    return {
        "any": np.ones(len(ev.bars), dtype=bool),
        "rsi14": _cmp(ev.ind("rsi", 14), "<" if long else ">", 30.0 if long else 70.0),
        "zscore20": _cmp(ev.ind("zscore", 20), "<" if long else ">", -2.0 if long else 2.0),
        "breakout20": _cmp(
            ev.bars.close,
            "<" if long else ">",
            _prev(ev.ind("rolling_min" if long else "rolling_max", 20)),
        ),
    }


def bollinger_stop(ev: Evaluator) -> pd.DataFrame:
    """P(Bollinger stop <= 0) — close at or beyond the band on the stop's side — per trigger."""
    rows = []
    for side in ("long", "short"):
        triggers = _mr_triggers(ev, side)
        for n in STOP_PERIODS:
            band = (
                R.boll_lower(ev.bars.close, n) if side == "long" else R.boll_upper(ev.bars.close, n)
            )
            dist = ev.bars.close - band if side == "long" else band - ev.bars.close
            valid = np.isfinite(dist)
            for name, trig in triggers.items():
                on = valid & trig
                rows.append(
                    {
                        "side": side,
                        "n": n,
                        "trigger": name,
                        "bars": int(on.sum()),
                        "stop_le_0": float(np.mean(dist[on] <= 0)) if on.any() else float("nan"),
                    }
                )
    return pd.DataFrame(rows)


def _quantiles(name: str, x: FloatArray) -> dict[str, float | str]:
    x = x[np.isfinite(x)]
    row: dict[str, float | str] = {"feature": name}
    for q in QUANTILES:
        row[f"q{round(q * 100):02d}"] = float(np.quantile(x, q)) if len(x) else float("nan")
    return row


def volatility(ev: Evaluator) -> pd.DataFrame:
    rows = []
    for n1, n2 in VOL_RATIOS:
        with np.errstate(divide="ignore", invalid="ignore"):
            ratio = ev.ind("atr", n1) / ev.ind("atr", n2)
        rows.append(_quantiles(f"atr{n1}/atr{n2}", ratio))
    for n in BAND_WIDTHS:
        rows.append(_quantiles(f"bandwidth{n}", bandwidth(ev.bars.close, n)))
    return pd.DataFrame(rows)


def features(ev: Evaluator) -> dict[str, FloatArray]:
    """Dimensionless features: what the grammar has today, then the P3-57/58/59 candidates."""
    close, atr14 = ev.bars.close, ev.ind("atr", 14)
    with np.errstate(divide="ignore", invalid="ignore"):
        return {
            "rsi14": ev.ind("rsi", 14),
            "zscore20": ev.ind("zscore", 20),
            "dist_sma20_atr": (close - ev.ind("sma", 20)) / atr14,
            "dist_ema20_atr": (close - ev.ind("ema", 20)) / atr14,
            "dist_sma100_atr": (close - ev.ind("sma", 100)) / atr14,
            "dist_sma100_sd": (close - ev.ind("sma", 100)) / ev.ind("stdev", 100),
            "ema20_slope": ev.ind("ema", 20) / _prev(ev.ind("ema", 20)) - 1.0,
            "atr14/atr100": atr14 / ev.ind("atr", 100),
            "bandwidth20": bandwidth(close, 20),
            "efficiency20": efficiency_ratio(close, 20),
            "tsmom20": tsmom(close, 20),
            "relvol20": relative_volume(ev.bars.volume, 20),
            "close_location": close_location(ev.bars),
        }


def correlation(ev: Evaluator) -> pd.DataFrame:
    return pd.DataFrame(features(ev)).corr(method="spearman")


# ── input ─────────────────────────────────────────────────────────────────────────────────────
def check_in_sample(path: Path) -> Path:
    resolved = path.resolve()
    if any(part.lower().startswith("holdout") for part in resolved.parts):
        raise DiagnosticsError(f"{path} is under a holdout folder: in-sample data only (P6)")
    return resolved


def load_folder(data_dir: Path, timeframe: str | None) -> list[Bars]:
    folder = check_in_sample(data_dir)
    out = []
    for file in sorted(folder.glob("*.parquet")):
        symbol, _, tf = file.stem.rpartition("_")
        if timeframe is None or tf == timeframe:
            out.append(read_bars(file, symbol.replace("-", "/"), tf))
    if not out:
        raise DiagnosticsError(f"no parquet bars in {folder}")
    return out


def sample_clauses(n_per_type: int, seed: int) -> list[Clause]:
    rng = np.random.default_rng(seed)
    return [sample_clause(rng, kind) for kind in CLAUSE_TYPES for _ in range(n_per_type)]


# ── report ────────────────────────────────────────────────────────────────────────────────────
Measure = Callable[[Evaluator, list[Clause]], pd.DataFrame]


def run(bars_list: list[Bars], clauses: list[Clause], seed: int = 0) -> dict[str, pd.DataFrame]:
    """Every measurement for every symbol, the symbol as the first column."""
    sets = scenario_clauses(clauses, seed)
    measures: dict[str, Measure] = {
        "fire": fire_rates,
        "grid": lambda ev, _: grid_rates(ev),
        "scenarios": lambda ev, _: scenario_rates(ev, sets),
        "distance": distance_saturation,
        "bollinger": lambda ev, _: bollinger_stop(ev),
        "volatility": lambda ev, _: volatility(ev),
    }
    parts: dict[str, list[pd.DataFrame]] = {k: [] for k in (*measures, "corr")}
    for bars in bars_list:
        ev = Evaluator(bars)
        for name, fn in measures.items():
            parts[name].append(fn(ev, clauses).assign(symbol=bars.symbol))
        corr = correlation(ev)
        long = corr.rename_axis("a").reset_index().melt(id_vars="a", var_name="b", value_name="rho")
        parts["corr"].append(long.assign(symbol=bars.symbol))
    out = {k: pd.concat(v, ignore_index=True) for k, v in parts.items()}
    for df in out.values():
        df.insert(0, "symbol", df.pop("symbol"))
    return out


def summaries(tables: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Symbols pooled: what the text report prints."""
    fire = (
        tables["fire"]
        .groupby(["clause", "band"])
        .agg(rate_median=("rate", "median"), degenerate=("degenerate", "mean"), n=("rate", "size"))
    )
    distance = (
        tables["distance"]
        .groupby(["band", "scale"])
        .agg(outside_k=("outside_k", "mean"), abs_median=("abs_median", "median"))
    )
    boll = tables["bollinger"].pivot_table(
        index=["side", "n"], columns="trigger", values="stop_le_0", aggfunc="mean"
    )
    vol = tables["volatility"].drop(columns="symbol").groupby("feature", sort=False).median()
    corr = tables["corr"].groupby(["a", "b"])["rho"].mean().unstack()
    scenarios = tables["scenarios"].pivot_table(
        index="clause", columns="scenario", values="degenerate", aggfunc="mean", sort=False
    )
    scenarios.loc["all"] = tables["scenarios"].groupby("scenario", sort=False)["degenerate"].mean()
    return {
        "fire": fire,
        **grid_summaries(tables["grid"]),
        "scenarios (share of sampled clauses degenerate)": scenarios,
        "distance": distance,
        "bollinger": boll,
        "volatility": vol,
        "corr": corr,
    }


def grid_summaries(grid: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Share of symbols on which each grid point is degenerate (0 = never, 1 = on every one)."""
    out: dict[str, pd.DataFrame] = {}
    for clause in ("Threshold", "CrossLevel"):
        for osc in OSC_OPS:
            part = grid[(grid.clause == clause) & (grid.osc == osc)]
            out[f"grid {clause} {osc}: period x (op, level)"] = part.pivot_table(
                index="n1", columns=["op", "value"], values="degenerate", aggfunc="mean"
            )
    cross = grid[grid.clause == "Cross"]
    out["grid Cross: fast (0 = close) x slow"] = cross.pivot_table(
        index="n1", columns="n2", values="degenerate", aggfunc="mean"
    )
    for scale in DISTANCE_SCALES:
        part = grid[(grid.clause == "Distance") & (grid.scale == scale)]
        out[f"grid Distance over {scale}: slow period x k (mean over fast)"] = part.pivot_table(
            index="n2", columns="value", values="degenerate", aggfunc="mean"
        )
    out["grid Breakout: period x op"] = grid[grid.clause == "Breakout"].pivot_table(
        index="n1", columns="op", values="degenerate", aggfunc="mean"
    )
    return out


def strong_pairs(corr: pd.DataFrame, threshold: float = 0.7) -> list[tuple[str, str, float]]:
    names = [str(c) for c in corr.columns]
    rho = corr.loc[names, names].to_numpy(dtype=np.float64)
    return [
        (a, b, float(rho[i, j]))
        for i, a in enumerate(names)
        for j, b in enumerate(names)
        if j > i and abs(rho[i, j]) >= threshold
    ]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--data-dir", type=Path, default=Path("data/is"))
    p.add_argument("--timeframe", default=None, help="only files of this timeframe, e.g. 1d")
    p.add_argument("--samples", type=int, default=200, help="clauses sampled per clause type")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--out", type=Path, default=None, help="folder for the per-symbol CSVs")
    args = p.parse_args(argv)
    try:
        bars_list = load_folder(args.data_dir, args.timeframe)
    except DiagnosticsError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    tables = run(bars_list, sample_clauses(args.samples, args.seed), args.seed)
    if args.out is not None:
        args.out.mkdir(parents=True, exist_ok=True)
        for name, df in tables.items():
            df.to_csv(args.out / f"{name}.csv", index=False)
    pd.set_option("display.width", 160)
    pd.set_option("display.max_columns", 20)
    print("symbols:", ", ".join(f"{b.symbol} {b.timeframe} ({len(b)} bars)" for b in bars_list))
    pooled = summaries(tables)
    for name, df in pooled.items():
        print(f"\n== {name} ==")
        print(df.round(3).to_string())
    print("\n== |spearman| >= 0.7 ==")
    for a, b, rho in strong_pairs(pooled["corr"]):
        print(f"{a:>18} ~ {b:<18} {rho:+.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
