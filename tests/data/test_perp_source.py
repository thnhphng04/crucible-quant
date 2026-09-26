"""Perpetual data: trade bars, mark bars, funding, with a manifest (P3-09) — INV-94.

The spot in-sample parquet has no manifest and no checksum at all; the only integrity record in
the project is `holdout.lock`. Perpetual data must not repeat that, because it carries two series
the backtest cannot reconstruct from price — mark prices and funding — and a silently missing
window in either would change every result without changing any error message.

So: missing funding fails closed and is never zero-filled, coverage is recorded rather than
assumed, and a checksum mismatch refuses the file. Every test uses a fake exchange and `tmp_path`
— none touches the network or the real data directory.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from quantcrucible.data.manifest import Manifest, verify_manifest, write_manifest
from quantcrucible.data.perp_source import PAGE_LIMIT, CoverageError, PerpSource

START = datetime(2021, 1, 1, tzinfo=UTC)
END = datetime(2021, 1, 11, tzinfo=UTC)
DAY_MS = 86_400_000


class FakeExchange:
    """Serves whatever it is given, in ccxt's shapes."""

    def __init__(self, days: int = 10, funding_days: int | None = None, mark: bool = True) -> None:
        self.days = days
        self.funding_days = days if funding_days is None else funding_days
        self._mark = mark

    def _ohlcv(self, n: int) -> list[list[float]]:
        base = int(START.timestamp() * 1000)
        return [[base + i * DAY_MS, 100.0, 101.0, 99.0, 100.5, 1_000.0] for i in range(n)]

    def fetch_ohlcv(self, symbol: str, timeframe: str, since: int, limit: int) -> list[list[float]]:
        return self._ohlcv(self.days)

    def fetch_mark_ohlcv(
        self, symbol: str, timeframe: str, since: int, limit: int
    ) -> list[list[float]]:
        if not self._mark:
            return []
        return self._ohlcv(self.days)

    def fetch_funding_rate_history(
        self, symbol: str, since: int, limit: int
    ) -> list[dict[str, Any]]:
        base = int(START.timestamp() * 1000)
        return [
            {"timestamp": base + i * DAY_MS, "fundingRate": 0.0001}
            for i in range(self.funding_days)
        ]


def source(**kw: Any) -> PerpSource:
    return PerpSource(exchange_id="binanceusdm", exchange=FakeExchange(**kw))


def test_trade_mark_and_funding_are_kept_apart() -> None:
    s = source()
    data = s.fetch("BTC/USDT:USDT", "1d", START, END)
    assert data.trades.symbol == "BTC/USDT:USDT"
    assert data.marks.symbol == "BTC/USDT:USDT"
    assert len(data.funding) == 10
    # the mark series is its own series, not an alias of the trade series
    assert data.marks is not data.trades


def test_a_missing_funding_window_raises_and_is_never_zero_filled() -> None:
    """INV-94: fail closed. A zero would read as 'funding was free', which is a real claim."""
    s = source(funding_days=4)
    with pytest.raises(CoverageError, match="funding"):
        s.fetch("BTC/USDT:USDT", "1d", START, END)


def test_missing_mark_data_raises() -> None:
    s = source(mark=False)
    with pytest.raises(CoverageError, match="mark"):
        s.fetch("BTC/USDT:USDT", "1d", START, END)


def test_a_spot_symbol_cannot_satisfy_a_perpetual_request() -> None:
    """ccxt spells a linear USDT-margined perpetual BASE/QUOTE:SETTLE. A spot pair is a
    different instrument and must not be silently accepted as one."""
    s = source()
    with pytest.raises(ValueError, match="not a linear perpetual"):
        s.fetch("BTC/USDT", "1d", START, END)


def test_the_source_is_not_continuous_and_needs_no_adjustment() -> None:
    """A perpetual never expires, so there is no roll and nothing to adjust — but it is not the
    same statement as spot's, so it is asserted rather than inherited."""
    s = source()
    assert s.is_continuous() is True
    assert s.adjustment() == "none"


# ── manifest ──────────────────────────────────────────────────────────────────────────


def test_a_manifest_records_checksum_coverage_and_source(tmp_path: Path) -> None:
    f = tmp_path / "BTCUSDT_1d.parquet"
    f.write_bytes(b"not really parquet, but it hashes")
    manifest = write_manifest(
        tmp_path / "manifest.json",
        files=[f],
        source="binanceusdm",
        coverage={"BTC/USDT:USDT": "2021-01-01/2021-01-11"},
    )
    assert manifest.source == "binanceusdm"
    assert manifest.coverage == {"BTC/USDT:USDT": "2021-01-01/2021-01-11"}
    assert len(manifest.files["BTCUSDT_1d.parquet"]) == 64  # sha256 hex


