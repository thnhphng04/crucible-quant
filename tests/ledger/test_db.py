"""Ledger (Architecture §4.1, §4.2, ADR-0002) — INV-02, INV-07, INV-23, INV-43."""

import dataclasses
import sqlite3
import statistics
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from quantcrucible.ledger.db import Ledger, LedgerError
from quantcrucible.ledger.records import (
    Event,
    GateResultRecord,
    GenerationEvent,
    HoldoutAccess,
    PortfolioVariant,
    TrialRecord,
)

TABLES = [
    "campaigns",
    "generation_log",
    "trials",
    "clustering_runs",
    "trial_clusters",
    "gate_results",
    "portfolio_variants",
    "holdout_access",
    "holdout_claims",
    "campaign_abandonments",
]


def _event(event: Event = Event.CANDIDATE_SUBMITTED, **kw: object) -> GenerationEvent:
    base: dict[str, object] = dict(
        run_id="r1", campaign_id="c1", engine="manual", seed=0, agent="human",
        model_used="none", event=event,
    )  # fmt: skip
    base.update(kw)
    return GenerationEvent(**base)  # type: ignore[arg-type]


def _trial(sharpe: float, candidate: str = "cand") -> TrialRecord:
    return TrialRecord(
        run_id="r1", campaign_id="c1", candidate_id=candidate, engine="manual", seed=0,
        strategy_hash=f"h-{candidate}", params={"fast": 20}, universe="BTC/USDT",
        timeframe="1d", timerange="2018-01-01/2025-09-21", source="manual",
        sharpe_is=sharpe, returns_path="results/x.parquet", verdict="PASS",
    )  # fmt: skip


@pytest.fixture
def ledger_path(tmp_path: Path) -> Path:
    return tmp_path / "ledger.db"


@pytest.fixture
def ledger(ledger_path: Path) -> Ledger:
    lg = Ledger.open(ledger_path)
    lg.open_campaign("c1", "2025-09-21/2026-09-21", lock_hash="abc")
    return lg


def _populate_every_table(lg: Ledger) -> None:
    lg.log_event(_event())
    tid = lg.record_trial(_trial(1.0))
    lg.record_gate_result(
        GateResultRecord(campaign_id="c1", candidate_id="cand", gate="g3_is", passed=True,
                         reason="ok", trial_id=tid)
    )  # fmt: skip
    lg.record_clustering("onc", {tid: 0})
    lg.record_portfolio_variant(
        PortfolioVariant(portfolio_hash="p1", campaign_id="c1", rule_config={"k": 20},
                         members=[{"h": "x", "w": 1.0}])
    )  # fmt: skip
    lg.transition("c1", "FROZEN")
    now = datetime.now(UTC)
    lg.claim_holdout("c1", "p1", "2025-09-21/2026-09-21", "hl")
    lg.open_campaign("c2", "2027-01-01/2027-12-31", lock_hash="def")
    lg.abandon_campaign("c2", "test")
    lg.record_holdout_access(
        HoldoutAccess("c1", "p1", now - timedelta(seconds=1), now, "2025/2026", "FAIL")
    )


def test_every_table_rejects_delete_at_sql_level(ledger: Ledger, ledger_path: Path) -> None:
    _populate_every_table(ledger)
    raw = sqlite3.connect(ledger_path)  # bypasses the API on purpose
    for table in TABLES:
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            raw.execute(f"DELETE FROM {table}")


def test_update_rejected_by_sql(ledger: Ledger, ledger_path: Path) -> None:
    _populate_every_table(ledger)
    raw = sqlite3.connect(ledger_path)
    for table in TABLES:
        with pytest.raises(sqlite3.DatabaseError, match=r"append-only|campaigns only move"):
            raw.execute(f"UPDATE {table} SET rowid = rowid")


def test_replace_rejected_by_sql(ledger: Ledger, ledger_path: Path) -> None:
    """REPLACE deletes the old row silently (no DELETE trigger fires unless recursive_triggers
    is on), so every existing key must be refused at INSERT time — on a raw connection too."""
    _populate_every_table(ledger)
    raw = sqlite3.connect(ledger_path)
    for table in TABLES:
        for verb in ("INSERT OR REPLACE", "REPLACE"):
            with pytest.raises(sqlite3.DatabaseError, match="append-only"):
                raw.execute(f"{verb} INTO {table} SELECT * FROM {table}")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        raw.execute(
            "INSERT OR REPLACE INTO campaigns (campaign_id, started_at, holdout_range, lock_hash,"
            " holdout_lock_hash, status) VALUES ('c1', '2000-01-01', 'evil', 'evil', NULL, 'OPEN')"
        )
    row = raw.execute("SELECT holdout_range, lock_hash, status FROM campaigns").fetchone()
    assert row == ("2025-09-21/2026-09-21", "abc", "FROZEN")


