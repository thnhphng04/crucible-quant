"""Gate ⑤ — DSR of the consolidated portfolio (§3.2 ⑤, §3.1.6, §4.1, INV-42, ADR-0014)."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from quantcrucible.ledger.db import Ledger
from quantcrucible.ledger.records import TrialRecord
from quantcrucible.validation.gates import GateContext, GateResult
from quantcrucible.validation.portfolio import Portfolio, PortfolioRule, build_and_record
from quantcrucible.validation.portfolio_dsr import (
    G5_DSR,
    G6P_ROBUSTNESS,
    DsrGate,
    PortfolioPipeline,
)
from tests.validation.test_portfolio import T, add, noise

SRC = Path(__file__).resolve().parents[2] / "src" / "quantcrucible"
LOCK: dict[str, Any] = {"research": {"gates": {"dsr_min": 0.95}}}


@pytest.fixture
def ledger(tmp_path: Path) -> Ledger:
    led = Ledger.open(tmp_path / "ledger.db")
    led.open_campaign("c1", "2030-01-01/2031-01-01", lock_hash="h")
    return led


def strong_portfolio(ledger: Ledger, tmp_path: Path) -> Portfolio:
    add(ledger, tmp_path, "a", noise(1, mean=0.004))
    add(ledger, tmp_path, "b", noise(2, mean=0.004))
    return build_and_record(ledger, "c1", PortfolioRule(), 365, tmp_path / "res")


def more_trials(ledger: Ledger, tmp_path: Path, n: int, start: int, engine: str = "random") -> None:
    """Independent (unclustered) trials with the same Sharpe spread, any verdict."""
    for i in range(start, start + n):
        rets = np.random.default_rng(1000 + i).normal(0, 0.01, T)
        path = tmp_path / f"x{i}.parquet"
        pd.DataFrame({"ts": pd.date_range("2020-01-01", periods=T), "ret": rets}).to_parquet(path)
        ledger.record_trial(
            TrialRecord(
                run_id="r", campaign_id="c1", candidate_id=f"x{i}", engine=engine, seed=i,
                strategy_hash=f"hx{i}", params={}, universe="X", timeframe="1d", timerange="t",
                source="evolution", sharpe_is=float(np.mean(rets) / np.std(rets) * np.sqrt(365)),
                returns_path=str(path), verdict="REJECT_g3_is" if i % 2 else "PASS",
            )
        )  # fmt: skip


def check(ledger: Ledger, p: Portfolio) -> GateResult:
    return DsrGate().check(p, GateContext(ledger, LOCK, {}))


def test_more_trials_lower_dsr(ledger: Ledger, tmp_path: Path) -> None:
    """INV-42: every statistical trial — any engine, any verdict — raises the bar."""
    p = strong_portfolio(ledger, tmp_path)
    more_trials(ledger, tmp_path, 10, 0)
    before = check(ledger, p)
    more_trials(ledger, tmp_path, 200, 10, engine="simple_loop")
    after = check(ledger, p)
    assert before.detail is not None and after.detail is not None
    assert after.detail["n_eff"] > before.detail["n_eff"]
    assert after.detail["dsr_n_eff"] < before.detail["dsr_n_eff"]
    assert after.detail["dsr_n_raw"] < before.detail["dsr_n_raw"]


def test_inputs_are_trial_stats_plus_variants(ledger: Ledger, tmp_path: Path) -> None:
    p = strong_portfolio(ledger, tmp_path)
    more_trials(ledger, tmp_path, 20, 0)
    result = check(ledger, p)
    assert result.detail is not None
    stats = ledger.trial_stats()
    assert result.detail["n_raw"] == stats.n_raw + ledger.total_portfolio_variants() == 23
    assert result.detail["n_eff"] == stats.n_eff + 1  # N_eff re-estimated by the gate itself
    assert result.value == result.detail["dsr_n_eff"] >= result.detail["dsr_n_raw"]


def test_threshold(ledger: Ledger, tmp_path: Path) -> None:
    p = strong_portfolio(ledger, tmp_path)
    assert check(ledger, p).passed  # two trials, a strong edge
    faint = p.returns * 0 + noise(5, mean=0.0002)[: len(p.returns)]
    weak = Portfolio("c1", p.rule, p.members, faint, 365)
    assert not check(ledger, weak).passed


def test_pipeline_records_every_result_under_the_portfolio_hash(
    ledger: Ledger, tmp_path: Path
) -> None:
    p = strong_portfolio(ledger, tmp_path)

    class Boom:
        id, cost = G6P_ROBUSTNESS, 4

        def check(self, portfolio: Portfolio, ctx: GateContext) -> GateResult:
            raise RuntimeError("boom")

    outcome = PortfolioPipeline([DsrGate(), Boom()]).run(p, GateContext(ledger, LOCK, {}))
    assert not outcome.passed and outcome.failed_gate == G6P_ROBUSTNESS  # fail closed
    rows = ledger.gate_results(p.portfolio_hash)
    assert [(g, ok) for g, ok, _ in rows] == [(G5_DSR, True), (G6P_ROBUSTNESS, False)]
    with pytest.raises(ValueError, match="follow"):
        PortfolioPipeline([Boom(), DsrGate()])


def test_no_per_cell_dsr_api() -> None:
    """INV-42: DSR is computed in one place — statistical.portfolio_dsr — for a portfolio."""
    callers = set()
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "id", getattr(node.func, "attr", ""))
                if name == "deflated_sharpe_ratio":
                    callers.add(path.relative_to(SRC).as_posix())
    assert callers == {"validation/statistical.py"}
    source = (SRC / "validation" / "statistical.py").read_text(encoding="utf-8")
    assert "cell" not in source.split("def portfolio_dsr", 1)[1].split('"""', 1)[0]


def test_both_counts_and_the_clusters_are_reported(ledger: Ledger, tmp_path: Path) -> None:
    """ADR-0014 amendment: DSR at N_eff and at N_raw side by side, with how N_eff was formed,
    so a large gap between the two is visible in the result itself."""
    p = strong_portfolio(ledger, tmp_path)
    base = np.random.default_rng(7).normal(0.001, 0.01, T)
    for i in range(12):  # near copies of one series: ONC puts them in one cluster
        rets = base + np.random.default_rng(100 + i).normal(0, 0.0005, T)
        path = tmp_path / f"c{i}.parquet"
        pd.DataFrame({"ts": pd.date_range("2020-01-01", periods=T), "ret": rets}).to_parquet(path)
        ledger.record_trial(
            TrialRecord(
                run_id="r", campaign_id="c1", candidate_id=f"c{i}", engine="manual", seed=i,
                strategy_hash="hc", params={"i": i}, universe="X", timeframe="1d", timerange="t",
                source="param_opt", sharpe_is=1.0 + 0.01 * i, returns_path=str(path),
                verdict="PASS",
            )
        )  # fmt: skip
    result = check(ledger, p)
    d = result.detail
    assert d is not None
    assert d["n_eff"] == d["n_clusters"] + d["n_unclustered"] + d["n_variants"]
    assert d["n_raw"] == d["n_trials"] + d["n_variants"] == 15
    assert d["n_clusters"] < d["n_clustered_trials"]  # the copies were merged
    assert f"{d['dsr_n_eff']:.3f} at N_eff={d['n_eff']}" in result.reason
    assert f"{d['dsr_n_raw']:.3f} at N_raw={d['n_raw']}" in result.reason
    assert f"{d['n_clusters']} clusters of {d['n_clustered_trials']} trials" in result.reason
