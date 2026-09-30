"""The holdout on the kernel engine (P3-47, ADR-0038/0039; INV-110).

The evaluator runs the engine router at the lock's precision. Everything that could stop it is
checked before ``claim()`` — a refusal leaves the holdout unclaimed — and a device fault mid-run
continues on the CPU build at the same precision. Synthetic projects in tmp dirs only; the real
root holdout/ is never touched."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("numba")

from quantcrucible.config.loader import parse_user_config
from quantcrucible.config.lock import open_campaign, sha256_file
from quantcrucible.config.schema import Compute
from quantcrucible.data.holdout_split import carve
from quantcrucible.execution.kernels import backend
from quantcrucible.holdout.campaign import HoldoutRefused
from quantcrucible.holdout.evaluator_proc import evaluate
from quantcrucible.ledger.db import Ledger
from quantcrucible.ledger.records import Event, GateResultRecord, TrialRecord
from quantcrucible.validation.archive import StrategyArchive
from quantcrucible.validation.engine_router import EngineRouter
from quantcrucible.validation.gates import G4_PBO
from quantcrucible.validation.numerics import numerics_tag
from quantcrucible.validation.portfolio import PortfolioRule, build_and_record
from quantcrucible.validation.run import derived_settings
from tests.factories import make_bars
from tests.holdout.test_evaluator import HOLDOUT, SYMBOL, Project, passes
from tests.validation.test_engine_router import FakeSandbox, _genome_job
from tests.validation.test_pbo_gate import PARAMS, ZOO

CPU = Compute("cpu_kernel", 0.0)  # no sandbox audit: the comparison is the test's


def build(tmp_path: Path, precision: str = "float64", genome: bool = True) -> Project:
    root = tmp_path / "proj"
    cfg = parse_user_config({
        "research": {
            "holdout_pass": -100.0, "data": {"symbols": [SYMBOL]},
            "backtest": {"precision": precision},
        }
    })  # fmt: skip
    bars = make_bars(2600, seed=1, symbol=SYMBOL, start="2018-01-01")
    carve(
        {SYMBOL: bars}, HOLDOUT[0], HOLDOUT[1], in_sample_dir=root / "data" / "is",
        holdout_dir=root / "holdout", lock_path=root / "holdout.lock", harden=False,
    )  # fmt: skip
    (root / "ledger").mkdir(parents=True)
    ledger = Ledger.open(root / "ledger" / "crucible.db")
    derived = derived_settings("joint")
    derived["exit_protocol"] = "bracket_timeout_v1"
    derived["backtest_numerics"] = numerics_tag(precision)
    open_campaign(
        cfg, ledger, "c1", root / "config" / "evaluation.lock.yaml",
        holdout_range=f"{HOLDOUT[0]}/{HOLDOUT[1]}",
        holdout_lock_hash=sha256_file(root / "holdout.lock"), derived=derived,
    )  # fmt: skip
    job = _genome_job()
    source = job.source if genome else ZOO
    base: dict[str, Any] = dict(job.params) if genome else dict(PARAMS)
    variants = [base, {**base, "n1": 14}] if genome else [base, {**base, "fast": 25}]
    s_hash = StrategyArchive(root / "results" / "strategies").put(source)
    ts = pd.date_range("2018-01-02", periods=1500, freq="D")
    for i, params in enumerate(variants):
        path = tmp_path / f"m{i}.parquet"
        rets = np.random.default_rng(i).normal(0.004, 0.01, len(ts))
        pd.DataFrame({"ts": ts, "ret": rets}).to_parquet(path, index=False)
        tid = ledger.record_trial(
            TrialRecord(
                run_id="r", campaign_id="c1", candidate_id=f"m{i}", engine="gp", seed=0,
                strategy_hash=s_hash, params=params, universe=SYMBOL, timeframe="1d",
                timerange="t", source="evolution", sharpe_is=1.0, returns_path=str(path),
                verdict="PASS",
            )
        )  # fmt: skip
        ledger.record_gate_result(
            GateResultRecord(campaign_id="c1", candidate_id=f"m{i}", gate=G4_PBO, passed=True,
                             reason="x", trial_id=tid)
        )  # fmt: skip
    p = build_and_record(ledger, "c1", PortfolioRule(), 365, root / "results")
    passes(ledger, "c1", p.portfolio_hash)
    from quantcrucible.validation.freeze import freeze_campaign

    freeze_campaign(ledger, "c1", p.portfolio_hash)
    return Project(root, ledger, p.portfolio_hash)


def _unclaimed(proj: Project) -> None:
    campaign = proj.ledger.campaign("c1")
    assert proj.ledger.holdout_access("c1") is None and not proj.ledger.holdout_claimed("c1")
    assert campaign is not None and campaign.status == "FROZEN"


@pytest.mark.parametrize("precision", ["float64", "float32"])
def test_a_genome_portfolio_is_judged_on_the_kernels(tmp_path: Path, precision: str) -> None:
    proj = build(tmp_path / "engine", precision)
    box = FakeSandbox()
    verdict = evaluate(proj.root, proj.portfolio_hash, compute=CPU, sandbox=box)
    assert verdict.verdict == "PASS" and not box.jobs  # no member reached the sandbox
    access = proj.ledger.holdout_access("c1")
    assert access is not None and access.sharpe_oos == verdict.sharpe_oos
    if precision == "float64":  # the canonical engine, run as the holdout runner, agrees exactly
        canon = build(tmp_path / "canon", precision)
        want = evaluate(canon.root, canon.portfolio_hash, FakeSandbox())
        assert np.float64(want.sharpe_oos).tobytes() == np.float64(verdict.sharpe_oos).tobytes()


def test_float32_refuses_a_member_without_a_genome_before_the_claim(tmp_path: Path) -> None:
    proj = build(tmp_path, "float32", genome=False)
    box = FakeSandbox()
    with pytest.raises(HoldoutRefused, match="float32 member has no kernel"):
        evaluate(proj.root, proj.portfolio_hash, compute=CPU, sandbox=box)
    assert not box.jobs
    _unclaimed(proj)


def test_float32_refuses_when_the_cpu_build_fails_its_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(backend, "cpu_check", lambda _dtype: False)
    proj = build(tmp_path, "float32")
    with pytest.raises(HoldoutRefused, match="CPU kernel"):
        evaluate(proj.root, proj.portfolio_hash, compute=CPU, sandbox=FakeSandbox())
    _unclaimed(proj)


def test_float32_refuses_the_sandbox_setting(tmp_path: Path) -> None:
    proj = build(tmp_path, "float32")
    with pytest.raises(HoldoutRefused, match="sandbox"):
        evaluate(proj.root, proj.portfolio_hash, compute=Compute("sandbox", 0.0))
    _unclaimed(proj)


def test_a_device_fault_mid_run_continues_on_the_cpu_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """INV-110: a GPU fault after the claim neither burns the holdout nor changes the answer."""
    original = EngineRouter._compute_on

    def faulty(self: EngineRouter, plan: Any, kind: str, target: str) -> Any:
        if target == "cuda":
            raise RuntimeError("device lost")
        return original(self, plan, kind, target)

    monkeypatch.setattr(EngineRouter, "_cuda_healthy", lambda self: self._cuda_ok is not False)
    monkeypatch.setattr(EngineRouter, "_compute_on", faulty)
    proj = build(tmp_path / "fault", "float32")
    box = FakeSandbox()
    got = evaluate(proj.root, proj.portfolio_hash, compute=Compute("gpu", 0.0), sandbox=box)
    assert got.verdict == "PASS" and not box.jobs
    assert proj.ledger.events_named(Event.ENGINE_FALLBACK)
    monkeypatch.undo()
    clean = build(tmp_path / "clean", "float32")
    want = evaluate(clean.root, clean.portfolio_hash, compute=CPU, sandbox=FakeSandbox())
    assert got.sharpe_oos == want.sharpe_oos