def test_upsert_cannot_rewrite_a_campaign(ledger: Ledger, ledger_path: Path) -> None:
    raw = sqlite3.connect(ledger_path)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        raw.execute(
            "INSERT INTO campaigns (campaign_id, started_at, holdout_range, lock_hash, status)"
            " VALUES ('c1', 'x', 'evil', 'evil', 'OPEN')"
            " ON CONFLICT (campaign_id) DO UPDATE SET status = 'FROZEN'"
        )


def test_v1_ledger_is_migrated(ledger: Ledger, ledger_path: Path) -> None:
    ledger.close()
    raw = sqlite3.connect(ledger_path, isolation_level=None)
    names = [r[0] for r in raw.execute(
        "SELECT name FROM sqlite_master WHERE type = 'trigger' AND name LIKE '%_no_replace'"
    )]  # fmt: skip
    assert len(names) == len(TABLES)
    for name in names:  # what a v1 ledger looked like
        raw.execute(f"DROP TRIGGER {name}")
    raw.execute("PRAGMA user_version = 1")
    raw.close()
    Ledger.open(ledger_path).close()
    raw = sqlite3.connect(ledger_path)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        raw.execute("INSERT OR REPLACE INTO campaigns SELECT * FROM campaigns")


def test_second_holdout_access_raises(ledger: Ledger) -> None:
    _populate_every_table(ledger)
    now = datetime.now(UTC)
    with pytest.raises(LedgerError):
        ledger.record_holdout_access(
            HoldoutAccess("c1", "p1", now - timedelta(seconds=1), now, "2025/2026", "PASS")
        )


def test_holdout_requires_frozen_campaign(ledger: Ledger) -> None:
    ledger.record_portfolio_variant(
        PortfolioVariant(portfolio_hash="p1", campaign_id="c1", rule_config={}, members=[])
    )
    now = datetime.now(UTC)
    with pytest.raises(LedgerError, match="FROZEN"):
        ledger.claim_holdout("c1", "p1", "2025-09-21/2026-09-21", "hl")
    with pytest.raises(LedgerError, match=r"FROZEN|claimed"):
        ledger.record_holdout_access(
            HoldoutAccess("c1", "p1", now - timedelta(seconds=1), now, "2025/2026", "PASS")
        )


def test_campaign_transitions(ledger: Ledger) -> None:
    with pytest.raises(LedgerError):
        ledger.transition("c1", "BURNED")  # skipping FROZEN
    ledger.transition("c1", "FROZEN")
    with pytest.raises(LedgerError):
        ledger.transition("c1", "OPEN")  # backwards
    ledger.transition("c1", "BURNED")
    with pytest.raises(LedgerError):
        ledger.transition("c1", "FROZEN")
    campaign = ledger.campaign("c1")
    assert campaign is not None and campaign.status == "BURNED"


def test_campaign_fields_immutable(ledger_path: Path, ledger: Ledger) -> None:
    raw = sqlite3.connect(ledger_path)
    with pytest.raises(sqlite3.DatabaseError):
        raw.execute("UPDATE campaigns SET lock_hash = 'forged', status = 'FROZEN'")


def test_campaign_must_start_open(ledger_path: Path, ledger: Ledger) -> None:
    raw = sqlite3.connect(ledger_path)
    with pytest.raises(sqlite3.DatabaseError, match="start OPEN"):
        raw.execute("INSERT INTO campaigns VALUES ('c2', '2026', 'x', 'h', NULL, 'FROZEN')")


def test_trials_read_back(ledger: Ledger) -> None:
    first = ledger.record_trial(_trial(1.5, "a"))
    ledger.open_campaign("c2", "2025-09-21/2026-09-21", lock_hash="def")
    ledger.record_trial(dataclasses.replace(_trial(0.5, "b"), campaign_id="c2"))
    rows = ledger.trials()
    assert [r.candidate_id for r in rows] == ["a", "b"]
    assert rows[0].id == first and rows[0].params == {"fast": 20} and rows[0].sharpe_is == 1.5
    assert [r.candidate_id for r in ledger.trials("c1")] == ["a"]
    assert [r.candidate_id for r in ledger.trials("c2")] == ["b"]


