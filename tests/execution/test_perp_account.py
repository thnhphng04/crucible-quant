"""Host-side USDT-M account: isolated wallets per side, funding, liquidation (P3-07, ADR-0032).

INV-93 and INV-94. Nautilus supplies the position and fill model; it cannot supply this, because
`MarginAccount` keys margin by `InstrumentId` alone and so cannot hold one symbol's LONG and
SHORT isolated wallets apart — which is exactly what Binance hedge mode is. The cost, recorded
in ADR-0032, is that P5 (backtest ≡ live) holds for order generation and not for this
arithmetic, so it must be reconciled against the venue before any capital.

Every expected value below is computed by hand from the formulas in `execution.margin`.
"""

from __future__ import annotations

import pytest

from quantcrucible.execution.margin import Bracket, BracketTable
from quantcrucible.execution.perp_account import Liquidated, PerpAccount

TABLE = BracketTable(
    symbol="A",
    brackets=(
        Bracket(cap=50_000.0, max_leverage=100, mmr=0.004, amount=0.0),
        Bracket(cap=500_000.0, max_leverage=50, mmr=0.005, amount=50.0),
    ),
)


def account(balance: float = 100_000.0) -> PerpAccount:
    return PerpAccount(balance=balance, tables={"A": TABLE})


def test_btc_long_and_short_hold_independent_isolated_margin() -> None:
    """INV-93: two wallets on one contract, each with its own margin and liquidation."""
    acc = account()
    acc.open("A", "long", qty=1.0, price=50_000.0, leverage=10)
    acc.open("A", "short", qty=0.5, price=50_000.0, leverage=5)
    long_wallet = acc.wallet("A", "long")
    short_wallet = acc.wallet("A", "short")
    assert long_wallet.margin == pytest.approx(5_000.0)  # 50_000 / 10
    assert short_wallet.margin == pytest.approx(5_000.0)  # 25_000 / 5
    assert acc.free == pytest.approx(100_000 - 10_000)
    # each side's liquidation reads only its own wallet
    assert acc.liquidation_price("A", "long") < 50_000.0
    assert acc.liquidation_price("A", "short") > 50_000.0


def test_closing_one_side_leaves_the_other_untouched() -> None:
    acc = account()
    acc.open("A", "long", qty=1.0, price=50_000.0, leverage=10)
    acc.open("A", "short", qty=1.0, price=50_000.0, leverage=10)
    before = acc.wallet("A", "short").margin
    acc.close("A", "long", price=51_000.0)
    assert acc.wallet("A", "long") is None
    assert acc.wallet("A", "short").margin == pytest.approx(before)
    assert acc.liquidation_price("A", "short") > 50_000.0


def test_realized_pnl_returns_to_the_free_balance() -> None:
    acc = account()
    acc.open("A", "long", qty=1.0, price=50_000.0, leverage=10)
    acc.close("A", "long", price=51_000.0)
    assert acc.free == pytest.approx(101_000.0)  # margin back plus 1_000 profit
    assert acc.equity(marks={}) == pytest.approx(101_000.0)


def test_a_short_profits_when_price_falls() -> None:
    acc = account()
    acc.open("A", "short", qty=1.0, price=50_000.0, leverage=10)
    acc.close("A", "short", price=49_000.0)
    assert acc.free == pytest.approx(101_000.0)


def test_liquidation_uses_the_mark_not_the_close() -> None:
    """A position is liquidated on the venue's mark price; the trade price may differ."""
    acc = account()
    acc.open("A", "long", qty=1.0, price=50_000.0, leverage=10)
    liq = acc.liquidation_price("A", "long")
    assert not acc.is_liquidated("A", "long", mark=liq + 1.0)
    assert acc.is_liquidated("A", "long", mark=liq - 1.0)


def test_funding_of_both_signs_debits_and_credits_the_right_side() -> None:
    """Positive funding: longs pay shorts. Negative: the other way round."""
    acc = account()
    acc.open("A", "long", qty=1.0, price=50_000.0, leverage=10)
    acc.open("A", "short", qty=1.0, price=50_000.0, leverage=10)
    long_before = acc.wallet("A", "long").margin
    short_before = acc.wallet("A", "short").margin
    acc.apply_funding("A", rate=0.0001, mark=50_000.0)  # 5 USDT on a 50_000 notional
    assert acc.wallet("A", "long").margin == pytest.approx(long_before - 5.0)
    assert acc.wallet("A", "short").margin == pytest.approx(short_before + 5.0)
    acc.apply_funding("A", rate=-0.0002, mark=50_000.0)  # 10 USDT, paid by shorts
    assert acc.wallet("A", "long").margin == pytest.approx(long_before + 5.0)
    assert acc.wallet("A", "short").margin == pytest.approx(short_before - 5.0)


def test_funding_moves_the_liquidation_price() -> None:
    """Funding is paid out of the isolated wallet, so paying it brings liquidation closer."""
    acc = account()
    acc.open("A", "long", qty=1.0, price=50_000.0, leverage=10)
    before = acc.liquidation_price("A", "long")
    acc.apply_funding("A", rate=0.001, mark=50_000.0)
    assert acc.liquidation_price("A", "long") > before  # closer to the entry from below


