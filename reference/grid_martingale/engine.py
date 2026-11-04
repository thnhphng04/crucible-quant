"""Grid and martingale (DCA) backtests on 5min perp bars, one coin per kernel call (numba, CPU).

Accounting is linear-perp: cash += -units x price on a buy (+ on a sell), equity = cash + units x price. Fees
come out of cash; funding is charged at the bar whose open is a funding time: cash -= units x open x rate (a long
pays a positive rate). No leverage beyond 1x of the sleeve: the grid's full inventory and the DCA's full ladder
are each worth the sleeve equity when they are set up. A sleeve whose equity reaches 0 stops (ruin).

Fills on 5min OHLC bars: price walks the bar as O -> L -> H -> C for an up bar and O -> H -> L -> C for a down
bar (path_mode 0), or the reverse (path_mode 1). A resting limit fills when the path trades through it
(breach, strict) or touches it (touch=True); a gap through a limit fills at the gap price. one_side=True forbids
buys and sells on the same bar (no intrabar round trips). Plan of 2026-10-21, INSIGHTS §9l.
"""

from __future__ import annotations

import math

import numpy as np
from numba import njit

# ------------------------------------------------------------------------------------------- helpers


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


# ----------------------------------------------------------------------------------------------- grid


@njit(cache=True)
def _grid_setup(price, vol, equity, n_levels, min_step, levels, units_per_gap):
    """Log-spaced levels around price over +-R, R = 2 x daily vol x sqrt(30); returns (n, step)."""
    r = 2.0 * vol * math.sqrt(30.0)
    n = n_levels
    step = 2.0 * r / n
    if step < min_step:
        n = int(2.0 * r / min_step) // 2 * 2
        if n < 4:
            n = 4
        step = max(2.0 * r / n, min_step)
    r = step * n / 2.0
    q = 2.0 * equity / n  # value per gap: the full half-ladder is worth the equity
    for i in range(n + 1):
        levels[i] = price * math.exp(-r + i * step)
    for g in range(n):
        units_per_gap[g] = q / levels[g]
    return n, step


