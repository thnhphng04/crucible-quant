"""Spot replay kernel K3 reproduces ``_run_spot_bracket`` bit for bit (P3-40, INV-105).

The signals are drawn at random and handed to both replays — the Python one as ``Signal``
objects, the kernel as arrays — so this isolates the replay from the signal kernel."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

pytest.importorskip("numba")

from quantcrucible.core.strategy.base import FLAT, Bars, Signal
from quantcrucible.execution.engine import BacktestResult, _run_spot_bracket
from quantcrucible.execution.exit_policy import ExitPolicy
from quantcrucible.execution.kernels.replay import REASONS, run_spot_replay
from quantcrucible.execution.nautilus_bridge import CostModel, lot_step
from quantcrucible.execution.risk import RiskSettings

SYMBOL, RATIO, PCT = "BTC/USDT", 1.1, 0.01
FEE = float(CostModel().taker_rate)


def _bars(n: int, seed: int, gap: float = 0.0) -> Bars:
    rng = np.random.default_rng(seed)
    close = 20_000 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    jumps = np.where(rng.random(n) < gap, rng.normal(0, 0.05, n), 0.0)
    open_ = np.r_[close[0], close[:-1]] * np.exp(jumps)
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.006, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.006, n)))
    ts = np.datetime64("2020-01-01", "ns") + np.arange(1, n + 1) * np.timedelta64(1, "h")
    return Bars(SYMBOL, "1h", ts, open_, high, low, close, np.ones(n))


def _signals(bars: Bars, seed: int, p: float, lo: float, hi: float) -> tuple[Any, Any]:
    rng = np.random.default_rng(seed)
    entry = (rng.random(len(bars)) < p).astype(np.int8)
    stop = np.where(entry == 1, bars.close * rng.uniform(lo, hi, len(bars)), 0.0)
    return entry, stop


def _oracle(bars: Bars, entry: Any, stop: Any, side: str, cash: float, maxh: int) -> Any:
    sigs = tuple(
        Signal(side, 1.0, float(s), RATIO * float(s)) if e else FLAT  # type: ignore[arg-type]
        for e, s in zip(entry, stop, strict=True)
    )
    counts = {d: sum(s.direction == d for s in sigs) for d in ("long", "short", "flat")}
    policy = ExitPolicy("bracket_timeout_v1", RATIO, maxh)
    return _run_spot_bracket(
        SYMBOL, bars, sigs, counts, CostModel(), cash, 50, RiskSettings(PCT), None, policy
    )


def _run(bars: Bars, entry: Any, stop: Any, side: int, cash: float, maxh: int, target: str,
         log: bool = True) -> Any:  # fmt: skip
    return run_spot_replay(
        bars.open, bars.high, bars.low, bars.close, entry, stop, direction=side,
        tp_sl_ratio=RATIO, fee=FEE, lot_step=lot_step(SYMBOL), max_risk_pct=PCT,
        initial_cash=cash, max_holding_bars=maxh, trade_log=log, target=target,
    )  # fmt: skip


def _assert_same(got: Any, c: int, want: BacktestResult, where: str) -> None:
    assert np.array_equal(got.equity[c], want.equity), f"{where}: equity bits"
    assert got.trades[c] == want.n_trades, where
    assert got.denied[c] == want.denied_orders, where
    assert got.ambiguous[c] == want.ambiguous_bars, where
    avg = got.hold_sum[c] / got.trades[c] if got.trades[c] else 0.0
    assert avg == want.avg_holding_bars, f"{where}: holding"
    if got.ilog is None:
        return
    k = int(got.logged[c])
    bars, codes, prices = got.ilog[c, :k, 1], got.ilog[c, :k, 2], got.flog[c, :k, 2]
    exits = [
        (int(b), float(p), REASONS[int(r)]) for b, r, p in zip(bars, codes, prices, strict=True)
    ]
    assert exits == [(e.bar, e.price, e.reason) for e in want.exits], f"{where}: exits"
    assert [float(q) for q in got.flog[c, :k, 0]] == [s.qty for s in want.stops_placed], where
    assert [float(s) for s in got.flog[c, :k, 3]] == [s.trigger for s in want.stops_placed], where


CASES = [  # (seed, n, gap prob, entry prob, stop lo, stop hi, cash, max holding)
    (1, 800, 0.0, 0.05, 0.005, 0.03, 100_000.0, 100),
    (2, 800, 0.05, 0.08, 0.002, 0.02, 100_000.0, 100),  # gaps through the stop
    (3, 600, 0.0, 0.2, 0.001, 0.004, 100_000.0, 3),  # tight brackets, same-bar SL+TP, timeouts
    (4, 600, 0.0, 0.1, 0.005, 0.03, 50.0, 100),  # cash caps the size, lots round to zero
    (5, 400, 0.02, 0.9, 0.01, 0.5, 100_000.0, 1),  # every bar signals, one-bar holding limit
]


@pytest.mark.parametrize("case", CASES)
def test_cpu_replay_matches_the_python_replay(case: tuple[Any, ...]) -> None:
    seed, n, gap, p, lo, hi, cash, maxh = case
    bars = _bars(n, seed, gap)
    entry, stop = _signals(bars, seed, p, lo, hi)
    got = _run(bars, entry[:, None], stop[:, None], 1, cash, maxh, "cpu")
    want = _oracle(bars, entry, stop, "long", cash, maxh)
    assert want.n_trades > 0
    _assert_same(got, 0, want, f"case {seed}")


def test_many_configurations_replay_independently_without_a_log() -> None:
    bars = _bars(700, 9, 0.02)
    cols = [_signals(bars, s, 0.06, 0.003, 0.03) for s in range(12)]
    entry = np.stack([e for e, _ in cols], axis=1)
    stop = np.stack([s for _, s in cols], axis=1)
    got = _run(bars, entry, stop, 1, 100_000.0, 50, "cpu", log=False)
    for c, (e, s) in enumerate(cols):
        _assert_same(got, c, _oracle(bars, e, s, "long", 100_000.0, 50), f"config {c}")


def test_a_short_scope_never_trades_on_spot() -> None:
    bars = _bars(300, 4)
    entry, stop = _signals(bars, 4, 0.2, 0.005, 0.02)
    got = _run(bars, entry[:, None], stop[:, None], -1, 100_000.0, 100, "cpu")
    want = _oracle(bars, entry, stop, "short", 100_000.0, 100)
    assert want.n_trades == 0
    _assert_same(got, 0, want, "short")


@pytest.mark.cudasim
def test_simulated_cuda_replay_matches() -> None:
    bars = _bars(80, 3)
    entry, stop = _signals(bars, 3, 0.2, 0.002, 0.01)
    got = _run(bars, entry[:, None], stop[:, None], 1, 100_000.0, 5, "cuda")
    _assert_same(got, 0, _oracle(bars, entry, stop, "long", 100_000.0, 5), "cudasim")


@pytest.mark.gpu
@pytest.mark.parametrize("case", CASES)
def test_cuda_replay_matches_the_python_replay(case: tuple[Any, ...]) -> None:
    seed, n, gap, p, lo, hi, cash, maxh = case
    bars = _bars(n, seed, gap)
    entry, stop = _signals(bars, seed, p, lo, hi)
    got = _run(bars, entry[:, None], stop[:, None], 1, cash, maxh, "cuda")
    _assert_same(got, 0, _oracle(bars, entry, stop, "long", cash, maxh), f"cuda case {seed}")
