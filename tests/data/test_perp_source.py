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

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from quantcrucible.data.manifest import Manifest, verify_manifest, write_manifest
from quantcrucible.data.perp_source import CoverageError, PerpSource

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
