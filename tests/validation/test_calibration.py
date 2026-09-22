"""Calibration, §3.2.1 step 5b (INV-44, ADR-0017): every Optuna evaluation is a trial."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from quantcrucible.ledger.db import Ledger
from quantcrucible.ledger.records import Event
from quantcrucible.validation.calibration import (
    CalibrationError,
    CalibrationRefused,
    calibrate,
)
from quantcrucible.validation.gates import G3_IS, G4_PBO, GateContext, GatePipeline, GateResult
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


class FailingRunner(Runner):
    """Backtests with ``fast`` below ``crash_below`` fail inside the sandbox (as a strategy
    emitting a NaN stop does): nothing is measured."""

    def __init__(self, crash_below: int) -> None:
        super().__init__()
        self.crash_below = crash_below

    def run(self, job: SandboxJob) -> SandboxResult:
        if job.kind == "backtest" and float(job.params["fast"]) < self.crash_below:
            report = {
                "ok": False,
                "error_type": "ValueError",
                "error": "ValueError: stop_distance must be finite and > 0, got nan",
            }
            return SandboxResult(False, report, "", "", 1, False, None, 0.1)  # fmt: skip
        return super().run(job)


def test_every_attempt_is_accounted_for(ledger: Ledger, tmp_path: Path) -> None:
    """ADR-0017 amendment: B attempts = trials (measured) + errors (nothing measured). Errors get
    no trials row and no Sharpe; every attempt is traceable in one audit event."""
    budget = 20
    result = calibrate(
        cand(), ctx(ledger, tmp_path, FailingRunner(crash_below=20)), budget=budget,
        full_pipeline=FULL,
    )  # fmt: skip
    errors = [a for a in result.attempts if a.outcome == "error"]
    measured = [a for a in result.attempts if a.outcome != "error"]
    assert len(result.attempts) == budget and errors and measured
    assert all(a.trial_id is None and a.sharpe_is is None for a in errors)
    assert all("stop_distance" in (a.reason or "") for a in errors)
    assert all(a.trial_id is not None and a.sharpe_is is not None for a in measured)
    rows = [r for r in ledger.trials("c1") if r.candidate_id.startswith("x-opt")]
    assert sorted(r.id for r in rows) == sorted(a.trial_id for a in measured if a.trial_id)
    sharpes = [r.sharpe_is for r in ledger.trials("c1")]
    stats = ledger.trial_stats()
    assert stats.n_raw == len(measured) + 1  # + the confirmation run
    assert stats.var_sr == pytest.approx(np.var(sharpes))  # V[SR] from measured trials only
    (event,) = [d for e, d in ledger.event_details("c1") if e == Event.CALIBRATION_FINISHED]
    assert event is not None
    assert (event["attempts"], event["trials"], event["errors"]) == (
        budget,
        len(measured),
        len(errors),
    )
    assert [a["attempt_id"] for a in event["log"]] == [a.attempt_id for a in result.attempts]


class LeakyFailure:
    """A ③ that shows a number but records no measurement — performance seen, yet no trial."""

    id = G3_IS
    cost = 3

    def check(self, candidate: Any, ctx: GateContext) -> GateResult:
        return GateResult(False, G3_IS, 1.7, "crashed after the backtest", report=None)


def test_an_error_after_output_is_not_silently_exempt(ledger: Ledger, tmp_path: Path) -> None:
    """ADR-0022: only an attempt that produced no performance output is exempt from N. One that
    exposed a number without a measurement stops calibration for a human to classify."""
    c = ctx(ledger, tmp_path, Runner())
    with pytest.raises(CalibrationError, match="classify"):
        calibrate(cand(), c, budget=5, full_pipeline=FULL, measure=GatePipeline([LeakyFailure()]))
    (event,) = [d for e, d in ledger.event_details("c1") if e == Event.CALIBRATION_FINISHED]
    assert event is not None and event["log"][0]["outcome"] == "error_after_output"
    assert ledger.trials("c1") == []


# ── once per strategy per campaign, fixed budget (review 2, ADR-0017 amendment 2) ──────────
def test_a_second_calibration_is_refused(ledger: Ledger, tmp_path: Path) -> None:
    """The reviewer's case: budget 1 twice gave N_raw 2 → 4 and reused `x-opt000`."""
    c = ctx(ledger, tmp_path, Runner())
    calibrate(cand(), c, budget=1, full_pipeline=FULL)
    assert ledger.trial_stats().n_raw == 2
    with pytest.raises(CalibrationRefused, match="already calibrated"):
        calibrate(cand(), c, budget=1, full_pipeline=FULL)
    with pytest.raises(CalibrationRefused, match="already calibrated"):
        calibrate(cand(), c, budget=5, full_pipeline=FULL)  # a bigger budget changes nothing
    assert ledger.trial_stats().n_raw == 2
    ids = [r.candidate_id for r in ledger.trials("c1")]
    assert ids.count("x-opt000") == 1
    run = ledger.calibration_run("c1", "x")
    assert run is not None and (run.budget, run.outcome, run.attempts) == (1, "finished", 1)


