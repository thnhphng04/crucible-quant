"""Timeframe is a real knob (P3-14, D18).

`research.data.timeframe` has always been configurable, and P3-01 confirmed it does **not**
touch gate ②: MinBTL is calendar-based and `span_years` divides by days, so 4h bars give six
times the observations over the same window and the same MinBTL.

Where it does bite is everywhere that counts **bars** rather than time. Three such places were
found by audit and are pinned here, because each fails silently:

  * `Member.from_dict` defaulted a missing timeframe to "1d", which would annualise a 4h member
    as if it were daily — a factor of six in every Sharpe downstream.
  * `DEFAULT_LOOKBACK = 400` is bars, so the warm-up window is 400 days at 1d and 66 at 4h.
  * D15's `min_trades` / `min_holding_bars` are bar-denominated, so the same numbers mean
    different things per timeframe.
"""

from __future__ import annotations

import pytest

from quantcrucible.data.source import timeframe_delta
from quantcrucible.validation.pbo_gate import periods_per_year
from quantcrucible.validation.portfolio import Member


def test_periods_per_year_is_derived_not_hardcoded() -> None:
    assert periods_per_year("1d") == pytest.approx(365.0)
    assert periods_per_year("4h") == pytest.approx(365.0 * 6)
    assert periods_per_year("1h") == pytest.approx(365.0 * 24)


def test_a_member_without_a_timeframe_is_refused_not_defaulted() -> None:
    """Defaulting to 1d would annualise a 4h member six times too small, and nothing downstream
    would notice: the Sharpe would simply be wrong."""
    d = {
        "trial_id": 1,
        "candidate_id": "c",
        "strategy_hash": "h",
        "params": {},
        "weight": 1.0,
        "universe": ["BTCUSDT"],
    }
    with pytest.raises(KeyError, match="timeframe"):
        Member.from_dict(d)


def test_a_member_round_trips_its_timeframe() -> None:
    m = Member(1, "c", "h", {}, 1.0, ("BTCUSDT",), "4h")
    assert Member.from_dict(m.as_dict()).timeframe == "4h"


@pytest.mark.parametrize("timeframe", ["1d", "4h", "8h", "1h"])
def test_every_configured_timeframe_is_understood(timeframe: str) -> None:
    """Funding lands every 8h on Binance, so 8h must work as well as the signal timeframes."""
    assert timeframe_delta(timeframe).total_seconds() > 0
    assert periods_per_year(timeframe) > 0


def test_funding_events_per_bar_follows_the_timeframe() -> None:
    """At 1d three funding events fall inside one bar; at 4h one falls every second bar; at 8h
    exactly one per bar. A replay that assumed any single one of these would be wrong at the
    other two."""
    eight_hours = 8 * 3600
    for timeframe, expected in (("1d", 3.0), ("8h", 1.0), ("4h", 0.5)):
        per_bar = timeframe_delta(timeframe).total_seconds() / eight_hours
        assert per_bar == pytest.approx(expected)


def test_an_unsupported_timeframe_is_refused() -> None:
    with pytest.raises(ValueError, match="unsupported timeframe"):
        timeframe_delta("1w")
