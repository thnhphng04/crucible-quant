"""Ledger (Architecture §4.1, §4.2, ADR-0002) — INV-02, INV-07, INV-23, INV-43."""

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
