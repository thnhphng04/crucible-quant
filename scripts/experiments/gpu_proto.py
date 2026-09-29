"""P3-30 prototype: genome G1 (Cross(ema,ema) & Slope(ema), ATR stop) on CUDA and njit.

One Python source per kernel, compiled for both targets by ``build(target)`` (E8). Arithmetic
goes through add/sub/mul/div so the CUDA build can use libdevice's non-contracted *_rn ops (E7);
the CPU build uses plain operators (LLVM does not contract without fastmath).

Semantics reproduced from the CPU oracle:
- features: registry on ``bars.window(t, L)``; ``prev`` is the same window's value at w-2
- signal:   rendered G1 source (ready guard, cross_up NaN rule, slope, stop=k*atr, tp=1.1*stop)
- replay:   execution.engine._run_spot_bracket (bracket_timeout_v1, spot)
"""

from __future__ import annotations

import math

from numba import cuda, njit, prange
from numba.cuda import libdevice

NAN = math.nan


def build(target: str):
    if target == "cpu":
        dev = njit

        @dev
        def add(a, b):
            return a + b

        @dev
        def sub(a, b):
            return a - b

        @dev
        def mul(a, b):
            return a * b

        @dev
        def div(a, b):
            return a / b

    else:
        dev = cuda.jit(device=True)

        @dev
        def add(a, b):
            return libdevice.dadd_rn(a, b)

        @dev
        def sub(a, b):
            return libdevice.dadd_rn(a, -b)  # no dsub_rn in numba-cuda; negation is exact

        @dev
        def mul(a, b):
            return libdevice.dmul_rn(a, b)

        @dev
        def div(a, b):
            return libdevice.ddiv_rn(a, b)

    # ── value sources for the pairwise sum: 0 = x[i]; 1 = (x[i]-m)^2; 2 = window true range
    @dev
    def val(x, h, lo_, i, m, mode, wlo):
        if mode == 0:
            return x[i]
        if mode == 1:
            d = sub(x[i], m)
            return mul(d, d)
        a = sub(h[i], lo_[i])
        if i == wlo:
            return a  # fmax(h-l, NaN) at the window's first bar
        b = abs(sub(h[i], x[i - 1]))
        e = abs(sub(lo_[i], x[i - 1]))
        mm = b if (b >= e or e != e) else e
        return a if (a >= mm or mm != mm) else mm

    @dev
    def pw_block(x, h, lo_, s, n, m, mode, wlo):
        if n < 8:
            res = -0.0
            for i in range(s, s + n):
                res = add(res, val(x, h, lo_, i, m, mode, wlo))
            return res
        r0 = val(x, h, lo_, s, m, mode, wlo)
        r1 = val(x, h, lo_, s + 1, m, mode, wlo)
        r2 = val(x, h, lo_, s + 2, m, mode, wlo)
        r3 = val(x, h, lo_, s + 3, m, mode, wlo)
        r4 = val(x, h, lo_, s + 4, m, mode, wlo)
        r5 = val(x, h, lo_, s + 5, m, mode, wlo)
        r6 = val(x, h, lo_, s + 6, m, mode, wlo)
        r7 = val(x, h, lo_, s + 7, m, mode, wlo)
        stop = n - (n % 8)
        i = 8
        while i < stop:
            r0 = add(r0, val(x, h, lo_, s + i, m, mode, wlo))
            r1 = add(r1, val(x, h, lo_, s + i + 1, m, mode, wlo))
            r2 = add(r2, val(x, h, lo_, s + i + 2, m, mode, wlo))
            r3 = add(r3, val(x, h, lo_, s + i + 3, m, mode, wlo))
            r4 = add(r4, val(x, h, lo_, s + i + 4, m, mode, wlo))
            r5 = add(r5, val(x, h, lo_, s + i + 5, m, mode, wlo))
            r6 = add(r6, val(x, h, lo_, s + i + 6, m, mode, wlo))
            r7 = add(r7, val(x, h, lo_, s + i + 7, m, mode, wlo))
            i += 8
        res = add(add(add(r0, r1), add(r2, r3)), add(add(r4, r5), add(r6, r7)))
        for j in range(stop, n):
            res = add(res, val(x, h, lo_, s + j, m, mode, wlo))
        return res

    @dev
    def pw1(x, h, lo_, s, n, m, mode, wlo):
        if n <= 128:
            return pw_block(x, h, lo_, s, n, m, mode, wlo)
        n2 = n // 2
        n2 -= n2 % 8
        return add(
            pw_block(x, h, lo_, s, n2, m, mode, wlo),
            pw_block(x, h, lo_, s + n2, n - n2, m, mode, wlo),
        )

    @dev
    def pw2(x, h, lo_, s, n, m, mode, wlo):
        if n <= 128:
            return pw_block(x, h, lo_, s, n, m, mode, wlo)
        n2 = n // 2
        n2 -= n2 % 8
        return add(
            pw1(x, h, lo_, s, n2, m, mode, wlo), pw1(x, h, lo_, s + n2, n - n2, m, mode, wlo)
        )

    @dev
    def pw_sum(x, h, lo_, s, n, m, mode, wlo):
        """numpy pairwise without recursion (E7: cached recursion segfaults); n <= ~480."""
        if n <= 128:
            return pw_block(x, h, lo_, s, n, m, mode, wlo)
        n2 = n // 2
        n2 -= n2 % 8
        return add(
            pw2(x, h, lo_, s, n2, m, mode, wlo), pw2(x, h, lo_, s + n2, n - n2, m, mode, wlo)
        )

    @dev
    def smooth_last2(x, h, lo_, wlo, w, n, alpha, mode):
        """(prev, now) of registry._smooth over window [wlo, wlo+w); mode 0 = x, 2 = true range.
        Assumes finite inputs (first valid run starts at the window start)."""
        if w < n:
            return NAN, NAN
        seed = div(pw_sum(x, h, lo_, wlo, n, 0.0, mode, wlo), float(n))
        prev = seed if n == w - 1 else NAN
        y = seed
        one_a = sub(1.0, alpha)
        z = mul(one_a, seed)
        for i in range(wlo + n, wlo + w):
            y = add(z, mul(alpha, val(x, h, lo_, i, 0.0, mode, wlo)))
            z = mul(one_a, y)
            if i == wlo + w - 2:
                prev = y
        return prev, y

    @dev
    def round_to_lot(q, lot):
        if not (q == q and abs(q) != math.inf) or q <= 0.0:
            return 0.0
        if lot > 0.0:
            q = mul(float(math.floor(add(div(q, lot), 1e-9))), lot)
        return q if q > 0.0 else 0.0

    # ── K1: features (lane = instance × bar) ─────────────────────────────────────────────
    def features_body(k, close, high, low, L, ema_n, ema_alpha, now, prev, atr_now, atr_n):
        n_bars = close.size
        inst = k // n_bars
        t = k % n_bars
        wlo = t - L + 1 if t - L + 1 > 0 else 0
        w = t - wlo + 1
        if inst < ema_n.size:
            p, q = smooth_last2(close, high, low, wlo, w, ema_n[inst], ema_alpha[inst], 0)
            prev[inst, t] = p
            now[inst, t] = q
        else:
            p, q = smooth_last2(close, high, low, wlo, w, atr_n, div(1.0, float(atr_n)), 2)
            atr_now[t] = q

    # ── K2: G1 signal (lane = config × bar) ─────────────────────────────────────────────
    @dev
    def g1_signal(c, t, now, prev, atr_now, i1, i2, i3, kstop, ratio):
        atr = add(atr_now[t], 0.0)
        stop = mul(kstop[c], atr)
        ready = atr > 0.0 and sub(atr, atr) == 0.0 and stop > 0.0 and sub(stop, stop) == 0.0
        if not ready:
            return False, 0.0
        a0 = prev[i1[c], t]
        a1 = now[i1[c], t]
        b0 = prev[i2[c], t]
        b1 = now[i2[c], t]
        cross = not (a0 != a0 or a1 != a1 or b0 != b0 or b1 != b1) and a0 <= b0 and a1 > b1
        slope = now[i3[c], t] > prev[i3[c], t]
        return cross and slope, stop

    def signals_body(k, n_bars, now, prev, atr_now, i1, i2, i3, kstop, ratio, entry, stopd):
        c = k // n_bars
        t = k % n_bars
        e, s = g1_signal(c, t, now, prev, atr_now, i1, i2, i3, kstop, ratio)
        entry[t, c] = 1 if e else 0
        stopd[t, c] = s

    # ── K3: spot bracket replay (thread = config), fp64 ──────────────────────────────────
    @dev
    def replay_one(
        c,
        opn,
        high,
        low,
        close,
        entry,
        stopd,
        fused,
        now,
        prev,
        atr_now,
        i1,
        i2,
        i3,
        kstop,
        ratio,
        fee,
        lot,
        pct,
        cash0,
        maxh,
        equity,
        stats,
    ):
        n = close.size
        cash = cash0
        qty = 0.0
        active = False
        a_stop = 0.0
        a_tp = 0.0
        opened_at = -1
        pend = False
        p_stop = 0.0
        p_tp = 0.0
        p_eq = 0.0
        pend_timeout = False
        denied = 0
        ambiguous = 0
        trades = 0
        one_fee = add(1.0, fee)
        one_mfee = sub(1.0, fee)
        for i in range(n):
            o = opn[i]
            if pend_timeout and qty > 0.0:
                cash = add(cash, mul(mul(qty, o), one_mfee))
                trades += 1
                qty = 0.0
                active = False
            pend_timeout = False
            if pend and qty == 0.0:
                ok = (
                    o == o
                    and abs(o) != math.inf
                    and o > 0.0
                    and p_stop > 0.0
                    and p_tp > 0.0
                    and p_stop < o
                    and o < p_tp
                )
                if ok:
                    d = sub(o, p_stop)
                    target = 0.0
                    if (
                        p_eq > 0.0
                        and close[i - 1] > 0.0
                        and d == d
                        and abs(d) != math.inf
                        and d > 0.0
                    ):
                        target = round_to_lot(div(mul(p_eq, pct), mul(d, 1.0)), lot)
                    affordable = round_to_lot(div(cash, mul(o, one_fee)), lot)
                    oq = target if target <= affordable else affordable
                    if oq > 0.0:
                        qty = oq
                        cash = sub(cash, mul(mul(qty, o), one_fee))
                        active = True
                        a_stop = p_stop
                        a_tp = p_tp
                        opened_at = i
                    else:
                        denied += 1
                else:
                    denied += 1
            pend = False
            if active and qty > 0.0:
                hit_stop = low[i] <= a_stop
                hit_tp = high[i] > a_tp
                if hit_stop:
                    if hit_tp:
                        ambiguous += 1
                    fill = a_stop if a_stop <= o else o
                    cash = add(cash, mul(mul(qty, fill), one_mfee))
                    trades += 1
                    qty = 0.0
                    active = False
                elif hit_tp:
                    cash = add(cash, mul(mul(qty, a_tp), one_mfee))
                    trades += 1
                    qty = 0.0
                    active = False
            eq = add(cash, mul(qty, close[i]))
            equity[c, i] = eq
            if active and qty > 0.0:
                if i - opened_at + 1 >= maxh and i + 1 < n:
                    pend_timeout = True
            else:
                if fused:
                    e, s = g1_signal(c, i, now, prev, atr_now, i1, i2, i3, kstop, ratio)
                else:
                    e = entry[i, c] == 1
                    s = stopd[i, c]
                if e and i + 1 < n:
                    tpd = mul(ratio, s)
                    p_stop = sub(close[i], s)
                    p_tp = add(close[i], tpd)
                    p_eq = eq
                    pend = True
        stats[c, 0] = trades
        stats[c, 1] = denied
        stats[c, 2] = ambiguous

    if target == "cpu":
        f_body = njit(features_body)
        s_body = njit(signals_body)

        @njit(parallel=True)
        def k_features(close, high, low, L, ema_n, ema_alpha, now, prev, atr_now, atr_n):
            total = (ema_n.size + 1) * close.size
            for k in prange(total):
                f_body(k, close, high, low, L, ema_n, ema_alpha, now, prev, atr_now, atr_n)

        @njit(parallel=True)
        def k_signals(now, prev, atr_now, i1, i2, i3, kstop, ratio, entry, stopd):
            n_bars = atr_now.size
            for k in prange(kstop.size * n_bars):
                s_body(k, n_bars, now, prev, atr_now, i1, i2, i3, kstop, ratio, entry, stopd)

        @njit(parallel=True)
        def k_replay(
            opn,
            high,
            low,
            close,
            entry,
            stopd,
            fused,
            now,
            prev,
            atr_now,
            i1,
            i2,
            i3,
            kstop,
            ratio,
            fee,
            lot,
            pct,
            cash0,
            maxh,
            equity,
            stats,
        ):
            for c in prange(kstop.size):
                replay_one(
                    c,
                    opn,
                    high,
                    low,
                    close,
                    entry,
                    stopd,
                    fused,
                    now,
                    prev,
                    atr_now,
                    i1,
                    i2,
                    i3,
                    kstop,
                    ratio,
                    fee,
                    lot,
                    pct,
                    cash0,
                    maxh,
                    equity,
                    stats,
                )

    else:
        f_body = cuda.jit(device=True)(features_body)
        s_body = cuda.jit(device=True)(signals_body)

        @cuda.jit
        def k_features(close, high, low, L, ema_n, ema_alpha, now, prev, atr_now, atr_n):
            k = cuda.grid(1)
            if k < (ema_n.size + 1) * close.size:
                f_body(k, close, high, low, L, ema_n, ema_alpha, now, prev, atr_now, atr_n)

        @cuda.jit
        def k_signals(now, prev, atr_now, i1, i2, i3, kstop, ratio, entry, stopd):
            k = cuda.grid(1)
            n_bars = atr_now.size
            if k < kstop.size * n_bars:
                s_body(k, n_bars, now, prev, atr_now, i1, i2, i3, kstop, ratio, entry, stopd)

        @cuda.jit
        def k_replay(
            opn,
            high,
            low,
            close,
            entry,
            stopd,
            fused,
            now,
            prev,
            atr_now,
            i1,
            i2,
            i3,
            kstop,
            ratio,
            fee,
            lot,
            pct,
            cash0,
            maxh,
            equity,
            stats,
        ):
            c = cuda.grid(1)
            if c < kstop.size:
                replay_one(
                    c,
                    opn,
                    high,
                    low,
                    close,
                    entry,
                    stopd,
                    fused,
                    now,
                    prev,
                    atr_now,
                    i1,
                    i2,
                    i3,
                    kstop,
                    ratio,
                    fee,
                    lot,
                    pct,
                    cash0,
                    maxh,
                    equity,
                    stats,
                )

    return k_features, k_signals, k_replay
