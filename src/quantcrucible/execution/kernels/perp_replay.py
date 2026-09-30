"""Perpetual replay kernel K4: one USDT-M slot, ``bracket_timeout_v1`` (P3-50, ADR-0038).

One thread per configuration walks the bars, reproducing ``joint_account.replay_signals`` for a
single :class:`SlotPlan` in bracket mode operation by operation, in float64 — the order of every
sum, the tie rules of ``min``/``max``/``sorted``, ``nextafter`` for the take-profit trade-through.
Per bar:

0. a timeout decided at the previous close fills at this open (fee, then close);
1. with a position open, segment by segment: the settlements at the segment's start move the
   isolated margin, the liquidation price is recomputed from the tier table, and the earliest of
   liquidation (mark path), stop and take-profit (trade path) decides — liquidation, then stop,
   then take-profit on the same minute, which also counts as ambiguous; without an exit the
   timeout is scheduled;
2. equity: free balance plus the wallet's margin and its PnL bounded below by the margin;
3. with no position and a signal: the bracket anchored at this close must accept the next open;
   ``Q = R/d`` from this equity; the lowest leverage the free balance funds whose liquidation
   clears the stop by a quarter of the stop distance; margin, then the fee, leave the balance.

The replay runs on the CPU only: it is one sequential walk per configuration, which the device
does poorly (E2), and the engine runs every replay on the host. The same arithmetic in njit and
in Python rounds alike (no fastmath, no contraction). The replay raises where the bundle is
inconsistent at a bar with a position open (``PerpArrays.bad``); the kernel flags that
configuration instead and the host raises for it.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from typing import Any

import numpy as np
import numpy.typing as npt

from quantcrucible.core.perp_arrays import PathArrays, PerpArrays
from quantcrucible.execution.kernels._numba import cpu_kernel, device, prange
from quantcrucible.execution.kernels.features import KernelInputError

# trade log columns
L_OPEN_BAR, L_CLOSE_BAR, L_REASON, L_LEVERAGE = 0, 1, 2, 3  # int log
L_QTY, L_ENTRY_PX, L_EXIT_PX, L_STOP = 0, 1, 2, 3  # float log
R_LIQ, R_STOP, R_TP, R_TIMEOUT = 0, 1, 2, 3
REASONS = {R_LIQ: "liquidation", R_STOP: "stop", R_TP: "take_profit", R_TIMEOUT: "timeout"}
# per-configuration counters
S_TRADES, S_DENIED, S_AMBIGUOUS, S_HOLD_SUM, S_LOGGED, S_OVERFLOW = 0, 1, 2, 3, 4, 5
S_LIQUIDATIONS, S_FIRST_LIQ, S_ERROR = 6, 7, 8
N_STATS = 9
# E4 (ADR-0038): both searches return the same index; bisection, the oracle's own, was ~8%
# faster on 16-point segments (200 configurations × 3k 4h bars: 5.1 ms against 5.5 ms).
LINEAR_SEARCH = False


@cache
def _build() -> Any:
    dev = device("cpu")
    INF = np.inf

    @dev
    def touch(ptr: Any, ix: Any, px: Any, s: int, level: float, above: bool) -> Any:
        """``PathSummary.first_touch``: bisect_left over the running highs, or over the negated
        running lows; -1 for none. The linear scan finds the same index on these monotone rows."""
        a = ptr[s]
        b = ptr[s + 1]
        if LINEAR_SEARCH:
            for k in range(a, b):
                if not ((px[k] < level) if above else (-px[k] < -level)):
                    return ix[k]
            return -1
        lo = a
        hi = b
        while lo < hi:
            mid = (lo + hi) // 2
            if (px[mid] < level) if above else (-px[mid] < -level):
                lo = mid + 1
            else:
                hi = mid
        return -1 if lo >= b else ix[lo]

    @dev
    def finite_pos(v: float) -> bool:
        return v == v and abs(v) != INF and v > 0

    @dev
    def tier(cap: Any, notional: float) -> int:
        """``BracketTable.bracket_for``: the first tier whose cap covers the notional, or -1."""
        for k in range(cap.size):
            if notional <= cap[k]:
                return k
        return -1

    @dev
    def liq_price(side: int, qty: float, entry: float, wallet: float, mmr: float,
                  amount: float) -> float:  # fmt: skip
        notional = entry * qty
        if side == 1:
            return (notional - wallet - amount) / (qty * (1 - mmr))
        return (notional + wallet + amount) / (qty * (1 + mmr))

    @dev
    def bounded(side: int, qty: float, entry: float, margin: float, price: float) -> float:
        move = price - entry
        u = move * qty if side == 1 else -move * qty
        lo = -margin
        return lo if lo > u else u  # Python max(u, -margin): the first unless the second is greater

    def body(
        c: int, opn: Any, close: Any, mclose: Any,
        m_bar: Any, m_start: Any, m_lptr: Any, m_lix: Any, m_lpx: Any, m_hptr: Any, m_hix: Any,
        m_hpx: Any, t_bar: Any, t_lptr: Any, t_lix: Any, t_lpx: Any, t_hptr: Any, t_hix: Any,
        t_hpx: Any, f_ptr: Any, f_min: Any, f_rate: Any, f_mark: Any, bad: Any,
        cap: Any, maxlev: Any, mmr: Any, amount: Any,
        entry: Any, stopd: Any, side: int, ratio: float, fee: float, pct: float,
        cap_pct: float, clear: float, cash0: float, leverage: int, maxh: int,
        equity: Any, stats: Any, funding: Any, ilog: Any, flog: Any,
    ) -> None:  # fmt: skip
        n = close.size
        logcap = ilog.shape[1]
        balance = cash0
        has = False
        qty = 0.0
        w_entry = 0.0
        margin = 0.0
        w_tier = 0
        stop = 0.0
        tp = 0.0
        entry_bar = 0
        pending = False
        paid = 0.0
        trades = 0
        denied = 0
        ambiguous = 0
        hold_sum = 0
        logged = 0
        overflow = 0
        liqs = 0
        first_liq = -1
        above_stop = side != 1
        for bar in range(n):
            # 0. a timeout decided at the previous close fills at this open
            if pending:
                if has:
                    fill = opn[bar]
                    balance += -abs(qty * fill) * fee
                    balance += margin + bounded(side, qty, w_entry, margin, fill)
                    has = False
                    trades += 1
                    hold_sum += bar - entry_bar
                    if logged - 1 < logcap:
                        ilog[c, logged - 1, L_CLOSE_BAR] = bar
                        ilog[c, logged - 1, L_REASON] = R_TIMEOUT
                        flog[c, logged - 1, L_EXIT_PX] = fill
                pending = False
            # 1. funding, liquidation, stop and take-profit, segment by segment
            if has:
                if bad[bar]:
                    stats[c, S_ERROR] = 1
                    return
                event = -1
                level = 0.0
                for s in range(m_bar[bar], m_bar[bar + 1]):
                    start = m_start[s]
                    ts_ = t_bar[bar] + (s - m_bar[bar])  # the same segment of the trade path
                    for f in range(f_ptr[bar], f_ptr[bar + 1]):
                        if f_min[f] == start:
                            payment = qty * f_mark[f] * f_rate[f]
                            cost = payment if side == 1 else -payment
                            margin -= cost
                            paid += cost
                    level = liq_price(side, qty, w_entry, margin, mmr[w_tier], amount[w_tier])
                    liq_m = touch(m_lptr, m_lix, m_lpx, s, level, False) if not above_stop else \
                        touch(m_hptr, m_hix, m_hpx, s, level, True)  # fmt: skip
                    if above_stop:
                        stop_m = touch(t_hptr, t_hix, t_hpx, ts_, stop, True)
                        through = np.nextafter(tp, -INF)
                        tp_m = touch(t_lptr, t_lix, t_lpx, ts_, through, False)
                    else:
                        stop_m = touch(t_lptr, t_lix, t_lpx, ts_, stop, False)
                        through = np.nextafter(tp, INF)
                        tp_m = touch(t_hptr, t_hix, t_hpx, ts_, through, True)
                    # sorted((minute, priority)): liquidation 0, stop 1, take-profit 2
                    best_m = -1
                    best = -1
                    second_m = -1
                    for prio in range(3):
                        mm = liq_m if prio == 0 else (stop_m if prio == 1 else tp_m)
                        if mm < 0:
                            continue
                        if best < 0 or mm < best_m:
                            second_m = best_m
                            best_m = mm
                            best = prio
                        elif second_m < 0 or mm < second_m:
                            second_m = mm
                    if best >= 0 and second_m >= 0 and second_m == best_m:
                        ambiguous += 1
                    if best >= 0:
                        event = best
                        break
                if event == 0:  # liquidation: the margin is gone, nothing returns to the balance
                    has = False
                    trades += 1
                    liqs += 1
                    if first_liq < 0:
                        first_liq = bar
                    hold_sum += bar - entry_bar + 1
                    if logged - 1 < logcap:
                        ilog[c, logged - 1, L_CLOSE_BAR] = bar
                        ilog[c, logged - 1, L_REASON] = R_LIQ
                        flog[c, logged - 1, L_EXIT_PX] = level
                elif event == 1 or event == 2:
                    o = opn[bar]
                    fill = tp
                    if event == 1 and side == 1:
                        fill = o if o < stop else stop  # min(stop, open)
                    elif event == 1:
                        fill = o if o > stop else stop  # max(stop, open)
                    balance += -abs(qty * fill) * fee
                    balance += margin + bounded(side, qty, w_entry, margin, fill)
                    has = False
                    trades += 1
                    hold_sum += bar - entry_bar + 1
                    if logged - 1 < logcap:
                        ilog[c, logged - 1, L_CLOSE_BAR] = bar
                        ilog[c, logged - 1, L_REASON] = R_STOP if event == 1 else R_TP
                        flog[c, logged - 1, L_EXIT_PX] = fill
                elif bar - entry_bar + 1 >= maxh:
                    pending = True
            # 2. one equity snapshot
            eq = balance
            if has:
                eq += margin + bounded(side, qty, w_entry, margin, mclose[bar])
            equity[c, bar] = eq
            # 3. admission on that snapshot
            if has or bar + 1 >= n or entry[bar, c] != 1:
                continue
            d = stopd[bar, c]
            anchor = close[bar]
            px = opn[bar + 1]
            if side == 1:
                trig = anchor - d
                target = anchor + ratio * d
                ok = trig < px and px < target
            else:
                trig = anchor + d
                target = anchor - ratio * d
                ok = target < px and px < trig
            if not (finite_pos(px) and finite_pos(trig) and finite_pos(target) and ok):
                denied += 1  # bracket_entry
                continue
            dist = abs(px - trig)
            if not (eq == eq and abs(eq) != INF and eq > 0):
                denied += 1  # no_equity
                continue
            risk = eq * pct
            budget = eq * cap_pct
            if not (dist == dist and abs(dist) != INF and dist > 0):
                denied += 1  # no_stop_distance
                continue
            if 0.0 + risk > budget:
                denied += 1  # portfolio_risk_cap
                continue
            q = risk / dist
            if leverage < 1:
                denied += 1  # leverage_cap
                continue
            notional = abs(q * px)
            fee_amt = notional * fee
            k = tier(cap, notional)
            if k < 0:
                denied += 1  # bracket
                continue
            top = leverage if leverage < maxlev[k] else maxlev[k]
            chosen = 0
            for lev in range(1, top + 1):
                mg = notional / lev
                if mg + fee_amt > balance:
                    continue
                lq = liq_price(side, q, px, mg, mmr[k], amount[k])
                gap = abs(px - trig)
                room = (trig - lq) if side == 1 else (lq - trig)
                if not (room >= clear * gap):
                    continue
                chosen = lev
                break
            if chosen == 0:
                denied += 1  # free_balance or clearance
                continue
            margin = (q * px) / chosen
            if margin > balance:
                denied += 1  # Liquidated: cannot fund
                continue
            balance -= margin
            balance += -fee_amt
            has = True
            qty = q
            w_entry = px
            w_tier = tier(cap, abs(q) * px)
            stop = trig
            tp = target
            entry_bar = bar + 1
            if logged < logcap:
                ilog[c, logged, L_OPEN_BAR] = bar + 1
                ilog[c, logged, L_CLOSE_BAR] = -1
                ilog[c, logged, L_REASON] = -1
                ilog[c, logged, L_LEVERAGE] = chosen
                flog[c, logged, L_QTY] = q
                flog[c, logged, L_ENTRY_PX] = px
                flog[c, logged, L_STOP] = trig
            else:
                overflow = 1
            logged += 1
        stats[c, S_TRADES] = trades
        stats[c, S_DENIED] = denied
        stats[c, S_AMBIGUOUS] = ambiguous
        stats[c, S_HOLD_SUM] = hold_sum
        stats[c, S_LOGGED] = logged
        stats[c, S_OVERFLOW] = overflow
        stats[c, S_LIQUIDATIONS] = liqs
        stats[c, S_FIRST_LIQ] = first_liq
        funding[c] = paid

    one = dev(body)

    def cpu(
        opn: Any, close: Any, mclose: Any,
        m_bar: Any, m_start: Any, m_lptr: Any, m_lix: Any, m_lpx: Any, m_hptr: Any, m_hix: Any,
        m_hpx: Any, t_bar: Any, t_lptr: Any, t_lix: Any, t_lpx: Any, t_hptr: Any, t_hix: Any,
        t_hpx: Any, f_ptr: Any, f_min: Any, f_rate: Any, f_mark: Any, bad: Any,
        cap: Any, maxlev: Any, mmr: Any, amount: Any,
        entry: Any, stopd: Any, side: int, ratio: float, fee: float, pct: float,
        cap_pct: float, clear: float, cash0: float, leverage: int, maxh: int,
        equity: Any, stats: Any, funding: Any, ilog: Any, flog: Any,
    ) -> None:  # fmt: skip
        for c in prange(entry.shape[1]):
            one(c, opn, close, mclose, m_bar, m_start, m_lptr, m_lix, m_lpx, m_hptr, m_hix,
                m_hpx, t_bar, t_lptr, t_lix, t_lpx, t_hptr, t_hix, t_hpx, f_ptr, f_min, f_rate,
                f_mark, bad, cap, maxlev, mmr, amount, entry, stopd, side, ratio, fee, pct,
                cap_pct, clear, cash0, leverage, maxh, equity, stats, funding, ilog,
                flog)  # fmt: skip

    return cpu_kernel(cpu)


@dataclass(frozen=True)
class PerpReplay:
    """Per configuration: the equity curve, counters, funding paid and (if asked) the trade log."""

    equity: npt.NDArray[np.float64]  # [configs, bars]
    trades: npt.NDArray[np.int64]
    denied: npt.NDArray[np.int64]
    ambiguous: npt.NDArray[np.int64]
    hold_sum: npt.NDArray[np.int64]
    liquidations: npt.NDArray[np.int64]
    first_liquidation: npt.NDArray[np.int64]  # bar of the first liquidation, -1 if none
    funding_paid: npt.NDArray[np.float64]
    ilog: npt.NDArray[np.int64] | None  # [configs, trades, (open bar, close bar, reason, lev)]
    flog: npt.NDArray[np.float64] | None  # [configs, trades, (qty, entry px, exit px, stop)]
    logged: npt.NDArray[np.int64]


def _paths(p: PathArrays) -> tuple[Any, ...]:
    return (p.low_ptr, p.low_ix, p.low_px, p.high_ptr, p.high_ix, p.high_px)


def run_perp_replay(
    open_: npt.ArrayLike,
    close: npt.ArrayLike,
    perp: PerpArrays,
    entry: npt.NDArray[np.int8],
    stop: npt.NDArray[Any],
    *,
    direction: int,
    tp_sl_ratio: float,
    fee: float,
    max_risk_pct: float,
    initial_cash: float,
    leverage: int,
    max_holding_bars: int,
    trade_log: bool,
    max_portfolio_risk_pct: float = 0.10,
    clearance: float = 0.25,
) -> PerpReplay:
    opn = np.ascontiguousarray(np.asarray(open_, dtype=np.float64))
    cls = np.ascontiguousarray(np.asarray(close, dtype=np.float64))
    n = cls.size
    if opn.shape != (n,) or not n or perp.mark_close.shape != (n,) or perp.bad.shape != (n,):
        raise KernelInputError("bars and perpetual arrays must be equal-length 1-D arrays")
    if not (np.isfinite(opn).all() and np.isfinite(cls).all()):
        raise KernelInputError("bars must be finite for the kernel engine")
    if perp.mark.bar_ptr.size != n + 1 or perp.trade.bar_ptr.size != n + 1:
        raise KernelInputError("paths must have one entry per bar")
    if entry.ndim != 2 or entry.shape[0] != n or stop.shape != entry.shape:
        raise KernelInputError("signals must be [bars, configs]")
    if max_holding_bars < 1:
        raise KernelInputError("max_holding_bars must be positive")
    m = entry.shape[1]
    cap = n + 1 if trade_log else 1  # a position can open and close within one bar
    equity = np.empty((m, n), dtype=np.float64)
    stats = np.zeros((m, N_STATS), dtype=np.int64)
    funding = np.zeros(m, dtype=np.float64)
    ilog = np.zeros((m, cap, 4), dtype=np.int64)
    flog = np.zeros((m, cap, 4), dtype=np.float64)
    b = perp.brackets
    _build()(
        opn, cls, perp.mark_close,
        perp.mark.bar_ptr, perp.mark.seg_start, *_paths(perp.mark), perp.trade.bar_ptr,
        *_paths(perp.trade),
        perp.fund_ptr, perp.fund_minute, perp.fund_rate, perp.fund_mark, perp.bad,
        b.cap, b.max_leverage, b.mmr, b.amount,
        np.ascontiguousarray(entry, dtype=np.int8),
        np.ascontiguousarray(stop.astype(np.float64)),  # fp32 signals widen exactly
        1 if direction == 1 else -1, float(tp_sl_ratio), float(fee), float(max_risk_pct),
        float(max_portfolio_risk_pct), float(clearance), float(initial_cash), int(leverage),
        int(max_holding_bars), equity, stats, funding, ilog, flog,
    )  # fmt: skip
    if stats[:, S_ERROR].any():
        raise KernelInputError(
            "a position was open at a bar whose trade and mark paths or funding minutes disagree"
        )
    if trade_log and stats[:, S_OVERFLOW].any():
        raise KernelInputError("trade log overflowed")
    return PerpReplay(
        equity=equity, trades=stats[:, S_TRADES].copy(), denied=stats[:, S_DENIED].copy(),
        ambiguous=stats[:, S_AMBIGUOUS].copy(), hold_sum=stats[:, S_HOLD_SUM].copy(),
        liquidations=stats[:, S_LIQUIDATIONS].copy(),
        first_liquidation=stats[:, S_FIRST_LIQ].copy(), funding_paid=funding,
        ilog=ilog if trade_log else None, flog=flog if trade_log else None,
        logged=stats[:, S_LOGGED].copy(),
    )  # fmt: skip
