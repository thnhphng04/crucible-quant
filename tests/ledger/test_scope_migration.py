"""Migration 007: instrument and direction on the ledger (P3-11, ADR-0033) — INV-95.

The requirements say two things that conflict: "migrate old trials to an explicit legacy scope"
and "do not edit existing trials". The database settles it — `schema.sql`'s append-only triggers
refuse UPDATE on `trials`, so the 1,325 rows already recorded can never be backfilled.

So the legacy scope is a **read-time default**, never an UPDATE. A row with NULL scope columns is
the five-symbol spot basket, and every reader resolves it that way.

`market_type` deliberately does not go on the row: it is a campaign constant, already bound by
the lock hash, and putting it on every row invites rows that disagree with their own campaign.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from quantcrucible.ledger.db import Ledger, LedgerError
from quantcrucible.ledger.records import LEGACY_INSTRUMENT, TrialRecord


def ledger(tmp_path: Path) -> Ledger:
    led = Ledger.open(tmp_path / "l.db")
    led.open_campaign("c1", "2025-09-21/2026-09-21", lock_hash="h")
    return led


def trial(**kw: object) -> TrialRecord:
    base = dict(
        run_id="r", campaign_id="c1", candidate_id="c", engine="gp", seed=0,
        strategy_hash="h", params={}, universe="BTC/USDT", timeframe="1d",
        timerange="2021-01-01/2021-06-01", source="evolution", sharpe_is=1.0,
        returns_path="p", verdict="PASS",
    )  # fmt: skip
    base.update(kw)
    return TrialRecord(**base)  # type: ignore[arg-type]


def test_a_scoped_trial_keeps_its_instrument_and_direction(tmp_path: Path) -> None:
    led = ledger(tmp_path)
    led.record_trial(trial(instrument="BTCUSDT", direction="short"))
    row = led.trials("c1")[0]
    assert row.instrument == "BTCUSDT"
    assert row.direction == "short"


def test_a_legacy_row_reads_as_the_legacy_scope(tmp_path: Path) -> None:
    """INV-95: NULL means the old five-symbol spot basket, resolved on the way out."""
    led = ledger(tmp_path)
    led.record_trial(trial())  # no scope given, as every pre-P3 caller does
    row = led.trials("c1")[0]
    assert row.instrument == LEGACY_INSTRUMENT
    assert row.direction == "long"
    assert row.is_legacy_scope


def test_scope_columns_cannot_be_backfilled_by_raw_sql(tmp_path: Path) -> None:
    """The adversarial half of INV-95, and the reason the legacy scope is read-time.

    Extends INV-02: the append-only triggers refuse UPDATE on trials, so no migration — and no
    later well-meant script — can rewrite what a recorded trial was.
    """
    led = ledger(tmp_path)
    led.record_trial(trial())
    con = sqlite3.connect(led.path)
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("UPDATE trials SET instrument = 'BTCUSDT'")
    con.close()


def test_trials_filter_by_instrument_and_direction(tmp_path: Path) -> None:
    led = ledger(tmp_path)
    led.record_trial(trial(candidate_id="a", instrument="BTCUSDT", direction="long"))
    led.record_trial(trial(candidate_id="b", instrument="BTCUSDT", direction="short"))
    led.record_trial(trial(candidate_id="c", instrument="ETHUSDT", direction="long"))
    assert len(led.trials("c1", instrument="BTCUSDT")) == 2
    assert len(led.trials("c1", instrument="BTCUSDT", direction="short")) == 1
    assert len(led.trials("c1", direction="long")) == 2


def test_a_legacy_row_is_not_returned_by_a_scoped_filter(tmp_path: Path) -> None:
    """The filter matches what is stored, not what a reader resolves it to: a legacy row belongs
    to no perpetual scope and must not be swept into one."""
    led = ledger(tmp_path)
    led.record_trial(trial(candidate_id="legacy"))
    led.record_trial(trial(candidate_id="new", instrument="BTCUSDT", direction="long"))
    assert [r.candidate_id for r in led.trials("c1", instrument="BTCUSDT")] == ["new"]


def test_trial_stats_still_counts_every_scope(tmp_path: Path) -> None:
    """INV-42 is unchanged: N is every configuration whose performance was measured, because
    selection bias does not respect instrument boundaries."""
    led = ledger(tmp_path)
    led.record_trial(trial(candidate_id="a", instrument="BTCUSDT", direction="long"))
    led.record_trial(trial(candidate_id="b", instrument="ETHUSDT", direction="short"))
    led.record_trial(trial(candidate_id="c"))  # a legacy row still counts
    assert led.trial_stats().n_raw == 3


def test_an_unknown_direction_is_refused(tmp_path: Path) -> None:
    led = ledger(tmp_path)
    with pytest.raises(LedgerError, match="direction"):
        led.record_trial(trial(instrument="BTCUSDT", direction="sideways"))


def test_events_carry_and_filter_by_scope(tmp_path: Path) -> None:
    from quantcrucible.ledger.records import Event, GenerationEvent

    led = ledger(tmp_path)
    led.log_event(
        GenerationEvent(
            run_id="r",
            campaign_id="c1",
            engine="gp",
            seed=0,
            agent="engine",
            model_used="none",
            event=Event.CANDIDATE_SUBMITTED,
            instrument="BTCUSDT",
            direction="short",
        )
    )
    assert len(led.events_for("c1", instrument="BTCUSDT", direction="short")) == 1
    assert len(led.events_for("c1", instrument="ETHUSDT")) == 0


def test_an_existing_v6_ledger_migrates_in_place(tmp_path: Path) -> None:
    """Opened again by newer code, an old file gains the columns and keeps every row."""
    path = tmp_path / "old.db"
    led = Ledger.open(path)
    led.open_campaign("c1", "2025-09-21/2026-09-21", lock_hash="h")
    led.record_trial(trial())
    con = sqlite3.connect(path)
    con.execute("PRAGMA user_version = 6")  # pretend it was written before migration 007
    con.commit()
    con.close()

    reopened = Ledger.open(path)
    rows = reopened.trials("c1")
    assert len(rows) == 1
    assert rows[0].is_legacy_scope
