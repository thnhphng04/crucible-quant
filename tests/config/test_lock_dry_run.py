"""Checking a lock without opening a campaign (P3-24) — §4.2, P6.

Opening a campaign is irreversible in three ways at once: it writes a read-only lock, archives
whatever lock was there, and registers the campaign in the append-only ledger. So there was no way
to answer "would this configuration produce a valid lock?" without doing all of it.

That question has to be answerable before the first perpetual campaign, because the answer depends
on data that has just been downloaded and on a holdout range that has just been carved — and
finding out by opening a campaign means burning one to learn the answer.

`dry_run_lock` is that answer. The test that matters is not that it refuses to write, but that the
text it produces is **byte-identical** to what `open_campaign` would have written: a preview of
something else is worse than no preview.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from quantcrucible.config.loader import parse_user_config
from quantcrucible.config.lock import (
    LockMismatchError,
    dry_run_lock,
    open_campaign,
    read_lock,
)
from quantcrucible.config.schema import UserConfig
from quantcrucible.ledger.db import Ledger

RANGE = "2025-09-21/2026-09-22"


def cfg() -> UserConfig:
    return parse_user_config({"research": {"holdout_pass": 1.3}})


def ledger(tmp_path: Path) -> Ledger:
    return Ledger.open(tmp_path / "ledger.db")


def test_a_dry_run_writes_nothing_and_registers_nothing(tmp_path: Path) -> None:
    led = ledger(tmp_path)
    lock_path = tmp_path / "config" / "evaluation.lock.yaml"
    result = dry_run_lock(cfg(), led, "c-dry", lock_path, RANGE, derived={"lookback": 400})
    assert result.ok and result.problems == ()
    assert not lock_path.exists()
    assert led.campaigns() == []
    assert led.used_holdouts() == []


def test_the_preview_is_byte_identical_to_what_opening_would_write(tmp_path: Path) -> None:
    """The whole point. A preview of a different document answers a different question."""
    led = ledger(tmp_path)
    lock_path = tmp_path / "config" / "evaluation.lock.yaml"
    preview = dry_run_lock(cfg(), led, "c1", lock_path, RANGE, derived={"lookback": 400})
    open_campaign(cfg(), led, "c1", lock_path, RANGE, derived={"lookback": 400})
    written = lock_path.read_text(encoding="utf-8")
    # `created_at` is generated when the lock is written, so it is compared structurally.
    assert yaml.safe_load(preview.text).keys() == yaml.safe_load(written).keys()

    def without_created_at(value: str) -> list[str]:
        return [line for line in value.splitlines() if not line.startswith("created_at:")]

    assert without_created_at(preview.text) == without_created_at(written)


def test_a_dry_run_reports_a_reused_holdout_instead_of_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dry run answers; it does not fail. The caller decides what to do with the answer.

    `holdout_collision` is stubbed rather than driven through a real claim: a claim needs a FROZEN
    campaign and a recorded variant, and the ledger's own detection is covered by its own tests.
    What is under test here is that the dry run **reports** what that call says, where
    `open_campaign` **raises** it.
    """
    led = ledger(tmp_path)
    lock_path = tmp_path / "config" / "evaluation.lock.yaml"
    monkeypatch.setattr(led, "holdout_collision", lambda *_: RANGE)

    result = dry_run_lock(cfg(), led, "c2", lock_path, RANGE)
    assert not result.ok
    assert any("holdout" in p for p in result.problems)
    # and the real call still raises, so nothing became permissive
    with pytest.raises(LockMismatchError, match="holdout"):
        open_campaign(cfg(), led, "c2", lock_path, RANGE)


def test_a_dry_run_reports_an_unfinished_previous_campaign(tmp_path: Path) -> None:
    led = ledger(tmp_path)
    lock_path = tmp_path / "config" / "evaluation.lock.yaml"
    open_campaign(cfg(), led, "c1", lock_path, RANGE)
    result = dry_run_lock(cfg(), led, "c2", lock_path, "2024-01-01/2024-06-01")
    assert not result.ok
    assert any("OPEN" in p for p in result.problems)


def test_a_dry_run_leaves_an_existing_lock_exactly_where_it_was(tmp_path: Path) -> None:
    """`open_campaign` archives the previous lock. A dry run must not move it — that archive is
    what an audit reads to see what a burned campaign was measured under."""
    led = ledger(tmp_path)
    lock_path = tmp_path / "config" / "evaluation.lock.yaml"
    open_campaign(cfg(), led, "c1", lock_path, RANGE)
    before = lock_path.read_bytes()
    dry_run_lock(cfg(), led, "c2", lock_path, "2024-01-01/2024-06-01")
    assert lock_path.read_bytes() == before
    assert not (lock_path.parent / "locks").exists()
    assert read_lock(lock_path)["campaign_id"] == "c1"
