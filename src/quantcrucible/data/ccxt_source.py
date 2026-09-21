"""Crypto OHLCV through ccxt (Architecture §6.1): free, and venue-exact for crypto."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

import numpy as np

from quantcrucible.core.strategy.base import Bars
from quantcrucible.data.source import timeframe_delta

logger = logging.getLogger(__name__)

PAGE_LIMIT = 1000


class OhlcvExchange(Protocol):
    """The slice of a ccxt exchange this source uses (lets tests inject a fake)."""

    def fetch_ohlcv(
        self, symbol: str, timeframe: str, since: int | None = None, limit: int | None = None
    ) -> list[list[float]]: ...


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


class CcxtSource:
    def __init__(self, exchange_id: str = "binance", exchange: OhlcvExchange | None = None) -> None:
        if exchange is None:
            import ccxt  # imported lazily: only real downloads need it

            exchange = getattr(ccxt, exchange_id)({"enableRateLimit": True})
        self.exchange: Any = exchange
        self.exchange_id = exchange_id

    def is_continuous(self) -> bool:
        return True  # spot has no expiry

    def adjustment(self) -> Literal["none", "ratio", "difference"]:
        return "none"

    def bars(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        now: datetime | None = None,
    ) -> Bars:
        """Closed bars with CLOSE time in [start, end]. The still-forming bar is never returned.

        ccxt timestamps are bar OPEN times; Bars are indexed by CLOSE time (= open + timeframe).
        """
        step = timeframe_delta(timeframe)
        now = now or datetime.now(UTC)
        limit_close = min(end, now)
        rows: dict[int, list[float]] = {}
        since = _ms(start - step)
        while True:
            page = self.exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=PAGE_LIMIT)
            if not page:
                break
            for row in page:
                rows[int(row[0])] = [float(v) for v in row[1:6]]
            last_open = int(page[-1][0])
            if last_open + _ms_delta(step) > _ms(limit_close) or len(page) < 2:
                break
            if last_open < since:  # exchange ignored `since` — avoid an infinite loop
                break
            since = last_open + 1
        opens = np.array(sorted(rows), dtype=np.int64)
        closes_ms = opens + _ms_delta(step)
        keep = (closes_ms >= _ms(start)) & (closes_ms <= _ms(limit_close))
        opens, closes_ms = opens[keep], closes_ms[keep]
        values = np.array([rows[int(o)] for o in opens], dtype=np.float64).reshape(-1, 5)
        ts = closes_ms.astype("datetime64[ms]").astype("datetime64[ns]")
        logger.info("%s %s %s: %d closed bars", self.exchange_id, symbol, timeframe, len(ts))
        return Bars(
            symbol, timeframe, ts,
            values[:, 0], values[:, 1], values[:, 2], values[:, 3], values[:, 4],
        )  # fmt: skip


def _ms_delta(step: Any) -> int:
    return int(step.total_seconds() * 1000)
