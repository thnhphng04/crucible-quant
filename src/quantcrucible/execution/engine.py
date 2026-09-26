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

from quantcrucible.core.perp_inputs import PerpInputs, assert_aligned
from quantcrucible.core.strategy.base import Bars, Strategy
from quantcrucible.data.source import timeframe_delta
from quantcrucible.execution.margin import BracketTable
from quantcrucible.execution.nautilus_bridge import (
    BridgeLog,
    BridgeStrategy,
    CostModel,
    FillRecord,
    StopPlacement,
    TargetSizer,
    build_engine,
    lot_step,
)
from quantcrucible.execution.perp_account import PerpAccount
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
    # Every protective stop the perpetual path submitted (P3-19). One per position: a second
    # entry for the same excursion would mean the stop moved. Empty on the spot path, where the
    # stop is re-issued every bar by design.
    stops_placed: tuple[StopPlacement, ...] = ()
    # Perpetual path only (P3-19). `funding_paid` is positive when the strategy paid out.
    funding_paid: float = 0.0
    liquidated: tuple[str, ...] = ()
    terminated_at: np.datetime64 | None = None

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
    perp: PerpInputs | None = None,
    leverage: int = 5,
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
    account: PerpAccount | None = None
    if perp is not None:
        assert_aligned(perp, bars_by_symbol)
        account = PerpAccount(
            balance=initial_cash,
            tables={s: BracketTable.from_rows(s, b.brackets) for s, b in perp.items()},
        )
    engine.add_strategy(
        BridgeStrategy(
            strategy, instruments, bar_types, timeframe, sizer, lookback, log,
            fixed_positions=perp is not None, account=account, perp=perp, leverage=leverage,
        )
    )  # fmt: skip
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
    if log.equity:
        # Perpetual path (P3-19): the curve is the host account's, not a cash reconstruction.
        # Funding and liquidation never touch the venue's cash, so the two differ — and the one
        # gate ③ measures Sharpe on has to be the one the sizer used.
        equity = _account_curve(log.equity, ts_ns, float(initial_cash))
    returns = _ratio_returns(equity)
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
        stops_placed=tuple(log.stops),
        funding_paid=log.funding_paid,
        liquidated=tuple(dict.fromkeys(log.liquidated)),
        terminated_at=(
            np.datetime64(log.terminated_at, "ns") if log.terminated_at is not None else None
        ),
    )


def _account_curve(
    samples: list[tuple[int, float]], ts_ns: npt.NDArray[np.int64], initial_cash: float
) -> npt.NDArray[np.float64]:
    """Lay the account's per-bar equity onto the result's timestamp axis.

    Several symbols can report on one timestamp — bars arrive one at a time and the account is
    shared — so the **last** sample for a timestamp is the settled one. A bar with no sample
    carries the previous value forward, and the curve starts at the account's opening balance.
    """
    curve = np.full(len(ts_ns), np.nan)
    for at, value in samples:
        k = int(np.searchsorted(ts_ns, at, side="left"))
        if 0 <= k < len(curve):
            curve[k] = value
    if np.isnan(curve[0]):
        curve[0] = initial_cash
    return _ffill(curve)


def _ratio_returns(equity: npt.NDArray[np.float64]) -> npt.NDArray[np.float64]:
    """Bar returns, with a wiped account reported as no further change.

    Isolated margin floors equity at zero, so the plain ratio divides by it and sends inf and nan
    into the Sharpe and into gate ④'s PBO matrix. A dead account did not lose an infinite amount;
    it stopped moving.
    """
    if len(equity) < 2:
        return np.zeros(0)
    previous = equity[:-1]
    out = np.zeros(len(equity) - 1)
    alive = previous > 0
    out[alive] = equity[1:][alive] / previous[alive] - 1.0
    return out


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
