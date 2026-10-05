"""The evaluation clock — what one observation of a return series is (P3-60, ADR-0049, D26).

A campaign locked with ``derived.evaluation_clock: daily_v1`` measures every Sharpe, Sortino,
PSR/DSR moment, ``N_eff`` correlation and holdout verdict on returns compounded per UTC day and
annualises them with 365. A sub-daily campaign is then judged on the same clock as a daily one,
and intraday autocorrelation stays inside one observation instead of inflating ``T``. The
backtest, ``min_trades`` and gate ④'s PBO/CPCV stay per bar. A lock without the key keeps the
per-bar clock it ran with.

A bar belongs to the UTC calendar day its interval lies in. ``Bars.ts`` is the bar's close
time, so the day's label is ``ts.ceil("D")``: an hourly bar closing at 00:00 closes the day
before, and a daily bar keeps its own timestamp — hourly days line up with daily bars.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

import numpy as np
import pandas as pd

from quantcrucible.data.source import timeframe_delta

DAILY_V1 = "daily_v1"
CLOCKS = (DAILY_V1,)
DAYS_PER_YEAR = 365.0  # crypto trades 24/7, like `periods_per_year`


def evaluation_clock(lock: Mapping[str, Any]) -> str | None:
    """The campaign's locked clock; ``None`` (per bar) for a lock without the key."""
    tag = lock.get("derived", {}).get("evaluation_clock")
    if tag is not None and tag not in CLOCKS:
        raise ValueError(f"unknown locked evaluation clock {tag!r}")
    return None if tag is None else str(tag)


def periods_per_year(timeframe: str) -> float:
    return DAYS_PER_YEAR * 86_400 / timeframe_delta(timeframe).total_seconds()


def observations_per_year(ppy: float, clock: str | None) -> float:
    """Observations a year, on ``clock``, of a series with ``ppy`` bars a year."""
    return DAYS_PER_YEAR if clock == DAILY_V1 else ppy


def clock_ppy(timeframe: str, clock: str | None) -> float:
    """Observations a year of a ``timeframe`` series on ``clock``."""
    return observations_per_year(periods_per_year(timeframe), clock)


def daily_returns(returns: pd.Series) -> pd.Series:
    """Per-bar returns (indexed by bar close time) compounded per UTC day.

    A day with one bar passes its return through untouched — ``(1 + r) - 1`` is not always ``r``
    in floating point — so a daily series comes back bit for bit. Days without a bar are absent.
    """
    index = pd.DatetimeIndex(returns.index)
    if not index.is_monotonic_increasing:
        raise ValueError("returns must be indexed by increasing bar close times")
    values = returns.to_numpy(dtype=np.float64)
    if not len(values):
        return pd.Series([], index=pd.DatetimeIndex([]), dtype=np.float64)
    labels = index.ceil("D")
    starts = np.flatnonzero(np.r_[True, labels[1:] != labels[:-1]])
    counts = np.diff(np.r_[starts, len(values)])
    compounded = np.multiply.reduceat(1.0 + values, starts) - 1.0
    out = np.where(counts == 1, values[starts], compounded)
    return pd.Series(out, index=labels[starts])


def on_clock(returns: pd.Series, ppy: float, clock: str | None) -> tuple[pd.Series, float]:
    """``returns`` (per bar, ``ppy`` a year) as observed on ``clock``, with its periods a year."""
    if clock == DAILY_V1:
        return daily_returns(returns), observations_per_year(ppy, clock)
    return returns, ppy


def annual_sharpe(r: pd.Series, periods_per_year: float) -> float:
    """Mean over sample deviation, annualised; 0 when the deviation is 0 or undefined. The same
    number as ``BacktestResult.sharpe`` on the same returns."""
    x = r.to_numpy(dtype=np.float64)
    std = float(np.std(x, ddof=1)) if len(x) > 1 else 0.0
    return float(np.mean(x) / std * np.sqrt(periods_per_year)) if std > 0 else 0.0


def annual_sortino(r: pd.Series, periods_per_year: float) -> float:
    """Mean over downside deviation (target 0), annualised; 0 without a losing observation. The
    same number as ``BacktestResult.sortino`` on the same returns."""
    x = r.to_numpy(dtype=np.float64)
    downside = float(np.sqrt(np.mean(np.minimum(x, 0.0) ** 2))) if len(x) else 0.0
    if len(x) < 2 or downside == 0.0:
        return 0.0
    return float(np.mean(x) / downside * math.sqrt(periods_per_year))
