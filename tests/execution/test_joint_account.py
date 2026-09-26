"""One account replay across many slots (P3-08, ADR-0032).

Until now a portfolio was N independently backtested, independently self-financed return streams
combined by weight (`validation.portfolio.combine`). That abstraction stops describing the real
thing the moment ten strategies share one USDT balance, one margin pool and one kill switch.

The replay takes each slot's fills — produced by its own single-strategy backtest — plus the
mark and funding series, and walks them through one `PerpAccount` in time order. What comes out
is an account equity curve and a per-slot PnL contribution, and the two must reconcile: a
contribution chart that does not add up to the account is an invented subaccount.
"""

from __future__ import annotations

import numpy as np
import pytest

from quantcrucible.execution.joint_account import SlotFill, replay_account
from quantcrucible.execution.margin import Bracket, BracketTable

TABLE = BracketTable(
    symbol="A",
    brackets=(Bracket(cap=10_000_000.0, max_leverage=100, mmr=0.004, amount=0.0),),
)
TABLES = {"A": TABLE, "B": TABLE}


def ts(n: int) -> np.ndarray:
    return np.arange("2021-01-01", n, dtype="datetime64[D]").astype("datetime64[ns]")


def test_a_single_slot_round_trip_reconciles() -> None:
    times = ts(3)
    fills = [
        SlotFill(ts=int(times[0]), instrument="A", direction="long", qty=1.0, price=100.0,
                 commission=0.0, opening=True),
        SlotFill(ts=int(times[2]), instrument="A", direction="long", qty=1.0, price=110.0,
                 commission=0.0, opening=False),
    ]  # fmt: skip
    result = replay_account(fills, times, marks={"A": np.full(3, 100.0)}, funding={},
                            tables=TABLES, initial_cash=100_000.0, leverage=10)  # fmt: skip
    assert result.contributions[("A", "long")] == pytest.approx(10.0)
    assert result.equity[-1] == pytest.approx(100_010.0)


def test_account_equity_reconciles_with_the_sum_of_slot_contributions() -> None:
    """The UI promise: starting equity + Σ contributions = account equity, to rounding."""
    times = ts(4)
    fills = [
        SlotFill(ts=int(times[0]), instrument="A", direction="long", qty=1.0, price=100.0,
                 commission=0.5, opening=True),
        SlotFill(ts=int(times[0]), instrument="B", direction="short", qty=2.0, price=50.0,
                 commission=0.5, opening=True),
        SlotFill(ts=int(times[3]), instrument="A", direction="long", qty=1.0, price=105.0,
                 commission=0.5, opening=False),
        SlotFill(ts=int(times[3]), instrument="B", direction="short", qty=2.0, price=45.0,
                 commission=0.5, opening=False),
    ]  # fmt: skip
    marks = {"A": np.full(4, 100.0), "B": np.full(4, 50.0)}
    result = replay_account(fills, times, marks, funding={}, tables=TABLES,
                            initial_cash=100_000.0, leverage=10)  # fmt: skip
    total = sum(result.contributions.values())
    assert 100_000.0 + total == pytest.approx(result.equity[-1], abs=1e-9)


def test_fees_land_in_the_slot_that_paid_them() -> None:
    times = ts(2)
    fills = [
        SlotFill(ts=int(times[0]), instrument="A", direction="long", qty=1.0, price=100.0,
                 commission=7.0, opening=True),
        SlotFill(ts=int(times[1]), instrument="A", direction="long", qty=1.0, price=100.0,
                 commission=3.0, opening=False),
    ]  # fmt: skip
    result = replay_account(fills, times, marks={"A": np.full(2, 100.0)}, funding={},
                            tables=TABLES, initial_cash=100_000.0, leverage=10)  # fmt: skip
    assert result.contributions[("A", "long")] == pytest.approx(-10.0)


