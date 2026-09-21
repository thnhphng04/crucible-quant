"""Holdout evaluator + campaign state machine (§4.2, P6, INV-07/08/09/24, ADR-0016).

Every test builds a SYNTHETIC project in a tmp dir — its own ledger, lock, in-sample data and
holdout. The real root holdout/ is never touched.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest
import yaml

from quantcrucible.config.loader import parse_user_config
from quantcrucible.config.lock import open_campaign, read_lock, sha256_file
from quantcrucible.data.holdout_split import carve
from quantcrucible.holdout.campaign import HoldoutRefused, claim, find_frozen
from quantcrucible.holdout.evaluator_proc import evaluate, main
from quantcrucible.ledger.db import Ledger
from quantcrucible.ledger.records import GateResultRecord, TrialRecord, utc_now
from quantcrucible.validation.archive import StrategyArchive
from quantcrucible.validation.freeze import FreezeError, freeze_campaign
from quantcrucible.validation.gates import G4_PBO, GateContext, GatePipeline
from quantcrucible.validation.portfolio import PortfolioRule, build_and_record
from quantcrucible.validation.portfolio_dsr import G5_DSR, G6P_ROBUSTNESS, SNAPSHOT_KEY
from quantcrucible.validation.run import derived_settings
from quantcrucible.validation.sandbox import SandboxJob, SandboxResult
from tests.factories import make_bars
from tests.validation.test_pbo_gate import ZOO, cand

HOLDOUT = (date(2024, 1, 1), date(2024, 12, 31))
SYMBOL = "BTC/USDT"


class FakeRunner:
    """Returns = edge (from params) − fees + noise, on whatever bars the job carries."""

    def __init__(self) -> None:
        self.jobs: list[SandboxJob] = []

    def run(self, job: SandboxJob) -> SandboxResult:
        self.jobs.append(job)
        bars = next(iter(job.bars.values()))
        rng = np.random.default_rng(int(job.params["seed"]))
        net = float(job.params["edge"]) - float(job.options["costs"]["fee_rate"])
        rets = net + rng.normal(0, 0.01, len(bars))
        result = {"ts": [str(t) for t in bars.ts], "returns": rets[1:].tolist()}
        return SandboxResult(True, {"ok": True, "result": result}, "", "", 0, False, None, 0.1)


@dataclass
class Project:
    root: Path
    ledger: Ledger
    portfolio_hash: str


def build_project(
    tmp_path: Path, edge: float = 0.004, holdout_pass: float | None = 0.5, freeze: bool = True
) -> Project:
    root = tmp_path / "proj"
    cfg = parse_user_config(
        {"research": {"holdout_pass": holdout_pass, "data": {"symbols": [SYMBOL]}}}
    )
    bars = make_bars(2600, seed=1, symbol=SYMBOL, start="2018-01-01")  # through 2025
    carve(
        {SYMBOL: bars}, HOLDOUT[0], HOLDOUT[1], in_sample_dir=root / "data" / "is",
        holdout_dir=root / "holdout", lock_path=root / "holdout.lock", harden=False,
    )  # fmt: skip
    (root / "ledger").mkdir(parents=True)
    ledger = Ledger.open(root / "ledger" / "crucible.db")
    open_campaign(
        cfg, ledger, "c1", root / "config" / "evaluation.lock.yaml",
        holdout_range=f"{HOLDOUT[0]}/{HOLDOUT[1]}",
        holdout_lock_hash=sha256_file(root / "holdout.lock"), derived=derived_settings("joint"),
    )  # fmt: skip
    s_hash = StrategyArchive(root / "results" / "strategies").put(ZOO)
    ts = pd.date_range("2018-01-02", periods=1500, freq="D")
    for i in range(2):
        params = {"edge": edge, "seed": i}
        path = tmp_path / f"m{i}.parquet"
        rets = np.random.default_rng(i).normal(0.004, 0.01, len(ts))
        pd.DataFrame({"ts": ts, "ret": rets}).to_parquet(path, index=False)
        tid = ledger.record_trial(
            TrialRecord(
                run_id="r", campaign_id="c1", candidate_id=f"m{i}", engine="manual", seed=0,
                strategy_hash=s_hash, params=params, universe=SYMBOL, timeframe="1d",
                timerange="t", source="manual", sharpe_is=1.0, returns_path=str(path),
                verdict="PASS",
            )
        )  # fmt: skip
        ledger.record_gate_result(
            GateResultRecord(campaign_id="c1", candidate_id=f"m{i}", gate=G4_PBO, passed=True,
                             reason="x", trial_id=tid)
        )  # fmt: skip
    p = build_and_record(ledger, "c1", PortfolioRule(), 365, root / "results")
    passes(ledger, "c1", p.portfolio_hash)
    if freeze:
        freeze_campaign(ledger, "c1", p.portfolio_hash)
    return Project(root, ledger, p.portfolio_hash)


def passes(ledger: Ledger, campaign_id: str, p_hash: str) -> None:
    """⑤ and ⑥′ passes computed on the ledger as it is now (what PortfolioPipeline records)."""
    for gate in (G5_DSR, G6P_ROBUSTNESS):
        ledger.record_gate_result(
            GateResultRecord(campaign_id=campaign_id, candidate_id=p_hash, gate=gate,
                             passed=True, reason="ok", detail={SNAPSHOT_KEY: ledger.snapshot()})
        )  # fmt: skip


def run_main(
    proj: Project, capsys: pytest.CaptureFixture[str], runner: Any = None
) -> tuple[int, str]:
    code = main(
        ["--portfolio", proj.portfolio_hash, "--root", str(proj.root)], runner or FakeRunner()
    )
    return code, capsys.readouterr().out


def test_output_is_single_token(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """INV-09: PASS or FAIL, nothing else; sharpe_oos is stored, never printed."""
    proj = build_project(tmp_path)
    code, out = run_main(proj, capsys)
    assert code == 0 and out == "PASS\n"
    access = proj.ledger.holdout_access("c1")
    assert access is not None and access.verdict == "PASS" and access.sharpe_oos is not None
    assert f"{access.sharpe_oos:.2f}" not in out
    campaign = proj.ledger.campaign("c1")
    assert campaign is not None and campaign.status == "BURNED"


def test_fail_verdict(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    proj = build_project(tmp_path, edge=-0.002)
    code, out = run_main(proj, capsys)
    assert (code, out) == (0, "FAIL\n")


def test_members_run_on_warmup_plus_the_holdout_only(tmp_path: Path) -> None:
    proj = build_project(tmp_path)
    runner = FakeRunner()
    evaluate(proj.root, proj.portfolio_hash, runner)
    (bars,) = runner.jobs[0].bars.values()
    start = np.datetime64(HOLDOUT[0])
    n_holdout = int(np.sum(bars.ts >= start))
    assert n_holdout > 300 and len(bars) - n_holdout == 400  # the locked lookback as warm-up
    assert bars.ts[-1] < np.datetime64(HOLDOUT[1])
    assert runner.jobs[0].kind == "backtest" and len(runner.jobs) == 2


def test_second_opening_raises(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """INV-07 through the evaluator: the campaign is BURNED; the DB PK is the backstop."""
    proj = build_project(tmp_path)
    run_main(proj, capsys)
    with pytest.raises(HoldoutRefused, match="BURNED"):
        evaluate(proj.root, proj.portfolio_hash, FakeRunner())
    code, out = run_main(proj, capsys)
    assert code == 2 and out == ""


def test_tampered_holdout_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """INV-08: a modified holdout file ⇒ refuse before reading it; nothing recorded."""
    proj = build_project(tmp_path)
    target = next((proj.root / "holdout").glob("*.parquet"))
    os.chmod(target, stat.S_IREAD | stat.S_IWRITE)  # the synthetic carve left it read-only
    frame = pd.read_parquet(target)
    frame.loc[0, "close"] = float(frame["close"].iloc[0]) * 1.01
    frame.to_parquet(target, index=False)
    code, out = run_main(proj, capsys)
    assert code == 2 and out == ""
    assert proj.ledger.holdout_access("c1") is None
    campaign = proj.ledger.campaign("c1")
    assert campaign is not None and campaign.status == "FROZEN"


def test_refuses_without_threshold(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """INV-24: D4 unset ⇒ the holdout stays closed."""
    proj = build_project(tmp_path, holdout_pass=None)
    runner = FakeRunner()
    code, out = run_main(proj, capsys, runner)
    assert code == 2 and out == "" and runner.jobs == []


def test_unfrozen_or_unknown_portfolio_refused(tmp_path: Path) -> None:
    proj = build_project(tmp_path, freeze=False)
    with pytest.raises(HoldoutRefused, match="FROZEN"):
        evaluate(proj.root, proj.portfolio_hash, FakeRunner())
    with pytest.raises(HoldoutRefused):
        evaluate(proj.root, "0" * 64, FakeRunner())


def test_freeze_rules(tmp_path: Path) -> None:
    proj = build_project(tmp_path, freeze=False)
    with pytest.raises(FreezeError, match="not a portfolio variant"):
        freeze_campaign(proj.ledger, "c1", "f" * 64)
    freeze_campaign(proj.ledger, "c1", proj.portfolio_hash)
    with pytest.raises(FreezeError, match="FROZEN"):
        freeze_campaign(proj.ledger, "c1", proj.portfolio_hash)
    # nothing may be added once frozen (§4.2 step 2)
    with pytest.raises(RuntimeError, match="FROZEN"):
        GatePipeline([]).run(cand(), GateContext(proj.ledger, {}, {}))
    with pytest.raises(ValueError, match="not OPEN"):
        build_and_record(proj.ledger, "c1", PortfolioRule(max_corr=0.9), 365, tmp_path / "r")


def test_freeze_requires_gates_5_and_6p(tmp_path: Path) -> None:
    proj = build_project(tmp_path, freeze=False)
    proj.ledger.record_gate_result(
        GateResultRecord(campaign_id="c1", candidate_id=proj.portfolio_hash, gate=G6P_ROBUSTNESS,
                         passed=False, reason="drop")
    )  # fmt: skip
    with pytest.raises(FreezeError, match="g6p"):
        freeze_campaign(proj.ledger, "c1", proj.portfolio_hash)


def test_entry_point_is_its_own_process(tmp_path: Path) -> None:
    """Layer 3: a separate process that answers with one token or refuses on stderr."""
    root = tmp_path / "empty"
    (root / "ledger").mkdir(parents=True)
    res = subprocess.run(
        [sys.executable, "-m", "quantcrucible.holdout.evaluator_proc", "--portfolio", "x",
         "--root", str(root)],
        capture_output=True, text=True, check=False, timeout=120,
    )  # fmt: skip
    assert res.returncode == 2 and res.stdout == "" and "refused" in res.stderr


# ── review fixes: stale gate results, claims, errors after the claim, reuse ────────────


def test_freeze_refuses_gate_results_from_an_older_ledger(tmp_path: Path) -> None:
    """Review finding 3: a trial added after ⑤ raises N — the old DSR pass is stale."""
    proj = build_project(tmp_path, freeze=False)
    proj.ledger.record_trial(
        TrialRecord(
            run_id="r", campaign_id="c1", candidate_id="late", engine="manual", seed=0,
            strategy_hash="h", params={}, universe=SYMBOL, timeframe="1d", timerange="t",
            source="manual", sharpe_is=-1.0, returns_path="x.parquet", verdict="PASS",
        )
    )  # fmt: skip
    with pytest.raises(FreezeError, match="older ledger"):
        freeze_campaign(proj.ledger, "c1", proj.portfolio_hash)
    passes(proj.ledger, "c1", proj.portfolio_hash)  # ⑤ → ⑥′ re-run on the current ledger
    freeze_campaign(proj.ledger, "c1", proj.portfolio_hash)


class BrokenRunner(FakeRunner):
    def run(self, job: SandboxJob) -> SandboxResult:
        raise OSError("docker went away")


def test_an_error_after_the_claim_still_consumes_the_holdout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Review finding 5: fail after reading the holdout ⇒ BURNED, FAIL, never retried."""
    proj = build_project(tmp_path)
    code, out = run_main(proj, capsys, BrokenRunner())
    assert code == 3 and out == ""
    campaign = proj.ledger.campaign("c1")
    access = proj.ledger.holdout_access("c1")
    assert campaign is not None and campaign.status == "BURNED"
    assert access is not None and access.verdict == "FAIL" and access.sharpe_oos is None
    code, out = run_main(proj, capsys)  # a retry with a working runner is refused
    assert code == 2 and out == ""


