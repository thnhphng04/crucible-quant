"""Feature kernel K1 reproduces ``registry.py`` on every lookback window, bit for bit (P3-38,
INV-105). The oracle is the registry itself applied to ``bars.window(t, L)`` — what ``step()``
does — reading the last value (``now``) and the one before it (``prev`` = ``.ago(1)``)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np
import numpy.typing as npt
import pytest

pytest.importorskip("numba")

from quantcrucible.core.strategy import registry as R
from quantcrucible.core.strategy.base import Bars
from quantcrucible.execution.kernels import program as P
from quantcrucible.execution.kernels.features import KernelInputError, run_features

Oracle = Callable[[Bars, int], npt.NDArray[np.float64]]
ORACLES: dict[int, Oracle] = {
    P.OP_CLOSE: lambda b, n: np.asarray(b.close, dtype=np.float64),
    P.OP_SMA: lambda b, n: R.sma(b.close, n),
    P.OP_EMA: lambda b, n: R.ema(b.close, n),
    P.OP_RSI: lambda b, n: R.rsi(b.close, n),
    P.OP_ZSCORE: lambda b, n: R.zscore(b.close, n),
    P.OP_RMAX: lambda b, n: R.rolling_max(b.close, n),
    P.OP_RMIN: lambda b, n: R.rolling_min(b.close, n),
    P.OP_BOLL_UPPER: lambda b, n: R.boll_upper(b.close, n),
    P.OP_BOLL_LOWER: lambda b, n: R.boll_lower(b.close, n),
    P.OP_ATR: lambda b, n: R.atr(b, n),
}


def _bars(n: int, seed: int) -> Bars:
    rng = np.random.default_rng(seed)
    close = 20_000 * np.exp(np.cumsum(rng.normal(0, 0.006, n)))
    close[n // 3 : n // 3 + 40] = close[n // 3 - 1]  # a flat stretch: std 0, loss 0
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.003, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.003, n)))
    ts = np.datetime64("2020-01-01", "ns") + np.arange(1, n + 1) * np.timedelta64(1, "h")
    return Bars("X/USDT", "1h", ts, open_, high, low, close, np.ones(n))


def _instances() -> tuple[list[int], list[int]]:
    ops: list[int] = [P.OP_CLOSE]
    pers: list[int] = [0]
    for op in ORACLES:
        if op == P.OP_CLOSE:
            continue
        for n in (2, 3, 8, 14, 20, 64, 129, 257):
            ops.append(op)
            pers.append(n)
    return ops, pers


def _expected(bars: Bars, ops: list[int], pers: list[int], lookback: int) -> tuple[Any, Any]:
    now = np.empty((len(ops), len(bars)))
    prev = np.empty((len(ops), len(bars)))
    for t in range(len(bars)):
        window = bars.window(t, lookback)
        for i, (op, n) in enumerate(zip(ops, pers, strict=True)):
            v = ORACLES[op](window, n)
            now[i, t] = v[-1]
            prev[i, t] = v[-2] if len(v) >= 2 else np.nan
    return now, prev


def _check(target: str, n_bars: int, lookbacks: tuple[int, ...], seed: int) -> None:
    bars = _bars(n_bars, seed)
    ops, pers = _instances()
    for lookback in lookbacks:
        now, prev = run_features(ops, pers, bars.close, bars.high, bars.low, lookback, target)  # type: ignore[arg-type]
        want_now, want_prev = _expected(bars, ops, pers, lookback)
        for i, (op, n) in enumerate(zip(ops, pers, strict=True)):
            for got, want, what in ((now, want_now, "now"), (prev, want_prev, "prev")):
                bad = ~np.array(
                    [
                        np.array_equal(a, b, equal_nan=True)
                        for a, b in zip(got[i], want[i], strict=True)
                    ]
                )
                assert not bad.any(), (
                    f"{target} op {op} n {n} L {lookback} {what}: {int(bad.sum())} bars differ,"
                    f" first at {int(np.argmax(bad))}"
                )


def test_cpu_kernel_matches_the_registry_on_every_window() -> None:
    _check("cpu", 700, (30, 300), seed=1)


@pytest.mark.cudasim
def test_simulated_cuda_kernel_matches_the_registry() -> None:
    _check("cuda", 60, (12,), seed=2)


@pytest.mark.gpu
def test_cuda_kernel_matches_the_registry_on_every_window() -> None:
    _check("cuda", 1_500, (30, 400), seed=3)


# (a_op, a_n, b_op, b_n, n): the spread_stdev instances a grammar-v4 Distance compiles to
SPREADS = [
    (P.OP_EMA, 8, P.OP_SMA, 20, 20),
    (P.OP_CLOSE, 0, P.OP_SMA, 14, 14),
    (P.OP_SMA, 3, P.OP_CLOSE, 0, 3),
    (P.OP_SMA, 64, P.OP_EMA, 129, 129),
    (P.OP_EMA, 2, P.OP_EMA, 30, 30),
    (P.OP_EMA, 129, P.OP_CLOSE, 0, 129),
]
OPERAND = {
    P.OP_CLOSE: ORACLES[P.OP_CLOSE],
    P.OP_SMA: ORACLES[P.OP_SMA],
    P.OP_EMA: ORACLES[P.OP_EMA],
}


def _check_spread(target: str, n_bars: int, lookbacks: tuple[int, ...], seed: int) -> None:
    """INV-119 (ADR-0045): each operand computed on the window, then the registry's deviation
    of their difference — what the render's indicators() returns at every bar."""
    bars = _bars(n_bars, seed)
    ops = [P.OP_SPREAD] * len(SPREADS)
    pers = [s[4] for s in SPREADS]
    aux = [s[:4] for s in SPREADS]
    for lookback in lookbacks:
        now, prev = run_features(
            ops, pers, bars.close, bars.high, bars.low, lookback, target, inst_aux=aux,  # type: ignore[arg-type]
        )  # fmt: skip
        finite = 0
        for t in range(n_bars):
            window = bars.window(t, lookback)
            for i, (a_op, a_n, b_op, b_n, n) in enumerate(SPREADS):
                v = R.spread_stdev(OPERAND[a_op](window, a_n), OPERAND[b_op](window, b_n), n)
                want_prev = v[-2] if len(v) >= 2 else np.nan
                where = f"{target} spread {SPREADS[i]} L {lookback} t {t}"
                assert np.array_equal(now[i, t], v[-1], equal_nan=True), where
                assert np.array_equal(prev[i, t], want_prev, equal_nan=True), where
                finite += bool(np.isfinite(v[-1]))
        assert finite > 0


