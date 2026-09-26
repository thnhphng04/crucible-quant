"""Campaign lock (Architecture §10.1, §4.2) — INV-21, INV-22."""

import dataclasses
import os
import stat
from pathlib import Path

import pytest

from quantcrucible.config.lock import (
    LockMismatchError,
    LockTamperedError,
    assert_lock_matches,
    open_campaign,
    sha256_file,
)
from quantcrucible.config.schema import Operational, UserConfig
from quantcrucible.ledger.db import Ledger

HOLDOUT = "2025-09-21/2026-09-21"


@pytest.fixture
def ledger(tmp_path: Path) -> Ledger:
    return Ledger.open(tmp_path / "ledger.db")


@pytest.fixture
def lock_path(tmp_path: Path) -> Path:
    return tmp_path / "config" / "evaluation.lock.yaml"


def _writable(path: Path) -> bool:
    return bool(os.stat(path).st_mode & stat.S_IWRITE)


def test_lock_readonly_and_hashed(ledger: Ledger, lock_path: Path) -> None:
    cfg = UserConfig()
    campaign = open_campaign(cfg, ledger, "c1", lock_path, HOLDOUT, derived={"template_hash": "t"})
    assert not _writable(lock_path)
    assert campaign.lock_hash == sha256_file(lock_path)
    stored = ledger.campaign("c1")
    assert stored is not None and stored.lock_hash == campaign.lock_hash
    lock = assert_lock_matches(cfg, lock_path, ledger, "c1")
    assert lock["derived"] == {"template_hash": "t"}
    assert lock["holdout_range"] == HOLDOUT


def test_group_b_change_refused(ledger: Ledger, lock_path: Path) -> None:
    cfg = UserConfig()
    open_campaign(cfg, ledger, "c1", lock_path, HOLDOUT)
    edited = dataclasses.replace(
        cfg, research=dataclasses.replace(cfg.research, seeds=5, max_risk_pct=0.02)
    )
    with pytest.raises(LockMismatchError, match="max_risk_pct, seeds"):
        assert_lock_matches(edited, lock_path, ledger, "c1")


def test_group_a_change_allowed(ledger: Ledger, lock_path: Path) -> None:
    cfg = UserConfig()
    open_campaign(cfg, ledger, "c1", lock_path, HOLDOUT)
    edited = dataclasses.replace(cfg, operational=Operational(live_capital=50_000.0))
    assert_lock_matches(edited, lock_path, ledger, "c1")


def test_tampered_lock_refused(ledger: Ledger, lock_path: Path) -> None:
    cfg = UserConfig()
    open_campaign(cfg, ledger, "c1", lock_path, HOLDOUT)
    os.chmod(lock_path, stat.S_IREAD | stat.S_IWRITE)
    lock_path.write_text(lock_path.read_text(encoding="utf-8").replace("0.95", "0.5"), "utf-8")
    with pytest.raises(LockTamperedError):
        assert_lock_matches(cfg, lock_path, ledger, "c1")


def test_new_campaign_requires_previous_burned(ledger: Ledger, lock_path: Path) -> None:
    cfg = UserConfig()
    open_campaign(cfg, ledger, "c1", lock_path, HOLDOUT)
    with pytest.raises(LockMismatchError, match="still OPEN"):
        open_campaign(cfg, ledger, "c2", lock_path, "2026-09-21/2027-09-21")
    ledger.transition("c1", "FROZEN")
    ledger.transition("c1", "BURNED")
    open_campaign(cfg, ledger, "c2", lock_path, "2026-09-21/2027-09-21")
    archived = lock_path.parent / "locks" / "c1.lock.yaml"
    assert archived.exists() and not _writable(archived)
    assert_lock_matches(cfg, lock_path, ledger, "c2")


def test_unknown_campaign(ledger: Ledger, lock_path: Path) -> None:
    with pytest.raises(LockMismatchError, match="unknown campaign"):
        assert_lock_matches(UserConfig(), lock_path, ledger, "nope")


def test_new_campaign_after_an_abandoned_one(ledger: Ledger, lock_path: Path) -> None:
    """ADR-0019: an abandoned campaign frees the lock path; its lock is archived unchanged."""
    cfg = UserConfig()
    open_campaign(cfg, ledger, "c1", lock_path, HOLDOUT)
    original = lock_path.read_bytes()
    ledger.abandon_campaign("c1", "lock predates derived.sizing")
    open_campaign(cfg, ledger, "c2", lock_path, HOLDOUT)  # never claimed: the same holdout is fine
    archived = lock_path.parent / "locks" / "c1.lock.yaml"
    assert archived.read_bytes() == original and not _writable(archived)
    stored = ledger.campaign("c1")
    assert stored is not None and sha256_file(archived) == stored.lock_hash


def test_a_used_holdout_cannot_open_a_new_campaign(ledger: Ledger, lock_path: Path) -> None:
    """Review finding 4 at the source: same manifest or an overlapping period is refused."""
    cfg = UserConfig()
    open_campaign(cfg, ledger, "c1", lock_path, HOLDOUT, holdout_lock_hash="m1")
    ledger.transition("c1", "FROZEN")
    ledger.claim_holdout("c1", "p", HOLDOUT, "m1")
    ledger.transition("c1", "BURNED")
    for holdout, manifest in [
        (HOLDOUT, "m2"),
        ("2026-03-01/2027-03-01", "m2"),
        ("2030-01-01/2031-01-01", "m1"),
    ]:
        with pytest.raises(LockMismatchError, match="already used"):
            open_campaign(cfg, ledger, "c2", lock_path, holdout, holdout_lock_hash=manifest)
    open_campaign(cfg, ledger, "c2", lock_path, "2026-09-21/2027-09-21", holdout_lock_hash="m2")


def test_holdout_pass_is_locked_for_the_whole_campaign(ledger: Ledger, lock_path: Path) -> None:
    """D4 (ADR-0020): set before the campaign opens, never adjusted during it."""
    cfg = dataclasses.replace(
        UserConfig(), research=dataclasses.replace(UserConfig().research, holdout_pass=1.3)
    )
    open_campaign(cfg, ledger, "c1", lock_path, HOLDOUT)
    for other in (1.0, 1.5, None):
        edited = dataclasses.replace(
            cfg, research=dataclasses.replace(cfg.research, holdout_pass=other)
        )
        with pytest.raises(LockMismatchError, match="holdout_pass"):
            assert_lock_matches(edited, lock_path, ledger, "c1")
    assert assert_lock_matches(cfg, lock_path, ledger, "c1")["research"]["holdout_pass"] == 1.3


def test_a_lock_with_the_old_engine_keys_is_refused(
    ledger: Ledger, lock_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A campaign locked before arch v0.6 (engines quantevolve/simple_loop/random) cannot run
    under the engine-C settings: Group B changed, so a new campaign is needed."""
    from quantcrucible.config.loader import research_to_dict as real

    target = "quantcrucible.config.lock.research_to_dict"

    def pre_v06(research: object) -> dict[str, object]:
        d = real(research)  # type: ignore[arg-type]
        d["engines"] = {"quantevolve": 0.5, "simple_loop": 0.4, "random": 0.1}
        for key in ("gp", "campaign"):
            d.pop(key)
        return d

    monkeypatch.setattr(target, pre_v06)
    open_campaign(UserConfig(), ledger, "c1", lock_path, HOLDOUT)
    monkeypatch.setattr(target, real)
    with pytest.raises(LockMismatchError, match="campaign, engines, gp"):
        assert_lock_matches(UserConfig(), lock_path, ledger, "c1")
