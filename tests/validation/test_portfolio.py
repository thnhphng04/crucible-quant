"""Portfolio construction, §3.2.1 steps 1–6 (P1-07, INV-45, ADR-0013)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from quantcrucible.ledger.db import Ledger
from quantcrucible.ledger.records import GateResultRecord, TrialRecord, TrialStats
from quantcrucible.validation.gates import G4_PBO
from quantcrucible.validation.portfolio import (
    Member,
    PortfolioRule,
    build_and_record,
    build_portfolio,
    combine,
    eligible_trials,
    load_returns,
    portfolio_hash,
    record_variant,
)

T = 400
TS = pd.date_range("2020-01-01", periods=T, freq="D")
STATS = TrialStats(n_raw=10, n_eff=10, var_sr=0.25)


@pytest.fixture
def ledger(tmp_path: Path) -> Ledger:
    led = Ledger.open(tmp_path / "ledger.db")
    led.open_campaign("c1", "2030-01-01/2031-01-01", lock_hash="h")
    return led


def add(
    ledger: Ledger,
    tmp_path: Path,
    name: str,
    rets: np.ndarray,
    cell: str | None = None,
    passed_g4: bool = True,
    params: dict[str, float] | None = None,
) -> int:
    path = tmp_path / f"{name}.parquet"
    pd.DataFrame({"ts": TS[: len(rets)], "ret": rets}).to_parquet(path, index=False)
    tid = ledger.record_trial(
        TrialRecord(
            run_id="r", campaign_id="c1", candidate_id=name, engine="manual", seed=0,
            strategy_hash=f"h-{name}", params=params or {"p": 1}, universe="X", timeframe="1d",
            timerange="t", source="manual", sharpe_is=float(np.mean(rets) / np.std(rets)),
            returns_path=str(path), verdict="PASS", cell_id=cell,
        )
    )  # fmt: skip
    ledger.record_gate_result(
        GateResultRecord(campaign_id="c1", candidate_id=name, gate=G4_PBO, passed=passed_g4,
                         reason="x", trial_id=tid)
    )  # fmt: skip
    return tid


def noise(seed: int, mean: float = 0.001, vol: float = 0.01) -> np.ndarray:
    return np.random.default_rng(seed).normal(mean, vol, T)


def build(ledger: Ledger, rule: PortfolioRule | None = None) -> object:
    trials = eligible_trials(ledger, "c1")
    rets = {t.id: load_returns(t.returns_path) for t in trials}
    return build_portfolio(trials, rets, rule or PortfolioRule(), STATS, 365, "c1")


def names(p: object) -> list[str]:
    return [m.candidate_id for m in p.members]  # type: ignore[attr-defined]


def test_only_candidates_that_passed_gate4_are_eligible(ledger: Ledger, tmp_path: Path) -> None:
    add(ledger, tmp_path, "a", noise(1))
    add(ledger, tmp_path, "b", noise(2), passed_g4=False)
    assert [t.candidate_id for t in eligible_trials(ledger, "c1")] == ["a"]


def test_a_later_rejected_trial_is_not_eligible(ledger: Ledger, tmp_path: Path) -> None:
    """Review finding 2: an earlier ④ pass must not vouch for a later trial of the same id
    that failed ③ (no ④ result at all). The latest measurement decides; no fallback."""
    add(ledger, tmp_path, "a", noise(1))
    add(ledger, tmp_path, "b", noise(2))
    ledger.record_trial(
        TrialRecord(
            run_id="r2", campaign_id="c1", candidate_id="a", engine="manual", seed=0,
            strategy_hash="h-a", params={"p": 2}, universe="X", timeframe="1d", timerange="t",
            source="param_opt", sharpe_is=0.1, returns_path="unused.parquet",
            verdict="REJECT_g3_is", gate_failed="g3_is",
        )
    )  # fmt: skip
    assert [t.candidate_id for t in eligible_trials(ledger, "c1")] == ["b"]


def test_the_4_pass_must_belong_to_the_selected_trial(ledger: Ledger, tmp_path: Path) -> None:
    first = add(ledger, tmp_path, "a", noise(1))
    second = add(ledger, tmp_path, "a", noise(3), params={"p": 2})
    assert [t.id for t in eligible_trials(ledger, "c1")] == [second]
    third = add(ledger, tmp_path, "a", noise(4), params={"p": 3}, passed_g4=False)
    assert eligible_trials(ledger, "c1") == [] and first < second < third


def test_step1_one_representative_per_cell(ledger: Ledger, tmp_path: Path) -> None:
    add(ledger, tmp_path, "weak", noise(1, mean=0.0002), cell="trend")
    add(ledger, tmp_path, "strong", noise(2, mean=0.003), cell="trend")
    add(ledger, tmp_path, "solo", noise(3))  # no cell ⇒ its own cell
    p = build(ledger)
    assert sorted(names(p)) == ["solo", "strong"]
    assert p.steps["representatives"][0] == "strong"  # type: ignore[attr-defined]


def test_step2_correlation_filter_in_score_order(ledger: Ledger, tmp_path: Path) -> None:
    base = noise(1, mean=0.003)
    add(ledger, tmp_path, "best", base)
    twin = base * 0.9 + np.random.default_rng(9).normal(0, 0.002, T)  # |ρ| ≈ 0.98, lower score
    add(ledger, tmp_path, "twin", twin - 0.0005)
    add(ledger, tmp_path, "other", noise(3, mean=0.001))
    p = build(ledger)
    assert names(p) == ["best", "other"]
    loose = build(ledger, PortfolioRule(max_corr=0.99))
    assert "twin" in names(loose)


def test_step3_cap(ledger: Ledger, tmp_path: Path) -> None:
    for i in range(6):
        add(ledger, tmp_path, f"s{i}", noise(i, mean=0.001 * (i + 1)))
    p = build(ledger, PortfolioRule(max_strategies=3))
    assert len(names(p)) == 3
    assert names(p) == p.steps["decorrelated"][:3]  # type: ignore[attr-defined]  # top by score


def test_step4_naive_risk_parity(ledger: Ledger, tmp_path: Path) -> None:
    calm, wild = noise(1, vol=0.01), noise(2, vol=0.02)
    add(ledger, tmp_path, "calm", calm)
    add(ledger, tmp_path, "wild", wild)
    p = build(ledger)
    w = {m.candidate_id: m.weight for m in p.members}  # type: ignore[attr-defined]
    # the same vol budget each: w ∝ 1/σ
    ratio = np.std(wild, ddof=1) / np.std(calm, ddof=1)
    assert w["calm"] / w["wild"] == pytest.approx(ratio, rel=1e-9)
    assert sum(w.values()) == pytest.approx(1.0)


def test_step5_monthly_rebalance_by_hand() -> None:
    idx = pd.DatetimeIndex(["2020-01-30", "2020-01-31", "2020-02-01"])
    frame = pd.DataFrame({1: [0.10, 0.0, 0.0], 2: [0.0, 0.0, 0.10]}, index=idx)
    out = combine(frame, np.array([0.5, 0.5]), "monthly")
    # Jan 30: 0.5·1.1 + 0.5 = 1.05; Jan 31: flat; Feb 1: reset to 0.525 each, B +10% ⇒ +5%
    np.testing.assert_allclose(out.to_numpy(), [0.05, 0.0, 0.05])
    drift = combine(frame, np.array([0.5, 0.5]), "quarterly")  # no reset: A holds 0.55 of 1.05
    assert drift.iloc[2] == pytest.approx(0.5 * 0.10 / 1.05)


def test_step6_hash_is_deterministic_and_sensitive() -> None:
    a = Member(1, "a", "ha", {"p": 1}, 0.6)
    b = Member(2, "b", "hb", {"p": 2}, 0.4)
    rule = PortfolioRule()
    h = portfolio_hash([a, b], rule)
    assert portfolio_hash([b, a], rule) == h  # member order irrelevant
    assert portfolio_hash([a, replace(b, weight=0.41)], rule) != h
    assert portfolio_hash([a, replace(b, params={"p": 3})], rule) != h
    assert portfolio_hash([a], rule) != h
    assert portfolio_hash([a, b], PortfolioRule(max_corr=0.4)) != h
    assert portfolio_hash([a, b], PortfolioRule(rebalance="weekly")) != h
    assert portfolio_hash([a, replace(b, weight=0.4 + 1e-13)], rule) == h  # rounding


def test_rule_change_is_new_variant(ledger: Ledger, tmp_path: Path) -> None:
    """INV-45: every distinct portfolio is a `portfolio_variants` row (counted in N at ⑤);
    rebuilding the same portfolio is not a new selection."""
    base = noise(1, mean=0.003)
    add(ledger, tmp_path, "best", base)
    add(ledger, tmp_path, "twin", base * 0.9 + np.random.default_rng(9).normal(0, 0.002, T))
    add(ledger, tmp_path, "other", noise(3))
    res = tmp_path / "res"
    first = build_and_record(ledger, "c1", PortfolioRule(), 365, res)
    assert ledger.total_portfolio_variants() == 1
    build_and_record(ledger, "c1", PortfolioRule(), 365, res)  # identical: not a new variant
    assert ledger.total_portfolio_variants() == 1
    second = build_and_record(ledger, "c1", PortfolioRule(max_corr=0.99), 365, res)
    assert ledger.total_portfolio_variants() == 2
    assert first.portfolio_hash != second.portfolio_hash
    (v1, v2) = ledger.portfolio_variants("c1")
    assert v1.rule_config["max_corr"] == 0.5 and v2.rule_config["max_corr"] == 0.99
    assert Path(v1.returns_path or "").is_file()


def test_a_dropped_candidate_does_not_change_the_portfolio(ledger: Ledger, tmp_path: Path) -> None:
    """Review 2, HIGH: a candidate the correlation filter drops — here with only 100 days of
    history — must not truncate the members' window. Same hash ⇒ same returns, same Sharpe."""
    base = noise(1, mean=0.003)
    add(ledger, tmp_path, "best", base)
    res = tmp_path / "res"
    before = build_and_record(ledger, "c1", PortfolioRule(), 365, res)
    short_twin = base[:100] * 0.9 + np.random.default_rng(9).normal(0, 0.002, 100) - 0.0005
    add(ledger, tmp_path, "twin", short_twin)
    after = build_and_record(ledger, "c1", PortfolioRule(), 365, res)
    assert names(after) == ["best"] and after.steps["decorrelated"] == ["best"]
    assert after.portfolio_hash == before.portfolio_hash
    assert len(after.returns) == len(before.returns) == T
    pd.testing.assert_series_equal(after.returns, before.returns)
    assert after.sharpe_is == before.sharpe_is
    (variant,) = ledger.portfolio_variants("c1")
    pd.testing.assert_series_equal(
        load_returns(variant.returns_path or ""), after.returns, check_names=False, check_freq=False
    )


