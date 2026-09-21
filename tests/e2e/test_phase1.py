"""Phase-1 gate, end to end (Architecture §7 phase 1): hand-written strategies through
①a → ①b → ② → ③ → ④, portfolio construction, ⑤ → ⑥′, calibration (5b), rebuild, freeze and
the holdout evaluator — real containers, a real ledger. ADR-0018.

Synthetic data only (a market with persistent trends, so a trend follower *can* pass; a second
"vendor" printing the same market with small differences; a synthetic holdout in a tmp dir) —
never real market data, never the root holdout/.
"""

from __future__ import annotations

from datetime import date
from importlib.resources import files
from pathlib import Path

import pytest

from quantcrucible.config.loader import parse_user_config
from quantcrucible.config.lock import open_campaign, read_lock, sha256_file
from quantcrucible.core.strategy.base import Bars
from quantcrucible.data.holdout_split import carve
from quantcrucible.data.store import ResearchStore, parse_range
from quantcrucible.holdout.evaluator_proc import evaluate
from quantcrucible.ledger.db import Ledger
from quantcrucible.ledger.records import Event
from quantcrucible.validation.freeze import freeze_campaign
from quantcrucible.validation.gates import G1A_STATIC, G1B_DYNAMIC, G2_MINBTL, G3_IS, G4_PBO
from quantcrucible.validation.portfolio_dsr import G5_DSR, G6P_ROBUSTNESS
from quantcrucible.validation.research_run import (
    Phase1Result,
    ResearchSession,
    run_phase1,
    submit,
)
from quantcrucible.validation.run import derived_settings
from quantcrucible.validation.sandbox import SandboxRunner
from tests.factories import make_trending_bars, second_vendor

pytestmark = [pytest.mark.docker, pytest.mark.slow]

SYMBOLS = ("A/USDT", "B/USDT")
HOLDOUT = (date(2024, 2, 1), date(2025, 2, 1))  # the tail of the synthetic series
ZOO = ("ema_crossover", "sma_trend", "rsi_momentum")
IND_IMPORT = "from quantcrucible.core.strategy.registry import ind"
CANDIDATE_GATES = [G1A_STATIC, G1B_DYNAMIC, G2_MINBTL, G3_IS, G4_PBO]


class Phase1:
    def __init__(self, root: Path, image: str) -> None:
        self.root = root
        cfg = parse_user_config({
            "research": {
                "data": {"symbols": list(SYMBOLS)},
                "pbo_grid": {"values_per_param": 3, "range": 0.3, "max_configs": 10},
                "calibration": {"enabled": True, "budget_per_strategy": 3},
                "holdout_pass": 0.5,
            }
        })  # fmt: skip
        full = {s: make_trending_bars(2_600, seed=7 + i, symbol=s, regime=100)
                for i, s in enumerate(SYMBOLS)}  # fmt: skip
        carve(
            full, HOLDOUT[0], HOLDOUT[1], in_sample_dir=root / "data" / "is",
            holdout_dir=root / "holdout", lock_path=root / "holdout.lock", harden=False,
        )  # fmt: skip
        (root / "ledger").mkdir()
        self.ledger = Ledger.open(root / "ledger" / "crucible.db")
        lock_path = root / "config" / "evaluation.lock.yaml"
        open_campaign(
            cfg, self.ledger, "c-p1", lock_path, holdout_range=f"{HOLDOUT[0]}/{HOLDOUT[1]}",
            holdout_lock_hash=sha256_file(root / "holdout.lock"),
            derived=derived_settings("joint"),
        )  # fmt: skip
        store = ResearchStore(root / "data" / "is", [parse_range(f"{HOLDOUT[0]}/{HOLDOUT[1]}")])
        is_data: dict[str, Bars] = {s: store.bars(s, "1d") for s in SYMBOLS}
        self.image = image
        self.session = ResearchSession(
            ledger=self.ledger, lock=read_lock(lock_path), campaign_id="c-p1", is_data=is_data,
            sandbox=SandboxRunner(image), results_dir=root / "results",
            second_is_data={s: second_vendor(b) for s, b in is_data.items()},
        )  # fmt: skip
        sources = {n: files("quantcrucible.core.zoo").joinpath(f"{n}.py").read_text("utf-8")
                   for n in ZOO}  # fmt: skip
        self.result: Phase1Result = run_phase1(self.session, sources)


