"""Signal kernel K2: the rendered ``signal()`` for every (configuration, bar) (P3-39, ADR-0038).

It reproduces what the template's ``signal()`` computes from the features at bar ``t``::

    atr = x["atr"] + 0
    stop = k_stop * atr                     # or k_stop * (close - boll_lower(8)), mirrored short
                                            # with n_stop (ADR-0041): x["atr_stop"], boll(n_stop)
    ready = atr > 0 and atr - atr == 0 and stop > 0 and stop - stop == 0
    if ready and (<entry clauses>):
        return Signal(direction, 1.0, stop, <tp>)

The clauses are evaluated only when ``ready`` holds, as Python's ``and`` short-circuits — so a
Distance clause never divides by a zero ATR. Comparisons with NaN are false, as in Python, and
``cross_up``/``cross_down`` are false whenever one of their four values is NaN. The output is an
entry flag and the stop distance per bar and configuration, laid out ``[bar, config]`` so the
replay threads read consecutive configurations together.
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
    cuda,
    cuda_kernel,
    device,
    prange,
)
from quantcrucible.execution.kernels.features import KernelInputError
from quantcrucible.execution.kernels.program import Program

CUDA_LANES_PER_LAUNCH = 1 << 22
THREADS = 128


@cache
def _build(target: Target, dtype: Dtype) -> tuple[Any, Any]:
    a = arith(target, dtype)
    T, sub, mul, div = a.T, a.sub, a.mul, a.div
    ZERO = T(0.0)
    dev = device(target)

    @dev
    def cmp(u: Any, v: Any, flag: int) -> Any:
        return u > v if flag == P.CMP_GT else u < v

    @dev
    def cross(a0: Any, a1: Any, b0: Any, b1: Any, up: int) -> Any:
        if a0 != a0 or a1 != a1 or b0 != b0 or b1 != b1:
            return False
        return (a0 <= b0 and a1 > b1) if up == 1 else (a0 >= b0 and a1 < b1)

    @dev
    def clause(k: int, c: int, t: int, cl: Any, si: Any, pv: Any, now: Any, prev: Any,
               atr: Any) -> Any:  # fmt: skip
        kind = cl[k, 0]
        ia = si[c, cl[k, 1]] if cl[k, 1] >= 0 else 0
        ib = si[c, cl[k, 2]] if cl[k, 2] >= 0 else 0
        flag = cl[k, 4]
        lvl = pv[c, cl[k, 3]] if cl[k, 3] >= 0 else ZERO
        if kind == P.CL_COMPARE:
            return cmp(now[ia, t], now[ib, t], flag)
        if kind == P.CL_CROSS:
            return cross(prev[ia, t], now[ia, t], prev[ib, t], now[ib, t], flag)
        if kind == P.CL_THRESHOLD:
            return cmp(now[ia, t], lvl, flag)
        if kind == P.CL_CROSSLEVEL:
            return cross(prev[ia, t], now[ia, t], lvl, lvl, flag)
        if kind == P.CL_DISTANCE:
            return cmp(div(sub(now[ia, t], now[ib, t]), atr), lvl, flag)
        if kind == P.CL_BREAKOUT:  # close now against the level's previous bar
            return now[ia, t] > prev[ib, t] if flag == 1 else now[ia, t] < prev[ib, t]
        # CL_SLOPE
        return now[ia, t] > prev[ia, t] if flag == 1 else now[ia, t] < prev[ia, t]

    def body(k: int, n_bars: int, cl: Any, si: Any, pv: Any, now: Any, prev: Any, meta: Any,
             entry: Any, stop_out: Any, err: Any) -> None:  # fmt: skip
        c = k // n_bars
        t = k % n_bars
        combine, close_slot, atr_slot, band_slot, stop_col, direction, stop_slot = (
            meta[0], meta[1], meta[2], meta[3], meta[4], meta[5], meta[6],
        )  # fmt: skip
        entry[t, c] = 0
        stop_out[t, c] = ZERO
        atr = now[si[c, atr_slot], t]
        if band_slot < 0:
            stop = mul(pv[c, stop_col], now[si[c, stop_slot], t])
        else:
            close = now[si[c, close_slot], t]
            band = now[si[c, band_slot], t]
            dist = sub(close, band) if direction == 1 else sub(band, close)
            stop = mul(pv[c, stop_col], dist)
        ready = atr > ZERO and sub(atr, atr) == ZERO and stop > ZERO and sub(stop, stop) == ZERO
        if not ready:
            return
        n_cl = cl.shape[0]
        if combine == P.COMBINE_OR:
            hit = False
            for j in range(n_cl):
                if clause(j, c, t, cl, si, pv, now, prev, atr):
                    hit = True
                    break
        else:  # a single clause, or `and`
            hit = True
            for j in range(n_cl):
                if not clause(j, c, t, cl, si, pv, now, prev, atr):
                    hit = False
                    break
        if hit:
            entry[t, c] = 1
            stop_out[t, c] = stop

    lane = dev(body)
    if target == "cpu":

        def cpu(n_bars: int, cl: Any, si: Any, pv: Any, now: Any, prev: Any, meta: Any,
                entry: Any, stop_out: Any, err: Any) -> None:  # fmt: skip
            for k in prange(si.shape[0] * n_bars):
                lane(k, n_bars, cl, si, pv, now, prev, meta, entry, stop_out, err)

        return cpu_kernel(cpu), None

    def gpu(n_bars: int, cl: Any, si: Any, pv: Any, now: Any, prev: Any, meta: Any, entry: Any,
            stop_out: Any, err: Any, offset: int, count: int) -> None:  # fmt: skip
        k = offset + cuda.grid(1)
        if k < count:
            lane(k, n_bars, cl, si, pv, now, prev, meta, entry, stop_out, err)

    return None, cuda_kernel(gpu)


Array = npt.NDArray[Any]


def _meta(prog: Program) -> npt.NDArray[np.int64]:
    return np.asarray(
        [prog.combine, prog.close_slot, prog.atr_slot, prog.band_slot, prog.stop_col,
         prog.direction, prog.stop_slot],
        dtype=np.int64,
    )  # fmt: skip


def run_signals(
    prog: Program,
    now: Array,
    prev: Array,
    target: Target = "cpu",
    dtype: Dtype = "float64",
) -> tuple[npt.NDArray[np.int8], Array]:
    """``(entry, stop)``, each ``[bars, configs]``; ``stop`` is 0 where there is no entry."""
    t = np.float64 if dtype == "float64" else np.float32
    if now.shape != prev.shape or now.ndim != 2 or now.shape[0] != prog.inst_op.size:
        raise KernelInputError("feature tables do not match the program's instances")
    if now.dtype != t or prev.dtype != t:
        raise KernelInputError(f"features must be {dtype}")
    n_bars, m = now.shape[1], prog.n_configs
    pv = np.ascontiguousarray(prog.pvals.astype(t))
    cl = np.ascontiguousarray(prog.clauses)
    si = np.ascontiguousarray(prog.slot_inst)
    meta = _meta(prog)
    err = np.zeros(1, dtype=np.int64)
    cpu, gpu = _build(target, dtype)
    if target == "cpu":
        entry = np.zeros((n_bars, m), dtype=np.int8)
        stop = np.zeros((n_bars, m), dtype=t)
        cpu(n_bars, cl, si, pv, now, prev, meta, entry, stop, err)
        return entry, stop
    with DEVICE_LOCK:
        d = [cuda.to_device(v) for v in (cl, si, pv, now, prev, meta)]
        d_entry = cuda.device_array((n_bars, m), dtype=np.int8)
        d_stop = cuda.device_array((n_bars, m), dtype=t)
        d_err = cuda.to_device(err)
        count = m * n_bars
        for offset in range(0, count, CUDA_LANES_PER_LAUNCH):
            lanes = min(CUDA_LANES_PER_LAUNCH, count - offset)
            blocks = math.ceil(lanes / THREADS)
            gpu[blocks, THREADS](n_bars, *d, d_entry, d_stop, d_err, offset, count)
            cuda.synchronize()
        return d_entry.copy_to_host(), d_stop.copy_to_host()