def test_portfolio_variants_and_holdout_read_back(ledger: Ledger) -> None:
    v = PortfolioVariant("p1", "c1", {"k": 20}, [{"h": "x", "w": 1.0}], 1.2, "r.parquet")
    ledger.record_portfolio_variant(v)
    (back,) = ledger.portfolio_variants("c1")
    assert (back.portfolio_hash, back.rule_config, back.members) == ("p1", {"k": 20}, v.members)
    assert ledger.portfolio_variants("nope") == []
    assert ledger.holdout_access("c1") is None
    ledger.transition("c1", "FROZEN")
    now = datetime.now(UTC)
    ledger.claim_holdout("c1", "p1", "2025-09-21/2026-09-21", "hl")
    ledger.record_holdout_access(
        HoldoutAccess("c1", "p1", now - timedelta(seconds=1), now, "2025/2026", "PASS", 0.9)
    )
    got = ledger.holdout_access("c1")
    assert got is not None and got.verdict == "PASS" and got.sharpe_oos == 0.9


def test_gate_result_details(ledger: Ledger) -> None:
    ledger.record_gate_result(
        GateResultRecord(campaign_id="c1", candidate_id="a", gate="g4_pbo", passed=True,
                         reason="x", detail={"n": 1})
    )  # fmt: skip
    assert ledger.gate_result_details("a", "g4_pbo") == [{"n": 1}]
    assert ledger.gate_result_details("a", "g3_is") == []


def test_passed_gate_uses_latest_result(ledger: Ledger) -> None:
    def result(cand: str, passed: bool) -> None:
        ledger.record_gate_result(
            GateResultRecord(campaign_id="c1", candidate_id=cand, gate="g4_pbo",
                             passed=passed, reason="x")
        )  # fmt: skip

    result("a", True)
    result("b", True)
    result("b", False)
    result("c", False)
    assert ledger.passed_gate("c1", "g4_pbo") == {"a"}
    assert ledger.passed_gate("c1", "g3_is") == set()


def test_trial_stats_n_eff_falls_back_to_n_raw(ledger: Ledger) -> None:
    sharpes = [0.5, 1.2, -0.3, 0.9]
    for i, s in enumerate(sharpes):
        ledger.record_trial(_trial(s, candidate=f"c{i}"))
    stats = ledger.trial_stats()
    assert stats.n_raw == 4
    assert stats.n_eff == 4
    assert stats.var_sr == pytest.approx(statistics.pvariance(sharpes))


def test_trial_stats_uses_latest_clustering_run(ledger: Ledger) -> None:
    ids = [ledger.record_trial(_trial(0.1 * i, candidate=f"c{i}")) for i in range(5)]
    ledger.record_clustering("onc", {ids[0]: 0, ids[1]: 0, ids[2]: 1, ids[3]: 1, ids[4]: 1})
    assert ledger.trial_stats().n_eff == 2
    ledger.record_clustering("onc", {i: 0 for i in ids[:4]})  # newer run, 5th trial uncovered
    assert ledger.trial_stats().n_eff == 2  # 1 cluster + 1 uncovered trial (conservative)


def test_trial_stats_ignores_audit_log(ledger: Ledger) -> None:
    for event in (Event.LLM_CALL, Event.COMPILE_FAIL, Event.AST_REJECT, Event.LEAK_REJECT):
        ledger.log_event(_event(event))
    assert ledger.trial_stats().n_raw == 0
    assert ledger.trial_stats().var_sr is None


def test_starved_cells(ledger: Ledger) -> None:
    for _ in range(4):
        ledger.log_event(_event(Event.LLM_CALL, agent="coding", cell_id="A"))
    for _ in range(3):
        ledger.log_event(_event(Event.COMPILE_FAIL, agent="coding", cell_id="A"))
    ledger.log_event(_event(Event.LLM_CALL, agent="coding", cell_id="B"))
    cells = ledger.starved_cells()
    assert [(c.cell_id, c.attempts) for c in cells] == [("A", 4)]
    assert cells[0].fail_rate == pytest.approx(0.75)


def test_event_requires_known_campaign(ledger: Ledger) -> None:
    with pytest.raises(LedgerError):
        ledger.log_event(_event(campaign_id="nope"))


def test_naive_timestamp_rejected(ledger: Ledger) -> None:
    with pytest.raises(ValueError, match="timezone"):
        ledger.log_event(_event(ts=datetime(2026, 1, 1)))  # noqa: DTZ001


def test_reopen_existing_ledger(ledger_path: Path, ledger: Ledger) -> None:
    ledger.record_trial(_trial(1.0))
    ledger.close()
    again = Ledger.open(ledger_path)
    assert again.trial_stats().n_raw == 1


# ── holdout claims and abandoned campaigns (ADR-0016 amendment, ADR-0019) ──────────────


