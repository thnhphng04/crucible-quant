"""Audit L3 before a freeze (P3-48, ADR-0038; INV-109): a member the kernel engine measured is
measured again on the canonical path and must match its recorded returns bit for bit."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("numba")

from quantcrucible.ledger.db import Ledger
from quantcrucible.ledger.records import GateResultRecord
from quantcrucible.validation.archive import StrategyArchive
from quantcrucible.validation.freeze import FreezeError, freeze_campaign
from quantcrucible.validation.gates import G4_PBO, GateContext, GatePipeline
from quantcrucible.validation.is_gates import InSampleGate
from quantcrucible.validation.portfolio import PortfolioRule, build_and_record
from quantcrucible.validation.reproduce import kernel_trials, reproduce_members
from quantcrucible.validation.sandbox import SandboxJob, SandboxResult
from tests.holdout.test_evaluator import passes
from tests.validation.test_engine_reruns import LOCK, _candidate
from tests.validation.test_engine_router import FakeSandbox, _bars, _router

LOCK32: dict[str, Any] = {
    **LOCK,
    "research": {**LOCK["research"], "backtest": {"precision": "float32"}},
    "derived": {**LOCK["derived"], "backtest_numerics": "fp32_signals_v1"},
}


class LyingSandbox(FakeSandbox):
    """The canonical engine, one ulp off on its first return."""

    def run(self, job: SandboxJob) -> SandboxResult:
        res = super().run(job)
        assert res.report is not None
        res.report["result"]["returns"][0] += 1e-12
        return res


def _project(tmp_path: Path, lock: dict[str, Any]) -> tuple[Ledger, str, StrategyArchive]:
    ledger = Ledger.open(tmp_path / "ledger.db")
    ledger.open_campaign("c1", "2030-01-01/2031-01-01", lock_hash="h")
    archive = StrategyArchive(tmp_path / "res" / "strategies")
    services = {"sandbox": _router(FakeSandbox(), lock), "is_data": _bars(),
                "results_dir": tmp_path / "res", "archive": archive}  # fmt: skip
    ctx = GateContext(ledger, lock, services)
    base = _candidate()
    for i, n1 in enumerate((10, 14)):
        c = dataclasses.replace(base, candidate_id=f"m{i}", params={**base.params, "n1": n1})
        outcome = GatePipeline([InSampleGate()]).run(c, ctx)
        assert outcome.trial_id is not None
        ledger.record_gate_result(
            GateResultRecord(campaign_id="c1", candidate_id=c.candidate_id, gate=G4_PBO,
                             passed=True, reason="x", trial_id=outcome.trial_id)
        )  # fmt: skip
    p = build_and_record(ledger, "c1", PortfolioRule(), 24 * 365.25, tmp_path / "res")
    passes(ledger, "c1", p.portfolio_hash)
    return ledger, p.portfolio_hash, archive


def test_a_kernel_measured_member_blocks_the_freeze_until_reproduced(tmp_path: Path) -> None:
    ledger, p_hash, archive = _project(tmp_path, LOCK)
    assert {t.backtest_engine for t in kernel_trials(ledger, "c1", p_hash)} == {"cpu_kernel"}
    with pytest.raises(FreezeError, match="re-produced"):
        freeze_campaign(ledger, "c1", p_hash)
    box = FakeSandbox()
    done = reproduce_members(ledger, "c1", p_hash, LOCK, _bars(), archive, box)
    assert done and all(r.matched and r.canonical == "sandbox" for r in done)
    assert [j.kind for j in box.jobs] == ["backtest"] * len(done)  # the canonical engine ran
    freeze_campaign(ledger, "c1", p_hash)


def test_a_member_that_does_not_reproduce_blocks_the_freeze(tmp_path: Path) -> None:
    ledger, p_hash, archive = _project(tmp_path, LOCK)
    done = reproduce_members(ledger, "c1", p_hash, LOCK, _bars(), archive, LyingSandbox())
    assert done and not any(r.matched for r in done)
    with pytest.raises(FreezeError, match="did not re-produce"):
        freeze_campaign(ledger, "c1", p_hash)


def test_a_float32_member_is_reproduced_on_the_cpu_build(tmp_path: Path) -> None:
    ledger, p_hash, archive = _project(tmp_path, LOCK32)
    box = FakeSandbox()
    done = reproduce_members(ledger, "c1", p_hash, LOCK32, _bars(), archive, box)
    assert done and all(r.matched and r.canonical == "cpu_kernel" for r in done)
    assert not box.jobs  # float32 never runs on the float64 sandbox
    freeze_campaign(ledger, "c1", p_hash)
