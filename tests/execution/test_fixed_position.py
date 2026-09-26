"""A perpetual position's size and stop are fixed at entry (P3-19, ADR-0031, ADR-0032).

The frozen requirements forbid scale-in and fix the stop from entry. Two things in the bridge
contradicted that, and only one of them is obvious:

- ``REBALANCE_BAND`` resized an open position whenever the target moved by more than 25%;
- and the protective stop was **cancelled and re-issued every bar** at ``close ± stop_distance``,
  so an ATR-derived stop drifted for the whole life of the position.

The second is the one that matters for safety. ``assert_clearance`` is checked at entry, and it
only means anything for the rest of the position's life if the stop it checked is the stop that
stays. A stop that widens as volatility rises can drift *under* the liquidation price mid-life —
in exactly the crash where liquidation is the thing being guarded against.

These tests drive the bridge through `run_backtest` with a signal whose stop distance grows every
bar, which is what an ATR stop does in a selloff.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np
import pytest

from quantcrucible.core.path_summary import segment_bar
from quantcrucible.core.perp_inputs import PerpBundle
from quantcrucible.core.strategy.base import Bars, Features, FeatureView, Signal, Strategy
from quantcrucible.execution.engine import BacktestResult, run_backtest
from quantcrucible.execution.nautilus_bridge import FillRecord
from quantcrucible.execution.risk import RiskSettings
from tests.factories import make_bars

SYMBOL = "BTC/USDT:USDT"


@dataclass
class WideningStop(Strategy):
    """Always long, with a stop distance that grows every bar — an ATR stop in a selloff.

    The growth is kept small enough that ``close - stop_distance`` stays positive for the whole
    run: a trigger at or below zero is refused by the bridge, which would end the test early for
    a reason that has nothing to do with what it is measuring.
    """

    step: float = 0.05
    seen: int = field(default=0)

    def indicators(self, bars: Bars) -> Features:
        return {}

    def signal(self, x: FeatureView) -> Signal:
        self.seen += 1
        return Signal("long", 1.0, 1.0 + self.step * self.seen)


def bundle(bars: Bars) -> PerpBundle:
    """Mark equal to trade, one flat intrabar path per bar, one bracket tier."""
    flat = [float(c) for c in bars.close]
    paths = tuple(
        segment_bar([0, 1, 2], [c, c, c], [c, c, c], cuts=[]) for c in (float(x) for x in flat)
    )
    return PerpBundle(
        symbol=bars.symbol,
        timeframe=bars.timeframe,
        marks=bars,
        funding=np.zeros((0, 4), dtype=np.float64),
        paths=paths,
        brackets=({"cap": 1e9, "max_leverage": 100, "mmr": 0.004, "amount": 0.0},),
    )


def perp_backtest(n: int = 60, seed: int = 1) -> BacktestResult:
    bars = make_bars(n, seed=seed, symbol=SYMBOL)
    return run_backtest(
        WideningStop(),
        {SYMBOL: bars},
        risk=RiskSettings(max_risk_pct=0.01),
        lookback=20,
        perp={SYMBOL: bundle(bars)},
    )


def excursions(fills: Sequence[FillRecord]) -> list[list[FillRecord]]:
    """Group fills into excursions away from flat, so each group is one position's whole life."""
    out: list[list[FillRecord]] = []
    position = 0.0
    current: list[FillRecord] = []
    for f in sorted(fills, key=lambda f: f.ts):
        current.append(f)
        position += f.qty if f.side == "BUY" else -f.qty
        if abs(position) < 1e-9:
            out.append(current)
            current = []
    if current:
        out.append(current)
    return out


def test_an_open_position_is_never_added_to() -> None:
    """A second same-side **order** while the position is open is a scale-in, which the frozen
    requirements forbid and which the joint replay cannot even represent. Re-entering after the
    stop has closed the position is a new excursion, and allowed.

    Counted by distinct fill timestamps, not by fill count: one order can fill in several pieces
    against one bar's liquidity, and all those pieces carry that bar's `ts_event`.
    """
    res = perp_backtest()
    runs = excursions(res.fills)
    assert runs, "the strategy never entered"
    for run in runs:
        entry_bars = {f.ts for f in run if f.side == "BUY"}
        exit_bars = {f.ts for f in run if f.side == "SELL"}
        assert len(entry_bars) == 1, f"entries on {len(entry_bars)} bars: the position was added to"
        assert len(exit_bars) <= 1, f"exits on {len(exit_bars)} bars: it was closed in stages"


