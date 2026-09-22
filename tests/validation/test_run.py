"""Campaign bootstrap for the `validate` CLI (validation/run.py)."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from quantcrucible.config.lock import CampaignNotOpened, LockMismatchError, read_lock
from quantcrucible.config.schema import UserConfig
from quantcrucible.core.strategy.template import template_hash
from quantcrucible.ledger.db import Ledger
from quantcrucible.ledger.records import Event
from quantcrucible.validation.freeze import FreezeError, abandon
from quantcrucible.validation.run import (
    candidate_pipeline,
    current_campaign,
    make_candidate,
    phase0_pipeline,
)
from tests.factories import make_bars


def test_opens_once_then_resumes(tmp_path: Path) -> None:
    manifest = {"range": "2030-01-01/2031-01-01", "files": {}, "created_at": "x"}
    (tmp_path / "holdout.lock").write_text(json.dumps(manifest), encoding="utf-8")
    ledger = Ledger.open(tmp_path / "ledger.db")
    lock_path = tmp_path / "config" / "evaluation.lock.yaml"
    cfg = replace(UserConfig(), research=replace(UserConfig().research, holdout_pass=0.5))
    first = current_campaign(cfg, ledger, lock_path, tmp_path)
    lock = read_lock(lock_path)
    assert lock["holdout_range"] == "2030-01-01/2031-01-01"
    assert lock["derived"]["template_hash"] == template_hash("joint")
    assert set(lock["derived"]["costs"]) == {"fee_rate", "slippage_bps"}
    assert current_campaign(cfg, ledger, lock_path, tmp_path) == first
    changed = replace(cfg, research=replace(cfg.research, seeds=5))
    with pytest.raises(LockMismatchError):
        current_campaign(changed, ledger, lock_path, tmp_path)


def test_candidate_defaults_to_tunable_values() -> None:
    source = (Path(__file__).parents[2] / "src/quantcrucible/core/zoo/ema_crossover.py").read_text(
        encoding="utf-8"
    )
    data = {"A/USDT": make_bars(10, symbol="A/USDT")}
    c = make_candidate(source, "c1", data)
    assert c.params == {"fast": 20, "slow": 100, "k_atr": 2.0}
    assert c.universe == ("A/USDT",) and c.timerange == "2020-01-02/2020-01-11"
    assert [g.id for g in phase0_pipeline().gates] == [
        "g1a_static", "g1b_dynamic", "g2_minbtl", "g3_is",
    ]  # fmt: skip
    assert [g.id for g in candidate_pipeline().gates][-1] == "g4_pbo"


def _project(tmp_path: Path) -> tuple[Ledger, Path]:
    manifest = {"range": "2030-01-01/2031-01-01", "files": {}, "created_at": "x"}
    (tmp_path / "holdout.lock").write_text(json.dumps(manifest), encoding="utf-8")
    return Ledger.open(tmp_path / "ledger.db"), tmp_path / "config" / "evaluation.lock.yaml"


def test_a_new_campaign_needs_d4(tmp_path: Path) -> None:
    ledger, lock_path = _project(tmp_path)
    with pytest.raises(CampaignNotOpened, match="holdout_pass"):
        current_campaign(UserConfig(), ledger, lock_path, tmp_path)
    assert not lock_path.exists() and ledger.campaigns() == []


def test_abandoned_campaign_is_replaced(tmp_path: Path) -> None:
    """O16 / ADR-0019: abandon (audited) → the next run opens a new campaign on the unused
    holdout; the old lock is archived unchanged."""
    ledger, lock_path = _project(tmp_path)
    cfg = replace(UserConfig(), research=replace(UserConfig().research, holdout_pass=0.5))
    first = current_campaign(cfg, ledger, lock_path, tmp_path)
    old_lock = lock_path.read_bytes()
    with pytest.raises(FreezeError, match="reason"):
        abandon(ledger, first, " ")
    abandon(ledger, first, "lock predates derived.sizing")
    assert (Event.CAMPAIGN_ABANDONED, None) in ledger.events(first)
    with pytest.raises(FreezeError, match="ABANDONED"):
        abandon(ledger, first, "again")
    second = current_campaign(cfg, ledger, lock_path, tmp_path)
    assert second != first
    assert (lock_path.parent / "locks" / f"{first}.lock.yaml").read_bytes() == old_lock
    statuses = {c.campaign_id: c.status for c in ledger.campaigns()}
    assert statuses == {first: "ABANDONED", second: "OPEN"}


def test_candidate_carries_engine_provenance() -> None:
    """An engine's candidate keeps its own id, run, engine, seed, island and lineage (P2-03)."""
    from quantcrucible.validation.run import Provenance

    source = (Path(__file__).parents[2] / "src/quantcrucible/core/zoo/ema_crossover.py").read_text(
        encoding="utf-8"
    )
    data = {"A/USDT": make_bars(10, symbol="A/USDT")}
    prov = Provenance(
        engine="gp", seed=2, run_id="gp-run-1", island="i3", cell_id="c-9",
        parents=("h1", "h2"), mutation="crossover",
    )  # fmt: skip
    c = make_candidate(source, "c1", data, params={"fast": 21, "slow": 90, "k_atr": 2.5},
                       candidate_id="gp-1", provenance=prov)  # fmt: skip
    assert (c.engine, c.seed, c.run_id, c.island, c.cell_id) == ("gp", 2, "gp-run-1", "i3", "c-9")
    assert (c.parents, c.mutation, c.trial_source, c.agent) == (
        ("h1", "h2"), "crossover", "evolution", "engine",
    )  # fmt: skip
    assert c.params == {"fast": 21, "slow": 90, "k_atr": 2.5} and c.candidate_id == "gp-1"
