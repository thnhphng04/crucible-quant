from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

DAY_MS = 86_400_000


class FakeExchange:
    """Daily OHLCV from 2020-01-01 (open times), served in pages like ccxt."""

    def __init__(self, days: int = 3000, start: datetime = datetime(2020, 1, 1, tzinfo=UTC)):
        self.t0 = int(start.timestamp() * 1000)
        self.rows = [
            [self.t0 + i * DAY_MS, 100.0 + i, 101.0 + i, 99.0 + i, 100.5 + i, 10.0 + i]
            for i in range(days)
        ]
        self.calls = 0

    def fetch_ohlcv(
        self, symbol: str, timeframe: str, since: int | None = None, limit: int | None = None
    ) -> list[list[float]]:
        self.calls += 1
        since = since or self.t0
        page = [r for r in self.rows if r[0] >= since]
        return [list(map(float, r)) for r in page[: limit or 1000]]


@pytest.fixture
def fake_exchange() -> FakeExchange:
    return FakeExchange()


def utc(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=UTC)


ONE_DAY = timedelta(days=1)