@njit(cache=True)
def grid_kernel(o, h, l, c, vol, funding, day_idx, n_days, start, n_levels, min_step, maker, taker, slip,
                exit_mode, path_mode, touch, one_side):
    """Neutral grid. exit_mode 0 (A1): beyond one step outside the range -> close at market and re-center;
    exit_mode 1 (A2): hold the inventory; re-center (new R) only when the position is flat again.
    Returns (daily equity, counters[fills, resets, fees, funding, ruined])."""
    nbar = len(o)
    out = np.full(n_days, np.nan)
    counters = np.zeros(5)
    levels = np.empty(4096)
    upg = np.empty(4096)
    cash = 1.0
    units = 0.0
    n = 0
    step = 0.0
    a = 0
    mid = 0
    live = False
    dead = False
    for i in range(start, nbar):
        if dead:
            out[day_idx[i]] = 0.0
            continue
        if funding[i] != 0.0 and units != 0.0:
            f = units * o[i] * funding[i]
            cash -= f
            counters[3] += f
        fresh = False
        if not live:
            if vol[i] > 0.0:
                n, step = _grid_setup(o[i], vol[i], cash + units * o[i], n_levels, min_step, levels, upg)
                mid = n // 2
                a = mid
                live = True
                fresh = True  # built around this open: the gap before it is not traded
            else:
                out[day_idx[i]] = cash + units * c[i]
                continue
        pts = _path(c[i - 1] if (i > start and not fresh) else o[i], o[i], h[i], l[i], c[i], path_mode)
        side = 0  # +1 bought this bar, -1 sold this bar
        for s in range(4):
            x = pts[s]
            y = pts[s + 1]
            if y < x:  # falling: fill resting buys
                while a - 1 >= 0 and _crosses_down(y, levels[a - 1], touch) and not (one_side and side == -1):
                    price = levels[a - 1] if x >= levels[a - 1] else x
                    qty = upg[a - 1]
                    cash -= qty * price + qty * price * maker
                    counters[2] += qty * price * maker
                    units += qty
                    a -= 1
                    side = 1
                    counters[0] += 1
                    if exit_mode == 1 and a == mid:
                        n, step = _grid_setup(levels[mid], vol[i], cash + units * levels[mid], n_levels, min_step,
                                              levels, upg)
                        mid = n // 2
                        a = mid
                if exit_mode == 0 and a == 0 and y < levels[0] * math.exp(-step):
                    price = y * (1.0 - slip)  # sell the long inventory at market
                    cash += units * price - abs(units) * price * taker
                    counters[2] += abs(units) * price * taker
                    units = 0.0
                    counters[1] += 1
                    n, step = _grid_setup(y, vol[i], cash, n_levels, min_step, levels, upg)
                    mid = n // 2
                    a = mid
            elif y > x:  # rising: fill resting sells
                while a + 1 <= n and _crosses_up(y, levels[a + 1], touch) and not (one_side and side == 1):
                    price = levels[a + 1] if x <= levels[a + 1] else x
                    qty = upg[a]
                    cash += qty * price - qty * price * maker
                    counters[2] += qty * price * maker
                    units -= qty
                    a += 1
                    side = -1
                    counters[0] += 1
                    if exit_mode == 1 and a == mid:
                        n, step = _grid_setup(levels[mid], vol[i], cash + units * levels[mid], n_levels, min_step,
                                              levels, upg)
                        mid = n // 2
                        a = mid
                if exit_mode == 0 and a == n and y > levels[n] * math.exp(step):
                    price = y * (1.0 + slip)  # buy back the short inventory at market
                    cash += units * price - abs(units) * price * taker
                    counters[2] += abs(units) * price * taker
                    units = 0.0
                    counters[1] += 1
                    n, step = _grid_setup(y, vol[i], cash, n_levels, min_step, levels, upg)
                    mid = n // 2
                    a = mid
        equity = cash + units * c[i]
        if equity <= 0.0:
            dead = True
            counters[4] = 1
            out[day_idx[i]] = 0.0
            continue
        out[day_idx[i]] = equity
    if not dead and units != 0.0:  # end of data (delisting or last bar): close at market
        price = c[nbar - 1] * (1.0 - slip if units > 0 else 1.0 + slip)
        cash += units * price - abs(units) * price * taker
        counters[2] += abs(units) * price * taker
        out[day_idx[nbar - 1]] = cash
    return out, counters


# ------------------------------------------------------------------------------------------------ DCA


