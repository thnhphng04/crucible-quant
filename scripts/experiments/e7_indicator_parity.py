"""E7 part 2: rsi, atr, zscore, rolling_max with an ITERATIVE pairwise sum (kernel-ready)."""

import sys

import numpy as np
from numba import njit

from quantcrucible.core.strategy import registry as R
from quantcrucible.core.strategy.base import Bars


@njit
def pw_block(a, lo, n):
    """numpy pairwise for n <= 128 (8 accumulators)."""
    if n < 8:
        res = -0.0
        for i in range(lo, lo + n):
            res += a[i]
        return res
    r0 = a[lo]
    r1 = a[lo + 1]
    r2 = a[lo + 2]
    r3 = a[lo + 3]
    r4 = a[lo + 4]
    r5 = a[lo + 5]
    r6 = a[lo + 6]
    r7 = a[lo + 7]
    i = 8
    stop = n - (n % 8)
    while i < stop:
        r0 += a[lo + i]
        r1 += a[lo + i + 1]
        r2 += a[lo + i + 2]
        r3 += a[lo + i + 3]
        r4 += a[lo + i + 4]
        r5 += a[lo + i + 5]
        r6 += a[lo + i + 6]
        r7 += a[lo + i + 7]
        i += 8
    res = ((r0 + r1) + (r2 + r3)) + ((r4 + r5) + (r6 + r7))
    for j in range(stop, n):
        res += a[lo + j]
    return res


@njit
def pw_sum(a, lo, n):
    """Iterative pairwise: explicit stack of (lo, n, state); combines like numpy's recursion."""
    if n <= 128:
        return pw_block(a, lo, n)
    # depth <= log2(n/128)+1; n <= 10_000 -> depth <= 8
    st_lo = np.empty(32, np.int64)
    st_n = np.empty(32, np.int64)
    st_stage = np.empty(32, np.int64)
    st_left = np.empty(32, np.float64)
    sp = 0
    st_lo[0] = lo
    st_n[0] = n
    st_stage[0] = 0
    result = 0.0
    while sp >= 0:
        cn = st_n[sp]
        if cn <= 128:
            val = pw_block(a, st_lo[sp], cn)
            sp -= 1
            # deliver val to parent
            while True:
                if sp < 0:
                    result = val
                    break
                if st_stage[sp] == 1:  # left done -> store, go right
                    st_left[sp] = val
                    st_stage[sp] = 2
                    n2 = st_n[sp] // 2
                    n2 -= n2 % 8
                    sp += 1
                    st_lo[sp] = st_lo[sp - 1] + n2
                    st_n[sp] = st_n[sp - 1] - n2
                    st_stage[sp] = 0
                    break
                # stage 2: right done
                val = st_left[sp] + val
                sp -= 1
            continue
        n2 = cn // 2
        n2 -= n2 % 8
        st_stage[sp] = 1
        sp += 1
        st_lo[sp] = st_lo[sp - 1]
        st_n[sp] = n2
        st_stage[sp] = 0
    return result


@njit
def smooth_last2(x, n, alpha):
    """(value at w-2, value at w-1) of registry._smooth over window x."""
    w = x.size
    run = 0
    start = -1
    for i in range(w):
        if np.isfinite(x[i]):
            run += 1
            if run == n:
                start = i - n + 1
                break
        else:
            run = 0
    if start < 0:
        return np.nan, np.nan
    seed = pw_sum(x, start, n) / n
    prev = seed if start + n - 1 == w - 2 else np.nan
    y = seed
    z = (1.0 - alpha) * seed
    for i in range(start + n, w):
        y = z + alpha * x[i]
        z = (1.0 - alpha) * y
        if i == w - 2:
            prev = y
    return prev, y


@njit
def rsi_last2(x, n):
    w = x.size
    if w <= n:
        return np.nan, np.nan
    d = np.empty(w - 1)
    gain = np.empty(w - 1)
    loss = np.empty(w - 1)
    for i in range(w - 1):
        d[i] = x[i + 1] - x[i]
        gain[i] = d[i] if d[i] > 0 else 0.0
        loss[i] = -d[i] if d[i] < 0 else 0.0
    gp, gn = smooth_last2(gain, n, 1.0 / n)
    lp, ln = smooth_last2(loss, n, 1.0 / n)
    out = np.empty(2)
    for k, (g, ls) in enumerate(((gp, lp), (gn, ln))):
        if not (np.isfinite(g) and np.isfinite(ls)):
            out[k] = np.nan
        elif ls == 0:
            out[k] = 100.0
        else:
            out[k] = 100.0 - 100.0 / (1.0 + g / ls)
    return out[0], out[1]