def test_a_checksum_mismatch_refuses_the_file(tmp_path: Path) -> None:
    f = tmp_path / "BTCUSDT_1d.parquet"
    f.write_bytes(b"original")
    path = tmp_path / "manifest.json"
    write_manifest(path, files=[f], source="binanceusdm", coverage={})
    verify_manifest(path, tmp_path)  # intact
    f.write_bytes(b"tampered")
    with pytest.raises(CoverageError, match="does not match"):
        verify_manifest(path, tmp_path)


def test_a_missing_file_refuses_rather_than_skipping(tmp_path: Path) -> None:
    f = tmp_path / "BTCUSDT_1d.parquet"
    f.write_bytes(b"x")
    path = tmp_path / "manifest.json"
    write_manifest(path, files=[f], source="binanceusdm", coverage={})
    f.unlink()
    with pytest.raises(CoverageError, match="missing"):
        verify_manifest(path, tmp_path)


def test_a_manifest_round_trips(tmp_path: Path) -> None:
    f = tmp_path / "a.parquet"
    f.write_bytes(b"x")
    path = tmp_path / "manifest.json"
    written = write_manifest(path, files=[f], source="s", coverage={"A": "r"})
    assert Manifest.read(path) == written


def test_common_window_is_the_latest_start_and_earliest_end() -> None:
    """What P3-01 computed by hand, as a function: the binding listing sets the IS start."""
    from quantcrucible.data.perp_source import common_window

    windows = {
        "BTC": (datetime(2019, 9, 8, tzinfo=UTC), datetime(2026, 9, 1, tzinfo=UTC)),
        "SOL": (datetime(2020, 9, 14, tzinfo=UTC), datetime(2026, 9, 1, tzinfo=UTC)),
    }
    start, end = common_window(windows)
    assert start == datetime(2020, 9, 14, tzinfo=UTC)
    assert end == datetime(2026, 9, 1, tzinfo=UTC)


def test_an_instrument_with_no_overlap_is_refused() -> None:
    from quantcrucible.data.perp_source import common_window

    windows = {
        "A": (datetime(2019, 1, 1, tzinfo=UTC), datetime(2020, 1, 1, tzinfo=UTC)),
        "B": (datetime(2021, 1, 1, tzinfo=UTC), datetime(2022, 1, 1, tzinfo=UTC)),
    }
    with pytest.raises(CoverageError, match="no common window"):
        common_window(windows)


def test_bars_are_indexed_by_close_time() -> None:
    """The project's convention everywhere: ccxt gives open times, Bars carry close times."""
    s = source()
    data = s.fetch("BTC/USDT:USDT", "1d", START, END)
    first_close = data.trades.ts[0].astype("datetime64[ms]").astype(np.int64)
    assert first_close == int(START.timestamp() * 1000) + DAY_MS


# ── pagination and resume (P3-17) ─────────────────────────────────────────────────────


