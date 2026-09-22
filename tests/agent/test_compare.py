"""Phase-2 comparison protocol and report (arch §3.1.11, ADR-0027, P2-15) — INV-70."""

from pathlib import Path
from typing import Any

import pytest

from quantcrucible.agent.compare import (
    PROTOCOL,
    ProtocolError,
    assert_protocol_predates_trials,
    compare,
    decide,
    lock_protocol,
    protocol_hash,
)
from quantcrucible.agent.run import EvolveError, evolve
from quantcrucible.ledger.db import Ledger
from quantcrucible.ledger.records import Event, GenerationEvent
from quantcrucible.validation.research_run import ResearchSession
from quantcrucible.validation.run import FEATURE_MAP
from tests.agent.evolution.test_feature_map import _candidate

LOCK: dict[str, Any] = {
    "research": {"engines": {"gp": 0.5, "random": 0.5}, "seeds": 3},
    "derived": {"feature_map": FEATURE_MAP},
}


@pytest.fixture
def ledger(tmp_path: Path) -> Ledger:
    lg = Ledger.open(tmp_path / "l.db")
    lg.open_campaign("c1", "2024-01-01/2025-01-01", lock_hash="h", purpose="harness_test",
                     trial_budget=60)  # fmt: skip
    return lg


def _session(lg: Ledger, tmp_path: Path) -> ResearchSession:
    return ResearchSession(
        ledger=lg, lock=LOCK, campaign_id="c1", is_data={},
        sandbox=None, results_dir=tmp_path,  # type: ignore[arg-type]
    )  # fmt: skip


def _submit(lg: Ledger, cid: str) -> None:
    lg.log_event(GenerationEvent(
        run_id="r", campaign_id="c1", engine="gp", seed=0, agent="engine", model_used="none",
        event=Event.CANDIDATE_SUBMITTED, detail={"candidate_id": cid},
    ))  # fmt: skip


def test_protocol_predates_first_trial(ledger: Ledger, tmp_path: Path) -> None:
    """INV-70: no protocol ⇒ no run and no report; a protocol recorded after a submission is
    refused; once trials exist the protocol can no longer be locked."""
    with pytest.raises(ProtocolError, match="no comparison protocol"):
        assert_protocol_predates_trials(ledger, "c1")
    with pytest.raises(EvolveError, match="protocol"):
        evolve(_session(ledger, tmp_path), ["gp"])
    _submit(ledger, "early")
    lock_protocol(ledger, "c1")  # no trial yet, but a submission already happened
    with pytest.raises(ProtocolError, match="before the protocol"):
        assert_protocol_predates_trials(ledger, "c1")


def test_the_protocol_is_recorded_once_before_anything_runs(tmp_path: Path) -> None:
    lg = Ledger.open(tmp_path / "ok.db")
    lg.open_campaign("c1", "2024-01-01/2025-01-01", lock_hash="h")
    assert lock_protocol(lg, "c1") == lock_protocol(lg, "c1") == protocol_hash()
    locked = [d for e, _i, d in lg.events_for("c1") if e == Event.PROTOCOL_LOCKED]
    assert len(locked) == 1 and locked[0] == {"protocol": PROTOCOL, "sha256": protocol_hash()}
    assert_protocol_predates_trials(lg, "c1")
    _candidate(lg, "a", "gp", 0, 1.0, ["trend"])
    with pytest.raises(ProtocolError, match="already has trials"):
        lock_protocol(lg, "c1")


@pytest.mark.parametrize(
    ("gp", "rnd", "outcome"),
    [
        ([12.0, 14.0, 13.0], [5.0, 6.0, 4.0], "gp_beats_random"),
        ([6.0, 7.0, 5.0], [5.0, 6.0, 4.0], "tie"),  # difference 1.0 == spread 1.0: not beyond
        ([0.5, 0.0, 0.2], [0.3, 0.6, 0.1], "neither_meaningful"),
        ([2.0, 3.0, 2.5], [9.0, 8.0, 10.0], "tie"),  # random ahead is not a GP win
    ],
)
def test_decision_rule(gp: list[float], rnd: list[float], outcome: str) -> None:
    d = decide({"gp": gp, "random": rnd})
    assert d["outcome"] == outcome and d["action"] == PROTOCOL["decision"][outcome]


def test_report_from_the_ledger(ledger: Ledger, tmp_path: Path) -> None:
    lock_protocol(ledger, "c1")
    for seed in range(3):
        for k in range(4):
            _candidate(ledger, f"g{seed}{k}", "gp", seed, 1.0 + k, ["trend"], g4_pass=k < 2)
            _candidate(ledger, f"r{seed}{k}", "random", seed, 1.0 + k, ["trend"], g4_pass=k < 1)
    report = compare(_session(ledger, tmp_path), seeds=3, with_portfolios=False)
    assert [r["trial_efficiency"] for r in report["per_seed"]["gp"]] == [50.0] * 3
    assert [r["trial_efficiency"] for r in report["per_seed"]["random"]] == [25.0] * 3
    assert report["decision"]["outcome"] == "gp_beats_random"
    assert report["protocol_sha256"] == protocol_hash()