def test_funding_is_charged_to_the_open_side_and_shows_in_its_contribution() -> None:
    times = ts(3)
    fills = [
        SlotFill(ts=int(times[0]), instrument="A", direction="long", qty=1.0, price=100.0,
                 commission=0.0, opening=True),
        SlotFill(ts=int(times[2]), instrument="A", direction="long", qty=1.0, price=100.0,
                 commission=0.0, opening=False),
    ]  # fmt: skip
    funding = {"A": np.array([0.0, 0.01, 0.0])}  # 1% of a 100 notional on the middle bar
    result = replay_account(fills, times, marks={"A": np.full(3, 100.0)}, funding=funding,
                            tables=TABLES, initial_cash=100_000.0, leverage=10)  # fmt: skip
    assert result.contributions[("A", "long")] == pytest.approx(-1.0)
    assert result.funding_paid[("A", "long")] == pytest.approx(1.0)


def test_the_two_sides_of_one_contract_are_separate_slots() -> None:
    times = ts(3)
    fills = [
        SlotFill(ts=int(times[0]), instrument="A", direction="long", qty=1.0, price=100.0,
                 commission=0.0, opening=True),
        SlotFill(ts=int(times[0]), instrument="A", direction="short", qty=1.0, price=100.0,
                 commission=0.0, opening=True),
        SlotFill(ts=int(times[2]), instrument="A", direction="long", qty=1.0, price=110.0,
                 commission=0.0, opening=False),
        SlotFill(ts=int(times[2]), instrument="A", direction="short", qty=1.0, price=110.0,
                 commission=0.0, opening=False),
    ]  # fmt: skip
    result = replay_account(fills, times, marks={"A": np.full(3, 100.0)}, funding={},
                            tables=TABLES, initial_cash=100_000.0, leverage=10)  # fmt: skip
    assert result.contributions[("A", "long")] == pytest.approx(10.0)
    assert result.contributions[("A", "short")] == pytest.approx(-10.0)
    assert result.equity[-1] == pytest.approx(100_000.0)


def test_a_liquidation_closes_the_slot_and_is_recorded() -> None:
    times = ts(3)
    fills = [
        SlotFill(ts=int(times[0]), instrument="A", direction="long", qty=10.0, price=100.0,
                 commission=0.0, opening=True),
    ]  # fmt: skip
    marks = {"A": np.array([100.0, 100.0, 50.0])}  # far below a 100x-thin wallet
    result = replay_account(fills, times, marks, funding={}, tables=TABLES,
                            initial_cash=100_000.0, leverage=100)  # fmt: skip
    assert result.liquidations == [("A", "long")]
    assert result.contributions[("A", "long")] < 0


def test_the_same_input_reproduces_the_same_curve() -> None:
    times = ts(5)
    fills = [
        SlotFill(ts=int(times[0]), instrument="A", direction="long", qty=1.0, price=100.0,
                 commission=0.1, opening=True),
        SlotFill(ts=int(times[4]), instrument="A", direction="long", qty=1.0, price=103.0,
                 commission=0.1, opening=False),
    ]  # fmt: skip
    marks = {"A": np.linspace(100.0, 103.0, 5)}
    kw = dict(marks=marks, funding={}, tables=TABLES, initial_cash=100_000.0, leverage=10)
    a = replay_account(fills, times, **kw)  # type: ignore[arg-type]
    b = replay_account(list(reversed(fills)), times, **kw)  # type: ignore[arg-type]
    np.testing.assert_allclose(a.equity, b.equity)


