"""Assembling perpetual campaign inputs from source + minute cache (P3-24)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from quantcrucible.data.perp_pipeline import prepare_perpetual
from quantcrucible.data.perp_source import CoverageError, PerpSource

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
    ) -> None:
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
            ts = base + h * 60 * MINUTE_MS
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
            if timeframe == "1m" and self.gap_minute is not None and i == self.gap_minute:
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
