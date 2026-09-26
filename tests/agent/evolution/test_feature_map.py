"""Feature map (fixed bounds) + per-engine archive rebuilt from the ledger (arch §3.1.3, P2-07)
— INV-63."""

from pathlib import Path
from typing import Any

import pytest

from quantcrucible.agent import grammar
from quantcrucible.agent.evolution.archive import Archive, coverage, load_entries, rebuild
from quantcrucible.agent.evolution.feature_map import DIMENSIONS, FeatureMap, FeatureMapError
from quantcrucible.ledger.db import Ledger
from quantcrucible.ledger.records import Event, GateResultRecord, GenerationEvent, TrialRecord
from quantcrucible.validation.gates import G3_IS, G4_PBO
from quantcrucible.validation.run import FEATURE_MAP, derived_settings
from tests.factories import unit

LOCK = {"derived": {"feature_map": FEATURE_MAP}}


def test_bounds_only_from_lock() -> None:
    """INV-63: the map comes from the campaign lock and nowhere else — no lock entry, no map;
    other locked bounds, other cells; observed values never move the bounds."""
    with pytest.raises(FeatureMapError, match=r"derived\.feature_map"):
        FeatureMap.from_lock({"derived": {}})
    fm = FeatureMap.from_lock(LOCK)
    assert fm.bins == 16 and set(fm.bounds) == set(DIMENSIONS)
    wide = {**FEATURE_MAP, "bounds": {**FEATURE_MAP["bounds"], "sharpe_is": [-10.0, 10.0]}}
    other = FeatureMap.from_lock({"derived": {"feature_map": wide}})
    assert (fm.bin("sharpe_is", 2.5), other.bin("sharpe_is", 2.5)) == (14, 10)
    before = fm.bin("sharpe_is", 1.0)
    fm.bin("sharpe_is", 50.0)  # an extreme observation changes nothing
    assert fm.bin("sharpe_is", 1.0) == before
    assert "feature_map" in derived_settings("joint")


def test_out_of_range_lands_in_the_edge_bin() -> None:
    fm = FeatureMap.from_lock(LOCK)
    assert fm.bin("sharpe_is", -99.0) == 0
    assert fm.bin("sharpe_is", 99.0) == fm.bins - 1
    assert fm.bin("sharpe_is", 3.0) == fm.bins - 1  # the upper bound itself
    assert fm.bin("sharpe_is", float("nan")) == 0
    assert fm.bin("sharpe_is", None) == 0


def test_categories_are_the_grammars() -> None:
    fm = FeatureMap.from_lock(LOCK)
    assert fm.categories == grammar.CATEGORIES
    assert fm.category_bits(["trend", "breakout"]) == 0b1001
    with pytest.raises(FeatureMapError):
        fm.category_bits(["astrology"])


# ── archive from the ledger ─────────────────────────────────────────────────────────────────
def _candidate(
    lg: Ledger, cid: str, engine: str, seed: int, sharpe: float, cats: list[str],
    g3_pass: bool = True, g4_pass: bool | None = True, trades: float = 60.0,
    g4_detail: dict[str, Any] | None = None,
) -> None:  # fmt: skip
    lg.log_event(GenerationEvent(
        run_id="r", campaign_id="c1", engine=engine, seed=seed, agent="engine", model_used="none",
        event=Event.CANDIDATE_SUBMITTED, strategy_hash=f"h-{cid}",
        detail={"candidate_id": cid, "descriptors": {"categories": cats}},
    ))  # fmt: skip
    tid = lg.record_trial(TrialRecord(
        run_id="r", campaign_id="c1", candidate_id=cid, engine=engine, seed=seed,
        strategy_hash=f"h-{cid}", params={"n1": 20}, universe="BTC/USDT", timeframe="1d",
        timerange="2018-01-01/2024-01-01", source="evolution", sharpe_is=sharpe,
        returns_path="x", verdict="PASS" if g3_pass else "REJECT_g3_is", island="i0",
    ))  # fmt: skip
    public: dict[str, Any] = {
        "sharpe_is": sharpe,
        "sortino_is": sharpe * 1.3,
        "max_drawdown": 0.3,
        "total_return": 0.8,
        "n_trades": trades,
    }
    lg.record_gate_result(GateResultRecord(
        campaign_id="c1", candidate_id=cid, gate=G3_IS, passed=g3_pass, reason="r",
        trial_id=tid, detail={"public": public},
    ))  # fmt: skip
    if g4_pass is not None:
        lg.record_gate_result(GateResultRecord(
            campaign_id="c1", candidate_id=cid, gate=G4_PBO, passed=g4_pass, reason="r",
            trial_id=tid, detail=g4_detail or {"pbo": 0.2, "cpcv_path_sharpes": [9.9]},
        ))  # fmt: skip


