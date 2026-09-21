"""Gate ④ — configuration set + PBO (§3.2, D13, ADR-0011). No Docker: a fake runner returns
the grid's return matrix."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from quantcrucible.core.strategy.tunable import parse_tunables
from quantcrucible.ledger.db import Ledger
from quantcrucible.ledger.records import TrialRecord
from quantcrucible.validation.gates import (
    G3_IS,
    G4_PBO,
    GateContext,
    GatePipeline,
    GateResult,
    StrategyCandidate,
    TrialMeasurement,
)
from quantcrucible.validation.pbo_gate import PboGate, configuration_set
from quantcrucible.validation.report import EvaluationReport
from quantcrucible.validation.sandbox import SandboxJob, SandboxResult
from tests.factories import make_bars
from tests.validation.test_is_gates import LOCK as IS_LOCK

ZOO = Path("src/quantcrucible/core/zoo/ema_crossover.py").read_text(encoding="utf-8")
PARAMS = {"fast": 20, "slow": 100, "k_atr": 2.0}
GRID = {"values_per_param": 3, "range": 0.3, "max_configs": 200}
LOCK: dict[str, Any] = {
    **IS_LOCK,
    "research": {**IS_LOCK["research"], "pbo_grid": GRID, "gates": {"pbo_max": 0.5}},
}
T = 1600


class GridRunner:
    """Returns a (n_configs × T) matrix: config 0 (the candidate) gets ``edge`` per bar."""

    def __init__(self, edge: float = 0.0, overfit: bool = False) -> None:
        self.edge, self.overfit = edge, overfit
        self.jobs: list[SandboxJob] = []

    def run(self, job: SandboxJob) -> SandboxResult:
        self.jobs.append(job)
        m = len(job.options["grid"])
        rng = np.random.default_rng(0)
        rets = rng.normal(0, 0.01, (m, T))
        if self.overfit:  # each config fits one regime and inverts in the other
            regime = np.where((np.arange(T) // 100) % 2 == 0, 1.0, -1.0)
            rets += np.linspace(-0.001, 0.001, m)[:, None] * regime
        rets[: max(1, m // 5)] += self.edge
        ts = [str(t) for t in pd.date_range("2020-01-02", periods=T, freq="D")]
        result = {"ts": ts, "returns": rets.tolist(), "n_trades": [40] * m}
        return SandboxResult(True, {"ok": True, "result": result}, "", "", 0, False, None, 0.1)


@pytest.fixture
def ledger(tmp_path: Path) -> Ledger:
    led = Ledger.open(tmp_path / "ledger.db")
    led.open_campaign("c1", "2030-01-01/2031-01-01", lock_hash="h")
    return led


def cand(params: dict[str, float | int] | None = None, cid: str = "x") -> StrategyCandidate:
    return StrategyCandidate(
        candidate_id=cid, source=ZOO, params=dict(params or PARAMS), universe=("BTC/USDT",),
        timeframe="1d", timerange="t", run_id="r", campaign_id="c1",
    )  # fmt: skip


def ctx(ledger: Ledger, tmp_path: Path, runner: Any) -> GateContext:
    data = {"BTC/USDT": make_bars(T + 1, symbol="BTC/USDT")}
    services = {"sandbox": runner, "is_data": data, "results_dir": tmp_path / "res"}
    return GateContext(ledger=ledger, lock=LOCK, services=services)


def trial(ledger: Ledger, c: StrategyCandidate, params: dict[str, float | int]) -> None:
    ledger.record_trial(
        TrialRecord(
            run_id="r", campaign_id="c1", candidate_id="old", engine="manual", seed=0,
            strategy_hash=c.strategy_hash, params=params, universe="BTC/USDT", timeframe="1d",
            timerange="t", source="manual", sharpe_is=0.5, returns_path="x.parquet",
            verdict="PASS",
        )
    )  # fmt: skip


# ── configuration set ──────────────────────────────────────────────────────────────


def test_configuration_set_candidate_first_then_variants_then_grid() -> None:
    variant = {"fast": 13, "slow": 77, "k_atr": 3.1}
    configs = configuration_set(cand(), [variant, PARAMS], GRID, seed=0)
    assert configs[0] == PARAMS and configs[1] == variant
    assert len(configs) == 1 + 1 + 3**3 - 1  # the grid contains the candidate: deduplicated
    assert len({tuple(sorted(c.items())) for c in configs}) == len(configs)


def test_configuration_set_is_centered_on_the_candidate_and_capped() -> None:
    tuned = {"fast": 30, "slow": 150, "k_atr": 3.0}
    configs = configuration_set(cand(tuned), [], GRID, seed=0)
    assert sorted({c["fast"] for c in configs}) == [21, 30, 39]
    capped = configuration_set(cand(), [], {**GRID, "values_per_param": 5, "max_configs": 40}, 0)
    assert len(capped) == 40 and capped[0] == PARAMS


def test_zoo_declares_its_tunables() -> None:
    assert [t.name for t in parse_tunables(ZOO)] == ["fast", "slow", "k_atr"]


# ── gate ④ ────────────────────────────────────────────────────────────────────────


def test_g4_passes_a_stable_edge_and_keeps_pbo_private(ledger: Ledger, tmp_path: Path) -> None:
    runner = GridRunner(edge=0.002)
    result = PboGate().check(cand(), ctx(ledger, tmp_path, runner))
    assert result.passed and result.value is not None and result.value < 0.05
    assert result.report is not None
    assert "pbo" in result.report.private and not result.report.public
    assert "pbo" not in result.report.feedback
    job = runner.jobs[0]
    assert job.kind == "grid_backtest" and job.options["grid"][0] == PARAMS
    assert job.options["risk"]["target_vol"] == 0.10  # same sizing as gate ③
    assert job.timeout_s is not None and job.timeout_s >= 300
    assert result.detail is not None
    saved = pd.read_parquet(result.detail["matrix_path"])
    assert saved.shape == (T, 1 + len(job.options["grid"]))


def test_g4_rejects_an_overfit_configuration_set(ledger: Ledger, tmp_path: Path) -> None:
    result = PboGate().check(cand(), ctx(ledger, tmp_path, GridRunner(overfit=True)))
    assert not result.passed and result.value is not None and result.value >= 0.5


def test_g4_includes_ledger_variants_of_the_same_code(ledger: Ledger, tmp_path: Path) -> None:
    c = cand()
    variant = {"fast": 13, "slow": 77, "k_atr": 3.1}
    trial(ledger, c, variant)
    runner = GridRunner(edge=0.002)
    result = PboGate().check(c, ctx(ledger, tmp_path, runner))
    assert runner.jobs[0].options["grid"][1] == variant
    assert result.detail is not None and result.detail["n_variants"] == 1


class FakeIS:
    id, cost = G3_IS, 3

    def check(self, candidate: StrategyCandidate, ctx: GateContext) -> GateResult:
        return GateResult(
            True, self.id, 1.0, "ok", report=EvaluationReport.build(public={"sharpe_is": 1.0}),
            measurement=TrialMeasurement(1.0, "r.parquet"),
        )  # fmt: skip


def test_grid_configurations_are_not_trials(ledger: Ledger, tmp_path: Path) -> None:
    """ADR-0011 / user decision: only the candidate is a trial; its grid is not."""
    outcome = GatePipeline([FakeIS(), PboGate()]).run(
        cand(), ctx(ledger, tmp_path, GridRunner(edge=0.002))
    )
    assert outcome.passed and ledger.trial_stats().n_raw == 1
    gates = [g for g, _, _ in ledger.gate_results("x")]
    assert gates == [G3_IS, G4_PBO]


def test_g4_without_tunables_fails_closed(ledger: Ledger, tmp_path: Path) -> None:
    bare = ZOO.replace("    # TUNABLE: fast = 20, bounds=(5, 60)\n", "")
    bare = bare.replace("    # TUNABLE: slow = 100, bounds=(40, 300)\n", "")
    bare = bare.replace("    # TUNABLE: k_atr = 2.0, bounds=(1.0, 4.0)\n", "")
    c = StrategyCandidate(
        candidate_id="b", source=bare, params={}, universe=("BTC/USDT",), timeframe="1d",
        timerange="t", run_id="r", campaign_id="c1",
    )  # fmt: skip
    result = PboGate().check(c, ctx(ledger, tmp_path, GridRunner()))
    assert not result.passed and "TUNABLE" in result.reason


def test_g4_matrix_is_immutable_per_measurement(ledger: Ledger, tmp_path: Path) -> None:
    """A second gate-④ run for the same candidate id writes a new matrix; the first stays."""
    first = PboGate().check(cand(), ctx(ledger, tmp_path, GridRunner(edge=0.002)))
    before = pd.read_parquet(first.detail["matrix_path"]) if first.detail else None
    second = PboGate().check(cand(), ctx(ledger, tmp_path, GridRunner(edge=0.0)))
    assert first.detail is not None and second.detail is not None and before is not None
    assert first.detail["matrix_path"] != second.detail["matrix_path"]
    pd.testing.assert_frame_equal(pd.read_parquet(first.detail["matrix_path"]), before)
