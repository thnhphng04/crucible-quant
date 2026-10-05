"""Trial statistics per campaign (P3-63, ADR-0050) — INV-125.

Every trial stays in the one append-only ledger, but a campaign locked with
``derived.trial_scope: campaign_v1`` counts only its own: ``N_raw``, ``N_eff``, ``V[SR]``, the
portfolio variants, the unmeasured attempts and the snapshot its ⑤/⑥′ results are pinned to.
Without a campaign the ledger-wide figures are exactly what they were.
"""

from __future__ import annotations

import dataclasses
import sqlite3
import statistics
from importlib.resources import files
from pathlib import Path

import pytest

from quantcrucible.ledger.db import ADDED_COLUMNS, MIGRATIONS, SCHEMA_VERSION, Ledger
from quantcrucible.ledger.records import GateResultRecord, PortfolioVariant, TrialRecord


def _trial(campaign: str, sharpe: float, candidate: str) -> TrialRecord:
    return TrialRecord(
        run_id="r1", campaign_id=campaign, candidate_id=candidate, engine="manual", seed=0,
        strategy_hash=f"h-{candidate}", params={}, universe="BTC/USDT", timeframe="1d",
        timerange="2018-01-01/2025-09-21", source="manual", sharpe_is=sharpe,
        returns_path="results/x.parquet", verdict="PASS",
    )  # fmt: skip


@pytest.fixture
def ledger(tmp_path: Path) -> Ledger:
    lg = Ledger.open(tmp_path / "ledger.db")
    lg.open_campaign("c1", "2025-09-21/2026-09-21", lock_hash="a")
    lg.open_campaign("c2", "2025-09-21/2026-09-21", lock_hash="b")
    return lg


def _fill(lg: Ledger) -> tuple[list[int], list[int]]:
    one = [lg.record_trial(_trial("c1", s, f"a{i}")) for i, s in enumerate([0.5, 1.2, -0.3])]
    two = [lg.record_trial(_trial("c2", s, f"b{i}")) for i, s in enumerate([0.1, 0.4])]
    return one, two


def test_a_campaign_counts_only_its_own_trials(ledger: Ledger) -> None:
    _fill(ledger)
    stats = ledger.trial_stats("c2")
    assert (stats.n_raw, stats.n_eff) == (2, 2)
    assert stats.var_sr == pytest.approx(statistics.pvariance([0.1, 0.4]))
    whole = ledger.trial_stats()
    assert (whole.n_raw, whole.n_eff) == (5, 5)
    assert whole.var_sr == pytest.approx(statistics.pvariance([0.5, 1.2, -0.3, 0.1, 0.4]))


def test_a_campaign_without_trials_has_none(ledger: Ledger) -> None:
    ledger.record_trial(_trial("c1", 0.5, "a0"))
    stats = ledger.trial_stats("c2")
    assert (stats.n_raw, stats.n_eff, stats.var_sr) == (0, 0, None)


def test_a_campaign_reads_its_own_latest_clustering(ledger: Ledger) -> None:
    one, two = _fill(ledger)
    ledger.record_clustering("onc", {i: 0 for i in one + two})  # ledger-wide: one cluster
    ledger.record_clustering("onc", {two[0]: 0, two[1]: 1}, campaign_id="c2")
    assert ledger.trial_stats("c2").n_eff == 2
    assert ledger.latest_clustering("c2") == (2, 2)
    # a campaign's run is never the ledger's "latest": the global N_eff is unchanged
    assert ledger.trial_stats().n_eff == 1
    assert ledger.latest_clustering() == (5, 1)
    # a campaign never clustered falls back to its N_raw, ignoring the global run
    assert ledger.trial_stats("c1").n_eff == 3
    assert ledger.latest_clustering("c1") is None


def test_a_campaign_run_does_not_cover_another_campaign(ledger: Ledger) -> None:
    one, two = _fill(ledger)
    ledger.record_clustering("onc", {two[0]: 0, two[1]: 0}, campaign_id="c2")
    ledger.record_clustering("onc", {one[0]: 0}, campaign_id="c1")
    assert ledger.trial_stats("c2").n_eff == 1  # c1's later run is not c2's latest
    assert ledger.trial_stats("c1").n_eff == 3  # 1 cluster + 2 uncovered (conservative)