def _frozen(lg: Ledger, cid: str, holdout_range: str) -> None:
    lg.open_campaign(cid, holdout_range, lock_hash=f"lock-{cid}")
    lg.record_portfolio_variant(
        PortfolioVariant(portfolio_hash=f"p-{cid}", campaign_id=cid, rule_config={}, members=[])
    )
    lg.transition(cid, "FROZEN")


def test_opening_needs_a_claim(ledger: Ledger) -> None:
    _frozen(ledger, "c2", "2027-01-01/2027-12-31")
    now = datetime.now(UTC)
    access = HoldoutAccess("c2", "p-c2", now - timedelta(seconds=1), now, "x", "PASS")
    with pytest.raises(LedgerError, match="claimed first"):
        ledger.record_holdout_access(access)
    ledger.claim_holdout("c2", "p-c2", "2027-01-01/2027-12-31", "hl-2027")
    assert ledger.holdout_claimed("c2")
    ledger.record_holdout_access(access)


def test_a_holdout_is_claimed_once_across_campaigns(ledger: Ledger) -> None:
    """Review finding 4: same manifest, or an overlapping period, from another campaign."""
    _frozen(ledger, "c2", "2027-01-01/2027-12-31")
    _frozen(ledger, "c3", "2027-01-01/2027-12-31")
    _frozen(ledger, "c4", "2027-06-01/2028-05-31")
    _frozen(ledger, "c5", "2028-06-01/2029-05-31")
    ledger.claim_holdout("c2", "p-c2", "2027-01-01/2027-12-31", "hl-2027")
    with pytest.raises(LedgerError, match="already used"):
        ledger.claim_holdout("c3", "p-c3", "2027-01-01/2027-12-31", "hl-2027")  # same manifest
    with pytest.raises(LedgerError, match="already used"):
        ledger.claim_holdout("c4", "p-c4", "2027-06-01/2028-05-31", "hl-other")  # overlap
    ledger.claim_holdout("c5", "p-c5", "2028-06-01/2029-05-31", "hl-2028")  # disjoint: fine
    _frozen(ledger, "c6", "2027-12-31/2028-06-01")
    ledger.claim_holdout("c6", "p-c6", "2027-12-31/2028-06-01", "hl-gap")  # ends are exclusive
    with pytest.raises(LedgerError, match="YYYY-MM-DD"):
        ledger.claim_holdout("c3", "p-c3", "2031/2032", "hl-bad")
    with pytest.raises(LedgerError, match="append-only"):
        ledger.claim_holdout("c2", "p-c2", "2030-01-01/2030-12-31", "hl-2030")  # second claim
    assert ledger.used_holdouts() == [
        ("2027-01-01/2027-12-31", "hl-2027"), ("2028-06-01/2029-05-31", "hl-2028"),
        ("2027-12-31/2028-06-01", "hl-gap"),
    ]  # fmt: skip
    assert ledger.holdout_collision("2027-06-01/2027-07-01", "new") == "2027-01-01/2027-12-31"
    assert ledger.holdout_collision("2029-05-31/2030-01-01", "new") is None
    assert ledger.holdout_collision("2040-01-01/2041-01-01", "hl-2028") is not None


def test_abandoned_campaign_is_final_and_keeps_its_trials(ledger: Ledger) -> None:
    tid = ledger.record_trial(_trial(1.0))
    ledger.abandon_campaign("c1", "lock predates derived.sizing")
    campaign = ledger.campaign("c1")
    assert campaign is not None and campaign.status == "ABANDONED"
    assert [t.id for t in ledger.trials()] == [tid] and ledger.trial_stats().n_raw == 1
    with pytest.raises(LedgerError, match="abandoned"):
        ledger.record_trial(_trial(0.5))
    with pytest.raises(LedgerError, match="abandoned"):
        ledger.transition("c1", "FROZEN")
    with pytest.raises(LedgerError, match="abandoned"):
        ledger.record_gate_result(
            GateResultRecord(campaign_id="c1", candidate_id="x", gate="g3_is", passed=True,
                             reason="x")
        )  # fmt: skip
    with pytest.raises(LedgerError, match="append-only"):
        ledger.abandon_campaign("c1", "again")
    with pytest.raises(LedgerError):
        ledger.transition("c1", "ABANDONED")


def test_only_an_open_campaign_can_be_abandoned(ledger: Ledger) -> None:
    ledger.transition("c1", "FROZEN")
    with pytest.raises(LedgerError, match="OPEN"):
        ledger.abandon_campaign("c1", "too late: frozen")