class PagingExchange:
    """Serves at most `limit` rows from `since`, the way ccxt actually behaves.

    The original `FakeExchange` returns the whole series on every call regardless of `since`
    and `limit`, so it could never have caught a fetcher that asks once and stops.
    """

    def __init__(self, days: int = 2_500, mark: bool = True, ignores_since: bool = False) -> None:
        self.days = days
        self._mark = mark
        self._ignores_since = ignores_since
        self.calls: dict[str, int] = {"ohlcv": 0, "mark": 0, "funding": 0}

    def _page(self, since: int, limit: int, step_ms: int, n: int) -> list[int]:
        base = int(START.timestamp() * 1000)
        if self._ignores_since:  # a venue that serves the same page forever
            return [base + i * step_ms for i in range(min(limit, n))]
        first = max(0, -(-(since - base) // step_ms))
        return [base + i * step_ms for i in range(first, min(first + limit, n))]

    def fetch_ohlcv(self, symbol: str, timeframe: str, since: int, limit: int) -> list[list[float]]:
        self.calls["ohlcv"] += 1
        opens = self._page(since, limit, DAY_MS, self.days)
        return [[t, 100.0, 101.0, 99.0, 100.5, 1_000.0] for t in opens]

    def fetch_mark_ohlcv(
        self, symbol: str, timeframe: str, since: int, limit: int
    ) -> list[list[float]]:
        self.calls["mark"] += 1
        if not self._mark:
            return []
        opens = self._page(since, limit, DAY_MS, self.days)
        return [[t, 100.0, 101.0, 99.0, 100.5, 0.0] for t in opens]

    def fetch_funding_rate_history(
        self, symbol: str, since: int, limit: int
    ) -> list[dict[str, Any]]:
        self.calls["funding"] += 1
        eight_h = DAY_MS // 3
        return [
            {"timestamp": t, "fundingRate": 0.0001}
            for t in self._page(since, limit, eight_h, self.days * 3)
        ]


def test_a_window_longer_than_one_page_is_fetched_whole() -> None:
    """The reason this task exists: `fetch` asked for exactly one 1000-row page, so five years
    of history came back as the first 1000 bars and nothing said so."""
    ex = PagingExchange(days=2_500)
    end = START + timedelta(days=2_500)
    src = PerpSource(exchange_id="binanceusdm", exchange=ex)
    data = src.fetch("BTC/USDT:USDT", "1d", START, end)
    assert len(data.trades) == 2_500
    assert len(data.marks) == 2_500
    assert len(data.funding) == 7_500
    assert ex.calls["ohlcv"] >= 3, "a 2,500-bar window cannot come from fewer than three pages"


def test_the_window_end_is_honoured_rather_than_ignored() -> None:
    """`end` was accepted and used only in error messages."""
    ex = PagingExchange(days=2_500)
    end = START + timedelta(days=1_200)
    src = PerpSource(exchange_id="binanceusdm", exchange=ex)
    data = src.fetch("BTC/USDT:USDT", "1d", START, end)
    assert len(data.trades) == 1_200


def test_an_exchange_that_ignores_since_does_not_loop_forever() -> None:
    """A venue serving the same page on every call must end the loop, not hang the fetch.

    It then refuses, which is correct — one page really is short of the window — but the point
    here is that it refuses after a bounded number of calls instead of paging forever.
    """
    ex = PagingExchange(days=2_500, ignores_since=True)
    end = START + timedelta(days=2_500)
    src = PerpSource(exchange_id="binanceusdm", exchange=ex)
    with pytest.raises(CoverageError):
        src.fetch("BTC/USDT:USDT", "1d", START, end)
    assert 0 < ex.calls["ohlcv"] <= 3
    assert PAGE_LIMIT == 1_000  # the bound the fake is written against


# ── leverage brackets: a signed endpoint, so fail closed (P3-17) ──────────────────────


class TieredExchange(PagingExchange):
    """Adds the signed endpoint. `tiers` of None means the call raises, as it does without a
    read-only API key in `.env`."""

    def __init__(self, tiers: list[dict[str, Any]] | None = None, **kw: Any) -> None:
        super().__init__(**kw)
        self._tiers = tiers

    def fetch_market_leverage_tiers(self, symbol: str) -> list[dict[str, Any]]:
        if self._tiers is None:
            raise RuntimeError("binanceusdm fetchLeverageTiers() requires `apiKey` credential")
        return self._tiers


TIERS = [
    {"maxNotional": 50_000, "maintenanceMarginRate": 0.004, "maxLeverage": 125,
     "info": {"cum": "0"}},
    {"maxNotional": 500_000, "maintenanceMarginRate": 0.005, "maxLeverage": 100,
     "info": {"cum": "50"}},
]  # fmt: skip


def test_brackets_without_an_api_key_are_refused_never_defaulted() -> None:
    """The bracket table decides margin, and therefore the liquidation price. A default would
    be an invented venue rule that every result silently depends on, so the download fails
    closed instead — the campaign cannot open without a real snapshot."""
    src = PerpSource(exchange_id="binanceusdm", exchange=TieredExchange(tiers=None))
    with pytest.raises(CoverageError, match="leverage bracket"):
        src.fetch_brackets("BTC/USDT:USDT")


def test_an_empty_bracket_table_is_refused_too() -> None:
    src = PerpSource(exchange_id="binanceusdm", exchange=TieredExchange(tiers=[]))
    with pytest.raises(CoverageError, match="leverage bracket"):
        src.fetch_brackets("BTC/USDT:USDT")


def test_brackets_come_back_in_the_shape_the_margin_table_needs() -> None:
    """`data` may not import `execution`, so this returns plain rows and the execution layer
    builds the `BracketTable` from them. The maintenance amount lives in ccxt's raw `info`."""
    src = PerpSource(exchange_id="binanceusdm", exchange=TieredExchange(tiers=TIERS))
    rows = src.fetch_brackets("BTC/USDT:USDT")
    assert rows == [
        {"cap": 50_000.0, "max_leverage": 125, "mmr": 0.004, "amount": 0.0},
        {"cap": 500_000.0, "max_leverage": 100, "mmr": 0.005, "amount": 50.0},
    ]


def test_brackets_are_sorted_by_cap_so_a_lookup_is_ordered() -> None:
    ex = TieredExchange(tiers=list(reversed(TIERS)))
    src = PerpSource(exchange_id="binanceusdm", exchange=ex)
    caps = [r["cap"] for r in src.fetch_brackets("BTC/USDT:USDT")]
    assert caps == sorted(caps)


def test_a_venue_without_the_endpoint_is_refused_rather_than_skipped() -> None:
    src = PerpSource(exchange_id="binanceusdm", exchange=PagingExchange())
    with pytest.raises(CoverageError, match="leverage bracket"):
        src.fetch_brackets("BTC/USDT:USDT")
