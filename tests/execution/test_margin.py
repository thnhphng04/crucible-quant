"""Binance USDT-M bracket margin (P3-05, ADR-0032) — the arithmetic, not the venue's numbers.

The bracket table is *data*: the venue serves it from a signed endpoint and publishes no history,
so a campaign locks a snapshot and records it as an assumption. What these tests pin is the
arithmetic on top of it, against values computed by hand from the published formulas:

    initial margin      = notional / leverage
    maintenance margin  = notional × mmr − maintenance_amount
    isolated liquidation (long)  = (entry·q − wallet − amount) / (q · (1 − mmr))
    isolated liquidation (short) = (entry·q + wallet + amount) / (q · (1 + mmr))

`maintenance_amount` is the venue's continuity term: it makes maintenance margin continuous where
one tier ends and the next begins, so a position does not jump in margin as it grows.
"""

from __future__ import annotations

import pytest

from quantcrucible.execution.margin import Bracket, BracketTable

# A synthetic three-tier table. The tiers are chosen so the continuity term is easy to verify by
# hand, NOT copied from the venue: real brackets arrive with the campaign lock.
TABLE = BracketTable(
    symbol="TESTUSDT",
    brackets=(
        Bracket(cap=50_000.0, max_leverage=100, mmr=0.004, amount=0.0),
        Bracket(cap=500_000.0, max_leverage=50, mmr=0.005, amount=50.0),
        Bracket(cap=1_000_000.0, max_leverage=20, mmr=0.010, amount=2_550.0),
    ),
)


def test_the_bracket_is_chosen_by_notional() -> None:
    assert TABLE.bracket_for(10_000.0).max_leverage == 100
    assert TABLE.bracket_for(60_000.0).max_leverage == 50
    assert TABLE.bracket_for(600_000.0).max_leverage == 20


def test_a_tier_boundary_belongs_to_the_lower_tier() -> None:
    """Exactly at the cap the position is still inside that tier — both sides of the boundary
    are pinned, because an off-by-one here silently changes every margin above it."""
    assert TABLE.bracket_for(50_000.0).max_leverage == 100
    assert TABLE.bracket_for(50_000.01).max_leverage == 50


def test_a_notional_above_the_last_tier_is_refused() -> None:
    with pytest.raises(ValueError, match="above the largest bracket"):
        TABLE.bracket_for(1_000_000.01)


def test_maintenance_margin_is_continuous_across_a_boundary() -> None:
    """The continuity term earns its keep: maintenance margin does not jump at the cap."""
    below = TABLE.maintenance_margin(50_000.0)
    above = TABLE.maintenance_margin(50_000.0 + 1e-9)
    assert below == pytest.approx(50_000 * 0.004)  # 200
    assert above == pytest.approx(50_000 * 0.005 - 50.0)  # 200
    assert above == pytest.approx(below, abs=1e-6)


def test_initial_margin_is_notional_over_leverage() -> None:
    assert TABLE.initial_margin(60_000.0, leverage=10) == pytest.approx(6_000.0)


def test_leverage_above_the_bracket_is_refused() -> None:
    with pytest.raises(ValueError, match="bracket allows at most 50"):
        TABLE.initial_margin(60_000.0, leverage=60)


def test_the_lowest_leverage_that_fits_the_free_balance() -> None:
    """Lowest integer leverage is the safest that the wallet can fund: it maximises margin and
    so pushes liquidation furthest from the stop."""
    # notional 60_000 in the 50x tier: leverage 1..5 need 60k..12k, 6 needs 10_000
    assert TABLE.lowest_leverage(60_000.0, free=10_000.0) == 6
    assert TABLE.lowest_leverage(60_000.0, free=60_000.0) == 1


def test_lowest_leverage_is_none_when_the_bracket_cannot_fund_it() -> None:
    # at the 50x cap, 60_000 notional still needs 1_200; less than that cannot be opened
    assert TABLE.lowest_leverage(60_000.0, free=1_199.0) is None


def test_isolated_liquidation_price_long() -> None:
    """One BTC-sized position, hand-computed: q=1, entry=50_000 ⇒ notional 50_000, tier 1."""
    q, entry, wallet = 1.0, 50_000.0, 5_000.0  # 10x ⇒ wallet 5_000
    price = TABLE.liquidation_price("long", q, entry, wallet)
    expected = (entry * q - wallet - 0.0) / (q * (1 - 0.004))
    assert price == pytest.approx(expected)
    assert price < entry  # a long is liquidated below its entry


def test_isolated_liquidation_price_short() -> None:
    q, entry, wallet = 1.0, 50_000.0, 5_000.0
    price = TABLE.liquidation_price("short", q, entry, wallet)
    expected = (entry * q + wallet + 0.0) / (q * (1 + 0.004))
    assert price == pytest.approx(expected)
    assert price > entry  # a short is liquidated above its entry


def test_more_margin_pushes_liquidation_further_away() -> None:
    near = TABLE.liquidation_price("long", 1.0, 50_000.0, 2_500.0)
    far = TABLE.liquidation_price("long", 1.0, 50_000.0, 10_000.0)
    assert far < near


def test_the_table_refuses_unsorted_or_empty_brackets() -> None:
    with pytest.raises(ValueError, match="at least one bracket"):
        BracketTable(symbol="X", brackets=())
    with pytest.raises(ValueError, match="ascending"):
        BracketTable(
            symbol="X",
            brackets=(
                Bracket(cap=500.0, max_leverage=10, mmr=0.01, amount=0.0),
                Bracket(cap=100.0, max_leverage=5, mmr=0.02, amount=5.0),
            ),
        )
