"""Gate ④ — CSCV + PBO over the candidate's pre-registered configuration set (Architecture §3.2,
D13, ADR-0011).

Configuration set = the candidate's own parameters + the parameter variants of the same strategy
code already tried in this campaign (ledger) + the TUNABLE grid around the candidate's
parameters, capped at ``pbo_grid.max_configs``. The grid configurations are *not* trials: they
never replace the candidate's parameters, and their return matrix goes to ``results/pbo/`` and
``gate_results`` only. PBO and the IS→OOS slope are private metrics (§3.3.2).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from quantcrucible.core.strategy.template import parse
from quantcrucible.core.strategy.tunable import pbo_grid
from quantcrucible.ledger.db import Ledger
from quantcrucible.validation.gates import G4_PBO, GateContext, GateResult, StrategyCandidate
from quantcrucible.validation.is_gates import backtest_options, universe_bars
from quantcrucible.validation.pbo import DEFAULT_SPLITS, pbo
from quantcrucible.validation.report import EvaluationReport
from quantcrucible.validation.sandbox import SandboxJob, SandboxRunner, sandbox_failure

SECONDS_PER_CONFIG = 30.0  # sandbox budget for the grid job, per configuration (O14)

Params = dict[str, float | int]


def _key(p: Mapping[str, float | int]) -> str:
    return json.dumps({k: float(v) for k, v in p.items()}, sort_keys=True)


def ledger_variants(ledger: Ledger, candidate: StrategyCandidate) -> list[Params]:
    """Parameter sets of the same strategy code already measured in this campaign."""
    return [
        dict(t.params)
        for t in ledger.trials(candidate.campaign_id)
        if t.strategy_hash == candidate.strategy_hash
    ]


def configuration_set(
    candidate: StrategyCandidate,
    variants: Sequence[Mapping[str, float | int]],
    grid_settings: Mapping[str, Any],
    seed: int,
) -> list[Params]:
    """Candidate first, then ledger variants, then the grid — deduplicated, capped at M."""
    tunables = list(parse(candidate.source).tunables)
    grid = pbo_grid(
        tunables,
        int(grid_settings["values_per_param"]),
        float(grid_settings["range"]),
        int(grid_settings["max_configs"]),
        seed,
        center=candidate.params,
    )
    cap = int(grid_settings["max_configs"])
    out: list[Params] = []
    seen: set[str] = set()
    for p in [dict(candidate.params), *(dict(v) for v in variants), *grid]:
        k = _key(p)
        if k not in seen and len(out) < cap:
            seen.add(k)
            out.append(p)
    return out


class PboGate:
    """Gate ④: reject when PBO ≥ ``gates.pbo_max`` (§3.2, §10.1 hard ceiling 0.5)."""

    id = G4_PBO
    cost = 4

    def check(self, candidate: StrategyCandidate, ctx: GateContext) -> GateResult:
        research: Mapping[str, Any] = ctx.lock["research"]
        pbo_max = float(research["gates"]["pbo_max"])
        n_splits = int(ctx.lock["derived"].get("pbo", {}).get("n_splits", DEFAULT_SPLITS))
        variants = ledger_variants(ctx.ledger, candidate)
        configs = configuration_set(candidate, variants, research["pbo_grid"], candidate.seed)
        if len(configs) < 2:
            return GateResult(
                False, self.id, None,
                "PBO needs >= 2 configurations: declare the free parameters as TUNABLE",
            )  # fmt: skip
        runner: SandboxRunner = ctx.services["sandbox"]
        job = SandboxJob(
            "grid_backtest", candidate.source, universe_bars(candidate, ctx), candidate.params,
            {**backtest_options(ctx.lock, candidate.seed), "grid": configs},
            timeout_s=max(300.0, SECONDS_PER_CONFIG * len(configs)),
        )  # fmt: skip
        res = runner.run(job)
        if not res.ok or res.report is None:
            return sandbox_failure(self.id, res)
        out: dict[str, Any] = res.report["result"]
        matrix = np.asarray(out["returns"], dtype=np.float64)
        path = Path(ctx.services["results_dir"]) / "pbo" / candidate.campaign_id
        path.mkdir(parents=True, exist_ok=True)
        path = path / f"{candidate.candidate_id}.parquet"
        frame = pd.DataFrame(matrix.T, columns=[f"c{i}" for i in range(len(configs))])
        frame.insert(0, "ts", pd.to_datetime(out["ts"]))
        frame.to_parquet(path, index=False)
        path.with_suffix(".configs.json").write_text(json.dumps(configs), encoding="utf-8")
        result = pbo(matrix, n_splits)
        private = {
            "pbo": result.pbo,
            "pbo_slope": result.slope,
            "pbo_n_configs": float(result.n_configs),
            "pbo_n_combos": float(result.n_combos),
        }
        verdict = f"PBO {result.pbo:.3f} over {len(configs)} configurations (max {pbo_max:g})"
        return GateResult(
            passed=result.pbo < pbo_max,
            gate=self.id,
            value=result.pbo,
            reason=verdict,
            detail={
                "n_configs": len(configs), "n_variants": len(variants), "n_splits": n_splits,
                "slope": result.slope, "matrix_path": str(path),
                "n_trades": list(out.get("n_trades", [])),
            },
            report=EvaluationReport.build(private=private),
        )  # fmt: skip
