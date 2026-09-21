"""ccxt source (Architecture §6.1): pagination, close-time index, no forming bar."""

from datetime import UTC, datetime

import numpy as np
import pytest

from quantcrucible.data.ccxt_source import CcxtSource
from quantcrucible.data.source import timeframe_delta
from tests.data.conftest import FakeExchange, utc


def test_paginates_and_indexes_by_close_time(fake_exchange: FakeExchange) -> None:
    src = CcxtSource(exchange=fake_exchange)
    bars = src.bars("BTC/USDT", "1d", utc(2020, 1, 2), utc(2024, 1, 1), now=utc(2030, 1, 1))
    assert fake_exchange.calls > 1  # > 1000 daily bars ⇒ several pages
    assert bars.ts[0] == np.datetime64("2020-01-02T00:00", "ns")  # open 01-01 closes 01-02
    assert bars.ts[-1] == np.datetime64("2024-01-01T00:00", "ns")
    assert bars.close[0] == 100.5
    assert np.all(np.diff(bars.ts) == np.timedelta64(1, "D"))


def test_forming_bar_never_returned(fake_exchange: FakeExchange) -> None:
    src = CcxtSource(exchange=fake_exchange)
    now = utc(2020, 3, 1, 15)  # the 2020-03-01 bar is still forming
    bars = src.bars("BTC/USDT", "1d", utc(2020, 2, 1), utc(2030, 1, 1), now=now)
    assert bars.ts[-1] == np.datetime64("2020-03-01T00:00", "ns")  # the bar that CLOSED at 00:00


def test_timeframes() -> None:
    assert timeframe_delta("4h").total_seconds() == 4 * 3600
    with pytest.raises(ValueError):
        timeframe_delta("1w")


@pytest.mark.network
def test_binance_download() -> None:
    now = datetime.now(UTC)
    bars = CcxtSource("binance").bars("BTC/USDT", "1d", utc(2024, 1, 1), utc(2024, 1, 31), now=now)
    assert len(bars) == 31