@njit(cache=True)
def dca_kernel(o, h, l, c, funding, day_idx, n_days, start, direction, n_so, first_dev, step_scale, volume_scale,
               take_profit, stop_beyond, maker, taker, slip, path_mode, touch, one_side):
    """Martingale DCA. direction +1 long (buy dips), -1 short (sell rallies). Base order at market at a bar open;
    safety order k at base x (1 -/+ D_k), D_k = first_dev x (1 + s + ... + s^(k-1)), worth base x volume_scale^k;
    take profit (limit) at average x (1 +/- take_profit). stop_beyond > 0: market exit when price passes the last
    safety order by that fraction (B2); 0 = hold (B1). A new cycle starts at the next bar's open.
    Returns (daily equity, counters[cycles, take_profits, stops, fees, funding, ruined, so_used_sum, max_so_hits])."""
    nbar = len(o)
    out = np.full(n_days, np.nan)
    counters = np.zeros(8)
    so_price = np.empty(n_so)
    so_value = np.empty(n_so)
    ladder = 0.0
    for k in range(n_so + 1):
        ladder += volume_scale ** k
    d = direction
    cash = 1.0
    units = 0.0
    cost = 0.0  # signed notional paid for the open units
    k_filled = 0
    tp = 0.0
    stop = 0.0
    in_cycle = False
    dead = False
    for i in range(start, nbar):
        if dead:
            out[day_idx[i]] = 0.0
            continue
        if funding[i] != 0.0 and units != 0.0:
            f = units * o[i] * funding[i]
            cash -= f
            counters[4] += f
        just_opened = False
        if not in_cycle:
            just_opened = True
            equity = cash + units * o[i]
            base_value = equity / ladder
            price = o[i] * (1.0 + d * slip)
            qty = base_value / price
            cash -= d * qty * price + qty * price * taker
            counters[3] += qty * price * taker
            units = d * qty
            cost = d * qty * price
            dev = 0.0
            inc = first_dev
            for k in range(n_so):
                dev += inc
                inc *= step_scale
                so_price[k] = price * (1.0 - d * dev)
                so_value[k] = base_value * volume_scale ** (k + 1)
            k_filled = 0
            avg = cost / units
            tp = avg * (1.0 + d * take_profit)
            stop = so_price[n_so - 1] * (1.0 - d * stop_beyond)
            in_cycle = True
            counters[0] += 1
        # a cycle opened at this bar's open does not see the gap before it
        pts = _path(c[i - 1] if (i > start and not just_opened) else o[i], o[i], h[i], l[i], c[i], path_mode)
        side = 0  # +1 added to the position this bar, -1 took profit this bar
        closed = False
        for s in range(4):
            if closed:
                break
            x = pts[s]
            y = pts[s + 1]
            adverse = (y < x) if d == 1 else (y > x)
            if adverse:
                while k_filled < n_so and not (one_side and side == -1):
                    lvl = so_price[k_filled]
                    hit = _crosses_down(y, lvl, touch) if d == 1 else _crosses_up(y, lvl, touch)
                    if not hit:
                        break
                    if d == 1:
                        price = lvl if x >= lvl else x
                    else:
                        price = lvl if x <= lvl else x
                    qty = so_value[k_filled] / price
                    cash -= d * qty * price + qty * price * maker
                    counters[3] += qty * price * maker
                    units += d * qty
                    cost += d * qty * price
                    k_filled += 1
                    side = 1
                    avg = cost / units
                    tp = avg * (1.0 + d * take_profit)
                if stop_beyond > 0.0 and k_filled == n_so:
                    hit = _crosses_down(y, stop, touch) if d == 1 else _crosses_up(y, stop, touch)
                    if hit:
                        price = (stop if (x >= stop if d == 1 else x <= stop) else x) * (1.0 - d * slip)
                        cash += units * price - abs(units) * price * taker
                        counters[3] += abs(units) * price * taker
                        units = 0.0
                        cost = 0.0
                        counters[2] += 1
                        counters[6] += k_filled
                        counters[7] += 1
                        in_cycle = False
                        closed = True
            elif y != x and not (one_side and side == 1):
                hit = _crosses_up(y, tp, touch) if d == 1 else _crosses_down(y, tp, touch)
                if hit:
                    if d == 1:
                        price = tp if x <= tp else x
                    else:
                        price = tp if x >= tp else x
                    cash += units * price - abs(units) * price * maker
                    counters[3] += abs(units) * price * maker
                    units = 0.0
                    cost = 0.0
                    counters[1] += 1
                    counters[6] += k_filled
                    if k_filled == n_so:
                        counters[7] += 1
                    in_cycle = False
                    closed = True
        equity = cash + units * c[i]
        if equity <= 0.0:
            dead = True
            counters[5] = 1
            out[day_idx[i]] = 0.0
            continue
        out[day_idx[i]] = equity
    if not dead and units != 0.0:
        price = c[nbar - 1] * (1.0 - slip if units > 0 else 1.0 + slip)
        cash += units * price - abs(units) * price * taker
        counters[3] += abs(units) * price * taker
        out[day_idx[nbar - 1]] = cash
    return out, counters
