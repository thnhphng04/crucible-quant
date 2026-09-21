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


def bars_from_close(close: list[float] | np.ndarray, symbol: str = "TEST/USDT") -> Bars:
    """Bars whose open/high/low equal the close — for hand-computed expectations."""
    c = np.asarray(close, dtype=np.float64)
    ts = np.datetime64("2020-01-01", "ns") + np.arange(1, len(c) + 1) * np.timedelta64(1, "D")
    return Bars(symbol, "1d", ts, c.copy(), c.copy(), c.copy(), c.copy(), np.ones(len(c)))
