"""Resolving one bar: stop, liquidation, or neither (P3-07, ADR-0032).

Two price levels sit below a long: its protective stop and its liquidation. Which is reached
first decides whether the loss is the planned one or the whole isolated wallet. The path summary
answers that exactly — except when both are first reached in the same minute, where the order
inside that minute is genuinely unknown and the honest answer is "the worse one, and flagged".

Also here: the clearance rule. ADR-0032 requires liquidation to sit at least 25% of the
entry-to-stop distance beyond the stop. That rule is close to vacuous at low leverage, which is
why one test *constructs* a case where it binds — a rule that passes vacuously in every test is
not a rule.
"""

from __future__ import annotations

import pytest

from quantcrucible.execution.margin import Bracket, BracketTable
from quantcrucible.execution.path_summary import summarize_path
from quantcrucible.execution.perp_account import (
    ClearanceRefused,
    PerpAccount,
    assert_clearance,
    resolve_bar,
)

TABLE = BracketTable(
    symbol="A",
    brackets=(Bracket(cap=1_000_000.0, max_leverage=100, mmr=0.004, amount=0.0),),
)


def account() -> PerpAccount:
    return PerpAccount(balance=100_000.0, tables={"A": TABLE})


def test_a_bar_that_touches_neither_level_leaves_the_position_open() -> None:
    acc = account()
    acc.open("A", "long", qty=1.0, price=50_000.0, leverage=2)
    path = summarize_path([50_000.0, 50_100.0, 49_950.0])
    outcome = resolve_bar(acc, "A", "long", stop=49_000.0, path=path)
    assert outcome.event == "open"
    assert not outcome.ambiguous


def test_the_stop_fires_when_it_is_reached_first() -> None:
    acc = account()
    acc.open("A", "long", qty=1.0, price=50_000.0, leverage=2)  # liquidation far below
    path = summarize_path([50_000.0, 49_500.0, 48_900.0])
    outcome = resolve_bar(acc, "A", "long", stop=49_000.0, path=path)
    assert outcome.event == "stop"
    assert not outcome.ambiguous


def test_liquidation_wins_when_it_is_reached_first() -> None:
    """A gap through both: at 10x the wallet dies before a far stop is ever touched."""
    acc = account()
    acc.open("A", "long", qty=1.0, price=50_000.0, leverage=10)
    liq = acc.liquidation_price("A", "long")
    path = summarize_path([50_000.0, liq - 500.0])
    outcome = resolve_bar(acc, "A", "long", stop=liq - 100.0, path=path)
    assert outcome.event == "liquidation"


def test_ambiguous_minute_bar_takes_the_worse_outcome_and_flags_it() -> None:
    """Both levels first reached in the same minute. The order inside it is unknown, so the
    caller is told rather than handed a guess — and the worse outcome is assumed."""
    acc = account()
    acc.open("A", "long", qty=1.0, price=50_000.0, leverage=10)
    liq = acc.liquidation_price("A", "long")
    path = summarize_path([50_000.0, liq - 10.0])  # one step through the stop and the liquidation
    outcome = resolve_bar(acc, "A", "long", stop=liq + 200.0, path=path)
    assert outcome.event == "liquidation"  # the worse of the two
    assert outcome.ambiguous


def test_a_short_resolves_upwards() -> None:
    acc = account()
    acc.open("A", "short", qty=1.0, price=50_000.0, leverage=2)
    path = summarize_path([50_000.0, 50_500.0, 51_100.0])
    outcome = resolve_bar(acc, "A", "short", stop=51_000.0, path=path)
    assert outcome.event == "stop"


def test_clearance_holds_comfortably_at_low_leverage() -> None:
    """The ordinary case: leverage 1 puts liquidation a whole entry-move away."""
    acc = account()
    acc.open("A", "long", qty=1.0, price=50_000.0, leverage=1)
    assert_clearance(acc, "A", "long", stop=49_000.0, fraction=0.25)


def test_clearance_below_the_floor_is_refused_with_a_reason() -> None:
    """Constructed to bind: at 100x the wallet is thin, so liquidation sits just under a stop
    placed far from entry. Without this case the rule would pass vacuously everywhere."""
    acc = account()
    acc.open("A", "long", qty=1.0, price=50_000.0, leverage=100)
    liq = acc.liquidation_price("A", "long")
    stop = liq - 1.0  # liquidation is 1 USDT beyond a stop ~500 from entry
    with pytest.raises(ClearanceRefused, match="clearance"):
        assert_clearance(acc, "A", "long", stop=stop, fraction=0.25)


def test_a_stop_on_the_wrong_side_of_entry_is_refused() -> None:
    acc = account()
    acc.open("A", "long", qty=1.0, price=50_000.0, leverage=2)
    with pytest.raises(ClearanceRefused, match="below the entry"):
        assert_clearance(acc, "A", "long", stop=51_000.0, fraction=0.25)
