"""Synthetic perpetual inputs for the kernel replay tests (P3-49, P3-50): 4h bars with a trade and
a (noisier) mark minute path per bar, funding settlements inside bars — which cut the paths into
segments — and a tiered leverage table. Synthetic only; nothing here is market data."""

from __future__ import annotations

import numpy as np

from quantcrucible.core.path_summary import segment_bar
from quantcrucible.core.perp_inputs import PerpBundle
from quantcrucible.core.strategy.base import Bars

MINUTES = 240  # a 4h bar
MINUTE_NS = 60_000_000_000
BRACKETS = (
    {"cap": 50_000.0, "max_leverage": 50, "mmr": 0.004, "amount": 0.0},
    {"cap": 250_000.0, "max_leverage": 20, "mmr": 0.005, "amount": 50.0},
    {"cap": 1_000_000.0, "max_leverage": 10, "mmr": 0.01, "amount": 1_300.0},
    {"cap": 5_000_000.0, "max_leverage": 5, "mmr": 0.025, "amount": 16_300.0},
)


def perp_market(
    n: int, seed: int, symbol: str = "BTC/USDT:USDT", vol: float = 0.0015, mark_noise: float = 0.002
) -> tuple[Bars, PerpBundle]:
    """``n`` 4h bars; funding at minute 120 of every other bar (sometimes two settlements at one
    minute), and at minute 0 of every fifth bar."""
    rng = np.random.default_rng(seed)
    minute_px = 20_000.0 * np.exp(np.cumsum(rng.normal(0.0, vol, n * MINUTES + 1)))
    ts = np.datetime64("2022-01-01", "ns") + np.arange(1, n + 1) * np.timedelta64(4, "h")
    open_, high, low, close = (np.empty(n) for _ in range(4))
    m_open, m_high, m_low, m_close = (np.empty(n) for _ in range(4))
    paths, trade_paths, funding = [], [], []
    for b in range(n):
        seg = minute_px[b * MINUTES : (b + 1) * MINUTES + 1]
        lows = np.minimum(seg[:-1], seg[1:]) * (1 - np.abs(rng.normal(0, 0.0004, MINUTES)))
        highs = np.maximum(seg[:-1], seg[1:]) * (1 + np.abs(rng.normal(0, 0.0004, MINUTES)))
        wobble = 1 + rng.normal(0, mark_noise, MINUTES)
        m_lows, m_highs = lows * wobble, highs * wobble
        open_[b], close[b], high[b], low[b] = seg[0], seg[-1], highs.max(), lows.min()
        m_open[b], m_close[b] = seg[0] * wobble[0], seg[-1] * wobble[-1]
        m_high[b], m_low[b] = m_highs.max(), m_lows.min()
        cuts: list[int] = []
        open_ns = int(ts[b].astype("int64")) - MINUTES * MINUTE_NS
        if b % 2 == 1:
            cuts.append(120)
            rate = float(rng.normal(0.0001, 0.0005))
            funding.append([b, open_ns + 120 * MINUTE_NS, rate, float(seg[120])])
            if b % 6 == 1:  # a second settlement at the same minute
                funding.append([b, open_ns + 120 * MINUTE_NS, -rate / 2, float(seg[120])])
        if b % 5 == 0:
            funding.append([b, open_ns, float(rng.normal(0.0001, 0.0005)), float(seg[0])])
        minutes = list(range(MINUTES))
        paths.append(segment_bar(minutes, list(m_lows), list(m_highs), cuts))
        trade_paths.append(segment_bar(minutes, list(lows), list(highs), cuts))
    bars = Bars(symbol, "4h", ts, open_, high, low, close, np.ones(n))
    marks = Bars(symbol, "4h", ts, m_open, m_high, m_low, m_close, np.ones(n))
    bundle = PerpBundle(
        symbol, "4h", marks, np.asarray(funding, dtype=np.float64).reshape(-1, 4),
        tuple(paths), BRACKETS, trade_paths=tuple(trade_paths),
    )  # fmt: skip
    return bars, bundle