def test_a_loss_beyond_the_margin_stops_at_the_isolated_wallet() -> None:
    """Isolated means isolated, and `close` is the one place that can break it.

    A close through the bankruptcy price is reachable: the mark can gap past liquidation between
    two bars, or a stop can fill far below it. Adding the whole realized loss to the free balance
    lets one wallet drain the account, which is exactly what the venue's isolated mode prevents —
    and it makes a wallet's worst case unknowable in advance, so the 10% portfolio cap stops
    bounding anything.
    """
    acc = account(balance=10_000.0)
    acc.open("A", "long", qty=1.0, price=50_000.0, leverage=10)  # margin 5_000
    assert acc.free == pytest.approx(5_000.0)
    pnl = acc.close("A", "long", price=20_000.0)  # a 30_000 move against a 5_000 wallet
    assert pnl == pytest.approx(-5_000.0)
    assert acc.free == pytest.approx(5_000.0)


def test_an_unrealized_loss_beyond_the_margin_is_not_negative_equity() -> None:
    """The same bound, read through `equity`: a wallet marked past bankruptcy contributes zero,
    not a negative number the free balance would have to absorb."""
    acc = account(balance=10_000.0)
    acc.open("A", "long", qty=1.0, price=50_000.0, leverage=10)
    assert acc.equity({"A": 20_000.0}) == pytest.approx(5_000.0)


def test_a_profitable_close_is_untouched_by_the_bound() -> None:
    acc = account(balance=10_000.0)
    acc.open("A", "long", qty=1.0, price=50_000.0, leverage=10)
    assert acc.close("A", "long", price=51_000.0) == pytest.approx(1_000.0)
    assert acc.free == pytest.approx(11_000.0)


def test_a_liquidated_wallet_cannot_draw_on_free_usdt() -> None:
    """Isolated means isolated: the loss stops at that wallet, and the account keeps the rest."""
    acc = account(balance=100_000.0)
    acc.open("A", "long", qty=1.0, price=50_000.0, leverage=10)
    free_after_open = acc.free
    acc.liquidate("A", "long")
    assert acc.wallet("A", "long") is None
    assert acc.free == pytest.approx(free_after_open)  # the 5_000 margin is gone, nothing more
    assert acc.equity(marks={}) == pytest.approx(95_000.0)


def test_opening_without_enough_free_balance_is_refused() -> None:
    acc = account(balance=1_000.0)
    with pytest.raises(Liquidated, match="cannot fund"):
        acc.open("A", "long", qty=1.0, price=50_000.0, leverage=10)


def test_opening_an_occupied_wallet_is_refused_without_changing_free_balance() -> None:
    acc = account()
    acc.open("A", "long", qty=1.0, price=50_000.0, leverage=10)
    free = acc.free
    with pytest.raises(ValueError, match="already open"):
        acc.open("A", "long", qty=0.5, price=50_000.0, leverage=10)
    assert acc.wallet("A", "long").qty == pytest.approx(1.0)
    assert acc.free == pytest.approx(free)


def test_leverage_above_the_bracket_is_refused() -> None:
    acc = account()
    with pytest.raises(ValueError, match="bracket allows at most"):
        acc.open("A", "long", qty=2.0, price=50_000.0, leverage=80)  # 100k -> 50x tier


def test_equity_marks_open_positions_to_market() -> None:
    acc = account()
    acc.open("A", "long", qty=1.0, price=50_000.0, leverage=10)
    assert acc.equity(marks={"A": 51_000.0}) == pytest.approx(101_000.0)
    assert acc.equity(marks={"A": 49_000.0}) == pytest.approx(99_000.0)


def test_both_sides_open_net_out_in_equity() -> None:
    acc = account()
    acc.open("A", "long", qty=1.0, price=50_000.0, leverage=10)
    acc.open("A", "short", qty=1.0, price=50_000.0, leverage=10)
    assert acc.equity(marks={"A": 55_000.0}) == pytest.approx(100_000.0)


def test_the_kill_switch_fires_on_account_drawdown() -> None:
    acc = account()
    acc.open("A", "long", qty=1.0, price=50_000.0, leverage=10)
    assert not acc.kill_switch_tripped(marks={"A": 49_000.0}, max_drawdown=0.20)
    acc.liquidate("A", "long")
    acc.open("A", "long", qty=4.0, price=50_000.0, leverage=10)
    acc.liquidate("A", "long")  # a second 20_000 gone: 75_000 against a 100_000 peak
    assert acc.kill_switch_tripped(marks={}, max_drawdown=0.20)


def test_an_unknown_symbol_is_refused_rather_than_guessed() -> None:
    acc = account()
    with pytest.raises(KeyError, match="no bracket table"):
        acc.open("B", "long", qty=1.0, price=100.0, leverage=1)