def test_a_concurrent_claim_wins_once(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Two evaluators that both passed preflight: the DB claim lets exactly one through."""
    proj = build_project(tmp_path)
    other = Ledger.open(proj.root / "ledger" / "crucible.db")  # the other process
    campaign = other.campaign("c1")
    assert campaign is not None and campaign.holdout_lock_hash is not None
    other.claim_holdout(
        "c1", proj.portfolio_hash, campaign.holdout_range, campaign.holdout_lock_hash
    )
    runner = FakeRunner()
    code = main(["--portfolio", proj.portfolio_hash, "--root", str(proj.root)], runner)
    captured = capsys.readouterr()
    assert code == 2 and captured.out == "" and runner.jobs == []  # nothing ran on the holdout
    assert "already opened" in captured.err
    with pytest.raises(HoldoutRefused, match="could not claim"):  # the race itself: DB decides
        claim(proj.ledger, *find_frozen(proj.ledger, proj.portfolio_hash), utc_now())


def test_a_used_holdout_is_never_reopened_by_a_new_campaign(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Review finding 4: campaign 2 on the same holdout (registered straight in the ledger,
    past the lock writer that refuses it too) is refused by the evaluator and the DB."""
    proj = build_project(tmp_path)
    assert run_main(proj, capsys) == (0, "PASS\n")
    lock_path = proj.root / "config" / "evaluation.lock.yaml"
    content = read_lock(lock_path)
    content["campaign_id"] = "c2"
    os.chmod(lock_path, stat.S_IREAD | stat.S_IWRITE)
    lock_path.write_text(yaml.safe_dump(content), encoding="utf-8")
    proj.ledger.open_campaign(
        "c2", f"{HOLDOUT[0]}/{HOLDOUT[1]}", sha256_file(lock_path),
        sha256_file(proj.root / "holdout.lock"),
    )  # fmt: skip
    for t in proj.ledger.trials("c1"):  # campaign 2 measures the same strategies again
        tid = proj.ledger.record_trial(
            TrialRecord(
                run_id="r2", campaign_id="c2", candidate_id=t.candidate_id, engine="manual",
                seed=0, strategy_hash=t.strategy_hash, params=dict(t.params), universe=SYMBOL,
                timeframe="1d", timerange="t", source="manual", sharpe_is=1.0,
                returns_path=t.returns_path, verdict="PASS",
            )
        )  # fmt: skip
        proj.ledger.record_gate_result(
            GateResultRecord(campaign_id="c2", candidate_id=t.candidate_id, gate=G4_PBO,
                             passed=True, reason="x", trial_id=tid)
        )  # fmt: skip
    p2 = build_and_record(proj.ledger, "c2", PortfolioRule(max_corr=0.95), 365, tmp_path / "r2")
    passes(proj.ledger, "c2", p2.portfolio_hash)
    freeze_campaign(proj.ledger, "c2", p2.portfolio_hash)
    runner = FakeRunner()
    code = main(["--portfolio", p2.portfolio_hash, "--root", str(proj.root)], runner)
    captured = capsys.readouterr()
    assert code == 2 and captured.out == "" and runner.jobs == []
    assert "already used" in captured.err
    assert proj.ledger.holdout_access("c2") is None and not proj.ledger.holdout_claimed("c2")