def test_a_close_frees_margin_for_an_open_at_the_same_timestamp() -> None:
    """`sort_key` promises closes before opens at one timestamp, so a slot frees its margin
    before another asks for it. Sorting by instrument first silently breaks that promise
    whenever the closing slot's symbol sorts *after* the opening one's: here B closes and A
    opens on the same bar, A is tried first, and the entry is denied for a shortfall that never
    existed.
    """
    times = ts(3)
    open_b = SlotFill(
        ts=int(times[0]),
        instrument="B",
        direction="long",
        qty=1.0,
        price=100.0,
        commission=0.0,
        opening=True,
    )
    close_b = SlotFill(
        ts=int(times[1]),
        instrument="B",
        direction="long",
        qty=1.0,
        price=110.0,
        commission=0.0,
        opening=False,
    )
    open_a = SlotFill(
        ts=int(times[1]),
        instrument="A",
        direction="long",
        qty=1.0,
        price=100.0,
        commission=0.0,
        opening=True,
    )
    marks = {"A": np.array([100.0, 100.0, 120.0]), "B": np.full(3, 100.0)}
    kw = dict(marks=marks, funding={}, tables=TABLES, initial_cash=10.0, leverage=10)  # margin 10
    forward = replay_account([open_b, close_b, open_a], times, **kw)  # type: ignore[arg-type]
    shuffled = replay_account([open_a, open_b, close_b], times, **kw)  # type: ignore[arg-type]
    assert forward.denied == [] and shuffled.denied == []
    assert forward.equity[-1] == pytest.approx(40.0)  # 10 free + 10 margin + 20 unrealized
    np.testing.assert_allclose(forward.equity, shuffled.equity)


def test_a_fill_after_the_last_bar_is_refused_not_folded_into_it() -> None:
    """Clamping the bar index let a fill from beyond the measured window move equity inside it,
    which is a look-ahead the equity curve would never show."""
    times = ts(3)
    late = SlotFill(ts=int(times[-1]) + 86_400_000_000_000, instrument="A", direction="long",
                    qty=1.0, price=100.0, commission=0.0, opening=True)  # fmt: skip
    with pytest.raises(ValueError, match="outside"):
        replay_account([late], times, marks={"A": np.full(3, 100.0)}, funding={},
                       tables=TABLES, initial_cash=100_000.0, leverage=10)  # fmt: skip


def test_a_fill_before_the_first_bar_is_refused_too() -> None:
    times = ts(3)
    early = SlotFill(ts=int(times[0]) - 86_400_000_000_000, instrument="A", direction="long",
                     qty=1.0, price=100.0, commission=0.0, opening=True)  # fmt: skip
    with pytest.raises(ValueError, match="outside"):
        replay_account([early], times, marks={"A": np.full(3, 100.0)}, funding={},
                       tables=TABLES, initial_cash=100_000.0, leverage=10)  # fmt: skip


def test_a_close_through_the_bankruptcy_price_is_a_liquidation() -> None:
    """A stop that fills past the point where the wallet is gone is not a fill the venue would
    have made. It is recorded as a liquidation, and the loss stops at the wallet's margin."""
    times = ts(3)
    fills = [
        SlotFill(ts=int(times[0]), instrument="A", direction="long", qty=10.0, price=100.0,
                 commission=0.0, opening=True),  # notional 1000, margin 10 at 100x
        SlotFill(ts=int(times[2]), instrument="A", direction="long", qty=10.0, price=40.0,
                 commission=0.0, opening=False),  # a 600 loss against a 10 wallet
    ]  # fmt: skip
    marks = {"A": np.array([100.0, 100.0, 100.0])}  # the mark never prints the gap
    result = replay_account(fills, times, marks, funding={}, tables=TABLES,
                            initial_cash=1_000.0, leverage=100)  # fmt: skip
    assert result.liquidations == [("A", "long")]
    assert result.contributions[("A", "long")] == pytest.approx(-10.0)
    assert result.equity[-1] == pytest.approx(990.0)


def test_an_unfunded_entry_is_denied_rather_than_opened() -> None:
    times = ts(2)
    fills = [
        SlotFill(ts=int(times[0]), instrument="A", direction="long", qty=1_000.0, price=100.0,
                 commission=0.0, opening=True),
    ]  # fmt: skip
    result = replay_account(fills, times, marks={"A": np.full(2, 100.0)}, funding={},
                            tables=TABLES, initial_cash=1_000.0, leverage=1)  # fmt: skip
    assert result.denied and result.denied[0][0] == ("A", "long")
    assert result.equity[-1] == pytest.approx(1_000.0)