@njit
def atr_last2(h, lo, c, n):
    w = c.size
    tr = np.empty(w)
    for i in range(w):
        a = h[i] - lo[i]
        if i == 0:
            tr[i] = a  # fmax(a, nan) == a
        else:
            b = abs(h[i] - c[i - 1])
            e = abs(lo[i] - c[i - 1])
            m = b if b >= e or e != e else e
            tr[i] = a if a >= m or m != m else m
    return smooth_last2(tr, n, 1.0 / n)


@njit
def zscore_last(x, n):
    w = x.size
    m = pw_sum(x, w - n, n) / n
    sq = np.empty(n)
    for k in range(n):
        dd = x[w - n + k] - m
        sq[k] = dd * dd
    sd = np.sqrt(pw_sum(sq, 0, n) / n)
    m2 = pw_sum(x, w - n, n) / n
    if not sd > 0:
        return np.nan
    return (x[w - 1] - m2) / sd


@njit
def rmax_last(x, n):
    w = x.size
    m = x[w - n]
    for k in range(w - n + 1, w):
        v = x[k]
        if v > m or v != v:
            m = v
    return m


def main():
    rng = np.random.default_rng(int(sys.argv[1]) if len(sys.argv) > 1 else 0)
    L = 400
    n_bars = 6000
    close = 20_000 * np.exp(np.cumsum(rng.normal(0, 0.006, n_bars)))
    close[3000:3050] = close[2999]  # flat stretch: loss == 0, std == 0
    open_ = np.concatenate(([close[0]], close[:-1]))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.003, n_bars)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.003, n_bars)))
    ts = np.datetime64("2018-01-01", "ns") + np.arange(n_bars) * np.timedelta64(1, "h")
    counts = {}

    def tally(name, o, e):
        o, e = np.atleast_1d(o), np.atleast_1d(e)
        bad, tot = counts.get(name, (0, 0))
        counts[name] = (
            bad
            + int(
                (
                    ~np.array(
                        [np.array_equal(a, b, equal_nan=True) for a, b in zip(o, e, strict=True)]
                    )
                ).sum()
            ),
            tot + len(o),
        )

    for t in range(1, n_bars, 5):
        s = max(0, t - L + 1)
        c = np.ascontiguousarray(close[s : t + 1])
        bars = Bars(
            "X/USDT", "1h", ts[s : t + 1], open_[s : t + 1], high[s : t + 1], low[s : t + 1], c, c
        )
        for n in (2, 9, 14, 50, 100):
            if len(c) >= 2:
                tally(f"rsi n={n}", R.rsi(c, n)[-2:], np.array(rsi_last2(c, n)))
                tally(
                    f"atr n={n}",
                    R.atr(bars, n)[-2:],
                    np.array(
                        atr_last2(
                            np.ascontiguousarray(high[s : t + 1]),
                            np.ascontiguousarray(low[s : t + 1]),
                            c,
                            n,
                        )
                    ),
                )
        for n in (5, 20, 150, 300):
            if len(c) >= n:
                tally(f"zscore n={n}", R.zscore(c, n)[-1], zscore_last(c, n))
                tally(f"rolling_max n={n}", R.rolling_max(c, n)[-1], rmax_last(c, n))
        for n in (150, 257, 300):  # iterative pairwise beyond one block
            if len(c) >= n:
                tally(f"sma(iter pw) n={n}", R.sma(c, n)[-1], pw_sum(c, len(c) - n, n) / n)
    worst = 0
    for k, (bad, tot) in sorted(counts.items()):
        worst = max(worst, bad)
        print(f"  {k:24} mismatches {bad}/{tot}")
    print("ALL EXACT" if worst == 0 else "MISMATCHES PRESENT")


if __name__ == "__main__":
    main()