def test_the_stop_is_placed_once_per_position_and_does_not_follow_the_price() -> None:
    """One stop per position, not one per bar. Under the old behaviour this would be ~60."""
    res = perp_backtest()
    assert res.stops_placed, "no stop was ever placed"
    assert len(res.stops_placed) <= len(excursions(res.fills))
    assert len(res.stops_placed) < 10, "one stop per position, not one per bar"


def test_the_stop_that_fires_is_the_one_placed_at_entry() -> None:
    """The behavioural form of the same rule, independent of what the log records.

    A drifting stop would exit near a *later* bar's close minus a *wider* distance. Pinning the
    exit to the level recorded at entry is what proves `assert_clearance` still means something
    once the position is open.
    """
    res = perp_backtest()
    runs = excursions(res.fills)
    closed = [r for r in runs if any(f.side == "SELL" for f in r)]
    assert closed, "no position was ever closed"
    for run, placement in zip(closed, res.stops_placed, strict=False):
        for f in run:
            if f.side == "SELL":
                # at or through the trigger placed at entry, never at a later drifted level
                assert f.price <= placement.trigger * 1.0001, (f.price, placement.trigger)
                assert f.from_stop, "the exit did not come from the protective stop"


def test_exactly_what_was_entered_is_what_leaves() -> None:
    """No partial residue. One order can fill in pieces, but the pieces must add up — a wallet
    left holding a remainder would keep paying funding on a position nothing tracks."""
    res = perp_backtest()
    for run in excursions(res.fills):
        if not any(f.side == "SELL" for f in run):
            continue  # still open at the end of the run
        entered = sum(f.qty for f in run if f.side == "BUY")
        exited = sum(f.qty for f in run if f.side == "SELL")
        assert exited == pytest.approx(entered)


def test_the_spot_path_still_rebalances() -> None:
    """The rule is perpetual-only. A spot campaign keeps the behaviour it was measured under,
    because changing it would silently re-define every legacy result."""
    bars = make_bars(60, seed=1, symbol="BTC/USDT")
    res = run_backtest(
        WideningStop(), {"BTC/USDT": bars}, risk=RiskSettings(max_risk_pct=0.01), lookback=20
    )
    assert res.stops_placed == (), "the spot path records no stop levels"
    assert res.fills


# ── the account is what sizes, and what reports equity (P3-19, decision ①) ─────────────


@dataclass
class OneShotLong(Strategy):
    """Enters long on the first bar it is allowed to and never asks to leave."""

    stop: float = 5.0

    def indicators(self, bars: Bars) -> Features:
        return {}

    def signal(self, x: FeatureView) -> Signal:
        return Signal("long", 1.0, self.stop)


def funded_bundle(bars: Bars, rate: float, *, mark: Bars | None = None) -> PerpBundle:
    """One settlement per bar at `rate`, charged on the bar's own mark close."""
    series = mark if mark is not None else bars
    rows = [
        [k, float(series.ts[k].astype("int64")), rate, float(series.close[k])]
        for k in range(len(series))
    ]
    base = bundle(series)
    return PerpBundle(
        symbol=base.symbol,
        timeframe=base.timeframe,
        marks=series,
        funding=np.array(rows, dtype=np.float64),
        paths=base.paths,
        brackets=base.brackets,
    )


def run(strategy: Strategy, bars: Bars, perp: PerpBundle, **kw: float) -> BacktestResult:
    return run_backtest(
        strategy,
        {bars.symbol: bars},
        risk=RiskSettings(max_risk_pct=0.01),
        lookback=20,
        perp={bars.symbol: perp},
        **kw,  # type: ignore[arg-type]
    )