def test_member_weights_use_the_members_window_only(ledger: Ledger, tmp_path: Path) -> None:
    """Weights and returns come from the chosen members' common window, not from the window
    shared with every candidate."""
    calm, wild = noise(1, vol=0.01), noise(2, vol=0.02)
    add(ledger, tmp_path, "calm", calm)
    add(ledger, tmp_path, "wild", wild)
    add(ledger, tmp_path, "short", noise(3, mean=-0.002)[:60], passed_g4=True)
    p = build(ledger, PortfolioRule(max_strategies=2))
    assert sorted(names(p)) == ["calm", "wild"] and len(p.returns) == T  # type: ignore[attr-defined]
    w = {m.candidate_id: m.weight for m in p.members}  # type: ignore[attr-defined]
    assert w["calm"] / w["wild"] == pytest.approx(np.std(wild, ddof=1) / np.std(calm, ddof=1))


def test_candidates_with_too_little_overlap_are_not_accepted(
    ledger: Ledger, tmp_path: Path
) -> None:
    """|ρ| cannot be measured on fewer than MIN_COMMON_OBS shared days: the lower-ranked one is
    not shown to be uncorrelated, so it is dropped (conservative)."""
    add(ledger, tmp_path, "long", noise(1, mean=0.003))
    path = tmp_path / "late.parquet"
    late = noise(2, mean=0.001)[:40]
    ts = pd.date_range(TS[T - 20], periods=40, freq="D")  # 20 days shared with "long"
    pd.DataFrame({"ts": ts, "ret": late}).to_parquet(path, index=False)
    tid = ledger.record_trial(
        TrialRecord(
            run_id="r", campaign_id="c1", candidate_id="late", engine="manual", seed=0,
            strategy_hash="h-late", params={"p": 1}, universe="X", timeframe="1d", timerange="t",
            source="manual", sharpe_is=0.1, returns_path=str(path), verdict="PASS",
        )
    )  # fmt: skip
    ledger.record_gate_result(
        GateResultRecord(campaign_id="c1", candidate_id="late", gate=G4_PBO, passed=True,
                         reason="x", trial_id=tid)
    )  # fmt: skip
    p = build(ledger)
    assert names(p) == ["long"] and len(p.returns) == T  # type: ignore[attr-defined]


def test_a_recorded_hash_must_match_its_artifact(ledger: Ledger, tmp_path: Path) -> None:
    """Same hash, different returns ⇒ refused: gate ⑤ never evaluates data other than the
    variant's stored artifact."""
    add(ledger, tmp_path, "best", noise(1, mean=0.003))
    res = tmp_path / "res"
    p = build_and_record(ledger, "c1", PortfolioRule(), 365, res)
    assert record_variant(ledger, p, res) is False  # identical: fine, not a new variant
    shifted = replace(p, returns=p.returns.iloc[:100])
    with pytest.raises(ValueError, match="artifact"):
        record_variant(ledger, shifted, res)
    assert ledger.total_portfolio_variants() == 1


def test_nothing_eligible_raises(ledger: Ledger, tmp_path: Path) -> None:
    add(ledger, tmp_path, "a", noise(1), passed_g4=False)
    with pytest.raises(ValueError, match="gate ④"):
        build(ledger)
