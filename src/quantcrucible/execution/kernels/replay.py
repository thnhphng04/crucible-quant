"""Spot replay kernel K3: ``bracket_timeout_v1`` on one account per configuration (P3-40).

One thread walks the bars for one configuration, reproducing ``execution/engine.py``
``_run_spot_bracket`` operation by operation, always in float64 — even in a float32 campaign the
account's cash, quantity and equity never lose precision (ADR-0039). Per bar:

1. a pending timeout sells at the open;
2. a pending entry buys at the open if the bracket still accepts it, sized ``R / (open - stop)``
   from the signal bar's equity and rounded down to the lot, capped by what the cash affords;
3. the open position's stop (worse fill on a gap) or take-profit (trade-through) resolves,
   the stop winning an ambiguous bar;
4. equity is marked to the close;
5. a timeout or a new entry is scheduled from this bar's signal.

Spot is long-only: a short scope's signals never trade, exactly as in the Python replay. Gate ③
asks for the trade log, from which the host rebuilds fills, exits and stops; gate ④ needs only the
equity rows, the trade count and the integer sum of holding bars.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import cache
from typing import Any

import numpy as np
import numpy.typing as npt

from quantcrucible.execution.kernels._numba import (
    arith,
    cpu_kernel,
    cuda,
    cuda_kernel,
    device,
    prange,
)
from quantcrucible.execution.kernels.features import KernelInputError

THREADS = 32
# trade log columns
L_ENTRY_BAR, L_EXIT_BAR, L_REASON = 0, 1, 2  # int log
L_QTY, L_ENTRY_PX, L_EXIT_PX, L_STOP = 0, 1, 2, 3  # float log
R_STOP, R_TP, R_TIMEOUT = 0, 1, 2
REASONS = {R_STOP: "stop", R_TP: "take_profit", R_TIMEOUT: "timeout"}
# per-configuration counters
S_TRADES, S_DENIED, S_AMBIGUOUS, S_HOLD_SUM, S_LOGGED, S_OVERFLOW = 0, 1, 2, 3, 4, 5
N_STATS = 6


@cache
def _build(target: str) -> tuple[Any, Any]:
    a = arith(target, "float64")  # type: ignore[arg-type]
    add, sub, mul, div = a.add, a.sub, a.mul, a.div
    dev = device(target)  # type: ignore[arg-type]
    INF = math.inf

    @dev
    def round_to_lot(q: Any, lot: Any) -> Any:
        """``position_sizer.round_to_lot``: floor to the lot (never round risk up), 0 if empty."""
        if q != q or abs(q) == INF or q <= 0.0:
            return 0.0
        if lot > 0.0:
            q = mul(float(math.floor(add(div(q, lot), 1e-9))), lot)
        return q if q > 0.0 else 0.0

    @dev
    def finite_pos(v: Any) -> Any:
        return v == v and abs(v) != INF and v > 0.0

    def body(
        c: int, opn: Any, high: Any, low: Any, close: Any, entry: Any, stopd: Any,
        long_only_side: int, ratio: float, fee: float, lot: float, pct: float,
        cash0: float, maxh: int, equity: Any, stats: Any, ilog: Any, flog: Any,
    ) -> None:  # fmt: skip
        n = close.size
        cap = ilog.shape[1]
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
        trades = 0
        denied = 0
        ambiguous = 0
        hold_sum = 0
        logged = 0
        overflow = 0
        one_fee = add(1.0, fee)
        one_mfee = sub(1.0, fee)
        for i in range(n):
            o = opn[i]
            if pend_timeout and qty > 0.0:
                cash = add(cash, mul(mul(qty, o), one_mfee))
                hold_sum += i - opened_at
                if logged - 1 < cap and logged > 0:
                    ilog[c, logged - 1, L_EXIT_BAR] = i
                    ilog[c, logged - 1, L_REASON] = R_TIMEOUT
                    flog[c, logged - 1, L_EXIT_PX] = o
                trades += 1
                qty = 0.0
                active = False
            pend_timeout = False
            if pend and qty == 0.0:
                ok = finite_pos(o) and finite_pos(p_stop) and finite_pos(p_tp)
                if ok and p_stop < o and o < p_tp:
                    d = sub(o, p_stop)
                    target = 0.0
                    if p_eq > 0.0 and close[i - 1] > 0.0:
                        den = mul(d, 1.0)
                        if den == den and abs(den) != INF and den > 0.0:
                            target = round_to_lot(div(mul(p_eq, pct), den), lot)
                    affordable = round_to_lot(div(cash, mul(o, one_fee)), lot)
                    oq = target if target <= affordable else affordable
                    if oq > 0.0:
                        qty = oq
                        cash = sub(cash, mul(mul(qty, o), one_fee))
                        active = True
                        a_stop = p_stop
                        a_tp = p_tp
                        opened_at = i
                        if logged < cap:
                            ilog[c, logged, L_ENTRY_BAR] = i
                            ilog[c, logged, L_EXIT_BAR] = -1
                            ilog[c, logged, L_REASON] = -1
                            flog[c, logged, L_QTY] = qty
                            flog[c, logged, L_ENTRY_PX] = o
                            flog[c, logged, L_STOP] = p_stop
                        else:
                            overflow = 1
                        logged += 1
                    else:
                        denied += 1
                else:
                    denied += 1
            pend = False
            if active and qty > 0.0:
                hit_stop = low[i] <= a_stop
                hit_tp = high[i] > a_tp
                if hit_stop or hit_tp:
                    if hit_stop:
                        ambiguous += 1 if hit_tp else 0
                        fill = a_stop if a_stop <= o else o
                        reason = R_STOP
                    else:
                        fill = a_tp
                        reason = R_TP
                    cash = add(cash, mul(mul(qty, fill), one_mfee))
                    hold_sum += i - opened_at + 1
                    if logged - 1 < cap:
                        ilog[c, logged - 1, L_EXIT_BAR] = i
                        ilog[c, logged - 1, L_REASON] = reason
                        flog[c, logged - 1, L_EXIT_PX] = fill
                    trades += 1
                    qty = 0.0
                    active = False
            eq = add(cash, mul(qty, close[i]))
            equity[c, i] = eq
            if active and qty > 0.0:
                if i - opened_at + 1 >= maxh and i + 1 < n:
                    pend_timeout = True
            elif long_only_side == 1 and entry[i, c] == 1 and i + 1 < n:
                s = stopd[i, c]
                p_stop = sub(close[i], s)
                p_tp = add(close[i], mul(ratio, s))
                p_eq = eq
                pend = True
        stats[c, S_TRADES] = trades
        stats[c, S_DENIED] = denied
        stats[c, S_AMBIGUOUS] = ambiguous
        stats[c, S_HOLD_SUM] = hold_sum
        stats[c, S_LOGGED] = logged
        stats[c, S_OVERFLOW] = overflow

    one = dev(body)
    if target == "cpu":

        def cpu(opn: Any, high: Any, low: Any, close: Any, entry: Any, stopd: Any, side: int,
                ratio: float, fee: float, lot: float, pct: float, cash0: float, maxh: int,
                equity: Any, stats: Any, ilog: Any, flog: Any) -> None:  # fmt: skip
            for c in prange(entry.shape[1]):
                one(c, opn, high, low, close, entry, stopd, side, ratio, fee, lot, pct, cash0,
                    maxh, equity, stats, ilog, flog)  # fmt: skip

        return cpu_kernel(cpu), None

    def gpu(opn: Any, high: Any, low: Any, close: Any, entry: Any, stopd: Any, side: int,
            ratio: float, fee: float, lot: float, pct: float, cash0: float, maxh: int,
            equity: Any, stats: Any, ilog: Any, flog: Any) -> None:  # fmt: skip
        c = cuda.grid(1)
        if c < entry.shape[1]:
            one(c, opn, high, low, close, entry, stopd, side, ratio, fee, lot, pct, cash0, maxh,
                equity, stats, ilog, flog)  # fmt: skip

    return None, cuda_kernel(gpu)


@dataclass(frozen=True)
class SpotReplay:
    """Per configuration: the equity curve, counters and (if asked) the trade log."""

    equity: npt.NDArray[np.float64]  # [configs, bars]
    trades: npt.NDArray[np.int64]
    denied: npt.NDArray[np.int64]
    ambiguous: npt.NDArray[np.int64]
    hold_sum: npt.NDArray[np.int64]  # integer sum of bars held, for np.mean(holding)
    ilog: npt.NDArray[np.int64] | None  # [configs, trades, (entry, exit, reason)]
    flog: npt.NDArray[np.float64] | None  # [configs, trades, (qty, entry px, exit px, stop)]
    logged: npt.NDArray[np.int64]


def run_spot_replay(
    open_: npt.ArrayLike,
    high: npt.ArrayLike,
    low: npt.ArrayLike,
    close: npt.ArrayLike,
    entry: npt.NDArray[np.int8],
    stop: npt.NDArray[Any],
    *,
    direction: int,
    tp_sl_ratio: float,
    fee: float,
    lot_step: float,
    max_risk_pct: float,
    initial_cash: float,
    max_holding_bars: int,
    trade_log: bool,
    target: str = "cpu",
) -> SpotReplay:
    bars = [
        np.ascontiguousarray(np.asarray(v, dtype=np.float64)) for v in (open_, high, low, close)
    ]
    n = bars[3].size
    if any(b.shape != (n,) for b in bars) or not n:
        raise KernelInputError("open, high, low and close must be equal-length 1-D arrays")
    if not all(np.isfinite(b).all() for b in bars):
        raise KernelInputError("bars must be finite for the kernel engine")
    if entry.ndim != 2 or entry.shape[0] != n or stop.shape != entry.shape:
        raise KernelInputError("signals must be [bars, configs]")
    if max_holding_bars < 1:
        raise KernelInputError("max_holding_bars must be positive")
    m = entry.shape[1]
    stop64 = np.ascontiguousarray(stop.astype(np.float64))  # fp32 signals widen exactly
    entry = np.ascontiguousarray(entry, dtype=np.int8)
    cap = n // 2 + 2 if trade_log else 1
    equity = np.empty((m, n), dtype=np.float64)
    stats = np.zeros((m, N_STATS), dtype=np.int64)
    ilog = np.zeros((m, cap, 3), dtype=np.int64)
    flog = np.zeros((m, cap, 4), dtype=np.float64)
    side = 1 if direction == 1 else 0  # spot is long-only
    args = (side, float(tp_sl_ratio), float(fee), float(lot_step), float(max_risk_pct),
            float(initial_cash), int(max_holding_bars))  # fmt: skip
    cpu, gpu = _build(target)
    if target == "cpu":
        cpu(*bars, entry, stop64, *args, equity, stats, ilog, flog)
    else:
        d_in = [cuda.to_device(v) for v in (*bars, entry, stop64)]
        d_out = [cuda.to_device(v) for v in (equity, stats, ilog, flog)]
        gpu[math.ceil(m / THREADS), THREADS](*d_in, *args, *d_out)
        cuda.synchronize()
        equity, stats, ilog, flog = (d.copy_to_host() for d in d_out)
    if trade_log and stats[:, S_OVERFLOW].any():
        raise KernelInputError("trade log overflowed")
    return SpotReplay(
        equity=equity, trades=stats[:, S_TRADES].copy(), denied=stats[:, S_DENIED].copy(),
        ambiguous=stats[:, S_AMBIGUOUS].copy(), hold_sum=stats[:, S_HOLD_SUM].copy(),
        ilog=ilog if trade_log else None, flog=flog if trade_log else None,
        logged=stats[:, S_LOGGED].copy(),
    )  # fmt: skip