def test_variants_and_unmeasured_attempts_per_campaign(ledger: Ledger) -> None:
    for cid, p in (("c1", "p1"), ("c1", "p2"), ("c2", "p3")):
        ledger.record_portfolio_variant(
            PortfolioVariant(portfolio_hash=p, campaign_id=cid, rule_config={}, members=[])
        )
    for cid in ("c1", "c1", "c2"):
        ledger.record_gate_result(
            GateResultRecord(campaign_id=cid, candidate_id="x", gate="g3_is", passed=False,
                             reason="no trades")
        )  # fmt: skip
    assert (ledger.total_portfolio_variants(), ledger.total_portfolio_variants("c2")) == (3, 1)
    assert (ledger.unmeasured_attempts(), ledger.unmeasured_attempts("c2")) == (3, 1)


def test_a_campaign_snapshot_ignores_other_campaigns(ledger: Ledger) -> None:
    _fill(ledger)
    before = ledger.snapshot("c2")
    assert before == {"trials": 2, "portfolio_variants": 0, "scope": "c2"}
    ledger.record_trial(_trial("c1", 0.7, "a9"))
    assert ledger.snapshot("c2") == before
    assert ledger.snapshot() == {"trials": 6, "portfolio_variants": 0}  # no scope key
    ledger.record_trial(_trial("c2", 0.7, "b9"))
    assert ledger.snapshot("c2") != before


def test_latest_is_end_is_the_newest_searched_day_of_any_campaign(ledger: Ledger) -> None:
    """D28 (ADR-0051): the newest IS day of any trial; a non-date timerange is skipped."""
    assert ledger.latest_is_end() is None
    ledger.record_trial(_trial("c1", 0.1, "a"))  # 2018-01-01/2025-09-21
    other = _trial("c2", 0.1, "b")
    ledger.record_trial(dataclasses.replace(other, timerange="2019-01-01/2024-01-01"))
    ledger.record_trial(dataclasses.replace(other, candidate_id="z", timerange="t"))
    assert ledger.latest_is_end() == "2025-09-21"


def test_a_v9_ledger_keeps_its_figures_after_migrating(tmp_path: Path) -> None:
    path = tmp_path / "v9.db"
    raw = sqlite3.connect(path, isolation_level=None)
    for step, name in enumerate(MIGRATIONS[:9]):
        for table, column, decl in ADDED_COLUMNS.get(name, ()):
            raw.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        script = files("quantcrucible.ledger").joinpath(name).read_text("utf-8")
        raw.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {step + 1};\nCOMMIT;")
    raw.execute(
        "INSERT INTO campaigns VALUES ('c1', '2026-01-01T00:00:00+00:00', 'x', 'h', NULL, 'OPEN')"
    )
    for i, sharpe in enumerate([0.5, 1.2, -0.3]):
        raw.execute(
            "INSERT INTO trials (ts, run_id, campaign_id, candidate_id, engine, seed,"
            " strategy_hash, params, universe, timeframe, timerange, source, sharpe_is,"
            " returns_path, verdict) VALUES ('2026-01-01T00:00:00+00:00', 'r', 'c1', ?, 'manual',"
            " 0, 'h', '{}', 'BTC', '1d', 'x', 'manual', ?, 'p', 'PASS')",
            (f"a{i}", sharpe),
        )
    raw.execute(
        "INSERT INTO clustering_runs (ts, method, n_trials)"
        " VALUES ('2026-01-01T00:00:00+00:00', 'onc', 3)"
    )
    raw.executemany("INSERT INTO trial_clusters VALUES (1, ?, ?)", [(1, 0), (2, 0), (3, 1)])
    before = raw.execute("SELECT n_raw, n_eff, var_sr FROM trial_stats").fetchone()
    raw.close()
    lg = Ledger.open(path)
    assert lg._conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 10
    stats = lg.trial_stats()
    assert (stats.n_raw, stats.n_eff, stats.var_sr) == tuple(before)
    assert before[:2] == (3, 2)
    assert lg.latest_clustering() == (3, 2)
    # the pre-v10 run belongs to no campaign, so it is not c1's own clustering
    assert lg.trial_stats("c1").n_eff == 3
    with pytest.raises(sqlite3.DatabaseError):
        lg._conn.execute("UPDATE clustering_runs SET campaign_id = 'c1'")
    lg.close()
    Ledger.open(path).close()  # idempotent: opening again changes nothing
