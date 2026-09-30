"""E5: how should eight pipeline workers share the kernel engine?

Eight threads submit gate-④ grid jobs (200 configurations) through the engine router, as the
evaluation pipeline does. Candidates: CUDA behind the router's lock; the CPU build with no lock
(numba's threading layer handles concurrent parallel regions, oversubscribed); the CPU build
behind a lock (one parallel region at a time, all cores). Throughput = jobs per second.

    uv run python scripts/experiments/e5_gpu_sharing.py --bars 20000 --jobs 3
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bench_backtest import hourly_bars

from quantcrucible.agent.grammar import GrammarConfig, sample_genome
from quantcrucible.config.schema import Compute
from quantcrucible.core.strategy.genome import render_genome
from quantcrucible.core.strategy.template import parse
from quantcrucible.core.strategy.tunable import pbo_grid
from quantcrucible.validation import engine_router
from quantcrucible.validation.engine_router import EngineRouter
from quantcrucible.validation.sandbox import SandboxJob, SandboxResult

OPTIONS: dict[str, Any] = {
    "lookback": 400,
    "risk": {"max_risk_pct": 0.01},
    "costs": {"fee_rate": 0.001, "slippage_bps": 5.0},
    "exit_policy": {"mode": "bracket_timeout_v1", "tp_sl_ratio": 1.1, "max_holding_bars": 100},
}


class NoSandbox:
    def run(self, job: SandboxJob) -> SandboxResult:
        raise AssertionError(f"unexpected sandbox job {job.kind}")


def jobs(n_jobs: int, n_bars: int) -> list[SandboxJob]:
    rng = np.random.default_rng(5)
    bars = {"BTC/USDT": hourly_bars(n_bars)}
    out = []
    while len(out) < n_jobs:
        g = sample_genome(rng, GrammarConfig())
        src, params = render_genome(g, "long", 1.1)
        grid = pbo_grid(list(parse(src).tunables), 5, 0.3, 200, 0, center=params)
        out.append(SandboxJob("grid_backtest", src, bars, params, {**OPTIONS, "grid": grid}))
    return out


def run(engine: str, lock_cpu: bool, work: list[SandboxJob], threads: int) -> float:
    router = EngineRouter(NoSandbox(), {"campaign_id": "e5"}, Compute(engine, 0.0))  # type: ignore[arg-type]
    cpu_lock = threading.Lock()
    original = router._compute_on
    if lock_cpu:

        def locked(plan: Any, kind: str, target: str) -> dict[str, Any]:
            if target == "cpu":
                with cpu_lock:
                    return original(plan, kind, target)
            return original(plan, kind, target)

        router._compute_on = locked  # type: ignore[method-assign]
    # Compile both builds off the clock: a CUDA job's L1 audit re-runs it on the CPU build, and
    # that build's first call is a JIT compile of 10-20 s (an earlier run timed it by mistake).
    router.run(work[0])
    router._compute(router._plan(work[0]), work[0].kind, engine_router.CPU)
    queue = list(work)
    guard = threading.Lock()

    def worker() -> None:
        while True:
            with guard:
                if not queue:
                    return
                job = queue.pop()
            res = router.run(job)
            assert res.ok, res.report

    start = time.perf_counter()
    pool = [threading.Thread(target=worker) for _ in range(threads)]
    for t in pool:
        t.start()
    for t in pool:
        t.join()
    return len(work) / (time.perf_counter() - start)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", type=int, default=20_000)
    ap.add_argument("--jobs", type=int, default=24)
    ap.add_argument("--threads", type=int, default=8)
    args = ap.parse_args()
    work = jobs(args.jobs, args.bars)
    assert engine_router.L1_RATE >= 0
    result = {
        "cuda_locked": run("gpu", False, work, args.threads),
        "cpu_unlocked": run("cpu_kernel", False, work, args.threads),
        "cpu_locked": run("cpu_kernel", True, work, args.threads),
    }
    print(json.dumps({k: round(v, 3) for k, v in result.items()} | vars(args), indent=1))


if __name__ == "__main__":
    main()