@pytest.fixture(scope="module")
def phase1(tmp_path_factory: pytest.TempPathFactory, sandbox_image: str) -> Phase1:
    return Phase1(tmp_path_factory.mktemp("phase1"), sandbox_image)


def test_ema_crossover_clears_every_candidate_gate(phase1: Phase1) -> None:
    outcome = phase1.result.candidates["ema_crossover"]
    assert outcome.passed, [(r.gate, r.reason) for r in outcome.results]
    first_run = [g for g, _, _ in phase1.ledger.gate_results("ema_crossover")][:5]
    assert first_run == CANDIDATE_GATES


def test_portfolio_clears_5_and_6p(phase1: Phase1) -> None:
    portfolio, outcome = phase1.result.final
    assert outcome.passed, [(r.gate, r.reason) for r in outcome.results]
    assert [r.gate for r in outcome.results] == [G5_DSR, G6P_ROBUSTNESS]
    assert portfolio.members
    detail = phase1.ledger.gate_result_details(portfolio.portfolio_hash, G5_DSR)[-1]
    assert detail["dsr_n_eff"] >= 0.95 and "dsr_n_raw" in detail  # both counts reported


def test_ledger_separates_audit_from_trials(phase1: Phase1) -> None:
    stats = phase1.ledger.trial_stats()
    rows = phase1.ledger.trials("c-p1")
    assert stats.n_raw == len(rows) and stats.var_sr is not None  # N and V[SR] computable
    assert 1 <= stats.n_eff <= stats.n_raw
    assert {r.source for r in rows} == {"manual", "param_opt"}
    events = phase1.ledger.events("c-p1")
    submitted = sum(e == Event.CANDIDATE_SUBMITTED for e, _ in events)
    assert submitted == len(ZOO) + sum(c.evaluations + 1 for c in phase1.result.calibration)
    # a submission rejected before ③ is audited, but it is no trial
    ema = files("quantcrucible.core.zoo").joinpath("ema_crossover.py").read_text("utf-8")
    tampered = ema.replace(IND_IMPORT, IND_IMPORT + "\nimport os")
    outcome = submit(phase1.session, tampered, "tampered")
    assert not outcome.passed and outcome.trial_id is None
    after = [e for e, _ in phase1.ledger.events("c-p1")][len(events) :]
    assert after == [Event.CANDIDATE_SUBMITTED, Event.TEMPLATE_TAMPER]
    assert len(phase1.ledger.trials("c-p1")) == len(rows)


def test_calibration_rows_and_new_variant(phase1: Phase1) -> None:
    assert phase1.result.calibration, "calibration is enabled in this campaign"
    first, final = phase1.result.first[0], phase1.result.final[0]
    assert phase1.ledger.total_portfolio_variants() == (
        1 if first.portfolio_hash == final.portfolio_hash else 2
    )


def test_freeze_then_one_holdout_opening(phase1: Phase1) -> None:
    portfolio, _ = phase1.result.final
    freeze_campaign(phase1.ledger, "c-p1", portfolio.portfolio_hash)
    verdict = evaluate(phase1.root, portfolio.portfolio_hash, SandboxRunner(phase1.image))
    assert verdict.verdict in ("PASS", "FAIL")
    access = phase1.ledger.holdout_access("c-p1")
    assert access is not None and access.portfolio_hash == portfolio.portfolio_hash
    campaign = phase1.ledger.campaign("c-p1")
    assert campaign is not None and campaign.status == "BURNED"
