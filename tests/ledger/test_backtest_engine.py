"""Schema v9: every trial records which backtest engine measured it (P3-36, INV-108, ADR-0038)."""

from __future__ import annotations

import sqlite3
from importlib.resources import files
from pathlib import Path

import pytest

from quantcrucible.ledger.db import MIGRATIONS, SCHEMA_VERSION, Ledger
from quantcrucible.ledger.records import Event, TrialRecord


def _trial(candidate: str, engine: str | None) -> TrialRecord:
    return TrialRecord(
        run_id="r1", campaign_id="c1", candidate_id=candidate, engine="gp", seed=0,
        strategy_hash=f"h-{candidate}", params={"n1": 20}, universe="BTC/USDT",
        timeframe="1h", timerange="2018-01-01/2025-09-21", source="evolution",
        sharpe_is=0.4, returns_path="results/x.parquet", verdict="PASS",
        backtest_engine=engine,
    )  # fmt: skip


def test_a_trial_records_its_engine_and_null_reads_as_sandbox(tmp_path: Path) -> None:
    lg = Ledger.open(tmp_path / "ledger.db")
    lg.open_campaign("c1", "2025-09-21/2026-09-21", lock_hash="abc")
    lg.record_trial(_trial("a", "cuda"))
    lg.record_trial(_trial("b", None))
    by_id = {r.candidate_id: r for r in lg.trials("c1")}
    assert by_id["a"].backtest_engine == "cuda"
    assert by_id["b"].backtest_engine == "sandbox"


def test_a_v8_ledger_is_migrated_without_touching_its_rows(tmp_path: Path) -> None:
    path = tmp_path / "ledger.db"
    raw = sqlite3.connect(path, isolation_level=None)
    for step, name in enumerate(MIGRATIONS[:8]):
        script = files("quantcrucible.ledger").joinpath(name).read_text("utf-8")
        if name == "migration_005_island.sql":
            raw.execute("ALTER TABLE generation_log ADD COLUMN island TEXT")
            raw.execute("ALTER TABLE trials ADD COLUMN island TEXT")
        if name == "migration_007_scope.sql":
            for table in ("generation_log", "trials"):
                raw.execute(f"ALTER TABLE {table} ADD COLUMN instrument TEXT")
                raw.execute(f"ALTER TABLE {table} ADD COLUMN direction TEXT")
        raw.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {step + 1};\nCOMMIT;")
    raw.execute(
        "INSERT INTO campaigns VALUES ('c1', '2026-01-01T00:00:00+00:00', 'x', 'h', NULL, 'OPEN')"
    )
    raw.execute(
        "INSERT INTO trials (ts, run_id, campaign_id, candidate_id, engine, seed, strategy_hash,"
        " params, universe, timeframe, timerange, source, sharpe_is, returns_path, verdict)"
        " VALUES ('2026-01-01T00:00:00+00:00', 'r', 'c1', 'old', 'gp', 1, 'h', '{}', 'BTC',"
        " '1h', 'x', 'evolution', 0.3, 'p', 'PASS')"
    )
    raw.close()
    lg = Ledger.open(path)
    assert lg._conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 10
    [row] = lg.trials("c1")
    assert (row.candidate_id, row.backtest_engine) == ("old", "sandbox")
    with pytest.raises(sqlite3.DatabaseError):
        lg._conn.execute("UPDATE trials SET backtest_engine = 'cuda'")
    Ledger.open(path)  # idempotent: opening again changes nothing


def test_engine_events_exist() -> None:
    assert Event.ENGINE_AUDIT_MISMATCH == "ENGINE_AUDIT_MISMATCH"
    assert Event.ENGINE_FALLBACK == "ENGINE_FALLBACK"
