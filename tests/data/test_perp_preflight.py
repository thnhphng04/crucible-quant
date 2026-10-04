"""Real-source perpetual preflight (P3-24, §6.1, D18).

Before a five-year minute download and a write-once holdout carve, the venue is asked one cheap
question per series: from when, and until when? The answer decides the in-sample start — the
binding listing, SOLUSDT at 2020-09-14 on the real venue — and a missing bracket snapshot or a
config start the data cannot serve is refused here, not after hours of downloading.

Every test uses a fake venue; nothing touches the network or the disk.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from quantcrucible.cli import main
from quantcrucible.data.perp_preflight import preflight_perpetual
from quantcrucible.data.perp_source import CoverageError, PerpSource

MINUTE_MS = 60_000
NOW = datetime(2026, 10, 2, 12, tzinfo=UTC)
END = datetime(2026, 10, 2, tzinfo=UTC)
HOLDOUT = datetime(2025, 10, 2, tzinfo=UTC)
LISTINGS = {
    "BTC/USDT:USDT": datetime(2019, 9, 8, 17, tzinfo=UTC),
    "SOL/USDT:USDT": datetime(2020, 9, 14, tzinfo=UTC),
}
FRAME_MS = {"1m": MINUTE_MS, "1h": 60 * MINUTE_MS, "1d": 1440 * MINUTE_MS}


def _ms(when: datetime) -> int:
    return int(when.timestamp() * 1000)


class FakeVenue:
    """Serves rows on a grid from each series' first timestamp until NOW, honouring
    ``since`` and ``limit`` the way ccxt does."""

    def __init__(
        self,
        *,
        funding_from: dict[str, datetime] | None = None,
        mark_until: dict[str, datetime] | None = None,
        brackets: bool = True,
    ) -> None:
        self.funding_from = funding_from or {}
        self.mark_until = mark_until or {}
        self.brackets = brackets
        self.calls = 0

    def _grid(
        self, first: datetime, last: datetime, step: int, since: int, limit: int
    ) -> list[int]:
        start = max(_ms(first), since)
        start += -start % step
        stop = _ms(last)
        return list(range(start, stop, step)[:limit])

    def _ohlcv(self, first: datetime, last: datetime, tf: str, since: int, limit: int) -> list[Any]:
        self.calls += 1
        stamps = self._grid(first, last, FRAME_MS[tf], since, limit)
        return [[t, 100.0, 101.0, 99.0, 100.5, 1.0] for t in stamps]

    def fetch_ohlcv(self, symbol: str, timeframe: str, since: int, limit: int) -> list[Any]:
        return self._ohlcv(LISTINGS[symbol], NOW, timeframe, since, limit)

    def fetch_mark_ohlcv(self, symbol: str, timeframe: str, since: int, limit: int) -> list[Any]:
        return self._ohlcv(
            LISTINGS[symbol], self.mark_until.get(symbol, NOW), timeframe, since, limit
        )

    def fetch_funding_rate_history(self, symbol: str, since: int, limit: int) -> list[Any]:
        self.calls += 1
        first = self.funding_from.get(symbol, LISTINGS[symbol] + timedelta(hours=8))
        # Binance stamps settlements a few milliseconds late; the preflight must not care.
        return [
            {"timestamp": t + 3, "fundingRate": 0.0001}
            for t in self._grid(first, NOW, 8 * 60 * MINUTE_MS, since, limit)
        ]

    def fetch_market_leverage_tiers(self, symbol: str) -> list[dict[str, Any]]:
        self.calls += 1
        if not self.brackets:
            raise PermissionError("signed endpoint")
        return [
            {"maxNotional": 50_000, "maxLeverage": 125, "maintenanceMarginRate": 0.004,
             "info": {"cum": "0"}},
            {"maxNotional": 250_000, "maxLeverage": 100, "maintenanceMarginRate": 0.005,
             "info": {"cum": "50"}},
        ]  # fmt: skip


def run(venue: FakeVenue, *, start: datetime, timeframe: str = "1h") -> Any:
    return preflight_perpetual(
        PerpSource(exchange=venue),
        list(LISTINGS),
        timeframe,
        start,
        END,
        HOLDOUT,
        funding_interval_hours=8,
    )


def test_the_common_window_starts_at_the_binding_listing() -> None:
    report = run(FakeVenue(), start=datetime(2020, 9, 15, tzinfo=UTC))
    assert report.common_start == datetime(2020, 9, 14, 8, tzinfo=UTC)
    assert report.common_start.date().isoformat() == "2020-09-14"
    assert report.ok, report.problems


def test_the_earliest_start_is_the_first_funding_on_the_bar_grid() -> None:
    """The fetch refuses a first trade bar older than the first funding event. A settlement
    stamped milliseconds late still belongs to its boundary minute (ADR-0042), so it does not
    push the start a whole funding interval later."""
    report = run(FakeVenue(), start=datetime(2020, 9, 15, tzinfo=UTC))
    assert report.earliest_start == datetime(2020, 9, 14, 8, tzinfo=UTC)
    daily = run(FakeVenue(), start=datetime(2020, 9, 15, tzinfo=UTC), timeframe="1d")
    assert daily.earliest_start == datetime(2020, 9, 15, tzinfo=UTC)


def test_a_config_start_before_the_data_is_refused_with_the_start_to_use() -> None:
    report = run(FakeVenue(), start=datetime(2018, 1, 1, tzinfo=UTC))
    assert not report.ok
    assert any("2020-09-14" in p and "start" in p for p in report.problems)


def test_without_a_bracket_snapshot_the_preflight_refuses() -> None:
    report = run(FakeVenue(brackets=False), start=datetime(2020, 9, 15, tzinfo=UTC))
    assert not report.ok
    assert any("bracket" in p and "API key" in p for p in report.problems)


def test_a_series_that_stops_before_the_window_end_is_refused() -> None:
    venue = FakeVenue(mark_until={"SOL/USDT:USDT": datetime(2026, 9, 1, tzinfo=UTC)})
    report = run(venue, start=datetime(2020, 9, 15, tzinfo=UTC))
    assert not report.ok
    assert any("SOL/USDT:USDT" in p and "mark" in p for p in report.problems)


def test_a_missing_funding_history_is_refused_not_assumed() -> None:
    venue = FakeVenue(funding_from={"SOL/USDT:USDT": NOW + timedelta(days=1)})
    report = run(venue, start=datetime(2020, 9, 15, tzinfo=UTC))
    assert not report.ok
    assert any("SOL/USDT:USDT" in p and "funding" in p for p in report.problems)


def test_the_report_names_every_contract_and_the_in_sample_length() -> None:
    report = run(FakeVenue(), start=datetime(2020, 9, 15, tzinfo=UTC))
    text = report.render()
    for symbol in LISTINGS:
        assert symbol in text
    assert "2020-09-14" in text
    assert "years" in text


def test_the_cli_preflight_is_read_only_and_reports_refusals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "perp.yaml"
    config.write_text(
        """research:
  data:
    exchange: binance
    market: usdt_m_perpetual
    symbols: [BTC/USDT:USDT, SOL/USDT:USDT]
    timeframe: 1h
    start: 2018-01-01
    end: 2026-10-02
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "quantcrucible.data.perp_preflight.PerpSource",
        lambda: PerpSource(exchange=FakeVenue()),
    )
    before = sorted(p.name for p in tmp_path.iterdir())
    assert main(["data-preflight", "--config", str(config)]) == 2
    assert sorted(p.name for p in tmp_path.iterdir()) == before
    err = capsys.readouterr().err
    assert "refused:" in err and "2020-09-14" in err


def test_an_unknown_contract_is_a_coverage_error_not_a_crash() -> None:
    class Unlisted(FakeVenue):
        def fetch_ohlcv(self, symbol: str, timeframe: str, since: int, limit: int) -> list[Any]:
            raise CoverageError(f"{symbol} is not listed")

    report = run(Unlisted(), start=datetime(2020, 9, 15, tzinfo=UTC))
    assert not report.ok
    assert any("trade" in p for p in report.problems)
