"""The kernel engine returns the same ``BacktestResult`` as ``run_backtest`` (P3-41, INV-105).

Every field a gate or a report reads is compared: equity and returns bit for bit, fills, stops and
exits record for record, turnover, signal counts, denials and ambiguity exactly."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

pytest.importorskip("numba")

from quantcrucible.agent.grammar import GrammarConfig, sample_genome
from quantcrucible.core.strategy.base import Bars
from quantcrucible.core.strategy.genome import render_genome
from quantcrucible.core.strategy.template import load_strategy_class, parse
from quantcrucible.core.strategy.tunable import pbo_grid
from quantcrucible.execution.engine import BacktestResult, run_backtest
from quantcrucible.execution.exit_policy import ExitPolicy
from quantcrucible.execution.kernels.backend import (
    KernelBackend,
    ReplaySpec,
    self_test,
)
from quantcrucible.execution.kernels.program import compile_program
from quantcrucible.execution.nautilus_bridge import CostModel, lot_step
from quantcrucible.execution.risk import RiskSettings

SYMBOL, RATIO, MAXH, LOOKBACK = "BTC/USDT", 1.1, 100, 150
SPEC = ReplaySpec(float(CostModel().taker_rate), lot_step(SYMBOL), 0.01, 100_000.0, MAXH, RATIO)
CFG = GrammarConfig(take_profit_probability=0.3, boll_stop_probability=0.4)


def _bars(n: int, seed: int) -> Bars:
    rng = np.random.default_rng(seed)
    regime = np.repeat(rng.choice([-1.0, 0.0, 1.0], size=n // 120 + 1), 120)[:n]
    close = 20_000 * np.exp(np.cumsum(rng.normal(regime * 0.0015, 0.008, n)))
    open_ = np.r_[close[0], close[:-1]] * np.exp(
        np.where(rng.random(n) < 0.02, rng.normal(0, 0.02, n), 0)
    )
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.004, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.004, n)))
    ts = np.datetime64("2020-01-01", "ns") + np.arange(1, n + 1) * np.timedelta64(1, "h")
    return Bars(SYMBOL, "1h", ts, open_, high, low, close, np.ones(n))


def _oracle(src: str, cfg: dict[str, Any], bars: Bars, name: str) -> BacktestResult:
    cls = load_strategy_class(src, name)
    return run_backtest(
        cls(cfg), {SYMBOL: bars}, costs=CostModel(), initial_cash=SPEC.initial_cash,
        lookback=LOOKBACK, risk=RiskSettings(SPEC.max_risk_pct),
        exit_policy=ExitPolicy("bracket_timeout_v1", RATIO, MAXH),
    )  # fmt: skip


def _same(got: BacktestResult, want: BacktestResult, where: str) -> None:
    assert np.array_equal(got.ts, want.ts), where
    assert np.array_equal(got.equity, want.equity), f"{where}: equity"
    assert np.array_equal(got.returns, want.returns), f"{where}: returns"
    assert got.fills == want.fills, f"{where}: fills"
    assert got.stops_placed == want.stops_placed, f"{where}: stops"
    assert got.exits == want.exits, f"{where}: exits"
    for field in ("n_trades", "avg_holding_bars", "turnover", "denied_orders",
                  "periods_per_year", "ambiguous_bars", "funding_paid", "liquidated",
                  "terminated_at"):  # fmt: skip
        assert getattr(got, field) == getattr(want, field), f"{where}: {field}"
    assert dict(got.signals) == dict(want.signals), f"{where}: signal counts"
    assert got.public_metrics() == want.public_metrics(), f"{where}: public metrics"


def _check(target: str, n_genomes: int) -> int:
    rng = np.random.default_rng(21)
    bars = _bars(900, 8)
    backend = KernelBackend(target)  # type: ignore[arg-type]
    trades = 0
    for i in range(n_genomes):
        genome = sample_genome(rng, CFG)
        src, params = render_genome(genome, "long", RATIO)
        configs = pbo_grid(list(parse(src).tunables), 3, 0.3, 4, i, center=params)
        want = [
            _oracle(src, cfg, bars, f"backend_{target}_{i}_{m}") for m, cfg in enumerate(configs)
        ]
        single = compile_program(genome, "long", RATIO, [dict(params)], LOOKBACK)
        _same(backend.backtest(single, bars, SPEC), want[0], f"{target} genome {i}")
        grid = backend.grid(compile_program(genome, "long", RATIO, configs, LOOKBACK), bars, SPEC)
        for m, w in enumerate(want):
            assert np.array_equal(grid.returns[m], w.returns), f"{target} genome {i} config {m}"
            assert grid.n_trades[m] == w.n_trades
            assert grid.avg_holding_bars[m] == w.avg_holding_bars
        trades += want[0].n_trades
    return trades


def test_cpu_backend_reproduces_run_backtest() -> None:
    assert _check("cpu", 12) > 20


@pytest.mark.gpu
def test_cuda_backend_reproduces_run_backtest() -> None:
    assert _check("cuda", 12) > 20


@pytest.mark.gpu
def test_the_start_up_self_test_passes_on_this_device() -> None:
    assert self_test("float64")
