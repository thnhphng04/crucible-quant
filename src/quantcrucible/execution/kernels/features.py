"""Feature kernel K1: every registry indicator on every lookback window (P3-38, ADR-0038).

``step()`` rebuilds each indicator on ``bars.window(t, L)`` at every bar; a strategy reads the
last value (``now``) and, through ``.ago(1)``, the value one bar earlier **in the same window**
(``prev``). One lane computes both for one (indicator instance, bar), redoing the window in
O(L) — that is what makes the seeded recursions (ema, rsi, atr) exact rather than approximately
equal. The arithmetic reproduces ``core/strategy/registry.py`` bit for bit in float64 (E7):
numpy's pairwise summation for every sum and mean, scipy ``lfilter``'s step for the smoothing.

Inputs must be finite (the host checks): with finite bars every window's first valid run starts at
the window start, which the smoothing relies on.
"""

from __future__ import annotations

import math
from functools import cache
from typing import Any

import numpy as np
import numpy.typing as npt

from quantcrucible.execution.kernels import program as P
from quantcrucible.execution.kernels._numba import (
    DEVICE_LOCK,
    Dtype,
    Target,
    arith,
    cpu_kernel,
    cuda,  # kernels call cuda.grid: the simulator swaps this module object per thread
    cuda_kernel,
    device,
    prange,
)

CUDA_LANES_PER_LAUNCH = 1 << 20  # keeps one launch well under the WDDM watchdog (O(L) per lane)
THREADS = 128

# value sources for the pairwise sum and the smoothing
V_X, V_SQDEV, V_TR, V_GAIN, V_LOSS = 0, 1, 2, 3, 4


class KernelInputError(ValueError):
    """The bars or tables cannot be computed exactly by the kernels."""


