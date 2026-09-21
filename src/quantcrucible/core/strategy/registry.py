"""Indicator whitelist ``ind`` (Architecture §3.1.6 DSL, §3.3.1 rule 3).

Series indicators return one value per bar (NaN during warm-up) and are **causal**: the value at
bar t depends only on bars ≤ t. ``tests/core/strategy/test_registry.py`` checks this for every
entry of :data:`INDICATORS`; a new indicator is added only with that test passing. Predicates
(``cross_up``/``cross_down``) act on :class:`SeriesView` inside ``signal()``.

Gate ①a allows calls to ``ind.<name>`` only for names listed in :data:`INDICATORS`.
"""

from __future__ import annotations

import math
from typing import Literal

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
from scipy.signal import lfilter

from quantcrucible.core.strategy.base import Bars, FloatArray, SeriesView

IndicatorKind = Literal["series", "bars", "predicate"]


def _check_period(n: int) -> None:
    if isinstance(n, bool) or not isinstance(n, int | np.integer) or n < 1:
        raise ValueError(f"period must be a positive int, got {n!r}")


def _as_float(x: FloatArray) -> FloatArray:
    return np.asarray(x, dtype=np.float64)


def _first_valid_run(x: FloatArray, n: int) -> int | None:
    """First index i where x[i:i+n] are all finite."""
    finite = np.isfinite(x)
    run = 0
    for i, ok in enumerate(finite):
        run = run + 1 if ok else 0
        if run == n:
            return i - n + 1
    return None


def _smooth(x: FloatArray, n: int, alpha: float) -> FloatArray:
    """Exponential smoothing seeded with the SMA of the first n valid values."""
    out = np.full(len(x), np.nan)
    start = _first_valid_run(x, n)
    if start is None:
        return out
    seed = float(np.mean(x[start : start + n]))
    out[start + n - 1] = seed
    rest = x[start + n :]
    if len(rest):
        y, _ = lfilter([alpha], [1.0, -(1.0 - alpha)], rest, zi=[(1.0 - alpha) * seed])
        out[start + n :] = y
    return out


def sma(x: FloatArray, n: int) -> FloatArray:
    _check_period(n)
    x = _as_float(x)
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        out[n - 1 :] = sliding_window_view(x, n).mean(axis=1)
    return out


def ema(x: FloatArray, n: int) -> FloatArray:
    _check_period(n)
    return _smooth(_as_float(x), n, 2.0 / (n + 1.0))


def rolling_max(x: FloatArray, n: int) -> FloatArray:
    _check_period(n)
    x = _as_float(x)
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        out[n - 1 :] = sliding_window_view(x, n).max(axis=1)
    return out


def rolling_min(x: FloatArray, n: int) -> FloatArray:
    _check_period(n)
    x = _as_float(x)
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        out[n - 1 :] = sliding_window_view(x, n).min(axis=1)
    return out


def zscore(x: FloatArray, n: int) -> FloatArray:
    """(x − rolling mean) / rolling population std over n bars; NaN where std is 0."""
    _check_period(n)
    x = _as_float(x)
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        win = sliding_window_view(x, n)
        std = win.std(axis=1)
        with np.errstate(divide="ignore", invalid="ignore"):
            z = (x[n - 1 :] - win.mean(axis=1)) / std
        out[n - 1 :] = np.where(std > 0, z, np.nan)
    return out


def atr(bars: Bars, n: int) -> FloatArray:
    """Wilder's average true range."""
    _check_period(n)
    h, lo, c = _as_float(bars.high), _as_float(bars.low), _as_float(bars.close)
    if len(c) == 0:
        return np.full(0, np.nan)
    prev_close = np.concatenate(([np.nan], c[:-1]))
    tr = np.fmax(h - lo, np.fmax(np.abs(h - prev_close), np.abs(lo - prev_close)))
    return _smooth(tr, n, 1.0 / n)


def rsi(x: FloatArray, n: int) -> FloatArray:
    """Wilder's RSI in [0, 100]."""
    _check_period(n)
    x = _as_float(x)
    out = np.full(len(x), np.nan)
    if len(x) <= n:
        return out
    delta = np.diff(x)
    gain = _smooth(np.where(delta > 0, delta, 0.0), n, 1.0 / n)
    loss = _smooth(np.where(delta < 0, -delta, 0.0), n, 1.0 / n)
    with np.errstate(divide="ignore", invalid="ignore"):
        value = np.where(loss == 0, 100.0, 100.0 - 100.0 / (1.0 + gain / loss))
    value = np.where(np.isfinite(gain) & np.isfinite(loss), value, np.nan)
    out[1:] = value
    return out


def _prev_now(v: SeriesView | float) -> tuple[float, float]:
    if isinstance(v, SeriesView):
        return v.ago(1), v.now
    return float(v), float(v)


def cross_up(a: SeriesView, b: SeriesView | float) -> bool:
    """``a`` crossed above ``b`` on this bar. False while either value is NaN."""
    a0, a1 = _prev_now(a)
    b0, b1 = _prev_now(b)
    if any(math.isnan(v) for v in (a0, a1, b0, b1)):
        return False
    return a0 <= b0 and a1 > b1


def cross_down(a: SeriesView, b: SeriesView | float) -> bool:
    """``a`` crossed below ``b`` on this bar. False while either value is NaN."""
    a0, a1 = _prev_now(a)
    b0, b1 = _prev_now(b)
    if any(math.isnan(v) for v in (a0, a1, b0, b1)):
        return False
    return a0 >= b0 and a1 < b1


class _Indicators:
    """The ``ind`` namespace strategies call."""

    sma = staticmethod(sma)
    ema = staticmethod(ema)
    rolling_max = staticmethod(rolling_max)
    rolling_min = staticmethod(rolling_min)
    zscore = staticmethod(zscore)
    atr = staticmethod(atr)
    rsi = staticmethod(rsi)
    cross_up = staticmethod(cross_up)
    cross_down = staticmethod(cross_down)


ind = _Indicators()

# name → how it is called: "series" f(array, n), "bars" f(bars, n), "predicate" f(view, view|float)
INDICATORS: dict[str, IndicatorKind] = {
    "sma": "series",
    "ema": "series",
    "rolling_max": "series",
    "rolling_min": "series",
    "zscore": "series",
    "rsi": "series",
    "atr": "bars",
    "cross_up": "predicate",
    "cross_down": "predicate",
}
