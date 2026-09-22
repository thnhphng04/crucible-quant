"""Gate ④ — CSCV + PBO, and CPCV, over the candidate's pre-registered configuration set
(Architecture §3.2, D13, ADR-0011, ADR-0012).

Configuration set = the candidate's own parameters + the parameter variants of the same strategy
code already tried in this campaign (ledger) + the TUNABLE grid around the candidate's
parameters, capped at ``pbo_grid.max_configs``; the same return matrix feeds CPCV. The grid
configurations are *not* trials: they never replace the candidate's parameters, and their
matrix goes to ``results/pbo/`` and ``gate_results`` only. PBO, the IS→OOS slope and the
CPCV-OOS Sharpe distribution are private metrics (§3.3.2).
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from quantcrucible.core.strategy.template import parse
from quantcrucible.core.strategy.tunable import pbo_grid
from quantcrucible.data.source import timeframe_delta
from quantcrucible.ledger.db import Ledger
from quantcrucible.validation.artifacts import measurement_path, write_once, write_parquet_once
from quantcrucible.validation.cpcv import cpcv
from quantcrucible.validation.gates import G4_PBO, GateContext, GateResult, StrategyCandidate
from quantcrucible.validation.is_gates import backtest_options, universe_bars
from quantcrucible.validation.pbo import DEFAULT_SPLITS, pbo
from quantcrucible.validation.report import EvaluationReport
from quantcrucible.validation.sandbox import SandboxJob, SandboxRunner, sandbox_failure

SECONDS_PER_CONFIG = 30.0  # sandbox budget for the grid job, per configuration (O14)

Params = dict[str, float | int]


def periods_per_year(timeframe: str) -> float:
    return 365.0 * 86_400 / timeframe_delta(timeframe).total_seconds()  # crypto trades 24/7


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


def param_stability(
    matrix: np.ndarray, periods_per_year: float, plateau_threshold: float
) -> tuple[float, float]:
    """(SPP median, plateau) of the configuration set, candidate in row 0 (D20).

    SPP median: the median annualized IS Sharpe over every configuration (Walton's System
    Parameter Permutation estimate). Plateau: the share of the other configurations whose IS
    Sharpe is ≥ ``plateau_threshold`` × the candidate's — 0 when the candidate has no positive
    Sharpe or no neighbours. IS-only and computed from the grid already run: no trial.
    """
    m = np.asarray(matrix, dtype=np.float64)
    mean = np.nanmean(m, axis=1)
    sd = np.nanstd(m, axis=1, ddof=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        sharpes = np.where(sd > 0, mean / sd * math.sqrt(periods_per_year), 0.0)
    median = float(np.median(sharpes))
    own, others = float(sharpes[0]), sharpes[1:]
    if own <= 0 or len(others) == 0:
        return median, 0.0
    return median, float(np.mean(others >= plateau_threshold * own))


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
        base = Path(ctx.services["results_dir"]) / "pbo" / candidate.campaign_id
        path = measurement_path(base, candidate.candidate_id)
        frame = pd.DataFrame(matrix.T, columns=[f"c{i}" for i in range(len(configs))])
        frame.insert(0, "ts", pd.to_datetime(out["ts"]))
        write_parquet_once(path, frame)
        write_once(path.with_suffix(".configs.json"), json.dumps(configs).encode("utf-8"))
        result = pbo(matrix, n_splits)
        holding = list(out.get("avg_holding_bars", [])) or [1.0]
        paths = cpcv(
            matrix, pd.to_datetime(out["ts"]), periods_per_year(candidate.timeframe),
            horizon_bars=max(1, math.ceil(float(holding[0]))),
        )  # fmt: skip
        threshold = float(research.get("gp", {}).get("plateau_threshold", 0.5))
        spp_median, plateau = param_stability(
            matrix, periods_per_year(candidate.timeframe), threshold
        )
        public = {"spp_median_sharpe": spp_median, "plateau": plateau}  # IS-only (§3.3.2, D20)
        private = {
            "pbo": result.pbo,
            "pbo_slope": result.slope,
            "pbo_n_configs": float(result.n_configs),
            "pbo_n_combos": float(result.n_combos),
            **paths.private_metrics(),
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
                "cpcv_path_sharpes": paths.path_sharpes.tolist(),
                "cpcv_selected": list(paths.selected),
                "public": public,
            },
            report=EvaluationReport.build(public=public, private=private),
        )  # fmt: skip
