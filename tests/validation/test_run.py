"""Campaign bootstrap for the `validate` CLI (validation/run.py)."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from quantcrucible.config.lock import CampaignNotOpened, LockMismatchError, read_lock
from quantcrucible.config.schema import UserConfig
from quantcrucible.core.strategy.template import template_hash
from quantcrucible.data.store import file_name, write_bars
from quantcrucible.ledger.db import Ledger, LedgerError
from quantcrucible.ledger.records import Event, TrialRecord
from quantcrucible.validation.freeze import FreezeError, abandon
from quantcrucible.validation.is_gates import DAYS_PER_YEAR
from quantcrucible.validation.run import (
    candidate_pipeline,
    current_campaign,
    make_candidate,
    phase0_pipeline,
)
from quantcrucible.validation.statistical import max_trials_within
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
    assert lock["derived"]["portfolio_protocol"] == "shared_account_v1"  # ADR-0040
    assert lock["derived"]["stop_period"] == "tunable_v1"  # ADR-0041
    assert lock["derived"]["max_tunables"] == 7
    assert lock["research"]["exit"]["stop_kinds"] == ["atr"]  # ADR-0042
    assert lock["derived"]["grammar_version"] == 5  # ADR-0043 … ADR-0045, ADR-0047
    assert lock["derived"]["evaluation_clock"] == "daily_v1"  # ADR-0049
    assert lock["derived"]["trial_scope"] == "campaign_v1"  # ADR-0050
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


def _cfg(purpose: str = "research", budget: int | None = None) -> UserConfig:
    from quantcrucible.config.schema import Campaign

    r = replace(UserConfig().research, holdout_pass=0.5, campaign=Campaign(purpose, budget))  # type: ignore[arg-type]
    return replace(UserConfig(), research=r)


def test_harness_test_campaign_cannot_freeze(tmp_path: Path) -> None:
    """INV-62: the phase-2 comparison campaign is recorded as harness_test and can never be
    frozen, so it can never claim or open a holdout (arch §3.1.11)."""
    ledger, lock_path = _project(tmp_path)
    cid = current_campaign(_cfg("harness_test", 500), ledger, lock_path, tmp_path)
    assert ledger.campaign_purpose(cid) == ("harness_test", 500)
    with pytest.raises(LedgerError, match="never frozen"):
        ledger.transition(cid, "FROZEN")


def test_a_trial_budget_beyond_minbtl_is_refused(tmp_path: Path) -> None:
    """Opening a campaign whose budget would need more history than the IS data has (gate ② at
    the locked target Sharpe) is refused up front."""
    ledger, lock_path = _project(tmp_path)
    with pytest.raises(CampaignNotOpened, match="MinBTL"):
        current_campaign(_cfg("harness_test", 10**9), ledger, lock_path, tmp_path)
    assert current_campaign(_cfg("harness_test", 200), ledger, lock_path, tmp_path)


def _three_years(budget: int) -> UserConfig:
    cfg = _cfg("research", budget)
    data = replace(cfg.research.data, start=date(2027, 1, 1))  # 3 years before the holdout
    return replace(cfg, research=replace(cfg.research, data=data))


def test_the_trial_budget_ignores_other_campaigns(tmp_path: Path) -> None:
    """ADR-0050: a new campaign counts only its own trials, so the ledger's N does not eat its
    budget — only ``max_trials_within`` its own IS years does."""
    ledger, lock_path = _project(tmp_path)
    allowed = max_trials_within(1096 / DAYS_PER_YEAR, 1.5)
    old = current_campaign(_three_years(10), ledger, lock_path, tmp_path)
    for i in range(allowed):  # the old campaign alone already uses up the whole MinBTL budget
        ledger.record_trial(
            TrialRecord(
                run_id="r", campaign_id=old, candidate_id=f"t{i}", engine="manual", seed=0,
                strategy_hash=f"h{i}", params={}, universe="X", timeframe="1h", timerange="t",
                source="manual", sharpe_is=0.0, returns_path="x.parquet", verdict="PASS",
            )
        )  # fmt: skip
    abandon(ledger, old, "replaced")
    with pytest.raises(CampaignNotOpened, match=f"{allowed} trials in this campaign"):
        current_campaign(_three_years(allowed + 1), ledger, lock_path, tmp_path)
    assert current_campaign(_three_years(allowed), ledger, lock_path, tmp_path) != old
    assert ledger.trial_stats().n_raw == allowed  # nothing left the ledger


# ── the data window and a clean holdout (P3-64, ADR-0051, D28) — INV-126 ──────────────────


def _searched_until(ledger: Ledger, last_day: str) -> None:
    """A trial of an earlier campaign whose IS ran up to ``last_day``."""
    ledger.open_campaign("old", "2019-01-01/2019-02-01", lock_hash="h")
    ledger.record_trial(
        TrialRecord(
            run_id="r", campaign_id="old", candidate_id="t", engine="manual", seed=0,
            strategy_hash="h", params={}, universe="X", timeframe="1d",
            timerange=f"2018-01-01/{last_day}", source="manual", sharpe_is=0.0,
            returns_path="x.parquet", verdict="PASS",
        )
    )  # fmt: skip


def test_a_holdout_inside_already_searched_data_is_refused(tmp_path: Path) -> None:
    ledger, lock_path = _project(tmp_path)  # holdout 2030-01-01/2031-01-01
    _searched_until(ledger, "2030-03-01")
    with pytest.raises(CampaignNotOpened, match="2030-03-02 or later"):
        current_campaign(_cfg(), ledger, lock_path, tmp_path)


def test_a_holdout_after_everything_searched_opens(tmp_path: Path) -> None:
    ledger, lock_path = _project(tmp_path)
    _searched_until(ledger, "2029-12-31")
    cid = current_campaign(_cfg(), ledger, lock_path, tmp_path)
    assert read_lock(lock_path)["derived"]["data_window"] == "v1"
    assert ledger.campaign(cid) is not None


def _spot_store(root: Path, start: str) -> None:
    for symbol in UserConfig().research.data.symbols:
        bars = make_bars(30, symbol=symbol, start=start)
        write_bars(root / "data" / "is" / file_name(symbol, "1h"), bars)


def test_a_legacy_store_longer_than_the_window_is_refused(tmp_path: Path) -> None:
    """The legacy store is read whole: one that starts before ``data.start`` would backtest on
    more history than the lock records."""
    ledger, lock_path = _project(tmp_path)
    _spot_store(tmp_path, "2018-01-01")
    cfg = _cfg()
    late = replace(cfg.research.data, start=date(2027, 1, 1))
    with pytest.raises(CampaignNotOpened, match=r"does not start at research.data.start"):
        current_campaign(
            replace(cfg, research=replace(cfg.research, data=late)), ledger, lock_path, tmp_path
        )
    early = replace(cfg.research.data, start=date(2018, 1, 1))
    assert current_campaign(
        replace(cfg, research=replace(cfg.research, data=early)), ledger, lock_path, tmp_path
    )


def test_an_old_lock_without_the_cut_still_matches() -> None:
    from quantcrucible.config.loader import research_to_dict
    from quantcrucible.config.lock import _comparable

    current = research_to_dict(_cfg().research)
    locked = {**current, "data": {k: v for k, v in current["data"].items() if k != "holdout_start"}}
    assert _comparable(current, {"research": locked, "derived": {}}) == locked
    cut = {**current, "data": {**current["data"], "holdout_start": "2029-06-01"}}
    assert _comparable(cut, {"research": locked, "derived": {}}) != locked
