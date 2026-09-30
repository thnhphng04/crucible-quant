"""float32 campaigns (P3-45, ADR-0039; INV-110): features and signals in float32, the replay in
float64. There is no float32 oracle, so the CPU build is the reference: the CUDA build must match
it bit for bit (audits L0/L1), and both must stay close to — not equal to — float64."""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("numba")

from quantcrucible.agent.grammar import GrammarConfig, sample_genome
from quantcrucible.core.strategy.base import Bars
from quantcrucible.core.strategy.genome import render_genome
from quantcrucible.core.strategy.template import parse
from quantcrucible.core.strategy.tunable import pbo_grid
from quantcrucible.execution.kernels import program as P
from quantcrucible.execution.kernels.backend import KernelBackend, ReplaySpec, self_test
from quantcrucible.execution.kernels.features import KernelInputError, run_features
from quantcrucible.execution.kernels.program import compile_program
from quantcrucible.execution.kernels.signals import run_signals
from quantcrucible.execution.nautilus_bridge import CostModel, lot_step

SYMBOL, RATIO, LOOKBACK = "BTC/USDT", 1.1, 150
SPEC = ReplaySpec(float(CostModel().taker_rate), lot_step(SYMBOL), 0.01, 100_000.0, 100, RATIO)
CFG = GrammarConfig(take_profit_probability=0.3, boll_stop_probability=0.4)
OPS = [P.OP_CLOSE, P.OP_SMA, P.OP_EMA, P.OP_RSI, P.OP_ZSCORE, P.OP_RMAX, P.OP_RMIN,
       P.OP_BOLL_UPPER, P.OP_BOLL_LOWER, P.OP_ATR]  # fmt: skip


def _bars(n: int, seed: int) -> Bars:
    rng = np.random.default_rng(seed)
    regime = np.repeat(rng.choice([-1.0, 0.0, 1.0], size=n // 120 + 1), 120)[:n]
    close = 20_000 * np.exp(np.cumsum(rng.normal(regime * 0.0015, 0.008, n)))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.004, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.004, n)))
    ts = np.datetime64("2020-01-01", "ns") + np.arange(1, n + 1) * np.timedelta64(1, "h")
    return Bars(SYMBOL, "1h", ts, open_, high, low, close, np.ones(n))


def _features(bars: Bars, target: str, dtype: str) -> tuple[np.ndarray, np.ndarray]:
    ops = [op for op in OPS for _ in (0, 1)]
    pers = [0 if op == P.OP_CLOSE else n for op in OPS for n in (14, 60)]
    return run_features(ops, pers, bars.close, bars.high, bars.low, LOOKBACK, target, dtype)  # type: ignore[arg-type]


def _programs(n: int, seed: int) -> list[P.Program]:
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n):
        genome = sample_genome(rng, CFG)
        src, params = render_genome(genome, "long", RATIO)
        configs = pbo_grid(list(parse(src).tunables), 3, 0.3, 6, i, center=params)
        out.append(compile_program(genome, "long", RATIO, configs, LOOKBACK))
    return out


def test_float32_features_are_float32_and_close_to_float64() -> None:
    bars = _bars(700, 1)
    now32, prev32 = _features(bars, "cpu", "float32")
    now64, _ = _features(bars, "cpu", "float64")
    assert now32.dtype == prev32.dtype == np.float32
    assert np.array_equal(np.isnan(now32), np.isnan(now64))
    scale = np.maximum(np.abs(now64), 1.0)
    assert np.nanmax(np.abs(now32 - now64) / scale) < 1e-3
    assert not np.array_equal(now32.astype(np.float64), now64, equal_nan=True)  # really fp32


def test_float32_signals_mostly_agree_and_the_replay_stays_float64() -> None:
    bars = _bars(900, 2)
    agree, total = 0, 0
    for prog in _programs(8, 5):
        s32 = KernelBackend("cpu", "float32").signals(prog, bars)
        s64 = KernelBackend("cpu", "float64").signals(prog, bars)
        assert s32.stop.dtype == np.float32
        agree += int((s32.entry == s64.entry).sum())
        total += s32.entry.size
        grid = KernelBackend("cpu", "float32").grid(prog, bars, SPEC, s32)
        assert grid.returns.dtype == np.float64
    assert agree / total > 0.99


def test_feature_tables_of_the_wrong_width_are_refused() -> None:
    bars = _bars(200, 3)
    [prog] = _programs(1, 7)
    now64, prev64 = run_features(
        prog.inst_op, prog.inst_period, bars.close, bars.high, bars.low, LOOKBACK, "cpu"
    )
    with pytest.raises(KernelInputError, match="float32"):
        run_signals(prog, now64, prev64, "cpu", "float32")


def _cuda_matches_cpu(target_n_bars: int, n_programs: int) -> None:
    bars = _bars(target_n_bars, 4)
    cpu_now, cpu_prev = _features(bars, "cpu", "float32")
    gpu_now, gpu_prev = _features(bars, "cuda", "float32")
    assert cpu_now.tobytes() == gpu_now.tobytes() and cpu_prev.tobytes() == gpu_prev.tobytes()
    for prog in _programs(n_programs, 9):
        cpu = KernelBackend("cpu", "float32")
        gpu = KernelBackend("cuda", "float32")
        a, b = cpu.signals(prog, bars), gpu.signals(prog, bars)
        assert np.array_equal(a.entry, b.entry) and a.stop.tobytes() == b.stop.tobytes()
        ga, gb = cpu.grid(prog, bars, SPEC, a), gpu.grid(prog, bars, SPEC, b)
        assert ga.returns.tobytes() == gb.returns.tobytes() and ga.n_trades == gb.n_trades


@pytest.mark.cudasim
def test_simulated_cuda_float32_matches_the_cpu_build() -> None:
    _cuda_matches_cpu(160, 1)


@pytest.mark.gpu
def test_cuda_float32_matches_the_cpu_build_bit_for_bit() -> None:
    _cuda_matches_cpu(1_500, 10)


@pytest.mark.gpu
def test_the_float32_self_test_passes_on_this_device() -> None:
    assert self_test("float32")