def test_the_same_code_under_another_id_is_refused(ledger: Ledger, tmp_path: Path) -> None:
    c = ctx(ledger, tmp_path, Runner())
    calibrate(cand(), c, budget=1, full_pipeline=FULL)
    other = cand()
    other = type(other)(**{**{f: getattr(other, f) for f in other.__dataclass_fields__},
                           "candidate_id": "y"})  # fmt: skip
    with pytest.raises(CalibrationRefused, match="already calibrated"):
        calibrate(other, c, budget=1, full_pipeline=FULL)
    assert not [r for r in ledger.trials("c1") if r.candidate_id.startswith("y")]


class Crash(BaseException):
    """The process dies (e.g. killed) — not a gate failure, nothing is recorded for the call."""


class CrashingRunner(Runner):
    def __init__(self, crash_on_call: int) -> None:
        super().__init__()
        self.calls, self.crash_on_call = 0, crash_on_call

    def run(self, job: SandboxJob) -> SandboxResult:
        self.calls += 1
        if self.calls == self.crash_on_call:
            raise Crash
        return super().run(job)


def test_a_technical_retry_is_allowed_only_if_nothing_was_measured(
    ledger: Ledger, tmp_path: Path
) -> None:
    with pytest.raises(Crash):
        calibrate(cand(), ctx(ledger, tmp_path, CrashingRunner(1)), budget=3, full_pipeline=FULL)
    assert ledger.trials("c1") == [] and ledger.calibration_attempts_recorded("c1", "x") == 0
    with pytest.raises(CalibrationRefused, match="same run"):  # a retry cannot change the budget
        calibrate(cand(), ctx(ledger, tmp_path, Runner()), budget=4, full_pipeline=FULL)
    result = calibrate(cand(), ctx(ledger, tmp_path, Runner()), budget=3, full_pipeline=FULL)
    assert result.evaluations == 3 and ledger.trial_stats().n_raw == 4
    started = [d for e, d in ledger.event_details("c1") if e == Event.CALIBRATION_STARTED]
    assert [d["technical_retry"] for d in started if d] == [False, True]


def test_an_interrupted_search_is_not_resumed(ledger: Ledger, tmp_path: Path) -> None:
    """Attempts were measured before the crash: running again would search beyond what was
    seen — further optimization, refused."""
    with pytest.raises(Crash):
        calibrate(cand(), ctx(ledger, tmp_path, CrashingRunner(3)), budget=5, full_pipeline=FULL)
    n = ledger.trial_stats().n_raw
    assert n == 2
    with pytest.raises(CalibrationRefused, match="interrupted after 2"):
        calibrate(cand(), ctx(ledger, tmp_path, Runner()), budget=5, full_pipeline=FULL)
    assert ledger.trial_stats().n_raw == n


def test_a_stopped_calibration_is_not_rerun(ledger: Ledger, tmp_path: Path) -> None:
    c = ctx(ledger, tmp_path, Runner())
    with pytest.raises(CalibrationError, match="classify"):
        calibrate(cand(), c, budget=5, full_pipeline=FULL, measure=GatePipeline([LeakyFailure()]))
    with pytest.raises(CalibrationRefused, match="stopped"):
        calibrate(cand(), c, budget=5, full_pipeline=FULL)
    assert ledger.trials("c1") == []