def test_funding_shrinks_the_next_entry_and_leaves_the_open_one_alone() -> None:
    """Decision ①. `RiskSizer` already takes equity as an argument; the bug was that the equity
    came from the venue's account, which has zero margin and never sees a funding payment. So a
    position sized after a week of funding risked more than 1% of what the account actually had.

    Measured as a difference, not an absolute: the same run with and without funding, where only
    the funding differs, so nothing else can explain a change in size.
    """
    bars = make_bars(60, seed=3, symbol=SYMBOL)
    free = run(WideningStop(), bars, funded_bundle(bars, 0.0))
    paid = run(WideningStop(), bars, funded_bundle(bars, 0.01))  # 1% of notional, every bar

    entries_free = [f.qty for f in sorted(free.fills, key=lambda f: f.ts) if f.side == "BUY"]
    entries_paid = [f.qty for f in sorted(paid.fills, key=lambda f: f.ts) if f.side == "BUY"]
    assert len(entries_free) > 2 and len(entries_paid) > 2, "the run needs several entries"

    assert paid.funding_paid > 0 and free.funding_paid == 0
    assert entries_paid[0] == pytest.approx(entries_free[0]), "the first entry precedes any funding"
    # Every later entry is sized from an account that has paid funding, so none can be larger.
    # Not every one is strictly smaller: `round_to_lot` floors, so a small payment can leave the
    # quantity where it was — which is why the claim is "never larger, and sometimes smaller".
    pairs = list(zip(entries_paid[1:], entries_free[1:], strict=False))
    assert all(p <= f * (1 + 1e-9) for p, f in pairs), pairs
    assert any(p < f for p, f in pairs), "funding never reached the sizer"


def test_the_reported_equity_is_the_account_not_a_cash_reconstruction() -> None:
    """Funding leaves the account, so an equity curve that ignores it is a different number.
    Gate ③ measures Sharpe on this curve, which is why it has to be the account's."""
    bars = make_bars(40, seed=4, symbol=SYMBOL)
    free = run(OneShotLong(), bars, funded_bundle(bars, 0.0))
    paid = run(OneShotLong(), bars, funded_bundle(bars, 0.005))
    assert paid.funding_paid > 0
    assert paid.equity[-1] < free.equity[-1]
    assert paid.equity[-1] == pytest.approx(free.equity[-1] - paid.funding_paid, rel=0.05)


def test_a_negative_rate_pays_a_long_rather_than_charging_it() -> None:
    bars = make_bars(40, seed=5, symbol=SYMBOL)
    paid = run(OneShotLong(), bars, funded_bundle(bars, -0.005))
    assert paid.funding_paid < 0
    assert np.all(np.isfinite(paid.equity))


def test_a_liquidation_ends_the_configuration_instead_of_diverging() -> None:
    """Decision ⑤. After a liquidation the host account holds nothing and the venue still holds
    a position, so every later fill describes a book the equity curve does not have. The run
    stops trading that contract and says where — it does not raise, because gate ④ runs 200
    configurations in one job and one of them must not kill the other 199.
    """
    bars = make_bars(60, seed=6, symbol=SYMBOL, vol=0.02)
    crash = bars.close.copy()
    crash[30:] *= 0.4  # the mark collapses well past any 100x wallet
    marks = Bars(SYMBOL, "1d", bars.ts, crash, crash, crash, crash, np.zeros(len(bars)))
    res = run(OneShotLong(), bars, funded_bundle(bars, 0.0, mark=marks), leverage=50)

    assert res.terminated_at is not None, "the liquidation was not recorded"
    assert res.liquidated == (SYMBOL,)
    after = res.equity[res.ts >= res.terminated_at]
    assert np.all(after == pytest.approx(after[0])), "equity moved after the account was wiped"
    assert np.all(np.isfinite(res.returns))


def test_a_wiped_account_produces_no_inf_or_nan() -> None:
    """Equity is floored at zero by isolation, so a naive ratio would divide by it."""
    bars = make_bars(60, seed=7, symbol=SYMBOL, vol=0.02)
    crash = bars.close.copy()
    crash[20:] *= 0.05
    marks = Bars(SYMBOL, "1d", bars.ts, crash, crash, crash, crash, np.zeros(len(bars)))
    res = run(OneShotLong(), bars, funded_bundle(bars, 0.0, mark=marks), leverage=100)
    assert np.all(np.isfinite(res.equity))
    assert np.all(np.isfinite(res.returns))
    assert np.isfinite(res.sharpe)
