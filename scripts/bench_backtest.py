"""Baseline cost of the current backtest path (P3-27, O14): timings only, synthetic data only.

Measures, per fixture genome, the stages a gate-③ sandbox ``backtest`` job runs on the host:

``signals``  ``generate_signals`` — ``step()`` rebuilds every indicator on a ``lookback`` window
             at each bar; the sandbox runs it twice per job (once for the fills, once for the
             ``signals_stream`` it reports).
``replay``   the ``bracket_timeout_v1`` spot replay on those signals.
``corr``     ``indicator_corr`` (full-series indicators).
``report``   JSON encoding of a gate-③-shaped report.

Per-bar costs are measured on ``--bars`` bars and projected to ``--project-bars`` and to a gate-④
grid of ``--configs`` configurations. ``--sandbox`` also times one Docker ``backtest`` and one
``leak_check`` job (gate ①b), which needs Docker running.

The fixture genomes are hand-written here, so loading their rendered source on the host is the
trusted-code case of ``load_strategy_class``. Nothing here reports a performance metric: the
output is seconds and counts, never Sharpe or returns.

    uv run python scripts/bench_backtest.py
    uv run python scripts/bench_backtest.py --bars 20000 --profile --sandbox
"""

from __future__ import annotations

import argparse
import cProfile
import io
import json
import pstats
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from quantcrucible.agent.grammar import (
    Breakout,
    Close,
    Combine,
    Cross,
    CrossLevel,
    Distance,
    Genome,
    Indicator,
    Param,
    Slope,
    Threshold,
    render_genome,
)
from quantcrucible.core.strategy.base import Bars, Strategy, generate_signals
from quantcrucible.core.strategy.template import load_strategy_class
from quantcrucible.execution.engine import _run_spot_bracket
from quantcrucible.execution.exit_policy import ExitPolicy
from quantcrucible.execution.nautilus_bridge import CostModel
from quantcrucible.execution.risk import RiskSettings
from quantcrucible.validation.feature_stats import max_abs_change_corr

SYMBOL = "BTC/USDT"
TP_SL_RATIO = 1.1
MAX_HOLDING_BARS = 100


