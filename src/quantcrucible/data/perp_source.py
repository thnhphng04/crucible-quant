"""Binance USDT-M perpetual data (Architecture §6.1, D18, P3-09).

Three series, not one. A perpetual backtest needs the **trade** price it fills at, the **mark**
price it is liquidated on, and the **funding** it pays or receives — and the last two cannot be
reconstructed from the first. Missing either is refused rather than filled with a default,
because a zero funding rate is a claim ("funding was free that week"), not an absence.

``ccxt`` is already a dependency and ``binanceusdm`` declares everything needed: ``fetchOHLCV``,
``fetchMarkOHLCV``, ``fetchFundingRateHistory``. Leverage brackets come from ``fetchLeverageTiers``,
which is a **signed** endpoint, so they are not fetched here — a campaign locks a snapshot and
records it as an assumption (ADR-0032).

The probe in ``scripts/perp_coverage.py`` measured what the venue actually serves: the binding
listing is SOLUSDT at 2020-09-14, which is what :func:`common_window` computes for a real set.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol

import numpy as np

from quantcrucible.core.strategy.base import Bars
from quantcrucible.data.source import timeframe_delta

PAGE_LIMIT = 1000


class CoverageError(RuntimeError):
    """A required series is missing, short, or does not match its manifest."""


class PerpExchange(Protocol):
    """The slice of ccxt this module uses (and that a test can fake)."""

    def fetch_ohlcv(
        self, symbol: str, timeframe: str, since: int, limit: int
    ) -> list[list[float]]: ...

    def fetch_mark_ohlcv(
        self, symbol: str, timeframe: str, since: int, limit: int
    ) -> list[list[float]]: ...

    def fetch_funding_rate_history(
        self, symbol: str, since: int, limit: int
    ) -> list[dict[str, Any]]: ...


@dataclass(frozen=True, slots=True)
class PerpData:
    """One instrument's three series over one window."""

    symbol: str
    trades: Bars
    marks: Bars
    funding: np.ndarray  # (timestamp_ns, rate) pairs, one row per funding event


def _is_linear_perp(symbol: str) -> bool:
    """ccxt spells a linear USDT-margined perpetual ``BASE/QUOTE:SETTLE``."""
    return ":" in symbol and symbol.split(":")[-1] == symbol.split("/")[1].split(":")[0]


def _bars(rows: list[list[float]], symbol: str, timeframe: str) -> Bars:
    """ccxt gives bar OPEN times; Bars are indexed by CLOSE time (the project convention)."""
    period = timeframe_delta(timeframe)
    ts = np.array(
        [np.datetime64(int(r[0]) + int(period.total_seconds() * 1000), "ms") for r in rows],
        dtype="datetime64[ns]",
    )
    arr = np.array(rows, dtype=np.float64)
    return Bars(symbol, timeframe, ts, arr[:, 1], arr[:, 2], arr[:, 3], arr[:, 4], arr[:, 5])


@dataclass(frozen=True, slots=True)
class PerpSource:
    exchange_id: str = "binanceusdm"
    exchange: PerpExchange | None = None

    def _venue(self) -> PerpExchange:
        if self.exchange is not None:
            return self.exchange
        import ccxt  # imported lazily: only real downloads need it

        return getattr(ccxt, self.exchange_id)({"enableRateLimit": True})  # type: ignore[no-any-return]

    def is_continuous(self) -> bool:
        return True  # a perpetual never expires, so there is no roll

    def adjustment(self) -> Literal["none", "ratio", "difference"]:
        return "none"

    def fetch(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> PerpData:
        if not _is_linear_perp(symbol):
            raise ValueError(
                f"{symbol!r} is not a linear perpetual: expected BASE/QUOTE:SETTLE, e.g. "
                "'BTC/USDT:USDT'"
            )
        venue = self._venue()
        since = int(start.timestamp() * 1000)
        trade_rows = venue.fetch_ohlcv(symbol, timeframe, since, PAGE_LIMIT)
        if not trade_rows:
            raise CoverageError(f"{symbol}: no trade bars in {start:%Y-%m-%d}/{end:%Y-%m-%d}")
        mark_rows = venue.fetch_mark_ohlcv(symbol, timeframe, since, PAGE_LIMIT)
        if not mark_rows:
            raise CoverageError(f"{symbol}: no mark bars — liquidation cannot be resolved")
        funding_rows = venue.fetch_funding_rate_history(symbol, since, PAGE_LIMIT)
        self._check_funding(symbol, funding_rows, trade_rows)
        funding = np.array(
            [[float(r["timestamp"]) * 1e6, float(r["fundingRate"])] for r in funding_rows],
            dtype=np.float64,
        )
        return PerpData(
            symbol=symbol,
            trades=_bars(trade_rows, symbol, timeframe),
            marks=_bars(mark_rows, symbol, timeframe),
            funding=funding,
        )

    @staticmethod
    def _check_funding(
        symbol: str, funding_rows: list[dict[str, Any]], trade_rows: list[list[float]]
    ) -> None:
        """Funding must span the trade window. A short series is refused, never zero-filled:
        a zero rate is a claim about the market, not an absence of data (INV-94)."""
        if not funding_rows:
            raise CoverageError(f"{symbol}: no funding history")
        last_trade_ms = int(trade_rows[-1][0])
        last_funding_ms = int(funding_rows[-1]["timestamp"])
        first_trade_ms = int(trade_rows[0][0])
        first_funding_ms = int(funding_rows[0]["timestamp"])
        if first_funding_ms > first_trade_ms or last_funding_ms < last_trade_ms:
            raise CoverageError(
                f"{symbol}: funding history covers "
                f"{_day(first_funding_ms)}/{_day(last_funding_ms)} but the trade bars run "
                f"{_day(first_trade_ms)}/{_day(last_trade_ms)} — refusing rather than filling"
            )


def _day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).date().isoformat()


def common_window(
    windows: Mapping[str, tuple[datetime, datetime]],
) -> tuple[datetime, datetime]:
    """The window every instrument covers: the latest start and the earliest end.

    The binding listing sets the in-sample start for the whole campaign, which is why one late
    contract shortens everyone's history — SOLUSDT at 2020-09-14 cost the set 2.7 years against
    the spot window (P3-01, ADR-0031).
    """
    if not windows:
        raise CoverageError("no instruments to find a common window for")
    start = max(w[0] for w in windows.values())
    end = min(w[1] for w in windows.values())
    if start >= end - timedelta(days=1):
        raise CoverageError(
            f"no common window: the latest start {start:%Y-%m-%d} is at or after the earliest "
            f"end {end:%Y-%m-%d}"
        )
    return start, end