@cache
def _build(target: Target, dtype: Dtype) -> tuple[Any, Any]:
    a = arith(target, dtype)
    T, add, sub, mul, div, sqrt, NAN, INF = a.T, a.add, a.sub, a.mul, a.div, a.sqrt, a.nan, a.inf
    ZERO, ONE, TWO, HUNDRED, NEG_ZERO = T(0.0), T(1.0), T(2.0), T(100.0), T(-0.0)
    dev = device(target)

    @dev
    def val(x: Any, h: Any, lo: Any, i: int, m: Any, mode: int, wlo: int) -> Any:
        if mode == V_X:
            return x[i]
        if mode == V_SQDEV:
            d = sub(x[i], m)
            return mul(d, d)
        if mode == V_TR:  # true range, NaN previous close at the window's first bar
            r = sub(h[i], lo[i])
            if i == wlo:
                return r
            b = abs(sub(h[i], x[i - 1]))
            e = abs(sub(lo[i], x[i - 1]))
            mm = b if (b >= e or e != e) else e
            return r if (r >= mm or mm != mm) else mm
        d = sub(x[i + 1], x[i])  # gain / loss of the delta starting at bar i
        if mode == V_GAIN:
            return d if d > ZERO else ZERO
        return -d if d < ZERO else ZERO

    @dev
    def pw_block(x: Any, h: Any, lo: Any, s: int, n: int, m: Any, mode: int, wlo: int) -> Any:
        """numpy's pairwise sum for n <= 128: eight accumulators, then the remainder."""
        if n < 8:
            res = NEG_ZERO
            for i in range(s, s + n):
                res = add(res, val(x, h, lo, i, m, mode, wlo))
            return res
        r0 = val(x, h, lo, s, m, mode, wlo)
        r1 = val(x, h, lo, s + 1, m, mode, wlo)
        r2 = val(x, h, lo, s + 2, m, mode, wlo)
        r3 = val(x, h, lo, s + 3, m, mode, wlo)
        r4 = val(x, h, lo, s + 4, m, mode, wlo)
        r5 = val(x, h, lo, s + 5, m, mode, wlo)
        r6 = val(x, h, lo, s + 6, m, mode, wlo)
        r7 = val(x, h, lo, s + 7, m, mode, wlo)
        stop = n - (n % 8)
        i = 8
        while i < stop:
            r0 = add(r0, val(x, h, lo, s + i, m, mode, wlo))
            r1 = add(r1, val(x, h, lo, s + i + 1, m, mode, wlo))
            r2 = add(r2, val(x, h, lo, s + i + 2, m, mode, wlo))
            r3 = add(r3, val(x, h, lo, s + i + 3, m, mode, wlo))
            r4 = add(r4, val(x, h, lo, s + i + 4, m, mode, wlo))
            r5 = add(r5, val(x, h, lo, s + i + 5, m, mode, wlo))
            r6 = add(r6, val(x, h, lo, s + i + 6, m, mode, wlo))
            r7 = add(r7, val(x, h, lo, s + i + 7, m, mode, wlo))
            i += 8
        res = add(add(add(r0, r1), add(r2, r3)), add(add(r4, r5), add(r6, r7)))
        for j in range(stop, n):
            res = add(res, val(x, h, lo, s + j, m, mode, wlo))
        return res

    # numpy recurses on halves rounded down to a multiple of 8; no recursion here (a cached
    # recursive njit segfaults, E7), so three fixed levels: exact up to P.MAX_PERIOD elements.
    @dev
    def pw1(x: Any, h: Any, lo: Any, s: int, n: int, m: Any, mode: int, wlo: int) -> Any:
        if n <= 128:
            return pw_block(x, h, lo, s, n, m, mode, wlo)
        n2 = n // 2
        n2 -= n2 % 8
        left = pw_block(x, h, lo, s, n2, m, mode, wlo)
        return add(left, pw_block(x, h, lo, s + n2, n - n2, m, mode, wlo))

    @dev
    def pw2(x: Any, h: Any, lo: Any, s: int, n: int, m: Any, mode: int, wlo: int) -> Any:
        if n <= 128:
            return pw_block(x, h, lo, s, n, m, mode, wlo)
        n2 = n // 2
        n2 -= n2 % 8
        return add(pw1(x, h, lo, s, n2, m, mode, wlo), pw1(x, h, lo, s + n2, n - n2, m, mode, wlo))

    @dev
    def pw_sum(x: Any, h: Any, lo: Any, s: int, n: int, m: Any, mode: int, wlo: int) -> Any:
        if n <= 128:
            return pw_block(x, h, lo, s, n, m, mode, wlo)
        n2 = n // 2
        n2 -= n2 % 8
        return add(pw2(x, h, lo, s, n2, m, mode, wlo), pw2(x, h, lo, s + n2, n - n2, m, mode, wlo))

    @dev
    def smooth2(
        x: Any, h: Any, lo: Any, s: int, cnt: int, n: int, alpha: Any, mode: int, wlo: int
    ) -> Any:
        """(value at cnt-2, value at cnt-1) of registry._smooth over ``cnt`` elements from s."""
        if cnt < n:
            return NAN, NAN
        seed = div(pw_sum(x, h, lo, s, n, ZERO, mode, wlo), T(n))
        prev = seed if n == cnt - 1 else NAN
        y = seed
        one_a = sub(ONE, alpha)
        z = mul(one_a, seed)
        for k in range(n, cnt):
            y = add(z, mul(alpha, val(x, h, lo, s + k, ZERO, mode, wlo)))
            z = mul(one_a, y)
            if k == cnt - 2:
                prev = y
        return prev, y

    @dev
    def mean_std(x: Any, s: int, n: int) -> Any:
        m = div(pw_sum(x, x, x, s, n, ZERO, V_X, 0), T(n))
        return m, sqrt(div(pw_sum(x, x, x, s, n, m, V_SQDEV, 0), T(n)))

    @dev
    def extreme(x: Any, s: int, n: int, top: bool) -> Any:
        e = x[s]
        for i in range(s + 1, s + n):
            v = x[i]
            if v != v or e != e:
                e = NAN
            elif (v > e) if top else (v < e):
                e = v
        return e

    @dev
    def rsi_value(g: Any, lo_: Any) -> Any:
        if g != g or lo_ != lo_ or abs(g) == INF or abs(lo_) == INF:
            return NAN
        if lo_ == ZERO:
            return HUNDRED
        return sub(HUNDRED, div(HUNDRED, add(ONE, div(g, lo_))))

    @dev
    def at(op: int, n: int, x: Any, h: Any, lo: Any, e: int, wlo: int) -> Any:
        """The value at bar ``e`` of op(n) computed on the window [wlo, e]; NaN during warm-up."""
        w = e - wlo + 1
        if op == P.OP_CLOSE:
            return x[e]
        if w < n:
            return NAN
        if op == P.OP_SMA:
            return div(pw_sum(x, h, lo, e - n + 1, n, ZERO, V_X, wlo), T(n))
        if op == P.OP_RMAX:
            return extreme(x, e - n + 1, n, True)
        if op == P.OP_RMIN:
            return extreme(x, e - n + 1, n, False)
        m, sd = mean_std(x, e - n + 1, n)
        if op == P.OP_ZSCORE:
            return div(sub(x[e], m), sd) if sd > ZERO else NAN
        if op == P.OP_BOLL_UPPER:
            return add(m, mul(TWO, sd))
        return sub(m, mul(TWO, sd))  # OP_BOLL_LOWER

    @dev
    def feature(op: int, n: int, x: Any, h: Any, lo: Any, t: int, lookback: int) -> Any:
        wlo = t - lookback + 1 if t - lookback + 1 > 0 else 0
        w = t - wlo + 1
        if op == P.OP_EMA:
            return smooth2(x, h, lo, wlo, w, n, div(TWO, add(T(n), ONE)), V_X, wlo)
        if op == P.OP_ATR:
            return smooth2(x, h, lo, wlo, w, n, div(ONE, T(n)), V_TR, wlo)
        if op == P.OP_RSI:
            if w <= n:
                return NAN, NAN
            alpha = div(ONE, T(n))
            gp, gn = smooth2(x, h, lo, wlo, w - 1, n, alpha, V_GAIN, wlo)
            lp, ln = smooth2(x, h, lo, wlo, w - 1, n, alpha, V_LOSS, wlo)
            return rsi_value(gp, lp), rsi_value(gn, ln)
        now = at(op, n, x, h, lo, t, wlo)
        prev = at(op, n, x, h, lo, t - 1, wlo) if w >= 2 else NAN
        return prev, now

    def body(k: int, x: Any, h: Any, lo: Any, ops: Any, pers: Any, L: int, now: Any, prev: Any,
             err: Any) -> None:  # fmt: skip
        n_bars = x.size
        inst = k // n_bars
        t = k % n_bars
        op = ops[inst]
        n = pers[inst]
        if op < P.OP_CLOSE or op > P.OP_ATR or n < 0 or n > P.MAX_PERIOD or (n == 0 and op != 0):
            err[0] = 1  # defence in depth (INV-107): the program never produces this
            prev[inst, t] = NAN
            now[inst, t] = NAN
            return
        p, q = feature(op, n, x, h, lo, t, L)
        prev[inst, t] = p
        now[inst, t] = q

    lane = dev(body)
    if target == "cpu":

        def cpu(x: Any, h: Any, lo: Any, ops: Any, pers: Any, L: int, now: Any, prev: Any,
                err: Any) -> None:  # fmt: skip
            for k in prange(ops.size * x.size):
                lane(k, x, h, lo, ops, pers, L, now, prev, err)

        return cpu_kernel(cpu), None

    def gpu(x: Any, h: Any, lo: Any, ops: Any, pers: Any, L: int, now: Any, prev: Any, err: Any,
            offset: int, count: int) -> None:  # fmt: skip
        k = offset + cuda.grid(1)
        if k < count:
            lane(k, x, h, lo, ops, pers, L, now, prev, err)

    return None, cuda_kernel(gpu)


