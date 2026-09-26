"""One member per (instrument, direction) slot (P3-13, ADR-0033) — INV-97.

Step 1 of §3.2.1 kept one representative per feature-map cell, campaign-wide. That was right
when every candidate searched the same five-symbol basket: two candidates in one cell really
were near-duplicates. It is wrong once scopes are independent — BTC-long and XRP-short landing
in the same cell are not duplicates of each other, and deduping would silently drop one.

So the cell dedupe is *replaced*, not worked around: the portfolio has ten slots, one per
(instrument, direction), and each takes the best candidate searched for it. A slot with no
qualifying candidate stays empty rather than borrowing from a neighbour.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quantcrucible.ledger.records import TrialStats
from quantcrucible.validation.portfolio import PortfolioRule, select_slots

RULE = PortfolioRule()
STATS = TrialStats(n_raw=100, n_eff=50, var_sr=0.3)
PPY = 365.0


class Row:
    """The fields slot selection reads off a trial."""

    def __init__(
        self, tid: int, instrument: str, direction: str, sharpe: float, passed4: bool = True
    ) -> None:
        self.id = tid
        self.candidate_id = f"c{tid}"
        self.instrument = instrument
        self.direction = direction
        self.sharpe_is = sharpe
        self.passed4 = passed4


def returns(seed: int, mean: float) -> pd.Series:
    """One fixed noise draw, shifted by ``mean``, so a higher mean is unambiguously better.

    Drawing fresh noise per row would let a lucky seed outrank a genuinely better mean, which
    tests the random generator rather than the selection rule.
    """
    rng = np.random.default_rng(0)
    idx = pd.date_range("2021-01-01", periods=400, freq="D")
    return pd.Series(mean + rng.normal(0.0, 0.01, 400), index=idx)


def test_one_member_per_slot_chosen_by_dsr_rank() -> None:
    rows = [
        Row(1, "BTCUSDT", "long", 1.0),
        Row(2, "BTCUSDT", "long", 2.0),  # the better one in this slot
        Row(3, "ETHUSDT", "short", 0.5),
    ]
    chosen = select_slots(
        rows, {r.id: returns(r.id, 0.001 * r.sharpe_is) for r in rows}, STATS, PPY
    )
    assert {(r.instrument, r.direction) for r in chosen} == {
        ("BTCUSDT", "long"),
        ("ETHUSDT", "short"),
    }
    assert next(r.id for r in chosen if r.instrument == "BTCUSDT") == 2


def test_ties_break_on_the_lowest_trial_id() -> None:
    """Deterministic, and it prefers the earlier trial rather than whichever sorted first."""
    rows = [Row(7, "BTCUSDT", "long", 1.0), Row(3, "BTCUSDT", "long", 1.0)]
    same = returns(1, 0.001)
    chosen = select_slots(rows, {r.id: same for r in rows}, STATS, PPY)
    assert [r.id for r in chosen] == [3]


def test_a_slot_with_no_gate_four_pass_stays_empty() -> None:
    rows = [Row(1, "BTCUSDT", "long", 2.0, passed4=False), Row(2, "ETHUSDT", "long", 1.0)]
    chosen = select_slots(rows, {r.id: returns(r.id, 0.001) for r in rows}, STATS, PPY)
    assert [(r.instrument, r.direction) for r in chosen] == [("ETHUSDT", "long")]


def test_a_negative_is_sharpe_is_never_selected() -> None:
    """A slot is left empty rather than filled with something that lost money in sample."""
    rows = [Row(1, "BTCUSDT", "long", -0.5)]
    assert select_slots(rows, {1: returns(1, -0.001)}, STATS, PPY) == []


def test_two_scopes_in_one_cell_are_both_kept() -> None:
    """The reason cell dedupe had to go: identical metrics on different contracts are not
    duplicates, and the old step-1 representative rule would have dropped one."""
    rows = [Row(1, "BTCUSDT", "long", 1.0), Row(2, "XRPUSDT", "short", 1.0)]
    same = returns(5, 0.001)
    chosen = select_slots(rows, {r.id: same for r in rows}, STATS, PPY)
    assert len(chosen) == 2


def test_the_slot_cap_is_two_per_instrument() -> None:
    rows = [
        Row(1, "BTCUSDT", "long", 1.0),
        Row(2, "BTCUSDT", "short", 1.0),
        Row(3, "BTCUSDT", "long", 2.0),
    ]
    chosen = select_slots(rows, {r.id: returns(r.id, 0.001) for r in rows}, STATS, PPY)
    assert len(chosen) == 2
    assert sorted(r.direction for r in chosen) == ["long", "short"]


def test_max_strategies_caps_the_whole_portfolio() -> None:
    rows = [Row(i, f"S{i:02d}", "long", 1.0 + i) for i in range(30)]
    chosen = select_slots(
        rows,
        {r.id: returns(r.id, 0.001) for r in rows},
        STATS,
        PPY,
        max_strategies=RULE.max_strategies,
    )
    assert len(chosen) == RULE.max_strategies


def test_no_candidates_gives_no_slots() -> None:
    assert select_slots([], {}, STATS, PPY) == []


def test_selection_does_not_depend_on_input_order() -> None:
    rows = [Row(1, "BTCUSDT", "long", 1.0), Row(2, "ETHUSDT", "long", 3.0)]
    data = {r.id: returns(r.id, 0.001 * r.sharpe_is) for r in rows}
    forward = [r.id for r in select_slots(rows, data, STATS, PPY)]
    backward = [r.id for r in select_slots(list(reversed(rows)), data, STATS, PPY)]
    assert forward == backward


def test_a_member_without_returns_is_skipped_not_guessed() -> None:
    rows = [Row(1, "BTCUSDT", "long", 1.0), Row(2, "ETHUSDT", "long", 1.0)]
    chosen = select_slots(rows, {2: returns(2, 0.001)}, STATS, PPY)
    assert [r.id for r in chosen] == [2]


def test_an_unscoped_legacy_trial_is_refused() -> None:
    """A pre-P3 trial searched the whole basket, so it belongs to no slot. Putting it in one
    would claim it was searched for that contract, which it was not."""
    rows = [Row(1, "legacy_spot", "long", 1.0)]
    with pytest.raises(ValueError, match="legacy"):
        select_slots(rows, {1: returns(1, 0.001)}, STATS, PPY)
