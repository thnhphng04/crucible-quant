"""Calibration, §3.2.1 step 5b (INV-44, ADR-0017): every Optuna evaluation is a trial."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from quantcrucible.ledger.db import Ledger
from quantcrucible.validation.calibration import calibrate
from quantcrucible.validation.gates import G3_IS, G4_PBO, GateContext, GatePipeline
from quantcrucible.validation.is_gates import InSampleGate
from quantcrucible.validation.pbo_gate import PboGate
from quantcrucible.validation.sandbox import SandboxJob, SandboxResult
from tests.factories import make_bars
from tests.validation.test_pbo_gate import LOCK, ZOO, cand

T = 1600


def sharpe_for(params: dict[str, Any]) -> float:
    """A smooth landscape with its peak at fast = 30, slow = 150."""
    return (
        1.5 - ((float(params["fast"]) - 30) / 20) ** 2 - ((float(params["slow"]) - 150) / 100) ** 2
    )


class Runner:
    def __init__(self, min_fast: int = 0) -> None:
        self.min_fast = min_fast  # below it: too few trades ⇒ rejected at ③
        self.kinds: list[str] = []

    def run(self, job: SandboxJob) -> SandboxResult:
        self.kinds.append(job.kind)
        ts = [str(t) for t in pd.date_range("2020-01-02", periods=T, freq="D")]
        rng = np.random.default_rng(0)
        if job.kind == "grid_backtest":
            m = len(job.options["grid"])
            rets = rng.normal(0, 0.01, (m, T))
            rets[0] += 0.002  # the candidate (first config) has a stable edge
            result: dict[str, Any] = {"ts": ts, "returns": rets.tolist(), "n_trades": [40] * m}
        else:
            s = sharpe_for(dict(job.params))
            trades = 40.0 if float(job.params["fast"]) >= self.min_fast else 5.0
            public = {"sharpe_is": s, "max_drawdown": 0.2, "total_return": 0.1,
                      "n_trades": trades, "avg_holding_bars": 5.0, "turnover": 1.0}  # fmt: skip
            result = {"public": public, "ts": ["2020-01-01", *ts],
                      "returns": rng.normal(0, 0.01, T).tolist(), "denied_orders": 0,
                      "indicator_corr": 0.5, "indicator_pair": ["f", "s"]}  # fmt: skip
        return SandboxResult(True, {"ok": True, "result": result}, "", "", 0, False, None, 0.1)


@pytest.fixture
def ledger(tmp_path: Path) -> Ledger:
    led = Ledger.open(tmp_path / "ledger.db")
    led.open_campaign("c1", "2030-01-01/2031-01-01", lock_hash="h")
    return led


def ctx(ledger: Ledger, tmp_path: Path, runner: Runner) -> GateContext:
    data = {"BTC/USDT": make_bars(T + 1, symbol="BTC/USDT")}
    services = {"sandbox": runner, "is_data": data, "results_dir": tmp_path / "res"}
    return GateContext(ledger, LOCK, services)


FULL = GatePipeline([InSampleGate(), PboGate()])


def test_every_eval_is_a_trial(ledger: Ledger, tmp_path: Path) -> None:
    """INV-44: B evaluations ⇒ B `param_opt` trials, plus the confirmation run (also a trial)."""
    result = calibrate(cand(), ctx(ledger, tmp_path, Runner()), budget=12, full_pipeline=FULL)
    rows = ledger.trials("c1")
    evaluations = [r for r in rows if r.candidate_id.startswith("x-opt")]
    assert len(evaluations) == 12 and result.evaluations == 12
    assert all(r.source == "param_opt" for r in rows)
    assert [r.candidate_id for r in rows if not r.candidate_id.startswith("x-opt")] == ["x"]
    assert ledger.trial_stats().n_raw == 13


def test_best_params_confirmed_through_the_full_pipeline(ledger: Ledger, tmp_path: Path) -> None:
    runner = Runner()
    result = calibrate(cand(), ctx(ledger, tmp_path, runner), budget=25, full_pipeline=FULL)
    assert result.best_sharpe is not None and result.best_sharpe > 1.0  # found the peak region
    assert result.confirmation is not None and result.confirmation.passed
    # the confirmation runs under the ORIGINAL candidate id: it replaces it at portfolio build
    gates = [g for g, _, _ in ledger.gate_results("x")]
    assert gates == [G3_IS, G4_PBO]
    assert runner.kinds.count("grid_backtest") == 1  # PBO recomputed once, afterwards
    # the calibration rows share the strategy hash ⇒ they are ledger variants in gate ④'s set
    detail = ledger.gate_result_details("x", G4_PBO)[-1]
    assert detail["n_variants"] >= 25


def test_rejected_evaluations_still_count(ledger: Ledger, tmp_path: Path) -> None:
    calibrate(cand(), ctx(ledger, tmp_path, Runner(min_fast=40)), budget=10, full_pipeline=FULL)
    opt = [r for r in ledger.trials("c1") if r.candidate_id.startswith("x-opt")]
    assert len(opt) == 10
    assert any(r.verdict.startswith("REJECT") for r in opt)


def test_budget_and_tunables_required(ledger: Ledger, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="budget"):
        calibrate(cand(), ctx(ledger, tmp_path, Runner()), budget=0, full_pipeline=FULL)
    bare = cand()
    bare = type(bare)(**{**{f: getattr(bare, f) for f in bare.__dataclass_fields__},
                         "source": ZOO.replace("# TUNABLE", "# NOTE")})  # fmt: skip
    with pytest.raises(ValueError, match="TUNABLE"):
        calibrate(bare, ctx(ledger, tmp_path, Runner()), budget=3, full_pipeline=FULL)
