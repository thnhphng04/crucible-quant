"""The daily evaluation clock (P3-60, ADR-0049, INV-124): hand-computed values only."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from quantcrucible.execution.engine import BacktestResult
from quantcrucible.validation.clock import (
    DAILY_V1,
    annual_sharpe,
    annual_sortino,
    clock_ppy,
    daily_returns,
    evaluation_clock,
    on_clock,
)


def _hourly(start: str, values: list[float]) -> pd.Series:
    """Returns indexed by bar CLOSE time: the first closes one hour after ``start``."""
    idx = pd.date_range(pd.Timestamp(start) + pd.Timedelta(hours=1), periods=len(values), freq="h")
    return pd.Series(values, index=idx, dtype=np.float64)


def test_two_days_of_hourly_bars_compound_into_two_daily_returns() -> None:
    # day 1: 24 bars of +1%; day 2: 12 bars of +2% then 12 of -1%
    r = _hourly("2024-01-01", [0.01] * 24 + [0.02] * 12 + [-0.01] * 12)
    out = daily_returns(r)
    assert list(out.index) == [pd.Timestamp("2024-01-02"), pd.Timestamp("2024-01-03")]
    assert out.iloc[0] == pytest.approx(1.01**24 - 1, rel=1e-12)
    assert out.iloc[1] == pytest.approx(1.02**12 * 0.99**12 - 1, rel=1e-12)


def test_a_bar_closing_at_midnight_belongs_to_the_day_before() -> None:
    idx = pd.to_datetime(["2024-01-01 23:00", "2024-01-02 00:00", "2024-01-02 01:00"])
    out = daily_returns(pd.Series([0.1, 0.2, 0.3], index=idx))
    assert list(out.index) == [pd.Timestamp("2024-01-02"), pd.Timestamp("2024-01-03")]
    assert out.iloc[0] == pytest.approx(1.1 * 1.2 - 1, rel=1e-12)
    assert out.iloc[1] == 0.3  # a one-bar day passes through untouched


def test_a_daily_series_is_returned_bit_for_bit() -> None:
    rng = np.random.default_rng(7)
    idx = pd.date_range("2023-01-02", periods=400, freq="D")  # 1d bars close at midnight
    r = pd.Series(rng.normal(0.0, 0.03, len(idx)), index=idx)
    out = daily_returns(r)
    assert np.array_equal(out.index.to_numpy(), r.index.to_numpy())
    assert np.array_equal(out.to_numpy(), r.to_numpy())  # no (1 + r) - 1 round trip


def test_partial_days_compound_what_they_have() -> None:
    idx = pd.to_datetime(["2024-01-01 22:00", "2024-01-01 23:00", "2024-01-03 05:00"])
    out = daily_returns(pd.Series([0.5, -0.5, 0.25], index=idx))
    assert list(out.index) == [pd.Timestamp("2024-01-02"), pd.Timestamp("2024-01-04")]
    assert out.iloc[0] == pytest.approx(1.5 * 0.5 - 1, rel=1e-12)
    assert out.iloc[1] == 0.25


def test_a_wiped_account_loses_the_whole_day() -> None:
    out = daily_returns(_hourly("2024-01-01", [0.1, -1.0, 0.0, 0.0]))
    assert out.iloc[0] == -1.0


def test_an_empty_series_stays_empty() -> None:
    out = daily_returns(pd.Series([], index=pd.DatetimeIndex([]), dtype=np.float64))
    assert out.empty


def test_unsorted_timestamps_are_refused() -> None:
    idx = pd.to_datetime(["2024-01-02", "2024-01-01"])
    with pytest.raises(ValueError, match="increasing"):
        daily_returns(pd.Series([0.1, 0.2], index=idx))


def test_the_lock_tag() -> None:
    assert evaluation_clock({}) is None
    assert evaluation_clock({"derived": {}}) is None
    assert evaluation_clock({"derived": {"evaluation_clock": "daily_v1"}}) == DAILY_V1
    with pytest.raises(ValueError, match="evaluation clock"):
        evaluation_clock({"derived": {"evaluation_clock": "weekly"}})


def test_on_clock_and_its_periods_per_year() -> None:
    r = _hourly("2024-01-01", [0.01] * 48)
    same, ppy = on_clock(r, 8760.0, None)
    assert same is r and ppy == 8760.0
    daily, ppy = on_clock(r, 8760.0, DAILY_V1)
    assert len(daily) == 2 and ppy == 365.0
    assert clock_ppy("1h", None) == 8760.0
    assert clock_ppy("1h", DAILY_V1) == 365.0
    assert clock_ppy("1d", None) == 365.0


def test_annual_sharpe_by_hand() -> None:
    r = pd.Series([0.01, -0.02, 0.03, 0.0])
    mean, sd = 0.005, math.sqrt((0.005**2 + 0.025**2 + 0.025**2 + 0.005**2) / 3)
    assert annual_sharpe(r, 365.0) == pytest.approx(mean / sd * math.sqrt(365), rel=1e-12)
    assert annual_sharpe(pd.Series([0.01, 0.01]), 365.0) == 0.0
    assert annual_sharpe(pd.Series([0.01]), 365.0) == 0.0


def test_annual_sortino_by_hand() -> None:
    r = pd.Series([0.01, -0.02, 0.03, 0.0])
    downside = math.sqrt(0.02**2 / 4)
    assert annual_sortino(r, 365.0) == pytest.approx(0.005 / downside * math.sqrt(365), rel=1e-12)
    assert annual_sortino(pd.Series([0.01, 0.02]), 365.0) == 0.0


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_the_same_numbers_as_the_backtest_result(seed: int) -> None:
    rng = np.random.default_rng(seed)
    r = rng.normal(0.0005, 0.02, 500)
    equity = np.concatenate([[1.0], np.cumprod(1.0 + r)])
    ts = pd.date_range("2022-01-01", periods=501, freq="D").to_numpy()
    res = BacktestResult(ts=ts, equity=equity, returns=r, fills=(), n_trades=0,
                         avg_holding_bars=0.0, turnover=0.0, signals={}, denied_orders=0,
                         periods_per_year=365.0)  # fmt: skip
    assert annual_sharpe(pd.Series(r), 365.0) == res.sharpe
    assert annual_sortino(pd.Series(r), 365.0) == res.sortino
