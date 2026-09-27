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
from quantcrucible.core.sizing.position_sizer import InstrumentSpec, round_to_lot
from quantcrucible.core.strategy.base import (
    Bars,
    ScopeDirection,
    Signal,
    Strategy,
    generate_signals,
)
from quantcrucible.data.source import timeframe_delta
from quantcrucible.execution.exit_policy import (
    Bracket,
    ExitPolicy,
    bracket_at_signal,
    expires,
    resolve_ohlc,
)
from quantcrucible.execution.joint_account import SlotPlan, replay_signals
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
class ExitRecord:
    bar: int
    symbol: str
    side: str
    price: float
    reason: str


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
    exits: tuple[ExitRecord, ...] = ()
    ambiguous_bars: int = 0

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
    exit_policy: ExitPolicy | None = None,
) -> BacktestResult:
    policy = exit_policy or ExitPolicy()
    if policy.mode == "bracket_timeout_v1":
        return _run_bracket(
            strategy,
            bars_by_symbol,
            costs or CostModel(),
            initial_cash,
            lookback,
            risk or RiskSettings(),
            sizer,
            perp,
            leverage,
            policy,
        )
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


def _run_bracket(
    strategy: Strategy,
    bars_by_symbol: Mapping[str, Bars],
    costs: CostModel,
    initial_cash: float,
    lookback: int,
    risk: RiskSettings,
    sizer: TargetSizer | None,
    perp: PerpInputs | None,
    leverage: int,
    policy: ExitPolicy,
) -> BacktestResult:
    if len(bars_by_symbol) != 1:
        raise ValueError("a bracket strategy backtest needs exactly one scoped instrument")
    symbol, bars = next(iter(bars_by_symbol.items()))
    signals = tuple(generate_signals(strategy, bars, lookback))
    for signal in signals:
        policy.validate_signal(signal)
    directions = {s.direction for s in signals if s.direction != "flat"}
    if len(directions) > 1:
        raise ValueError("a bracket strategy must emit one direction or flat")
    side: ScopeDirection = "short" if "short" in directions else "long"
    counts = {name: sum(s.direction == name for s in signals) for name in ("long", "short", "flat")}
    if perp is None:
        return _run_spot_bracket(
            symbol, bars, signals, counts, costs, initial_cash, lookback, risk, sizer, policy
        )
    if sizer is not None:
        raise ValueError("perpetual bracket replay sizes from the shared account")
    assert_aligned(perp, bars_by_symbol)
    replay = replay_signals(
        [SlotPlan(symbol, side, signals)],
        bars_by_symbol,
        perp,
        initial_cash,
        risk,
        costs,
        leverage=leverage,
        exit_policy=policy,
    )
    fills: list[FillRecord] = []
    stops: list[StopPlacement] = []
    exits: list[ExitRecord] = []
    holding: list[int] = []
    for opened in replay.opened:
        ts = int(bars.ts[opened.bar].astype("datetime64[ns]").astype("int64"))
        fills.append(
            FillRecord(
                ts,
                symbol,
                "BUY" if side == "long" else "SELL",
                opened.qty,
                opened.price,
                opened.qty * opened.price * float(costs.taker_rate),
                "LONG" if side == "long" else "SHORT",
            )
        )
        stops.append(StopPlacement(ts, symbol, side, opened.stop, opened.qty))
    for closed, opened in zip(replay.closed, replay.opened, strict=False):
        ts = int(bars.ts[closed.bar].astype("datetime64[ns]").astype("int64"))
        fills.append(
            FillRecord(
                ts,
                symbol,
                "SELL" if side == "long" else "BUY",
                closed.qty,
                closed.price,
                (
                    0.0
                    if closed.reason == "liquidation"
                    else closed.qty * closed.price * float(costs.taker_rate)
                ),
                "LONG" if side == "long" else "SHORT",
                closed.reason == "stop",
            )
        )
        exits.append(ExitRecord(closed.bar, symbol, side, closed.price, closed.reason))
        holding.append(closed.bar - opened.bar + (0 if closed.reason == "timeout" else 1))
    ppy = _periods_per_year(bars.timeframe)
    liquidated_at = next((e.bar for e in replay.closed if e.reason == "liquidation"), None)
    return BacktestResult(
        ts=replay.ts,
        equity=replay.equity,
        returns=replay.returns,
        fills=tuple(sorted(fills, key=lambda f: f.ts)),
        n_trades=len(replay.closed),
        avg_holding_bars=float(np.mean(holding)) if holding else 0.0,
        turnover=_bracket_turnover(fills, replay.equity, ppy),
        signals=counts,
        denied_orders=len(replay.denied),
        periods_per_year=ppy,
        stops_placed=tuple(stops),
        funding_paid=sum(replay.funding_paid.values()),
        liquidated=tuple(dict.fromkeys(s for s, _ in replay.liquidations)),
        terminated_at=bars.ts[liquidated_at] if liquidated_at is not None else None,
        exits=tuple(exits),
        ambiguous_bars=replay.ambiguous_bars,
    )


