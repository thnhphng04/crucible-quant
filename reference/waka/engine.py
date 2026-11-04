"""Waka Waka style basket grid + limited martingale on 5min bars, one shared account over S aligned symbols (numba).

A basket opens with a market order at a bar open when the symbol's signal says so (+1 buy, -1 sell, 2 both) and no
basket of that direction is open on the symbol (one buy and one sell basket may coexist: hedging). Grid trades are resting
limits at entry -/+ k x distance (levels from the initial trade); trade k has lots[k] x the first lot. The basket
closes as a whole at a money take profit: profit = first units x tp_dist, i.e. TP price = average +/- that profit /
total units (a one-trade basket takes tp_dist). Other exits: grid stop (sl_dist from the entry), break even after
be_level trades (TP at the average), account max floating drawdown (close everything, pause), broker stop-out
(equity < stop_out x margin used), end of data.

Prices: bid OHLC plus a per-bar spread; buys execute at ask = bid + spread, sells at bid. The path through a bar
is the grid engine's (reference/grid_martingale/engine.py `_path`): previous close -> open -> low/high -> high/low
-> close on the bid. Orders triggered by the gap (previous close -> open) fill at the open; inside the bar, limits
and stops fill at their price. Costs: comm_mkt / comm_lim per side on notional, slip on market orders; carry per bar as a
fraction of notional (forex swap at rollover, crypto funding), positive = paid. Money is in quote units with the
account equity starting at 1 (the quote/account currency conversion is ignored). Plan of 2026-10-21, INSIGHTS §9m.
"""

from __future__ import annotations

import numpy as np
from numba import njit, prange


# The three fill helpers are copied from reference/grid_martingale/engine.py: importing that module under a second
# name breaks numba's on-disk cache.
@njit(cache=True)
def _path(prev, o, h, l, c, mode):
    """Previous close -> open (the gap, if any) -> low/high -> high/low -> close."""
    pts = np.empty(5)
    up = c >= o
    if mode == 1:
        up = not up
    pts[0] = prev
    pts[1] = o
    if up:
        pts[2] = l
        pts[3] = h
    else:
        pts[2] = h
        pts[3] = l
    pts[4] = c
    return pts


@njit(cache=True)
def _crosses_down(y, level, touch):
    return y <= level if touch else y < level


@njit(cache=True)
def _crosses_up(y, level, touch):
    return y >= level if touch else y > level

# basket log columns
B_SYM, B_DIR, B_OPEN, B_CLOSE, B_TRADES, B_PNL, B_REASON, B_WORST, B_FEES, B_CARRY, B_ENTRY, B_EXIT = range(12)
REASONS = {1: "take profit", 2: "grid stop", 3: "break even", 4: "max drawdown", 5: "stop-out", 6: "end of data"}


def lot_sequence(max_trades: int, m2: float, m35: float, m6: float, custom: tuple = ()) -> np.ndarray:
    """Lots relative to the first trade: trade 2 x m2, trades 3-5 x m35 each, trade 6+ x m6 each (or custom)."""
    if custom:
        return np.asarray(custom[:max_trades], float) / custom[0]
    lots = [1.0]
    for k in range(1, max_trades):
        lots.append(lots[-1] * (m2 if k == 1 else m35 if k <= 4 else m6))
    return np.asarray(lots)


@njit(cache=True)
def _close_basket(s, j, price, units, cost, rate, bar, reason, log, nlog, bstate, bfees, bcarry, bworst, bopen,
                  btrades, bentry):
    """Realize basket (s, j) at execution price; returns the realized pnl including this exit's fee."""
    fee = units * price * rate
    pnl = (units * price - cost) if j == 0 else (cost - units * price)
    if nlog < log.shape[0]:
        log[nlog, B_SYM] = s
        log[nlog, B_DIR] = 1 if j == 0 else -1
        log[nlog, B_OPEN] = bopen[s, j]
        log[nlog, B_CLOSE] = bar
        log[nlog, B_TRADES] = btrades[s, j]
        log[nlog, B_PNL] = pnl - fee - bfees[s, j] - bcarry[s, j]
        log[nlog, B_REASON] = reason
        log[nlog, B_WORST] = bworst[s, j]
        log[nlog, B_FEES] = fee + bfees[s, j]
        log[nlog, B_CARRY] = bcarry[s, j]
        log[nlog, B_ENTRY] = bentry[s, j]
        log[nlog, B_EXIT] = price
    bstate[s, j] = 0
    return pnl - fee


