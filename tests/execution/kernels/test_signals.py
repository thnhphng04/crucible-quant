"""Signal kernel K2 reproduces ``generate_signals`` of the rendered source, bit for bit (P3-39,
INV-105). The oracle loads each genome's render — trusted here: the genomes are sampled by the
test itself — and steps it through the bars exactly as the sandbox does."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

pytest.importorskip("numba")

from quantcrucible.agent.grammar import GrammarConfig, sample_genome
from quantcrucible.core.strategy.base import Bars, generate_signals
from quantcrucible.core.strategy.genome import Genome, render_genome
from quantcrucible.core.strategy.template import load_strategy_class, parse
from quantcrucible.core.strategy.tunable import pbo_grid
from quantcrucible.execution.kernels.features import run_features
from quantcrucible.execution.kernels.program import compile_program
from quantcrucible.execution.kernels.signals import run_signals

RATIO = 1.1
CFG = GrammarConfig(take_profit_probability=0.5, boll_stop_probability=0.5)


def _bars(n: int, seed: int) -> Bars:
    rng = np.random.default_rng(seed)
    regime = np.repeat(rng.choice([-1.0, 0.0, 1.0], size=n // 80 + 1), 80)[:n]
    close = 100 * np.exp(np.cumsum(rng.normal(regime * 0.002, 0.01, n)))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.004, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.004, n)))
    ts = np.datetime64("2020-01-01", "ns") + np.arange(1, n + 1) * np.timedelta64(1, "h")
    return Bars("X/USDT", "1h", ts, open_, high, low, close, np.ones(n))


def _configs(genome: Genome, seed: int, n: int) -> tuple[str, list[dict[str, Any]]]:
    src, params = render_genome(genome, "long", RATIO)
    return src, pbo_grid(list(parse(src).tunables), 3, 0.3, n, seed, center=params)


def _check(target: str, n_genomes: int, n_bars: int, lookback: int, n_configs: int) -> int:
    rng = np.random.default_rng(11)
    bars = _bars(n_bars, 5)
    entries = 0
    for i in range(n_genomes):
        genome = sample_genome(rng, CFG)
        _, configs = _configs(genome, i, n_configs)
        for direction in ("long", "short"):
            prog = compile_program(genome, direction, RATIO, configs, lookback)
            now, prev = run_features(
                prog.inst_op, prog.inst_period, bars.close, bars.high, bars.low, lookback,
                target,  # type: ignore[arg-type]
            )  # fmt: skip
            entry, stop = run_signals(prog, now, prev, target)  # type: ignore[arg-type]
            src, _ = render_genome(genome, direction, RATIO)
            cls = load_strategy_class(src, f"k2_oracle_{i}_{direction}")
            for m, cfg in enumerate(configs):
                sigs = generate_signals(cls(cfg), bars, lookback)
                want_entry = np.array([s.direction == direction for s in sigs])
                want_stop = np.array(
                    [s.stop_distance if s.direction == direction else 0.0 for s in sigs]
                )
                where = f"{target} genome {i} {direction} config {m}"
                np.testing.assert_array_equal(entry[:, m] == 1, want_entry, err_msg=where)
                assert np.array_equal(stop[:, m], want_stop), where  # every bit
                tp = np.array([s.take_profit for s in sigs if s.direction == direction])
                assert np.array_equal(tp, RATIO * stop[entry[:, m] == 1, m]), where
                entries += int(want_entry.sum())
    return entries


def test_cpu_kernel_matches_generate_signals() -> None:
    assert _check("cpu", n_genomes=25, n_bars=400, lookback=120, n_configs=3) > 100


@pytest.mark.cudasim
def test_simulated_cuda_kernel_matches_generate_signals() -> None:
    _check("cuda", n_genomes=2, n_bars=60, lookback=30, n_configs=2)


@pytest.mark.gpu
def test_cuda_kernel_matches_generate_signals() -> None:
    assert _check("cuda", n_genomes=25, n_bars=400, lookback=120, n_configs=3) > 100