def _run_spot_bracket(
    symbol: str,
    bars: Bars,
    signals: tuple[Signal, ...],
    counts: dict[str, int],
    costs: CostModel,
    initial_cash: float,
    lookback: int,
    risk: RiskSettings,
    sizer: TargetSizer | None,
    policy: ExitPolicy,
) -> BacktestResult:
    if policy.max_holding_bars is None:
        raise ValueError("bracket policy needs max_holding_bars")
    fee = float(costs.taker_rate)
    risk_sizer = sizer or RiskSizer([symbol], risk, {symbol: lot_step(symbol)})
    cash, qty = float(initial_cash), 0.0
    equity = np.empty(len(bars), dtype=np.float64)
    fills: list[FillRecord] = []
    stops: list[StopPlacement] = []
    exits: list[ExitRecord] = []
    holding: list[int] = []
    denied = 0
    ambiguous_bars = 0
    active: Bracket | None = None
    opened_at = -1
    pending_entry: tuple[Signal, Bracket, float] | None = None
    pending_timeout = False
    for i in range(len(bars)):
        bar_open = float(bars.open[i])
        ts = int(bars.ts[i].astype("datetime64[ns]").astype("int64"))
        if pending_timeout and qty > 0:
            proceeds = qty * bar_open
            cash += proceeds * (1.0 - fee)
            fills.append(FillRecord(ts, symbol, "SELL", qty, bar_open, proceeds * fee))
            exits.append(ExitRecord(i, symbol, "long", bar_open, "timeout"))
            holding.append(i - opened_at)
            qty, active = 0.0, None
        pending_timeout = False
        if pending_entry is not None and qty == 0:
            sig, proposed, signal_equity = pending_entry
            if proposed.accepts_entry(bar_open):
                adjusted = Signal("long", sig.strength, bar_open - proposed.stop, sig.take_profit)
                target = risk_sizer.target(
                    symbol, adjusted, bars.window(i - 1, lookback), signal_equity
                )
                affordable = round_to_lot(
                    cash / (bar_open * (1.0 + fee)),
                    InstrumentSpec(bar_open, lot_step=lot_step(symbol)),
                )
                order_qty = min(target, affordable)
                if order_qty > 0:
                    qty = order_qty
                    notional = qty * bar_open
                    cash -= notional * (1.0 + fee)
                    active, opened_at = proposed, i
                    fills.append(FillRecord(ts, symbol, "BUY", qty, bar_open, notional * fee))
                    stops.append(StopPlacement(ts, symbol, "long", proposed.stop, qty))
                else:
                    denied += 1
            else:
                denied += 1
        pending_entry = None
        if active is not None and qty > 0:
            reason, ambiguous = resolve_ohlc(
                active, bar_open, float(bars.high[i]), float(bars.low[i])
            )
            ambiguous_bars += ambiguous
            if reason is not None:
                fill = active.stop_fill(bar_open) if reason == "stop" else active.take_profit
                proceeds = qty * fill
                cash += proceeds * (1.0 - fee)
                fills.append(
                    FillRecord(
                        ts, symbol, "SELL", qty, fill, proceeds * fee, from_stop=reason == "stop"
                    )
                )
                exits.append(ExitRecord(i, symbol, "long", fill, reason))
                holding.append(i - opened_at + 1)
                qty, active = 0.0, None
        equity[i] = cash + qty * float(bars.close[i])
        if active is not None and qty > 0:
            if expires(opened_at, i, policy.max_holding_bars) and i + 1 < len(bars):
                pending_timeout = True
        elif signals[i].direction == "long" and i + 1 < len(bars):
            proposed = bracket_at_signal("long", float(bars.close[i]), signals[i])
            pending_entry = (signals[i], proposed, equity[i])
    ppy = _periods_per_year(bars.timeframe)
    return BacktestResult(
        ts=bars.ts,
        equity=equity,
        returns=_ratio_returns(equity),
        fills=tuple(fills),
        n_trades=len(exits),
        avg_holding_bars=float(np.mean(holding)) if holding else 0.0,
        turnover=_bracket_turnover(fills, equity, ppy),
        signals=counts,
        denied_orders=denied,
        periods_per_year=ppy,
        stops_placed=tuple(stops),
        exits=tuple(exits),
        ambiguous_bars=ambiguous_bars,
    )


def _bracket_turnover(
    fills: list[FillRecord], equity: npt.NDArray[np.float64], ppy: float
) -> float:
    if len(equity) == 0 or float(np.mean(equity)) <= 0:
        return 0.0
    years = max(len(equity) / ppy, 1e-9)
    return sum(f.qty * f.price for f in fills) / float(np.mean(equity)) / years


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
