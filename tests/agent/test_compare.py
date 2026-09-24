"""Phase-2 comparison protocol and report (arch §3.1.11, ADR-0027, P2-15) — INV-70."""

from pathlib import Path
from typing import Any

import pytest

from quantcrucible.agent.compare import (
    MONITOR_SCENARIOS,
    PROTOCOL,
    ProtocolError,
    assert_protocol_predates_trials,
    compare,
    decide,
    lock_protocol,
    monitor_fingerprint,
    protocol,
    protocol_hash,
)
from quantcrucible.agent.run import EvolveError, evolve
from quantcrucible.ledger.db import Ledger
from quantcrucible.ledger.records import Event, GenerationEvent
from quantcrucible.validation import cpcv
from quantcrucible.validation.cpcv import is_oos_diverging
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


def test_a_campaign_is_only_reported_under_the_protocol_it_locked(
    ledger: Ledger, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """INV-70: the locked hash binds. Changing PROTOCOL in the code does not re-interpret a
    campaign that ran under the old one."""
    lock_protocol(ledger, "c1")
    monkeypatch.setitem(PROTOCOL, "beats", "mean(gp) > mean(random)")
    with pytest.raises(ProtocolError, match="locked protocol"):
        assert_protocol_predates_trials(ledger, "c1")
    with pytest.raises(ProtocolError, match="another protocol"):
        lock_protocol(ledger, "c1")
    with pytest.raises(ProtocolError, match="locked protocol"):
        compare(_session(ledger, tmp_path), seeds=3, with_portfolios=False)


def test_the_protocol_is_recorded_once_before_anything_runs(tmp_path: Path) -> None:
    lg = Ledger.open(tmp_path / "ok.db")
    lg.open_campaign("c1", "2024-01-01/2025-01-01", lock_hash="h")
    assert lock_protocol(lg, "c1") == lock_protocol(lg, "c1") == protocol_hash()
    locked = [d for e, _i, d in lg.events_for("c1") if e == Event.PROTOCOL_LOCKED]
    assert len(locked) == 1 and locked[0] == {"protocol": protocol(), "sha256": protocol_hash()}
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


def test_an_unfinished_run_yields_no_engine_decision() -> None:
    """A landslide measured at different budgets is not a result (protocol v2)."""
    landslide = {"gp": [40.0, 41.0, 39.0], "random": [0.0, 0.0, 0.0]}
    assert decide(landslide)["outcome"] == "gp_beats_random"
    half = decide(landslide, unfinished=["random-s0", "random-s1", "random-s2"])
    assert half["outcome"] == "incomplete" and half["unfinished"][0] == "random-s0"


def test_a_diverging_arm_no_longer_withholds_the_decision() -> None:
    """v3 (ADR-0028): the monitor warns, so no arm is cut short and `stopped_early` cannot
    arise. The warning is reported next to the decision, never inside it."""
    assert "stopped_early" not in PROTOCOL["decision"]
    assert PROTOCOL["monitor"]["mode"] == "warn"
    landslide = {"gp": [40.0, 41.0, 39.0], "random": [0.0, 0.0, 0.0]}
    assert decide(landslide)["outcome"] == "gp_beats_random"


def test_the_protocol_hash_covers_the_monitor_behaviour(monkeypatch: pytest.MonkeyPatch) -> None:
    """ADR-0028: `is_oos_diverging` is reachable from the protocol only as a string, so the hash
    folds in what it actually decides. Moving its threshold must invalidate a locked campaign."""
    before_hash, before_print = protocol_hash(), monitor_fingerprint()
    monkeypatch.setattr(cpcv, "FLAT_SLOPE", 10.0)  # nothing is a rising line any more
    assert monitor_fingerprint() != before_print
    assert protocol_hash() != before_hash
    assert protocol()["monitor_fingerprint"] == monitor_fingerprint()


def test_the_fingerprint_scenarios_straddle_the_decision_boundary() -> None:
    """A fingerprint whose verdicts never differ would hash the same under any rule."""
    verdicts = [is_oos_diverging(points) for points in MONITOR_SCENARIOS]
    assert True in verdicts and False in verdicts


def _fill(ledger: Ledger, per_unit: int, gp_pass: int, random_pass: int) -> None:
    for seed in range(3):
        for k in range(per_unit):
            _candidate(ledger, f"g{seed}{k}", "gp", seed, 1.0 + k, ["trend"], g4_pass=k < gp_pass)
            _candidate(ledger, f"r{seed}{k}", "random", seed, 1.0 + k, ["trend"],
                       g4_pass=k < random_pass)  # fmt: skip


def test_report_from_the_ledger(ledger: Ledger, tmp_path: Path) -> None:
    """The campaign's budget is 60 over 2 arms x 3 seeds, so a unit is done at 10 trials."""
    lock_protocol(ledger, "c1")
    _fill(ledger, per_unit=10, gp_pass=5, random_pass=2)
    report = compare(_session(ledger, tmp_path), seeds=3, with_portfolios=False)
    assert [r["trial_efficiency"] for r in report["per_seed"]["gp"]] == [50.0] * 3
    assert [r["trial_efficiency"] for r in report["per_seed"]["random"]] == [20.0] * 3
    assert report["completeness"]["unfinished"] == []
    assert report["completeness"]["per_unit"]["gp-s0"] == {"trials": 10, "quota": 10}
    assert report["decision"]["outcome"] == "gp_beats_random"
    assert report["protocol_sha256"] == protocol_hash()


def test_a_report_before_the_quotas_are_used_is_progress_only(
    ledger: Ledger, tmp_path: Path
) -> None:
    """INV-73: 4 of 10 trials per unit, none of them random's — the arms are not comparable."""
    lock_protocol(ledger, "c1")
    _fill(ledger, per_unit=4, gp_pass=2, random_pass=0)
    report = compare(_session(ledger, tmp_path), seeds=3, with_portfolios=False)
    assert report["decision"]["outcome"] == "incomplete"
    assert len(report["completeness"]["unfinished"]) == 6
    assert report["per_seed"]["gp"][0]["trial_efficiency"] == 50.0  # progress is still shown