Array = npt.NDArray[Any]


def _inputs(
    close: npt.ArrayLike, high: npt.ArrayLike, low: npt.ArrayLike, dtype: Dtype
) -> tuple[Array, Array, Array]:
    t = np.float64 if dtype == "float64" else np.float32
    out = tuple(np.ascontiguousarray(np.asarray(v, dtype=np.float64)) for v in (close, high, low))
    if not all(np.isfinite(v).all() for v in out):
        raise KernelInputError("bars must be finite for the kernel engine")
    if not (out[0].shape == out[1].shape == out[2].shape and out[0].ndim == 1 and out[0].size):
        raise KernelInputError("close, high and low must be equal-length, non-empty 1-D arrays")
    c, h, lo = (v.astype(t) for v in out)
    return c, h, lo


def run_features(
    inst_op: npt.ArrayLike,
    inst_period: npt.ArrayLike,
    close: npt.ArrayLike,
    high: npt.ArrayLike,
    low: npt.ArrayLike,
    lookback: int,
    target: Target = "cpu",
    dtype: Dtype = "float64",
) -> tuple[Array, Array]:
    """``(now, prev)``, each ``[instances, bars]`` in ``dtype``."""
    x, h, lo = _inputs(close, high, low, dtype)
    ops = np.ascontiguousarray(np.asarray(inst_op, dtype=np.int64))
    pers = np.ascontiguousarray(np.asarray(inst_period, dtype=np.int64))
    if ops.ndim != 1 or ops.shape != pers.shape or not ops.size:
        raise KernelInputError("instance tables must be equal-length, non-empty 1-D arrays")
    if not 2 <= lookback <= P.MAX_LOOKBACK:
        raise KernelInputError("lookback out of range")
    shape = (ops.size, x.size)
    err = np.zeros(1, dtype=np.int64)
    cpu, gpu = _build(target, dtype)
    if target == "cpu":
        now, prev = np.empty(shape, x.dtype), np.empty(shape, x.dtype)
        cpu(x, h, lo, ops, pers, lookback, now, prev, err)
    else:
        with DEVICE_LOCK:
            d = [cuda.to_device(v) for v in (x, h, lo, ops, pers)]
            d_now, d_prev = cuda.device_array(shape, x.dtype), cuda.device_array(shape, x.dtype)
            d_err = cuda.to_device(err)
            count = ops.size * x.size
            for offset in range(0, count, CUDA_LANES_PER_LAUNCH):
                lanes = min(CUDA_LANES_PER_LAUNCH, count - offset)
                blocks = math.ceil(lanes / THREADS)
                gpu[blocks, THREADS](*d, lookback, d_now, d_prev, d_err, offset, count)
                cuda.synchronize()
            now, prev, err = d_now.copy_to_host(), d_prev.copy_to_host(), d_err.copy_to_host()
    if err[0]:
        raise KernelInputError("the feature kernel met an instance outside its tables")
    return now, prev