@pytest.fixture
def ledger(tmp_path: Path) -> Ledger:
    lg = Ledger.open(tmp_path / "ledger.db")
    lg.open_campaign("c1", "2024-01-01/2025-01-01", lock_hash="h")
    return lg


def test_archive_keeps_the_best_eligible_entry_per_cell(ledger: Ledger) -> None:
    fm = FeatureMap.from_lock(LOCK)
    _candidate(ledger, "a", "gp", 0, 1.00, ["trend"])
    _candidate(ledger, "b", "gp", 0, 1.05, ["trend"])  # same cell, better
    _candidate(ledger, "c", "gp", 0, 2.00, ["trend"], g4_pass=False)  # rejected at ④
    _candidate(ledger, "d", "gp", 0, 2.00, ["trend"], g3_pass=False)  # rejected at ③
    _candidate(ledger, "e", "gp", 0, 0.50, ["breakout"])  # other cell
    archive = rebuild(ledger, "c1", unit("gp", 0), fm)
    assert sorted(e.candidate_id for e in archive.elites()) == ["b", "e"]
    assert len(archive) == 2


def test_rebuilding_from_the_ledger_reproduces_the_archive(ledger: Ledger) -> None:
    fm = FeatureMap.from_lock(LOCK)
    for i, s in enumerate([0.2, 1.4, 0.9, 2.2, 1.1, 0.4]):
        _candidate(ledger, f"x{i}", "gp", 1, s, ["trend", "momentum"][i % 2 :], trades=20 + 30 * i)
    incremental = Archive()
    for entry in load_entries(ledger, "c1", unit("gp", 1), fm):
        incremental.add(entry)
    again = rebuild(ledger, "c1", unit("gp", 1), fm)
    assert (
        incremental.elites() == again.elites() == rebuild(ledger, "c1", unit("gp", 1), fm).elites()
    )


def test_engines_never_share_an_archive(ledger: Ledger) -> None:
    fm = FeatureMap.from_lock(LOCK)
    _candidate(ledger, "g", "gp", 0, 1.0, ["trend"])
    _candidate(ledger, "r", "random", 0, 1.0, ["trend"])
    _candidate(ledger, "g2", "gp", 1, 1.0, ["trend"])
    gp0 = rebuild(ledger, "c1", unit("gp", 0), fm)
    rnd = rebuild(ledger, "c1", unit("random", 0), fm)
    assert [e.candidate_id for e in gp0.elites()] == ["g"]
    assert [e.candidate_id for e in rnd.elites()] == ["r"]
    assert coverage([gp0, rnd]) == 1  # same cell, counted once


def test_the_archive_never_reads_private_metrics(ledger: Ledger) -> None:
    """Only gate ③'s `public` (and a future ④ `public`) feed an entry — never PBO/CPCV."""
    fm = FeatureMap.from_lock(LOCK)
    _candidate(ledger, "a", "gp", 0, 1.0, ["trend"])
    [entry] = load_entries(ledger, "c1", unit("gp", 0), fm)
    assert set(entry.public) == {"sharpe_is", "sortino_is", "max_drawdown", "total_return",
                                 "n_trades"}  # fmt: skip
