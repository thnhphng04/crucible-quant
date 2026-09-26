"""Phase-2 comparison protocol and report (arch §3.1.11, ADR-0027, P2-15) — INV-70."""

import dataclasses
from pathlib import Path
from typing import Any

import pytest

from quantcrucible.agent import compare as compare_module
from quantcrucible.agent.compare import (
    MONITOR_SCENARIOS,
    PROTOCOL,
    RANKING_CTX,
    RANKING_SCENARIOS,
    ProtocolError,
    assert_protocol_predates_trials,
    compare,
    decide,
    divergence_rule,
    lock_protocol,
    monitor_fingerprint,
    protocol,
    protocol_hash,
    ranking_fingerprint,
)
from quantcrucible.agent.evolution import ranking as ranking_module
from quantcrucible.agent.evolution.ranking import scores
from quantcrucible.agent.monitor import EarlyStop
from quantcrucible.agent.run import EvolveError, evolve
from quantcrucible.ledger.db import Ledger
from quantcrucible.ledger.records import Event, GenerationEvent
from quantcrucible.validation.cpcv import (
    DEFAULT_DIVERGENCE,
    DegradationPoint,
    DivergenceRule,
    is_oos_diverging,
)
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


def test_the_protocol_hash_covers_the_monitor_thresholds(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fingerprint alone is not a lock: moving `flat_slope` from 1e-9 to 1.1e-9 changes what a
    slowly drifting arm is called while flipping none of `MONITOR_SCENARIOS`. The thresholds are
    therefore hashed as data (ADR-0028 amendment)."""
    slow = [DegradationPoint(f"p{k}", 1.05e-9 * k, -1.05e-9 * k) for k in range(3)]
    loose = DivergenceRule(flat_slope=1.1e-9)
    assert is_oos_diverging(slow) and not is_oos_diverging(slow, loose)
    assert monitor_fingerprint(loose) == monitor_fingerprint()  # the scenarios do not notice

    before = protocol_hash()
    monkeypatch.setattr(compare_module, "DEFAULT_DIVERGENCE", loose)
    assert protocol()["monitor"]["rule"]["flat_slope"] == 1.1e-9
    assert protocol_hash() != before


def test_the_monitor_runs_the_rule_its_campaign_locked(ledger: Ledger) -> None:
    """The hashed rule is the operative one, not decoration: what a campaign locked is what the
    monitor and the report are then read with."""
    assert divergence_rule(ledger, "c1") == DEFAULT_DIVERGENCE  # no protocol yet
    lock_protocol(ledger, "c1")
    assert divergence_rule(ledger, "c1") == DEFAULT_DIVERGENCE
    assert protocol()["monitor"]["rule"] == dataclasses.asdict(DEFAULT_DIVERGENCE)


def test_the_fingerprint_scenarios_straddle_the_decision_boundary() -> None:
    """A fingerprint whose verdicts never differ would hash the same under any rule."""
    verdicts = [is_oos_diverging(points) for points in MONITOR_SCENARIOS]
    assert True in verdicts and False in verdicts


@pytest.mark.parametrize("points,expected", tuple(zip(MONITOR_SCENARIOS, (
    True, False, False, True, True, True, False, False,
    False, False, False, True, True, False, False, True, True,
), strict=True)))  # fmt: skip
def test_monitor_probe_verdicts(points: tuple[DegradationPoint, ...], expected: bool) -> None:
    """Explicit outcomes catch changes to tolerance, sample count and whole-curve OLS."""
    assert is_oos_diverging(points) is expected


def test_a_recovered_arm_keeps_the_warning_it_earned(ledger: Ledger, tmp_path: Path) -> None:
    """ADR-0028 amendment: `divergence_warnings` comes from the ledger, not from the curve as it
    stands now. An arm that diverged at checkpoint 3 and recovered at 4 stays in the report."""
    lock_protocol(ledger, "c1")
    warn = EarlyStop(ledger, "c1", "run", every=1, mode="warn")
    for k, (is_sr, oos) in enumerate([(1.0, 0.8), (1.5, 0.5), (2.0, 0.2), (9.0, 9.0)]):
        _candidate(ledger, f"w{k}", "random", 1, is_sr, ["trend"], g4_pass=True,
                   g4_detail={"pbo": 0.1, "cpcv_path_sharpes": [oos]})  # fmt: skip
        warn(("random", 1), k + 1)
    report = compare(_session(ledger, tmp_path), seeds=3, with_portfolios=False)
    assert not report["per_seed"]["random"][1]["diverging"]  # the curve recovered
    assert report["divergence_warnings"] == ["random-s1"]  # the event did not


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


# ── the ranking is part of the treatment, so the protocol carries it (ADR-0029) ───────────────
def test_the_protocol_hash_covers_the_ranking_lambdas(monkeypatch: pytest.MonkeyPatch) -> None:
    """INV-80. The fingerprint alone is not a lock: a λ nudged by 1e-9 changes every score by
    less than the sixth decimal the fingerprint rounds to, so it flips nothing — yet it is a
    different engine. The λ are therefore hashed as data, exactly as ADR-0028's amendment did
    for the monitor's thresholds."""
    before_hash, before_fp = protocol_hash(), ranking_fingerprint()
    monkeypatch.setattr(ranking_module, "LAMBDA_PLATEAU", 0.05 + 1e-9)
    assert ranking_fingerprint() == before_fp  # below the rounding: the scenarios do not notice
    monkeypatch.setattr(compare_module, "LAMBDA_PLATEAU", 0.05 + 1e-9)
    assert protocol()["ranking"]["lambdas"]["plateau"] == 0.05 + 1e-9
    assert protocol_hash() != before_hash  # but the data does


def test_the_ranking_fingerprint_notices_a_reordering() -> None:
    """What the fingerprint is for: an arithmetic rewrite that keeps every λ but changes who the
    engine would breed from."""
    before = ranking_fingerprint()
    original = ranking_module.scores

    def inverted(entries: Any, ctx: Any) -> dict[str, float]:
        return {cid: -value for cid, value in original(entries, ctx).items()}

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(compare_module, "scores", inverted)
        assert ranking_fingerprint() != before
    assert ranking_fingerprint() == before  # and the rewrite undone leaves it alone


def test_the_ranking_scenarios_straddle_the_decision_boundary() -> None:
    """A probe set that decided the same thing everywhere would hash the same under any ranking.

    These span it: the degenerate populations (one entry, two entries), a fully tied one where
    only the auxiliary terms can separate the candidates — and there they do reorder it — and
    populations where the returns term decides. What the fingerprint hashes is the selection
    order together with the rounded scores, and every scenario contributes a distinct pair.
    """
    payloads, flipped = [], 0
    for population in RANKING_SCENARIOS:
        s = scores(population, RANKING_CTX)
        order = sorted(s, key=lambda cid: (-s[cid], cid))
        by_sharpe = sorted(
            s, key=lambda cid: -next(
                e.public.get("sr_obs", float("-inf"))
                for e in population if e.candidate_id == cid
            )
        )  # fmt: skip
        flipped += order != by_sharpe
        payloads.append((tuple(order), tuple(round(s[cid], 6) for cid in order)))
    assert len(set(payloads)) == len(RANKING_SCENARIOS)
    assert flipped >= 1  # the auxiliary terms must be able to decide something
    assert {1, 2} <= {len(p) for p in RANKING_SCENARIOS}


def test_the_ranking_margin_is_reported_for_both_arms_and_decides_nothing(
    ledger: Ledger, tmp_path: Path
) -> None:
    """INV-79 is a diagnostic: it reaches the report and neither `decide` nor `EarlyStop`
    (ADR-0028 — a comparison may not let a measured quantity set its stopping time)."""
    lock_protocol(ledger, "c1")
    _fill(ledger, per_unit=10, gp_pass=6, random_pass=3)
    report = compare(_session(ledger, tmp_path), seeds=3, with_portfolios=False)
    for arm in PROTOCOL["arms"]:
        assert "ranking_margin" in report["per_seed"][arm][0]
    assert "ranking_margin" in PROTOCOL["supporting"]
    assert "ranking_margin" not in repr(report["decision"])
