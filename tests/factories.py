"""Synthetic data for tests — never real market data, never the holdout."""

from __future__ import annotations

import numpy as np

from quantcrucible.core.strategy.base import Bars


def make_bars(
    n: int,
    seed: int = 0,
    symbol: str = "TEST/USDT",
    start: str = "2020-01-01",
    drift: float = 0.0,
    vol: float = 0.02,
) -> Bars:
    """A daily geometric random walk with consistent OHLC."""
    rng = np.random.default_rng(seed)
    close = 100.0 * np.exp(np.cumsum(rng.normal(drift, vol, n)))
    open_ = np.concatenate(([100.0], close[:-1])) * np.exp(rng.normal(0, vol / 4, n))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, vol / 2, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, vol / 2, n)))
    volume = rng.uniform(1_000, 5_000, n)
    ts = np.datetime64(start, "ns") + np.arange(1, n + 1) * np.timedelta64(1, "D")
    return Bars(symbol, "1d", ts, open_, high, low, close, volume)


def make_trending_bars(
    n: int,
    seed: int = 0,
    symbol: str = "TEST/USDT",
    start: str = "2018-01-01",
    regime: int = 150,
    drift: float = 0.004,
    vol: float = 0.02,
) -> Bars:
    """Persistent up/down regimes (random lengths around ``regime`` bars, up-biased): a market
    in which trend-following parameters stay good out of sample — for tests that need a
    hand-written strategy to clear the statistical gates (P1-12)."""
    rng = np.random.default_rng(seed)
    sign: list[float] = []
    s = 1.0
    while len(sign) < n:
        sign += [s] * int(rng.integers(regime // 2, regime * 2))
        s = -s if rng.random() < 0.8 else s
    mu = np.asarray(sign[:n]) * drift + drift * 0.3
    close = 100.0 * np.exp(np.cumsum(rng.normal(mu, vol)))
    open_ = np.concatenate(([100.0], close[:-1]))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, vol / 3, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, vol / 3, n)))
    ts = np.datetime64(start, "ns") + np.arange(1, n + 1) * np.timedelta64(1, "D")
    return Bars(symbol, "1d", ts, open_, high, low, close, rng.uniform(1_000, 5_000, n))


def second_vendor(bars: Bars, seed: int = 99, noise: float = 0.001) -> Bars:
    """The same market as printed by another exchange: prices off by a small random spread."""
    rng = np.random.default_rng(seed)
    k = 1 + rng.normal(0, noise, len(bars))
    high = np.maximum(bars.high * k, np.maximum(bars.open, bars.close) * k)
    low = np.minimum(bars.low * k, np.minimum(bars.open, bars.close) * k)
    return Bars(bars.symbol, bars.timeframe, bars.ts, bars.open * k, high, low, bars.close * k,
                bars.volume * 0.7)  # fmt: skip


def bars_from_close(close: list[float] | np.ndarray, symbol: str = "TEST/USDT") -> Bars:
    """Bars whose open/high/low equal the close — for hand-computed expectations."""
    c = np.asarray(close, dtype=np.float64)
    ts = np.datetime64("2020-01-01", "ns") + np.arange(1, len(c) + 1) * np.timedelta64(1, "D")
    return Bars(symbol, "1d", ts, c.copy(), c.copy(), c.copy(), c.copy(), np.ones(len(c)))
