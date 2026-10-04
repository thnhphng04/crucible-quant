"""Assembling perpetual campaign inputs from source + minute cache (P3-24)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from quantcrucible.core.strategy.base import FLAT, Bars, Features, FeatureView, Signal, Strategy
from quantcrucible.data.perp_pipeline import normalize_window, prepare_perpetual
from quantcrucible.data.perp_source import CoverageError, PerpSource
from quantcrucible.execution.engine import run_backtest
from quantcrucible.execution.exit_policy import ExitPolicy
from quantcrucible.execution.joint_account import SlotPlan, replay_signals
from quantcrucible.execution.nautilus_bridge import CostModel
from quantcrucible.execution.risk import RiskSettings

SYMBOL = "BTC/USDT:USDT"
START = datetime(2021, 1, 1, tzinfo=UTC)
MINUTE_MS = 60_000


class AssemblyExchange:
    def __init__(
        self,
        *,
        funding_offsets_hours: tuple[int, ...] = (0, 8, 16, 24, 32, 40),
        tiers: list[dict[str, Any]] | None = None,
        gap_minute: int | None = None,
        mark_gap: range = range(0),
        stamp_offset_ms: int = 0,
    ) -> None:
        self.mark_gap = mark_gap
        self.stamp_offset_ms = stamp_offset_ms
        self.funding_offsets_hours = funding_offsets_hours
        self.tiers = (
            tiers
            if tiers is not None
            else [
                {
                    "maxNotional": 50_000,
                    "maintenanceMarginRate": 0.004,
                    "maxLeverage": 125,
                    "info": {"cum": "0"},
                }
            ]
        )
        self.gap_minute = gap_minute

    def fetch_ohlcv(self, symbol: str, timeframe: str, since: int, limit: int) -> list[list[float]]:
        return self._ohlcv(timeframe, since, limit, kind="trade")

    def fetch_mark_ohlcv(
        self, symbol: str, timeframe: str, since: int, limit: int
    ) -> list[list[float]]:
        return self._ohlcv(timeframe, since, limit, kind="mark")

    def fetch_funding_rate_history(
        self, symbol: str, since: int, limit: int
    ) -> list[dict[str, Any]]:
        base = int(START.timestamp() * 1000)
        rows = []
        for h in self.funding_offsets_hours:
            ts = base + h * 60 * MINUTE_MS + self.stamp_offset_ms
            if ts >= since:
                rows.append({"timestamp": ts, "fundingRate": 0.0001 + h / 1_000_000})
        return rows[:limit]

    def fetch_market_leverage_tiers(self, symbol: str) -> list[dict[str, Any]]:
        return self.tiers

    def _ohlcv(
        self,
        timeframe: str,
        since: int,
        limit: int,
        *,
        kind: str,
    ) -> list[list[float]]:
        step = self._step_ms(timeframe)
        base = int(START.timestamp() * 1000)
        first = max(0, -(-(since - base) // step))
        rows: list[list[float]] = []
        i = first
        while len(rows) < limit:
            if timeframe == "1m" and (
                (self.gap_minute is not None and i == self.gap_minute)
                or (kind == "mark" and i in self.mark_gap)
            ):
                i += 1
                continue
            ts = base + i * step
            price = 100.0 + i * 0.01 + (0.5 if kind == "mark" else 0.0)
            rows.append([ts, price, price + 0.2, price - 0.2, price + 0.05, 1_000.0])
            i += 1
        return rows

    @staticmethod
    def _step_ms(timeframe: str) -> int:
        unit = timeframe[-1]
        n = int(timeframe[:-1])
        if unit == "m":
            return n * MINUTE_MS
        if unit == "h":
            return n * 60 * MINUTE_MS
        return n * 24 * 60 * MINUTE_MS


def source(exchange: AssemblyExchange) -> PerpSource:
    return PerpSource(exchange_id="binanceusdm", exchange=exchange)


def test_prepare_returns_carve_ready_aligned_data(tmp_path: Path) -> None:
    prep = prepare_perpetual(
        source(AssemblyExchange(funding_offsets_hours=(0, 7, 16, 24, 31, 40))),
        tmp_path,
        [SYMBOL],
        "1d",
        START,
        START + timedelta(days=2),
    )

    trades, bundle = prep.data[SYMBOL]
    bundle.aligned_with(trades)
    assert prep.coverage[SYMBOL].bars == 2
    assert prep.coverage[SYMBOL].minute_rows == 2_880
    assert len(bundle.funding) == 5  # the start-boundary event has no prior minute
    assert bundle.paths[0].starts == (0, 420, 960)
    assert bundle.brackets == prep.brackets[SYMBOL]
    assert bundle.funding[0, 3] == pytest.approx(100.5 + 419 * 0.01 + 0.05)
    assert bundle.funding[1, 3] > trades.close[0], "mark_at_settlement comes from mark minutes"


@pytest.mark.parametrize("timeframe,expected", [("1h", 24), ("15m", 96)])
def test_intraday_windows_keep_trade_and_mark_paths(
    tmp_path: Path, timeframe: str, expected: int
) -> None:
    prep = prepare_perpetual(
        source(AssemblyExchange(funding_offsets_hours=(0, 8, 16, 24, 32))),
        tmp_path,
        [SYMBOL],
        timeframe,
        START,
        START + timedelta(days=1),
    )
    trades, bundle = prep.data[SYMBOL]
    assert len(trades) == expected
    assert bundle.trade_paths is not None and len(bundle.trade_paths) == expected
    assert bundle.trade_paths[0].segments[0].highs != bundle.paths[0].segments[0].highs


@pytest.mark.parametrize("timeframe", ["1h", "15m"])
def test_prepared_intraday_paths_replay_fixed_bracket(tmp_path: Path, timeframe: str) -> None:
    prep = prepare_perpetual(
        source(AssemblyExchange(funding_offsets_hours=(0, 8, 16, 24, 32))),
        tmp_path,
        [SYMBOL],
        timeframe,
        START,
        START + timedelta(days=1),
    )
    bars, bundle = prep.data[SYMBOL]

    class FirstBar(Strategy):
        def indicators(self, data: Bars) -> Features:
            return {"index": np.arange(len(data), dtype=float)}

        def signal(self, x: FeatureView) -> Signal:
            return Signal("long", 1.0, 10.0, 11.0) if float(x["index"]) == 0 else FLAT

    policy = ExitPolicy("bracket_timeout_v1", 1.1, 100)
    costs = CostModel(fee_rate=0.0, slippage_bps=0.0)
    standalone = run_backtest(
        FirstBar(), {SYMBOL: bars}, costs=costs, perp={SYMBOL: bundle}, exit_policy=policy
    )
    replay = replay_signals(
        [SlotPlan(SYMBOL, "long", (Signal("long", 1.0, 10.0, 11.0),) + (FLAT,) * (len(bars) - 1))],
        {SYMBOL: bars},
        {SYMBOL: bundle},
        100_000.0,
        RiskSettings(),
        costs,
        exit_policy=policy,
    )
    assert standalone.exits and standalone.exits[0].reason == "take_profit"
    np.testing.assert_allclose(standalone.equity, replay.equity)


def test_15m_window_requires_a_quarter_hour_boundary() -> None:
    start = START + timedelta(minutes=15)
    assert normalize_window("15m", start, start + timedelta(minutes=45)) == (
        start,
        start + timedelta(minutes=45),
    )
    with pytest.raises(ValueError, match="UTC grid"):
        normalize_window("15m", start + timedelta(minutes=1), start + timedelta(minutes=46))


def test_prepare_4h_fetches_enough_tail_for_funding_preflight(tmp_path: Path) -> None:
    prep = prepare_perpetual(
        source(AssemblyExchange(funding_offsets_hours=(0, 8, 16, 24))),
        tmp_path,
        [SYMBOL],
        "4h",
        START + timedelta(hours=4),
        START + timedelta(hours=20),
    )

    trades, bundle = prep.data[SYMBOL]
    assert len(trades) == 4
    assert len(bundle.funding) == 2
    assert bundle.funding[:, 0].tolist() == [1.0, 3.0]


def test_prepare_refuses_non_grid_windows(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="midnight UTC"):
        prepare_perpetual(
            source(AssemblyExchange()),
            tmp_path,
            [SYMBOL],
            "1d",
            START + timedelta(hours=1),
            START + timedelta(days=2, hours=1),
        )


def test_prepare_refuses_minute_gaps(tmp_path: Path) -> None:
    with pytest.raises(CoverageError, match="minute"):
        prepare_perpetual(
            source(AssemblyExchange(gap_minute=77)),
            tmp_path,
            [SYMBOL],
            "1d",
            START,
            START + timedelta(days=1),
        )


def test_a_short_mark_gap_is_filled_and_reported_in_the_coverage(tmp_path: Path) -> None:
    """ADR-0042: the 24-minute hole every contract has on 2020-12-17 must not block the fetch;
    the filled minutes travel with the coverage so the CLI can record them in the manifest."""
    prep = prepare_perpetual(
        source(AssemblyExchange(mark_gap=range(452, 476))),
        tmp_path,
        [SYMBOL],
        "1h",
        START,
        START + timedelta(days=1),
    )
    base = int(START.timestamp() * 1000)
    assert prep.coverage[SYMBOL].mark_fills == tuple(base + i * MINUTE_MS for i in range(452, 476))
    assert prep.coverage[SYMBOL].minute_rows == 1_440
    trades, bundle = prep.data[SYMBOL]
    bundle.aligned_with(trades)


@pytest.mark.parametrize("timeframe", ["1d", "1h"])
def test_settlements_stamped_milliseconds_late_assemble_like_exact_ones(
    tmp_path: Path, timeframe: str
) -> None:
    """The real venue stamps settlements a few ms after the boundary; the first real fetch
    stopped on it. A late stamp lands in the same bar and cut, with the mark of the last
    completed minute before it — exactly what an on-the-minute stamp gives."""

    def prepared(offset: int, folder: str) -> Any:
        return prepare_perpetual(
            source(AssemblyExchange(stamp_offset_ms=offset)),
            tmp_path / folder,
            [SYMBOL],
            timeframe,
            START,
            START + timedelta(days=1),
        ).data[SYMBOL][1]

    exact, late = prepared(0, "exact"), prepared(5, "late")
    assert late.funding[:, [0, 2, 3]].tolist() == exact.funding[:, [0, 2, 3]].tolist()
    assert [p.starts for p in late.paths] == [p.starts for p in exact.paths]
    assert [p.starts for p in late.trade_paths] == [p.starts for p in exact.trade_paths]


def test_prepare_refuses_missing_brackets(tmp_path: Path) -> None:
    with pytest.raises(CoverageError, match="leverage bracket"):
        prepare_perpetual(
            source(AssemblyExchange(tiers=[])),
            tmp_path,
            [SYMBOL],
            "1d",
            START,
            START + timedelta(days=1),
        )
