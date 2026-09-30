"""E6 (P3-45, ADR-0039): how far float32 features and signals drift from float64, synthetic only.

For sampled engine-C genomes on synthetic 1h series, both precisions run the same kernels (the
replay stays float64 in both). Reported, never gated on:

- the share of (bar, configuration) entry flags that differ, and of stop distances that differ;
- the share of configurations whose trade count differs;
- |ΔSharpe| of the gate-③ configuration and of every grid row;
- gate-③ verdict flips (min_trades, min_holding_bars at their defaults) and gate-④ flips
  (PBO < pbo_max over the 200-configuration grid);
- warm seconds of the features + signals stages per precision and target — the input to the
  ``auto`` choice for float32 campaigns.

    uv run python scripts/experiments/e6_fp32_drift.py --genomes 30 --seeds 3
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bench_backtest import hourly_bars

from quantcrucible.agent.grammar import GrammarConfig, sample_genome
from quantcrucible.config.schema import PBO_MAX_CEILING
from quantcrucible.core.strategy.genome import render_genome
from quantcrucible.core.strategy.template import parse
from quantcrucible.core.strategy.tunable import pbo_grid
from quantcrucible.execution.kernels._numba import cuda_available
from quantcrucible.execution.kernels.backend import KernelBackend, ReplaySpec
from quantcrucible.execution.kernels.program import compile_program
from quantcrucible.execution.nautilus_bridge import CostModel, lot_step
from quantcrucible.validation.pbo import DEFAULT_SPLITS, pbo

RATIO, LOOKBACK, MIN_TRADES, MIN_HOLDING = 1.1, 400, 30, 1
PPY = 24 * 365.25


def sharpe(r: np.ndarray) -> float:
    sd = float(np.std(r))
    return float(np.mean(r) / sd * np.sqrt(PPY)) if sd > 0 else 0.0


def timed_signals(kb: KernelBackend, prog: Any, bars: Any) -> tuple[Any, float]:
    kb.signals(prog, bars)  # warm (JIT and device)
    start = time.perf_counter()
    sig = kb.signals(prog, bars)
    return sig, time.perf_counter() - start


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--genomes", type=int, default=30, help="per seed")
    parser.add_argument("--seeds", type=int, default=3)
    parser.add_argument("--bars", type=int, default=67_000)
    parser.add_argument("--configs", type=int, default=200)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    target = "cuda" if cuda_available() else "cpu"
    rows: list[dict[str, Any]] = []
    timing: dict[str, list[float]] = {}
    for seed in range(args.seeds):
        bars = hourly_bars(args.bars, seed=100 + seed)
        spec = ReplaySpec(
            float(CostModel().taker_rate), lot_step(bars.symbol), 0.01, 100_000.0, 100, RATIO
        )
        rng = np.random.default_rng(600 + seed)
        for g in range(args.genomes):
            genome = sample_genome(rng, GrammarConfig())
            src, params = render_genome(genome, "long", RATIO)
            grid = pbo_grid(list(parse(src).tunables), 5, 0.3, args.configs, g, center=params)
            prog = compile_program(genome, "long", RATIO, grid, LOOKBACK)
            single = compile_program(genome, "long", RATIO, [dict(params)], LOOKBACK)
            out: dict[str, Any] = {}
            for dtype in ("float64", "float32"):
                sigs = {}
                for tgt in sorted({target, "cpu"}):
                    sigs[tgt], dt = timed_signals(KernelBackend(tgt, dtype), prog, bars)  # type: ignore[arg-type]
                    timing.setdefault(f"{tgt}/{dtype}", []).append(dt)
                sig = sigs[target]
                kb = KernelBackend(target, dtype)  # type: ignore[arg-type]
                res = kb.grid(prog, bars, spec, sig)
                one = kb.backtest(single, bars, spec)
                out[dtype] = (sig, res, one)
            (s64, g64, b64), (s32, g32, b32) = out["float64"], out["float32"]
            ok3 = [
                b.n_trades >= MIN_TRADES and b.avg_holding_bars >= MIN_HOLDING for b in (b64, b32)
            ]
            p64 = pbo(g64.returns, DEFAULT_SPLITS).pbo  # [configs, bars], as gate ④ passes it
            p32 = pbo(g32.returns, DEFAULT_SPLITS).pbo
            row_sh64 = np.array([sharpe(r) for r in g64.returns])
            row_sh32 = np.array([sharpe(r) for r in g32.returns])
            rows.append({
                "seed": seed, "genome": g,
                "entry_diff": float((s64.entry != s32.entry).mean()),
                "stop_diff": float(((s64.entry == 1) & (s64.stop != s32.stop.astype(np.float64)))
                                   .sum() / max(1, int((s64.entry == 1).sum()))),
                "configs_trades_diff": float(np.mean(np.subtract(g64.n_trades, g32.n_trades) != 0)),
                "d_sharpe3": abs(sharpe(b64.returns) - sharpe(b32.returns)),
                "d_sharpe_grid_max": float(np.max(np.abs(row_sh64 - row_sh32))),
                "flip3": ok3[0] != ok3[1],
                "pbo64": p64, "pbo32": p32,
                "flip4": (p64 < PBO_MAX_CEILING) != (p32 < PBO_MAX_CEILING),
                "trades3": b64.n_trades,
            })  # fmt: skip
            print(json.dumps(rows[-1]), file=sys.stderr, flush=True)

    def q(key: str) -> dict[str, float]:
        v = np.array([r[key] for r in rows], dtype=float)
        return {"mean": float(v.mean()), "median": float(np.median(v)), "max": float(v.max())}

    summary = {
        "target": target, "genomes": len(rows), "bars": args.bars, "configs": args.configs,
        "entry_diff": q("entry_diff"), "stop_diff": q("stop_diff"),
        "configs_trades_diff": q("configs_trades_diff"),
        "d_sharpe3": q("d_sharpe3"), "d_sharpe_grid_max": q("d_sharpe_grid_max"),
        "flips_gate3": sum(r["flip3"] for r in rows), "flips_gate4": sum(r["flip4"] for r in rows),
        "d_pbo_max": float(max(abs(r["pbo64"] - r["pbo32"]) for r in rows)),
        "signals_seconds_median": {k: float(np.median(v)) for k, v in timing.items()},
    }  # fmt: skip
    text = json.dumps(summary, indent=1)
    print(text)
    if args.output is not None:
        args.output.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