def test_cpu_spread_kernel_matches_the_registry_on_every_window() -> None:
    _check_spread("cpu", 500, (30, 300), seed=4)


@pytest.mark.cudasim
def test_simulated_cuda_spread_kernel_matches_the_registry() -> None:
    _check_spread("cuda", 40, (25,), seed=5)


@pytest.mark.gpu
def test_cuda_spread_kernel_matches_the_registry_on_every_window() -> None:
    _check_spread("cuda", 1_000, (30, 400), seed=6)


@pytest.mark.parametrize(
    "aux", [(P.OP_RSI, 14, P.OP_SMA, 20), (P.OP_SMA, 0, P.OP_SMA, 20), (P.OP_CLOSE, 3, P.OP_SMA, 9)]
)
def test_a_spread_with_a_bad_operand_is_caught_in_the_kernel(aux: tuple[int, ...]) -> None:
    bars = _bars(50, 0)
    with pytest.raises(KernelInputError):
        run_features([P.OP_SPREAD], [20], bars.close, bars.high, bars.low, 30, inst_aux=[aux])


def test_non_finite_bars_are_refused() -> None:
    bars = _bars(50, 0)
    close = bars.close.copy()
    close[10] = np.nan
    with pytest.raises(KernelInputError):
        run_features([P.OP_SMA], [5], close, bars.high, bars.low, 20)


def test_an_instance_outside_the_tables_is_caught_in_the_kernel() -> None:
    bars = _bars(50, 0)
    with pytest.raises(KernelInputError):
        run_features([42], [5], bars.close, bars.high, bars.low, 20)