@njit(cache=True)
def waka_kernel(bo, bh, bl, bc, spread, valid, signal, dist, notional, carry_long, carry_short, day_idx, n_days,
                lots, tp_mult, sl_mult, comm_mkt, comm_lim, slip, be_level, max_dd, pause_bars, leverage, stop_out,
                path_mode, touch):
    """Arrays are [S, T] except day_idx [T] and lots [max_trades]. dist is the grid distance in price at a basket's
    open; tp_dist = tp_mult x dist and sl_dist = sl_mult x dist (sl_mult <= 0: no grid stop); notional is the
    first trade's notional per unit of balance. max_dd >= 1 disables the floating-drawdown exit.

    Returns (daily equity at the last bar close, daily balance, daily min equity over bar closes, basket log,
    counters[fees, carry, baskets, dd_exits, stop_outs, ruined])."""
    S, T = bo.shape
    max_trades = lots.shape[0]
    eq_day = np.full(n_days, np.nan)
    bal_day = np.full(n_days, np.nan)
    min_day = np.full(n_days, np.nan)
    log = np.full((max(1000, T // 2), 12), np.nan)
    nlog = 0
    counters = np.zeros(6)
    # basket state, j = 0 long, 1 short
    bstate = np.zeros((S, 2), np.int64)  # 1 open
    btrades = np.zeros((S, 2), np.int64)
    bunits = np.zeros((S, 2))
    bcost = np.zeros((S, 2))  # sum of qty x execution price
    bq1 = np.zeros((S, 2))  # first trade units
    bentry = np.zeros((S, 2))  # first trade execution price
    bdist = np.zeros((S, 2))
    btp = np.zeros((S, 2))  # TP execution price
    bsl = np.zeros((S, 2))  # stop execution price (long: bid, short: ask)
    bopen = np.zeros((S, 2), np.int64)
    bfees = np.zeros((S, 2))
    bcarry = np.zeros((S, 2))
    bworst = np.zeros((S, 2))  # worst unrealized pnl / balance at a bar close
    fresh = np.zeros((S, 2), np.bool_)
    cash = 1.0
    paused_until = -1
    dead = False
    for t in range(T):
        d = day_idx[t]
        if dead:
            eq_day[d] = 0.0
            bal_day[d] = 0.0
            min_day[d] = 0.0
            continue
        # carry at the open, new baskets at the open
        for s in range(S):
            if not valid[s, t]:
                continue
            for j in range(2):
                fresh[s, j] = False
                if bstate[s, j] == 1:
                    rate = carry_long[s, t] if j == 0 else carry_short[s, t]
                    if rate != 0.0:
                        f = bunits[s, j] * bo[s, t] * rate
                        cash -= f
                        bcarry[s, j] += f
                        counters[1] += f
            sig = signal[s, t]  # +1 buy, -1 sell, 2 both
            if sig == 0 or t < paused_until or dist[s, t] <= 0.0 or t == 0:
                continue
            for j in range(2):
                wanted = (j == 0 and (sig == 1 or sig == 2)) or (j == 1 and (sig == -1 or sig == 2))
                if wanted and bstate[s, j] == 0:
                    price = (bo[s, t] + spread[s, t]) * (1.0 + slip) if j == 0 else bo[s, t] * (1.0 - slip)
                    q1 = notional[s, t] * cash / price
                    fee = q1 * price * comm_mkt
                    cash -= fee
                    counters[0] += fee
                    bstate[s, j] = 1
                    btrades[s, j] = 1
                    bunits[s, j] = q1
                    bcost[s, j] = q1 * price
                    bq1[s, j] = q1
                    bentry[s, j] = price
                    bdist[s, j] = dist[s, t]
                    tpd = tp_mult * dist[s, t]
                    btp[s, j] = price + tpd if j == 0 else price - tpd
                    if sl_mult > 0.0:
                        btp_sl = sl_mult * dist[s, t]
                        bsl[s, j] = price - btp_sl if j == 0 else price + btp_sl
                    else:
                        bsl[s, j] = -1.0 if j == 0 else 1e300
                    bopen[s, j] = t
                    bfees[s, j] = fee
                    bcarry[s, j] = 0.0
                    bworst[s, j] = 0.0
                    fresh[s, j] = True
                    counters[2] += 1
        # walk each symbol's bar
        for s in range(S):
            if not valid[s, t]:
                continue
            sp = spread[s, t]
            prev = bc[s, t - 1] if t > 0 and valid[s, t - 1] else bo[s, t]
            pts = _path(prev, bo[s, t], bh[s, t], bl[s, t], bc[s, t], path_mode)
            for seg in range(4):
                x = pts[seg]
                y = pts[seg + 1]
                if y == x:
                    continue
                for j in range(2):
                    if bstate[s, j] == 0 or (seg == 0 and fresh[s, j]):
                        continue
                    long = j == 0
                    gap = seg == 0  # previous close -> open: anything triggered fills at the open
                    adverse = (y < x) if long else (y > x)
                    if adverse:
                        # grid trades: long buy limit at ask level L fills when bid < L - spread
                        while btrades[s, j] < max_trades:
                            k = btrades[s, j]
                            lvl = bentry[s, j] - k * bdist[s, j] if long else bentry[s, j] + k * bdist[s, j]
                            trig = lvl - sp if long else lvl
                            hit = _crosses_down(y, trig, touch) if long else _crosses_up(y, trig, touch)
                            if not hit:
                                break
                            if long:
                                price = y + sp if gap else lvl
                            else:
                                price = y if gap else lvl
                            qty = lots[k] * bq1[s, j]
                            fee = qty * price * comm_lim
                            cash -= fee
                            bfees[s, j] += fee
                            counters[0] += fee
                            bunits[s, j] += qty
                            bcost[s, j] += qty * price
                            btrades[s, j] += 1
                            avg = bcost[s, j] / bunits[s, j]
                            target = 0.0 if (be_level > 0 and btrades[s, j] >= be_level) else \
                                bq1[s, j] * tp_mult * bdist[s, j]
                            btp[s, j] = avg + target / bunits[s, j] if long else avg - target / bunits[s, j]
                        # grid stop (market): long sells at bid, short buys at ask
                        trig = bsl[s, j] if long else bsl[s, j] - sp
                        hit = _crosses_down(y, trig, touch) if long else _crosses_up(y, trig, touch)
                        if hit:
                            if long:
                                price = (y if gap else trig) * (1.0 - slip)
                            else:
                                price = ((y if gap else trig) + sp) * (1.0 + slip)
                            counters[0] += bunits[s, j] * price * comm_mkt
                            cash += _close_basket(s, j, price, bunits[s, j], bcost[s, j], comm_mkt, t, 2, log, nlog,
                                                  bstate, bfees, bcarry, bworst, bopen, btrades, bentry)
                            nlog += 1
                    else:
                        # take profit (limit): long sells at bid >= TP, short buys at ask <= TP
                        trig = btp[s, j] if long else btp[s, j] - sp
                        hit = _crosses_up(y, trig, touch) if long else _crosses_down(y, trig, touch)
                        if hit:
                            if long:
                                price = y if gap else trig
                            else:
                                price = y + sp if gap else btp[s, j]
                            reason = 3 if (be_level > 0 and btrades[s, j] >= be_level) else 1
                            counters[0] += bunits[s, j] * price * comm_lim
                            cash += _close_basket(s, j, price, bunits[s, j], bcost[s, j], comm_lim, t, reason, log,
                                                  nlog, bstate, bfees, bcarry, bworst, bopen, btrades, bentry)
                            nlog += 1
        # mark to market at the bar close
        unreal = 0.0
        margin = 0.0
        for s in range(S):
            for j in range(2):
                if bstate[s, j] == 1:
                    px = bc[s, t] if j == 0 else bc[s, t] + spread[s, t]
                    u = bunits[s, j] * px - bcost[s, j] if j == 0 else bcost[s, j] - bunits[s, j] * px
                    unreal += u
                    margin += bunits[s, j] * px / leverage
                    if cash > 0.0 and u / cash < bworst[s, j]:
                        bworst[s, j] = u / cash
        equity = cash + unreal
        reason = 0
        if margin > 0.0 and equity < stop_out * margin:
            reason = 5
        elif max_dd < 1.0 and cash > 0.0 and -unreal / cash >= max_dd:
            reason = 4
        if reason > 0:
            for s in range(S):
                for j in range(2):
                    if bstate[s, j] == 1:
                        px = bc[s, t] * (1.0 - slip) if j == 0 else (bc[s, t] + spread[s, t]) * (1.0 + slip)
                        counters[0] += bunits[s, j] * px * comm_mkt
                        cash += _close_basket(s, j, px, bunits[s, j], bcost[s, j], comm_mkt, t, reason, log, nlog,
                                              bstate, bfees, bcarry, bworst, bopen, btrades, bentry)
                        nlog += 1
            counters[3 if reason == 4 else 4] += 1
            paused_until = t + pause_bars
            equity = cash
        if equity <= 0.0:
            dead = True
            counters[5] = 1
            equity = 0.0
        eq_day[d] = equity
        bal_day[d] = cash
        min_day[d] = equity if np.isnan(min_day[d]) else min(min_day[d], equity)
    if not dead:  # end of data: close at the last close
        for s in range(S):
            for j in range(2):
                if bstate[s, j] == 1:
                    px = bc[s, T - 1] * (1.0 - slip) if j == 0 else (bc[s, T - 1] + spread[s, T - 1]) * (1.0 + slip)
                    counters[0] += bunits[s, j] * px * comm_mkt
                    cash += _close_basket(s, j, px, bunits[s, j], bcost[s, j], comm_mkt, T - 1, 6, log, nlog,
                                          bstate, bfees, bcarry, bworst, bopen, btrades, bentry)
                    nlog += 1
        eq_day[day_idx[T - 1]] = cash
        bal_day[day_idx[T - 1]] = cash
    return eq_day, bal_day, min_day, log[:nlog], counters


# ------------------------------------------------------------------------------------- isolated baskets

# basket outcome columns
O_PNL, O_TRADES, O_WORST, O_BARS, O_TP, O_MAE, O_FP = range(7)


@njit(cache=True, parallel=True)
def basket_outcomes(bo, bh, bl, bc, spread, carry_long, carry_short, starts, dirs, dist, lots, tp_mult, comm_mkt,
                    comm_lim, slip, max_bars, fp_down):
    """One isolated basket per (start bar, direction), opened at the start bar's open with one unit as the first
    trade, the same grid / martingale / money-TP rules as waka_kernel, no stop; closed at market after max_bars.
    Also a first-passage label on the bid path: +1 if price moves dist in the basket's favour before moving
    fp_down x dist against it, 0 if the adverse move comes first, -1 if neither within max_bars.

    1-D price arrays. Returns [n, 7] (see O_*): pnl in units of first units x dist (net of costs and carry),
    trades filled, worst floating pnl (same units), bars held, 1 if closed at TP, max adverse excursion of the bid
    from the entry in dist units, first-passage label."""
    n = starts.shape[0]
    T = bo.shape[0]
    max_trades = lots.shape[0]
    out = np.full((n, 7), np.nan)
    for b in prange(n):
        t0 = starts[b]
        long = dirs[b] > 0
        d = dist[b]
        entry = (bo[t0] + spread[t0]) * (1.0 + slip) if long else bo[t0] * (1.0 - slip)
        bid0 = bo[t0]
        units = 1.0
        cost = entry
        cash = -entry * comm_mkt  # fees and carry accumulate here; pnl = cash + realised trade pnl
        trades = 1
        tp = entry + tp_mult * d if long else entry - tp_mult * d
        worst = 0.0
        mae = 0.0
        fp = -1
        open_ = True
        realised = 0.0
        t_end = min(t0 + max_bars, T)
        t = t0
        while t < t_end and (open_ or fp < 0):
            if open_ and t > t0:
                rate = carry_long[t] if long else carry_short[t]
                cash -= units * bo[t] * rate
            sp = spread[t]
            prev = bo[t] if t == t0 else bc[t - 1]
            pts = _path(prev, bo[t], bh[t], bl[t], bc[t], 0)
            for seg in range(4):
                x = pts[seg]
                y = pts[seg + 1]
                if y == x:
                    continue
                gap = seg == 0
                # first passage and adverse excursion on the bid
                move = (y - bid0) / d if long else (bid0 - y) / d
                if -move > mae:
                    mae = -move
                if fp < 0:
                    if move >= 1.0:
                        fp = 1
                    elif move <= -fp_down:
                        fp = 0
                if not open_:
                    continue
                adverse = (y < x) if long else (y > x)
                if adverse:
                    while trades < max_trades:
                        lvl = entry - trades * d if long else entry + trades * d
                        trig = lvl - sp if long else lvl
                        hit = (y < trig) if long else (y > trig)
                        if not hit:
                            break
                        if long:
                            price = y + sp if gap else lvl
                        else:
                            price = y if gap else lvl
                        qty = lots[trades]
                        cash -= qty * price * comm_lim
                        units += qty
                        cost += qty * price
                        trades += 1
                        avg = cost / units
                        tp = avg + tp_mult * d / units if long else avg - tp_mult * d / units
                else:
                    trig = tp if long else tp - sp
                    hit = (y > trig) if long else (y < trig)
                    if hit:
                        if long:
                            price = y if gap else tp
                        else:
                            price = y + sp if gap else tp
                        cash -= units * price * comm_lim
                        realised = units * price - cost if long else cost - units * price
                        open_ = False
                        out[b, O_BARS] = t - t0 + 1
                        out[b, O_TP] = 1.0
            if open_:
                px = bc[t] if long else bc[t] + sp
                u = units * px - cost if long else cost - units * px
                if u + cash < worst:
                    worst = u + cash
            t += 1
        if open_:  # time cap or end of data: close at market
            tl = t - 1
            px = bc[tl] * (1.0 - slip) if long else (bc[tl] + spread[tl]) * (1.0 + slip)
            cash -= units * px * comm_mkt
            realised = units * px - cost if long else cost - units * px
            out[b, O_BARS] = t - t0
            out[b, O_TP] = 0.0
        out[b, O_PNL] = (realised + cash) / d
        out[b, O_TRADES] = trades
        out[b, O_WORST] = min(worst, realised + cash) / d
        out[b, O_MAE] = mae
        out[b, O_FP] = fp
    return out


@njit(cache=True)
def anchor_grid_kernel(bo, bh, bl, bc, spread, carry_long, carry_short, signal, dist, hold_end, ma, day_idx, n_days,
                       lots, pause_bars, stop_steps, risk, comm, use_anchor_exit):
    """Anchored mean-reversion grid on one symbol (INSIGHTS §9r). signal[t] = +1 / -1 opens a basket at bar t's
    open (market) when none is open; dist[t] = step in price; hold_end[t] = last bar of a basket opened at t.
    Level j (j = number of lots open, 0..len(lots)-1) sits j steps against the entry; a lot is added at the close
    of a bar beyond the next level (market, ask for buys) once pause_bars have passed since the last fill. Each lot
    closes at its own take profit = its fill + 1 step (limit, last in first out); everything closes when price
    reaches the anchor ma[t] (use_anchor_exit), at the stop (stop_steps against the entry, market), or at the close
    of hold_end. First-lot units are sized so the loss at the stop is `risk` x equity at the open.
    Returns (daily equity at bar closes, daily min equity, basket log [open, close, dir, lots used, pnl, reason])."""
    T = bo.shape[0]
    L = lots.shape[0]
    eq_day = np.full(n_days, np.nan)
    min_day = np.full(n_days, np.nan)
    log = np.full((max(1000, T // 50), 6), np.nan)
    nlog = 0
    fill = np.zeros(L)
    qty = np.zeros(L)
    is_open = np.zeros(L, np.bool_)
    cash = 1.0
    active = False
    d = 0.0
    entry = 0.0
    direction = 0
    end = 0
    last_fill = 0
    used = 0
    t_open = 0
    cash_open = 0.0
    loss_steps = 0.0
    for j in range(L):
        loss_steps += lots[j] * (stop_steps - j)
    for t in range(T):
        sp = spread[t]
        if active:
            rate = carry_long[t] if direction > 0 else carry_short[t]
            if rate != 0.0:
                for j in range(L):
                    if is_open[j]:
                        cash -= qty[j] * bo[t] * rate
        fresh = False
        if not active and signal[t] != 0 and dist[t] > 0.0 and t > 0:
            direction = signal[t]
            d = dist[t]
            px = bo[t] + sp if direction > 0 else bo[t]
            entry = px
            q1 = risk * cash / (loss_steps * d)
            for j in range(L):
                is_open[j] = False
            fill[0] = px
            qty[0] = q1 * lots[0]
            is_open[0] = True
            cash -= qty[0] * px * comm
            active = True
            fresh = True
            end = hold_end[t]
            last_fill = t
            used = 1
            t_open = t
            cash_open = cash + qty[0] * px * comm
        if active:
            long = direction > 0
            stop = entry - stop_steps * d if long else entry + stop_steps * d
            pts = _path(bo[t] if (fresh or t == 0) else bc[t - 1], bo[t], bh[t], bl[t], bc[t], 0)
            reason = 0
            for seg in range(4):
                x = pts[seg]
                y = pts[seg + 1]
                if y == x or (seg == 0 and fresh):
                    continue
                gap = seg == 0
                favourable = (y > x) if long else (y < x)
                if not favourable:
                    hit = (y < stop) if long else (y + sp > stop)
                    if hit:
                        px = (y if gap else stop) if long else ((y + sp) if gap else stop)
                        for j in range(L):
                            if is_open[j]:
                                cash += qty[j] * (px - fill[j]) * direction - qty[j] * px * comm
                                is_open[j] = False
                        reason = 2
                        break
                else:
                    # last in first out: deeper lots have nearer take profits
                    for j in range(L - 1, -1, -1):
                        if not is_open[j]:
                            continue
                        tp = fill[j] + d if long else fill[j] - d
                        hit = (y > tp) if long else (y + sp < tp)
                        if hit:
                            px = (y if gap else tp) if long else ((y + sp) if gap else tp)
                            cash += qty[j] * (px - fill[j]) * direction - qty[j] * px * comm
                            is_open[j] = False
                    if use_anchor_exit:
                        hit = (y >= ma[t]) if long else (y + sp <= ma[t])
                        if hit:
                            px = (y if gap else ma[t]) if long else ((y + sp) if gap else ma[t])
                            for j in range(L):
                                if is_open[j]:
                                    cash += qty[j] * (px - fill[j]) * direction - qty[j] * px * comm
                                    is_open[j] = False
                            reason = 3
                            break
                    n_open = 0
                    for j in range(L):
                        if is_open[j]:
                            n_open += 1
                    if n_open == 0:
                        reason = 1
                        break
            if reason == 0:
                n_open = 0
                for j in range(L):
                    if is_open[j]:
                        n_open = j + 1  # depth = deepest open level + 1
                if t >= end:  # time stop at this bar's close
                    px = bc[t] if long else bc[t] + sp
                    for j in range(L):
                        if is_open[j]:
                            cash += qty[j] * (px - fill[j]) * direction - qty[j] * px * comm
                            is_open[j] = False
                    reason = 4
                elif n_open < L and t - last_fill >= pause_bars:
                    lvl = entry - n_open * d if long else entry + n_open * d
                    px = bc[t] + sp if long else bc[t]
                    beyond = (px <= lvl) if long else (px >= lvl)
                    if beyond:
                        q1 = qty[0] / lots[0] if qty[0] > 0 else 0.0
                        fill[n_open] = px
                        qty[n_open] = q1 * lots[n_open]
                        is_open[n_open] = True
                        cash -= qty[n_open] * px * comm
                        last_fill = t
                        if n_open + 1 > used:
                            used = n_open + 1
            if reason > 0:
                if nlog < log.shape[0]:
                    log[nlog, 0] = t_open
                    log[nlog, 1] = t
                    log[nlog, 2] = direction
                    log[nlog, 3] = used
                    log[nlog, 4] = cash - cash_open
                    log[nlog, 5] = reason
                nlog += 1
                active = False
        equity = cash
        if active:
            for j in range(L):
                if is_open[j]:
                    px = bc[t] if direction > 0 else bc[t] + sp
                    equity += qty[j] * (px - fill[j]) * direction
        dd = day_idx[t]
        eq_day[dd] = equity
        min_day[dd] = equity if np.isnan(min_day[dd]) else min(min_day[dd], equity)
    return eq_day, min_day, log[:min(nlog, log.shape[0])]


@njit(cache=True, parallel=True)
def first_passage(bh, bl, bc, bo, starts, ends, dirs, step, k_up, k_down):
    """Label per start: 1 if the bid moves k_up x step in the direction before k_down x step against it, 0 if the
    adverse move comes first, -1 if neither by bar ends[b] (exclusive). Path as `_path` (gap, then low/high)."""
    n = starts.shape[0]
    out = np.full(n, -1, np.int8)
    for b in prange(n):
        t0 = starts[b]
        p0 = bo[t0]
        long = dirs[b] > 0
        up = k_up * step[b]
        down = k_down * step[b]
        for t in range(t0, ends[b]):
            prev = bo[t] if t == t0 else bc[t - 1]
            pts = _path(prev, bo[t], bh[t], bl[t], bc[t], 0)
            done = False
            for seg in range(1, 5):
                move = pts[seg] - p0 if long else p0 - pts[seg]
                if move >= up:
                    out[b] = 1
                    done = True
                    break
                if move <= -down:
                    out[b] = 0
                    done = True
                    break
            if done:
                break
    return out