def hourly_bars(n: int, seed: int = 0) -> Bars:
    """A 1h random walk with trend/range regimes and occasional gaps (consistent OHLC)."""
    rng = np.random.default_rng(seed)
    regime = np.repeat(rng.choice([-1.0, 0.0, 1.0], size=n // 500 + 1), 500)[:n]
    ret = rng.normal(regime * 2e-4, 0.006, n)
    close = 20_000.0 * np.exp(np.cumsum(ret))
    gap = np.where(rng.random(n) < 0.002, rng.normal(0.0, 0.02, n), 0.0)
    open_ = np.concatenate(([20_000.0], close[:-1])) * np.exp(gap + rng.normal(0.0, 0.001, n))
    high = np.maximum(open_, close) * (1.0 + np.abs(rng.normal(0.0, 0.003, n)))
    low = np.minimum(open_, close) * (1.0 - np.abs(rng.normal(0.0, 0.003, n)))
    volume = rng.uniform(100.0, 500.0, n)
    ts = np.datetime64("2018-01-01T00:00", "ns") + np.arange(1, n + 1) * np.timedelta64(1, "h")
    return Bars(SYMBOL, "1h", ts, open_, high, low, close, volume)


def _p(kind: str, low: float, high: float, value: float) -> Param:
    return Param(kind, low, high, int(value) if kind == "period" else value)  # type: ignore[arg-type]


def fixture_genomes() -> dict[str, Genome]:
    """G1 trend (EMA cross + slope, ATR stop), G2 momentum/breakout (RSI or breakout, Bollinger
    stop), G3 mean reversion (SMA distance in ATRs + z-score level cross)."""

    def ema(n: int) -> Indicator:
        return Indicator("ema", _p("period", 2, 300, n))

    return {
        "G1": Genome(
            Combine("and", (Cross(ema(20), True, ema(50)), Slope(ema(100), True))),
            _p("mult", 0.5, 5.0, 2.0),
        ),
        "G2": Genome(
            Combine(
                "or",
                (
                    Threshold(
                        Indicator("rsi", _p("period", 2, 100, 14)), ">", _p("level", 50, 90, 60)
                    ),
                    Breakout(True, _p("period", 2, 300, 50)),
                ),
            ),
            _p("mult", 0.5, 5.0, 1.5),
            stop_kind="bollinger",
        ),
        "G3": Genome(
            Combine(
                "and",
                (
                    Distance(
                        Close(),
                        Indicator("sma", _p("period", 2, 300, 50)),
                        "<",
                        _p("level", -3, 3, -1.0),
                    ),
                    CrossLevel(
                        Indicator("zscore", _p("period", 5, 300, 30)),
                        True,
                        _p("level", -3, 0, -1.0),
                    ),
                ),
            ),
            _p("mult", 0.5, 5.0, 2.5),
        ),
    }


def timed[T](fn: Callable[[], T]) -> tuple[T, float]:
    start = time.perf_counter()
    out = fn()
    return out, time.perf_counter() - start


def bench_genome(
    name: str, genome: Genome, bars: Bars, lookback: int, profile: bool
) -> dict[str, Any]:
    source, params = render_genome(genome, "long", tp_sl_ratio=TP_SL_RATIO)
    strategy: Strategy = load_strategy_class(source, f"bench_{name}")(params)
    costs, risk = CostModel(), RiskSettings()
    policy = ExitPolicy("bracket_timeout_v1", TP_SL_RATIO, MAX_HOLDING_BARS)

    profiler = cProfile.Profile() if profile else None
    if profiler is not None:
        profiler.enable()
    signals, t_signals = timed(lambda: generate_signals(strategy, bars, lookback))
    if profiler is not None:
        profiler.disable()
    for s in signals:
        policy.validate_signal(s)
    counts = {d: sum(s.direction == d for s in signals) for d in ("long", "short", "flat")}

    def replay() -> Any:
        return _run_spot_bracket(
            SYMBOL, bars, tuple(signals), counts, costs, 100_000.0, lookback, risk, None, policy
        )

    result, t_replay = timed(replay)
    (_corr, t_corr) = timed(lambda: max_abs_change_corr(strategy.indicators(bars)))
    report = {
        "ts": [str(t) for t in result.ts],
        "equity": result.equity.tolist(),
        "returns": result.returns.tolist(),
        "signals_stream": {
            SYMBOL: [[s.direction, s.strength, s.stop_distance, s.take_profit] for s in signals]
        },
    }
    encoded, t_report = timed(lambda: json.dumps(report))
    out: dict[str, Any] = {
        "entries": counts["long"],
        "trades": result.n_trades,
        "seconds": {
            "signals": t_signals,
            "replay": t_replay,
            "corr": t_corr,
            "report": t_report,
        },
        "report_bytes": len(encoded),
    }
    if profiler is not None:
        buf = io.StringIO()
        pstats.Stats(profiler, stream=buf).sort_stats("cumulative").print_stats(15)
        out["profile_top"] = buf.getvalue().splitlines()[:45]
    return out


def project(stages: dict[str, float], n: int, target: int, configs: int) -> dict[str, float]:
    """Seconds at ``target`` bars, for one gate-③ job and one gate-④ grid of ``configs``."""
    scale = target / n
    # The sandbox gate-③ job generates the signals twice (fills + signals_stream).
    gate3 = (2 * stages["signals"] + stages["replay"] + stages["corr"] + stages["report"]) * scale
    gate4 = (stages["signals"] + stages["replay"]) * scale * configs
    return {"gate3_job_s": gate3, "gate4_grid_s": gate4}


def bench_sandbox(bars: Bars, genome: Genome, lookback: int) -> dict[str, Any]:
    from quantcrucible.validation.sandbox import SandboxJob, SandboxRunner, ensure_image

    root = Path(__file__).resolve().parents[1]
    image, t_image = timed(lambda: ensure_image(root))
    runner = SandboxRunner(image, label="bench")
    source, params = render_genome(genome, "long", tp_sl_ratio=TP_SL_RATIO)
    options = {
        "costs": {"fee_rate": 0.001, "slippage_bps": 5.0},
        "lookback": lookback,
        "risk": {"max_risk_pct": 0.01},
        "leverage": 5,
        "seed": 0,
        "exit_policy": {
            "mode": "bracket_timeout_v1",
            "tp_sl_ratio": TP_SL_RATIO,
            "max_holding_bars": MAX_HOLDING_BARS,
        },
    }
    data = {SYMBOL: bars}
    backtest, t_bt = timed(
        lambda: runner.run(SandboxJob("backtest", source, data, params, options))
    )
    leak_opts = {"cut_points": 20, "window": 20, "seed": 0}
    leak, t_leak = timed(
        lambda: runner.run(SandboxJob("leak_check", source, data, params, leak_opts))
    )
    return {
        "image_s": t_image,
        "backtest": {"ok": backtest.ok, "wall_s": t_bt, "container_s": backtest.elapsed_s},
        "leak_check": {"ok": leak.ok, "wall_s": t_leak, "container_s": leak.elapsed_s},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bars", type=int, default=8_000, help="bars actually measured")
    parser.add_argument("--project-bars", type=int, default=67_000)
    parser.add_argument("--configs", type=int, default=200, help="gate-④ grid size projected")
    parser.add_argument("--lookback", type=int, default=400)
    parser.add_argument("--genomes", default="G1,G2,G3")
    parser.add_argument("--profile", action="store_true", help="cProfile the signals stage")
    parser.add_argument("--sandbox", action="store_true", help="also time Docker jobs")
    parser.add_argument("--sandbox-bars", type=int, default=2_000)
    parser.add_argument("--output", type=Path, help="write the JSON here as well")
    args = parser.parse_args()

    bars = hourly_bars(args.bars)
    genomes = fixture_genomes()
    results: dict[str, Any] = {
        "bars": args.bars,
        "lookback": args.lookback,
        "project_bars": args.project_bars,
        "configs": args.configs,
        "genomes": {},
    }
    for name in args.genomes.split(","):
        r = bench_genome(name, genomes[name], bars, args.lookback, args.profile)
        r["per_bar_us"] = {k: v / args.bars * 1e6 for k, v in r["seconds"].items()}
        r["projected"] = project(r["seconds"], args.bars, args.project_bars, args.configs)
        results["genomes"][name] = r
        print(f"{name}: {json.dumps(r['per_bar_us'])}", file=sys.stderr)
    if args.sandbox:
        results["sandbox"] = bench_sandbox(
            hourly_bars(args.sandbox_bars), genomes["G1"], args.lookback
        )
    text = json.dumps(results, indent=2)
    print(text)
    if args.output is not None:
        args.output.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
