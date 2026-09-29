"""E7: which summation / recursion order reproduces registry.py bit for bit (fp64)?

Oracle: the actual registry functions applied to each lookback window, exactly as step() does.
Candidates are njit emulations; the winner becomes the kernel's numerics spec.
"""

import sys

import numpy as np
from numba import njit

from quantcrucible.core.strategy import registry as R

PW_BLOCK = 128


@njit
def seq_sum(a, lo, n):
    s = 0.0
    for i in range(lo, lo + n):
        s += a[i]
    return s


@njit
def pw_sum(a, lo, n):
    """numpy's DOUBLE_pairwise_sum (8 accumulators, block 128, recursive halves)."""
    if n < 8:
        res = -0.0
        for i in range(lo, lo + n):
            res += a[i]
        return res
    if n <= PW_BLOCK:
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
    n2 = n // 2
    n2 -= n2 % 8
    return pw_sum(a, lo, n2) + pw_sum(a, lo + n2, n - n2)


@njit
def reduce_sum(a, lo, n, mode):
    """mode 0: sequential from 0; 1: pairwise from -0.0 init; 2: first element + pairwise(rest);
    3: 0.0 + pairwise(all)."""
    if mode == 0:
        return seq_sum(a, lo, n)
    if mode == 1:
        return pw_sum(a, lo, n)
    if mode == 2:
        return a[lo] + pw_sum(a, lo + 1, n - 1)
    return 0.0 + pw_sum(a, lo, n)


@njit
def sma_last(x, n, mode):
    w = x.size
    return reduce_sum(x, w - n, n, mode) / n


@njit
def std_last(x, n, mode_mean, mode_sq):
    w = x.size
    m = reduce_sum(x, w - n, n, mode_mean) / n
    sq = np.empty(n)
    for k in range(n):
        d = x[w - n + k] - m
        sq[k] = d * d
    return m, np.sqrt(reduce_sum(sq, 0, n, mode_sq) / n)


@njit
def ema_last(x, n, alpha, mode_seed):
    """_smooth: seed = mean of first run of n finite values, then y = (1-a)*y + a*x."""
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
    seed = reduce_sum(x, start, n, mode_seed) / n
    prev = np.nan
    y = seed
    if start + n - 1 == w - 2:
        prev = seed
    z = (1.0 - alpha) * seed
    for i in range(start + n, w):
        y = z + alpha * x[i]
        z = (1.0 - alpha) * y
        if i == w - 2:
            prev = y
    return prev, y


def windows(x, L, stride):
    for t in range(0, len(x), stride):
        yield x[max(0, t - L + 1) : t + 1]


def check(name, oracle, emul, cases):
    bad = 0
    total = 0
    for args in cases:
        o = oracle(*args)
        e = emul(*args)
        for ov, ev in zip(np.atleast_1d(o), np.atleast_1d(e), strict=True):
            total += 1
            if not (np.array_equal(np.float64(ov), np.float64(ev), equal_nan=True)):
                bad += 1
    print(f"  {name:38} mismatches {bad}/{total}")
    return bad


def main():
    rng = np.random.default_rng(int(sys.argv[1]) if len(sys.argv) > 1 else 0)
    L = 400
    series = {
        "walk": 20_000 * np.exp(np.cumsum(rng.normal(0, 0.006, 6000))),
        "small": 0.05 * np.exp(np.cumsum(rng.normal(0, 0.01, 6000))),
        "flat": np.full(3000, 123.456),
        "osc": 50 + 20 * np.sin(np.arange(4000) / 7.0) + rng.normal(0, 1, 4000),
    }
    periods = [2, 3, 5, 7, 8, 9, 14, 16, 20, 31, 50, 64, 100, 127, 128, 129, 200, 257, 300]
    for sname, x in series.items():
        print(f"series {sname}")
        wins = [w for w in windows(x, L, 7) if len(w) >= 2]
        for mode, label in (
            (0, "seq"),
            (1, "pairwise(-0)"),
            (2, "first+pairwise"),
            (3, "0+pairwise"),
        ):
            cases = [(w, n) for w in wins for n in periods if len(w) >= n]
            check(
                f"sma {label}",
                lambda w, n: R.sma(w, n)[-1],
                lambda w, n, m=mode: sma_last(np.ascontiguousarray(w), n, m),
                cases,
            )
        for mode in (0, 1, 2, 3):
            for msq in (1, 2):
                cases = [(w, n) for w in wins for n in (5, 8, 20, 30, 64, 150, 300) if len(w) >= n]

                def emul(w, n, m=mode, q=msq):
                    mean, sd = std_last(np.ascontiguousarray(w), n, m, q)
                    return mean + 2.0 * sd

                check(
                    f"boll_upper mean{mode} sq{msq}",
                    lambda w, n: R.boll_upper(w, n)[-1],
                    emul,
                    cases,
                )
        for mode in (0, 1, 2, 3):
            cases = [(w, n) for w in wins for n in (2, 9, 14, 20, 50, 128, 200) if len(w) >= 2]
            check(
                f"ema prev/now seed{mode}",
                lambda w, n: R.ema(w, n)[-2:],
                lambda w, n, m=mode: np.array(
                    ema_last(np.ascontiguousarray(w), n, 2.0 / (n + 1.0), m)
                ),
                cases,
            )


if __name__ == "__main__":
    main()
