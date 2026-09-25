"""Backtest runner — NautilusTrader underneath, the same path live trading will use (§3.5, P5).

``run_backtest`` returns the equity curve (marked to each bar's close), per-bar returns, fills and
trade statistics. Sizing is the Risk layer's :class:`~quantcrucible.execution.risk.RiskSizer`
(§3.4, ADR-0010) unless a caller passes its own ``TargetSizer``.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from quantcrucible.core.strategy.base import Bars, Strategy
from quantcrucible.data.source import timeframe_delta
from quantcrucible.execution.nautilus_bridge import (
    BridgeLog,
    BridgeStrategy,
    CostModel,
    FillRecord,
    TargetSizer,
    build_engine,
    lot_step,
)
from quantcrucible.execution.risk import RiskSettings, RiskSizer

_FLAT = 1e-12  # a |position| at or below this is flat


class BacktestAbortedError(RuntimeError):
    """The engine stopped before the last bar (e.g. the cash balance went negative)."""


@dataclass(frozen=True, slots=True)
class BacktestResult:
    ts: npt.NDArray[np.datetime64]  # union of bar close times
    equity: npt.NDArray[np.float64]
    returns: npt.NDArray[np.float64]  # per bar, len(ts) - 1
    fills: tuple[FillRecord, ...]
    n_trades: int  # round trips from flat, counted from the fills
    avg_holding_bars: float
    turnover: float  # traded notional / mean equity, annualized
    signals: Mapping[str, int]
    denied_orders: int
    periods_per_year: float

    @property
    def sharpe(self) -> float:
        r = self.returns
        if len(r) < 2 or float(np.std(r)) == 0.0:
            return 0.0
        return float(np.mean(r) / np.std(r, ddof=1) * math.sqrt(self.periods_per_year))

    @property
    def sortino(self) -> float:
        """Annualized mean over downside deviation (target 0); 0 without any losing bar."""
        r = self.returns
        downside = float(np.sqrt(np.mean(np.minimum(r, 0.0) ** 2))) if len(r) else 0.0
        if len(r) < 2 or downside == 0.0:
            return 0.0
        return float(np.mean(r) / downside * math.sqrt(self.periods_per_year))

    @property
    def max_drawdown(self) -> float:
        peak = np.maximum.accumulate(self.equity)
        return float(np.max(1.0 - self.equity / peak)) if len(self.equity) else 0.0

    @property
    def total_return(self) -> float:
        return float(self.equity[-1] / self.equity[0] - 1.0) if len(self.equity) else 0.0

    def public_metrics(self) -> dict[str, float]:
        return {
            "sharpe_is": self.sharpe,
            "sortino_is": self.sortino,
            "max_drawdown": self.max_drawdown,
            "total_return": self.total_return,
            "n_trades": float(self.n_trades),
            "avg_holding_bars": self.avg_holding_bars,
            "turnover": self.turnover,
        }


def _periods_per_year(timeframe: str) -> float:
    return 365.0 * 86_400 / timeframe_delta(timeframe).total_seconds()  # crypto trades 24/7


def run_backtest(
    strategy: Strategy,
    bars_by_symbol: Mapping[str, Bars],
    exchange: str = "binance",
    costs: CostModel | None = None,
    initial_cash: float = 100_000.0,
    lookback: int = 400,
    seed: int = 0,
    risk: RiskSettings | None = None,
    sizer: TargetSizer | None = None,
) -> BacktestResult:
    if not bars_by_symbol:
        raise ValueError("no data")
    costs = costs or CostModel()
    timeframes = {b.timeframe for b in bars_by_symbol.values()}
    if len(timeframes) != 1:
        raise ValueError(f"mixed timeframes {timeframes}")
    (timeframe,) = timeframes
    engine, instruments, bar_types = build_engine(
        bars_by_symbol, exchange, costs, initial_cash, seed
    )
    log = BridgeLog()
    ppy = _periods_per_year(timeframe)
    if sizer is None:
        steps = {s: lot_step(s) for s in bars_by_symbol}
        sizer = RiskSizer(list(bars_by_symbol), risk or RiskSettings(), steps)
    engine.add_strategy(
        BridgeStrategy(strategy, instruments, bar_types, timeframe, sizer, lookback, log)
    )
    try:
        engine.run()
    finally:
        engine.dispose()
    assert_complete(log.bars_seen, sum(len(b) for b in bars_by_symbol.values()))
    return _summarize(bars_by_symbol, log, initial_cash, ppy)


def assert_complete(bars_seen: int, expected: int) -> None:
    """Nautilus stops quietly on an engine-level problem, and a truncated run must never pass as
    a whole backtest: its metrics would describe a shorter history than the one asked for.

    Until P3-06 the usual cause was a cash account going negative. The USDT-M margin venue does
    not run out that way, so no test drives this through the account any more — the guard stays
    because it catches *any* early stop, and is tested directly.
    """
    if bars_seen != expected:
        raise BacktestAbortedError(f"engine stopped after {bars_seen}/{expected} bars")


def _summarize(
    bars_by_symbol: Mapping[str, Bars], log: BridgeLog, initial_cash: float, ppy: float
) -> BacktestResult:
    ts = np.unique(np.concatenate([b.ts.astype("datetime64[ns]") for b in bars_by_symbol.values()]))
    ts_ns = ts.astype(np.int64)
    fills = sorted(log.fills, key=lambda f: f.ts)
    cash = np.full(len(ts), float(initial_cash))
    equity = np.zeros(len(ts))
    traded = 0.0
    n_trades = 0
    holding: list[int] = []
    for symbol, bars in bars_by_symbol.items():
        closes = np.full(len(ts), np.nan)
        closes[np.searchsorted(ts_ns, bars.ts.astype("datetime64[ns]").astype(np.int64))] = (
            bars.close
        )
        closes = _ffill(closes)
        position = np.zeros(len(ts))
        sym_fills = [f for f in fills if f.symbol == symbol]
        for f in sym_fills:
            k = int(np.searchsorted(ts_ns, f.ts, side="left"))  # first close at/after the fill
            signed = f.qty if f.side == "BUY" else -f.qty
            position[k:] += signed
            cash[k:] -= signed * f.price + f.commission
            traded += f.qty * f.price
        equity += position * np.nan_to_num(closes)
        trades = _round_trips(sym_fills, ts_ns)
        n_trades += len(trades)
        holding += trades
    equity += cash
    returns = equity[1:] / equity[:-1] - 1.0 if len(equity) > 1 else np.zeros(0)
    years = max(len(ts) / ppy, 1e-9)
    turnover = traded / float(np.mean(equity)) / years if len(equity) else 0.0
    return BacktestResult(
        ts=ts,
        equity=equity,
        returns=returns,
        fills=tuple(fills),
        n_trades=n_trades,
        avg_holding_bars=float(np.mean(holding)) if holding else 0.0,
        turnover=turnover,
        signals=dict(log.signals),
        denied_orders=log.denied_orders,
        periods_per_year=ppy,
    )


def _round_trips(fills: list[FillRecord], ts_ns: npt.NDArray[np.int64]) -> list[int]:
    """Bars held by each trade, walking the fills in time order.

    A trade is any excursion away from flat and back, on **either** side (P3-06). Counting only
    ``flat → long → flat`` reported zero trades for every short strategy, which made gate ③
    reject it on ``min_trades`` for a reason that had nothing to do with the strategy.

    Taken from the fills, not from positions at bar closes: an entry stopped out inside the
    same bar is a trade too, held 0 bars. A fill belongs to the first bar closing at or after
    it; a trade still open at the end is held until the last bar. A flip straight from long to
    short closes one trade and opens another at the same bar.
    """
    held: list[int] = []
    position, entry = 0.0, -1
    for f in fills:
        k = int(np.searchsorted(ts_ns, f.ts, side="left"))
        before = position
        position += f.qty if f.side == "BUY" else -f.qty
        was_flat = abs(before) <= _FLAT
        is_flat = abs(position) <= _FLAT
        if was_flat and not is_flat:
            entry = k
        elif not was_flat and is_flat:
            held.append(k - entry)
        elif not was_flat and not is_flat and before * position < 0:  # flipped side
            held.append(k - entry)
            entry = k
    if abs(position) > _FLAT:
        held.append(len(ts_ns) - entry)
    return held


def _ffill(x: npt.NDArray[np.float64]) -> npt.NDArray[np.float64]:
    idx = np.where(np.isnan(x), 0, np.arange(len(x)))
    np.maximum.accumulate(idx, out=idx)
    out = x[idx]
    return out
