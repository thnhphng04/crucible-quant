"""One shared spot cash account across ``(instrument, long)`` slots (Architecture §3.2.1, §3.4,
ADR-0040).

A spot bracket portfolio used to be a weighted sum of standalone accounts: every member traded
its own 100,000 of cash, nothing capped their summed risk at the stop, and w ∝ 1/σ re-introduced
the volatility leg ADR-0031 retired. This walks every member's signals through **one** account
instead, the way :func:`~quantcrucible.execution.joint_account.replay_signals` does for
perpetuals — without margin, funding or liquidation, which spot does not have.

**One member reproduces its own gate-③ backtest bit for bit** (``_run_spot_bracket``, INV-112),
so the portfolio adds only what sharing an account adds. Per step of the axis, in this order:

1. timeouts decided at a previous close sell at this bar's open;
2. pending entries fill at this bar's open, in canonical order (instrument ascending). Each is
   sized ``Q = R/d`` from the equity snapshot of its signal bar, admitted only while the summed
   commitment at the stop stays inside the portfolio cap (:func:`admit_batch`), then floored to
   what the shared cash can still buy;
3. stops and take-profits resolve on each held bar's OHLC, the worse touch first;
4. one equity snapshot at the close — cash plus every holding at its last close;
5. timeouts and new entries are scheduled for the next bar of each slot's own instrument.

**The axis is the union of the instruments' timestamps.** Spot symbols list on different days
(SOL two and a half years after BTC); an intersection would cut every member's history to the
youngest one. A slot simply waits until its instrument has a bar, and a held instrument with no
bar at a step is marked at its last close.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np
import numpy.typing as npt

from quantcrucible.core.sizing.position_sizer import InstrumentSpec, round_to_lot
from quantcrucible.core.strategy.base import Bars
from quantcrucible.execution.admission import EntryRequest, admit_batch
from quantcrucible.execution.exit_policy import (
    Bracket,
    ExitPolicy,
    bracket_at_signal,
    expires,
    resolve_ohlc,
)
from quantcrucible.execution.joint_account import Closed, Opened, SignalReplay, Slot, SlotPlan
from quantcrucible.execution.nautilus_bridge import CostModel, lot_step
from quantcrucible.execution.risk import RiskSettings


def union_axis(bars: Mapping[str, Bars]) -> npt.NDArray[np.datetime64]:
    """Every timestamp any instrument has, ascending."""
    if not bars:
        raise ValueError("a replay needs at least one instrument")
    axis = np.empty(0, dtype="datetime64[ns]")
    for b in bars.values():
        axis = np.union1d(axis, b.ts.astype("datetime64[ns]"))
    return axis


def replay_spot_signals(
    plans: Sequence[SlotPlan],
    bars: Mapping[str, Bars],
    initial_cash: float,
    settings: RiskSettings,
    costs: CostModel,
    exit_policy: ExitPolicy,
    max_portfolio_risk_pct: float = 0.10,
    lot_steps: Mapping[str, float] | None = None,
) -> SignalReplay:
    """Walk one shared spot account through every long slot's signals."""
    if exit_policy.mode != "bracket_timeout_v1" or exit_policy.max_holding_bars is None:
        raise ValueError("the spot account replays bracket_timeout_v1 campaigns only")
    by_slot: dict[Slot, SlotPlan] = {}
    for p in plans:
        if p.direction != "long":
            raise ValueError(f"{p.instrument}: spot is long only, got a {p.direction} slot")
        if p.slot in by_slot:
            raise ValueError(f"duplicate slot {p.slot}: one strategy per slot")
        if p.instrument not in bars or len(p.signals) != len(bars[p.instrument]):
            raise ValueError(f"{p.instrument}: signal stream length does not match its bars")
        by_slot[p.slot] = p
    max_hold = int(exit_policy.max_holding_bars)
    fee = float(costs.taker_rate)
    steps = {s: lot_step(s) if lot_steps is None else lot_steps.get(s, 0.0) for s in bars}

    axis = union_axis(bars)
    n = len(axis)
    local: dict[str, npt.NDArray[np.int64]] = {}  # axis step -> the instrument's bar, or -1
    for symbol, b in bars.items():
        at = np.full(n, -1, dtype=np.int64)
        at[np.searchsorted(axis, b.ts.astype("datetime64[ns]"))] = np.arange(len(b))
        local[symbol] = at

    result = SignalReplay(ts=axis, equity=np.empty(n, dtype=np.float64))
    slots = sorted(by_slot)
    curves = {slot: np.zeros(n, dtype=np.float64) for slot in slots}
    booked = dict.fromkeys(slots, 0.0)
    cash = float(initial_cash)
    qty = dict.fromkeys(slots, 0.0)
    active: dict[Slot, Bracket] = {}
    opened_at: dict[Slot, int] = {}  # the instrument's own bar index
    distance: dict[Slot, float] = {}  # d at entry, frozen: the commitment is qty · d
    pending_entry: dict[Slot, tuple[Bracket, float, int]] = {}  # + signal equity, its axis step
    pending_timeout: set[Slot] = set()
    last_close: dict[str, float] = {}

    for k in range(n):
        ts = int(axis[k].astype("int64"))
        here = {s: int(local[s][k]) for s in bars if local[s][k] >= 0}

        # 1. timeouts sell at this open
        for slot in slots:
            if slot not in pending_timeout or slot[0] not in here:
                continue
            pending_timeout.discard(slot)
            if qty[slot] > 0:
                bar_open = float(bars[slot[0]].open[here[slot[0]]])
                proceeds = qty[slot] * bar_open
                cash += proceeds * (1.0 - fee)
                booked[slot] += proceeds * (1.0 - fee)
                result.closed.append(Closed(k, slot[0], "long", qty[slot], bar_open, "timeout"))
                qty[slot] = 0.0
                active.pop(slot, None)
                distance.pop(slot, None)

        # 2. entries fill at this open, oldest snapshot first, canonical order within it
        due = [s for s in slots if s in pending_entry and s[0] in here]
        for slot in sorted(due, key=lambda s: (pending_entry[s][2], s)):
            proposed, signal_equity, _ = pending_entry.pop(slot)
            if qty[slot] != 0:
                continue
            bar_open = float(bars[slot[0]].open[here[slot[0]]])
            if not proposed.accepts_entry(bar_open):
                result.denied.append((slot, "bracket_entry"))
                continue
            d = bar_open - proposed.stop
            open_risk = sum(qty[s] * distance[s] for s in distance)
            (decision,) = admit_batch(
                [EntryRequest(slot[0], "long", bar_open, d)],
                signal_equity,
                settings.max_risk_pct,
                max_portfolio_risk_pct,
                open_risk,
            )
            if not decision.admitted:
                result.denied.append((slot, decision.reason))
                continue
            spec = InstrumentSpec(bar_open, lot_step=steps[slot[0]])
            target = round_to_lot(decision.quantity, spec)
            affordable = round_to_lot(cash / (bar_open * (1.0 + fee)), spec)
            order_qty = min(target, affordable)
            if order_qty <= 0:
                result.denied.append((slot, "cash" if target > 0 else "lot"))
                continue
            qty[slot] = order_qty
            notional = order_qty * bar_open
            cash -= notional * (1.0 + fee)
            booked[slot] -= notional * (1.0 + fee)
            active[slot], opened_at[slot], distance[slot] = proposed, here[slot[0]], d
            result.opened.append(Opened(k, ts, slot[0], "long", order_qty, bar_open, proposed.stop))

        # 3. stops and take-profits on each held bar's OHLC
        for slot in slots:
            if slot not in active or qty[slot] <= 0 or slot[0] not in here:
                continue
            b, i = bars[slot[0]], here[slot[0]]
            bracket = active[slot]
            reason, ambiguous = resolve_ohlc(
                bracket, float(b.open[i]), float(b.high[i]), float(b.low[i])
            )
            result.ambiguous_bars += ambiguous
            if reason is None:
                continue
            fill = bracket.stop_fill(float(b.open[i])) if reason == "stop" else bracket.take_profit
            proceeds = qty[slot] * fill
            cash += proceeds * (1.0 - fee)
            booked[slot] += proceeds * (1.0 - fee)
            result.closed.append(Closed(k, slot[0], "long", qty[slot], fill, reason))
            qty[slot] = 0.0
            active.pop(slot)
            distance.pop(slot, None)

        # 4. one equity snapshot at the close
        for symbol, i in here.items():
            last_close[symbol] = float(bars[symbol].close[i])
        equity = cash
        for slot in slots:
            if qty[slot] > 0:
                equity += qty[slot] * last_close[slot[0]]
        result.equity[k] = equity
        for slot in slots:
            held = qty[slot] * last_close[slot[0]] if qty[slot] > 0 else 0.0
            curves[slot][k] = booked[slot] + held

        # 5. schedule the next bar of each slot's own instrument
        for slot in slots:
            if slot[0] not in here:
                continue
            i, length = here[slot[0]], len(bars[slot[0]])
            if slot in active and qty[slot] > 0:
                if expires(opened_at[slot], i, max_hold) and i + 1 < length:
                    pending_timeout.add(slot)
                continue
            wanted = by_slot[slot].wants(i)
            if wanted is not None and i + 1 < length:
                exit_policy.validate_signal(wanted)
                proposed = bracket_at_signal("long", float(bars[slot[0]].close[i]), wanted)
                pending_entry[slot] = (proposed, equity, k)

    result.contributions = curves
    result.funding_paid = dict.fromkeys(slots, 0.0)
    return result
