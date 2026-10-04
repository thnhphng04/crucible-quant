"""Indicator whitelist ``ind`` (Architecture §3.1.6 DSL, §3.3.1 rule 3).

Series indicators return one value per bar (NaN during warm-up) and are **causal**: the value at
bar t depends only on bars ≤ t. ``tests/core/strategy/test_registry.py`` checks this for every
entry of :data:`INDICATORS`; a new indicator is added only with that test passing. Predicates
(``cross_up``/``cross_down``) act on :class:`SeriesView` inside ``signal()``.

Gate ①a allows calls to ``ind.<name>`` only for names listed in :data:`INDICATORS`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
from scipy.signal import lfilter

from quantcrucible.core.strategy.base import Bars, FloatArray, SeriesView

IndicatorKind = Literal["series", "bars", "pair", "series_ratio", "bars_ratio", "predicate"]


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


def boll_upper(x: FloatArray, n: int) -> FloatArray:
    """Upper Bollinger band, two population standard deviations above SMA."""
    _check_period(n)
    x = _as_float(x)
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        windows = sliding_window_view(x, n)
        out[n - 1 :] = windows.mean(axis=1) + 2.0 * windows.std(axis=1)
    return out


def boll_lower(x: FloatArray, n: int) -> FloatArray:
    """Lower Bollinger band, two population standard deviations below SMA."""
    _check_period(n)
    x = _as_float(x)
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        windows = sliding_window_view(x, n)
        out[n - 1 :] = windows.mean(axis=1) - 2.0 * windows.std(axis=1)
    return out


def bandwidth(x: FloatArray, n: int) -> FloatArray:
    """Bollinger band width per √bar: ``(boll_upper − boll_lower) / (sma · √n) = 4·std / (sma·√n)``.
    A random walk's band widens as √n, so the ratio of two widths no longer depends on their
    periods (``band_ratio``, ADR-0047). NaN where the sma or the std is not positive."""
    _check_period(n)
    x = _as_float(x)
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        windows = sliding_window_view(x, n)
        mean, sd = windows.mean(axis=1), windows.std(axis=1)
        with np.errstate(divide="ignore", invalid="ignore"):
            width = 4.0 * sd / (mean * np.sqrt(n))
        out[n - 1 :] = np.where((mean > 0) & (sd > 0), width, np.nan)
    return out


def spread_stdev(x: FloatArray, y: FloatArray, n: int) -> FloatArray:
    """Population standard deviation of the spread ``x − y`` over n bars; NaN where it is 0 or
    a window holds a NaN — the yardstick of a grammar-v4 ``Distance`` (ADR-0045)."""
    _check_period(n)
    d = _as_float(x) - _as_float(y)
    out = np.full(len(d), np.nan)
    if len(d) >= n:
        sd = sliding_window_view(d, n).std(axis=1)
        out[n - 1 :] = np.where(sd > 0, sd, np.nan)
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


def _ratio(a: FloatArray, b: FloatArray) -> FloatArray:
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(b > 0, a / b, np.nan)


def band_ratio(x: FloatArray, fast: int, slow: int) -> FloatArray:
    """``bandwidth(x, fast) / bandwidth(x, slow)``: the band squeezed (< 1) or expanded (> 1)
    against its longer self, both per √bar — one feature, so a gate-③ correlation test never
    sets its two widths against each other (grammar v5's ``Bandwidth``, ADR-0047)."""
    _check_period(slow)
    return _ratio(bandwidth(x, fast), bandwidth(x, slow))


def atr_ratio(bars: Bars, fast: int, slow: int) -> FloatArray:
    """``atr(bars, fast) / atr(bars, slow)``: bar ranges expanding (> 1) or contracting (< 1)
    against their longer average; NaN where the slow ATR is not positive (grammar v5's
    ``VolRatio``, ADR-0047)."""
    _check_period(slow)
    return _ratio(atr(bars, fast), atr(bars, slow))


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
    boll_upper = staticmethod(boll_upper)
    boll_lower = staticmethod(boll_lower)
    bandwidth = staticmethod(bandwidth)
    band_ratio = staticmethod(band_ratio)
    spread_stdev = staticmethod(spread_stdev)
    atr = staticmethod(atr)
    atr_ratio = staticmethod(atr_ratio)
    rsi = staticmethod(rsi)
    cross_up = staticmethod(cross_up)
    cross_down = staticmethod(cross_down)


ind = _Indicators()

IndicatorOutput = Literal["same", "dimensionless", "price", "bool"]


@dataclass(frozen=True, slots=True)
class OpSpec:
    """What the typed DSL knows about one indicator (arch §3.1.11, P2-04).

    ``output`` is its unit: ``same`` as the input series, ``dimensionless`` (bounded or
    normalized), ``price`` (price units whatever the input) or ``bool`` (predicates).
    ``period`` is the default range of its period argument, ``value_range`` the typical range
    of a dimensionless output (where thresholds are drawn), ``warmup(n)`` the number of bars
    until the first finite value — for a ratio, ``n`` is its slow period.
    """

    kind: IndicatorKind
    output: IndicatorOutput
    period: tuple[int, int] | None = None
    value_range: tuple[float, float] | None = None
    warmup_extra: int = 0

    def warmup(self, n: int) -> int:
        return n + self.warmup_extra


OPS: dict[str, OpSpec] = {
    "sma": OpSpec("series", "same", (2, 300)),
    "ema": OpSpec("series", "same", (2, 300)),
    "rolling_max": OpSpec("series", "same", (2, 300)),
    "rolling_min": OpSpec("series", "same", (2, 300)),
    "zscore": OpSpec("series", "dimensionless", (5, 300), (-3.0, 3.0)),
    "boll_upper": OpSpec("series", "same", (8, 8)),
    "boll_lower": OpSpec("series", "same", (8, 8)),
    # per √bar: q95 ≈ 0.14 on daily crypto, 0.03 hourly — the grammar only reads its ratios
    "bandwidth": OpSpec("series", "dimensionless", (2, 300), (0.0, 0.2)),
    "rsi": OpSpec("series", "dimensionless", (2, 100), (0.0, 100.0), warmup_extra=1),
    "atr": OpSpec("bars", "price", (2, 300)),
    "spread_stdev": OpSpec("pair", "price", (2, 300)),
    # fast/slow ratios, ≈ 1 on average: q05–q95 ≈ 0.3–2.7 (band), 0.4–2.4 (atr) on IS bars
    "band_ratio": OpSpec("series_ratio", "dimensionless", (2, 300), (0.3, 2.5)),
    "atr_ratio": OpSpec("bars_ratio", "dimensionless", (2, 300), (0.6, 1.6)),
    "cross_up": OpSpec("predicate", "bool"),
    "cross_down": OpSpec("predicate", "bool"),
}

# name → how it is called: "series" f(array, n), "bars" f(bars, n), "pair" f(array, array, n),
# "series_ratio" f(array, fast, slow), "bars_ratio" f(bars, fast, slow),
# "predicate" f(view, view|float)
INDICATORS: dict[str, IndicatorKind] = {name: spec.kind for name, spec in OPS.items()}
