"""Downloading perpetual series to disk, resumably (P3-17) — INV-94.

Five years of one-minute marks for five contracts is tens of thousands of paged requests. A run
that long **will** be interrupted — a dropped connection, a rate limit, a closed laptop — so the
download has to be restartable without starting over, and restarting must not quietly leave a
hole in the middle of the series.

The minute closes are kept on disk as the checksummed source of truth rather than discarded once
summaries are built: the timeframe is a campaign knob (P3-14), and switching it invalidates every
stored summary. Without the minutes those summaries could not be rebuilt, and the knob would be
jammed by a download decision.

Every test uses a fake exchange and `tmp_path` — none touches the network or `data/`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from quantcrucible.data.manifest import verify_manifest
from quantcrucible.data.perp_source import CoverageError, PerpSource
from quantcrucible.data.perp_store import (
    download_minutes,
    minute_path,
    read_minutes,
    rebuild_summaries,
    write_perp_manifest,
)

START = datetime(2021, 1, 1, tzinfo=UTC)
MINUTE_MS = 60_000


class MinuteExchange:
    """Serves one-minute bars, at most `limit` per call, counting its calls."""

    def __init__(self, minutes: int = 4_320) -> None:  # three days
        self.minutes = minutes
        self.calls = 0

    def fetch_mark_ohlcv(
        self, symbol: str, timeframe: str, since: int, limit: int
    ) -> list[list[float]]:
        self.calls += 1
        base = int(START.timestamp() * 1000)
        first = max(0, -(-(since - base) // MINUTE_MS))
        out = []
        for i in range(first, min(first + limit, self.minutes)):
            mid = 100.0 + (i % 60) * 0.01
            out.append([base + i * MINUTE_MS, mid, mid + 0.05, mid - 0.05, mid, 1.0])
        return out

    def fetch_ohlcv(self, symbol: str, timeframe: str, since: int, limit: int) -> list[list[float]]:
        return self.fetch_mark_ohlcv(symbol, timeframe, since, limit)

    def fetch_funding_rate_history(
        self, symbol: str, since: int, limit: int
    ) -> list[dict[str, Any]]:
        return []


class FailingMinuteExchange(MinuteExchange):
    """Raises on a configured page request, after earlier pages have been returned."""

    def __init__(self, minutes: int = 4_320, *, fail_on_call: int) -> None:
        super().__init__(minutes=minutes)
        self.fail_on_call = fail_on_call

    def fetch_mark_ohlcv(
        self, symbol: str, timeframe: str, since: int, limit: int
    ) -> list[list[float]]:
        if self.calls + 1 == self.fail_on_call:
            self.calls += 1
            raise RuntimeError("simulated dropped connection")
        return super().fetch_mark_ohlcv(symbol, timeframe, since, limit)


class BrokenMinuteExchange(MinuteExchange):
    """Returns one malformed page so the downloader has to fail closed."""

    def __init__(self, mode: str) -> None:
        super().__init__(minutes=1_440)
        self.mode = mode

    def fetch_mark_ohlcv(
        self, symbol: str, timeframe: str, since: int, limit: int
    ) -> list[list[float]]:
        rows = super().fetch_mark_ohlcv(symbol, timeframe, since, limit)
        if self.calls == 1 and len(rows) > 3:
            if self.mode == "duplicate":
                rows[2][0] = rows[1][0]
            elif self.mode == "nonmonotonic":
                rows[1], rows[2] = rows[2], rows[1]
            elif self.mode == "missing":
                rows.pop(2)
        return rows


def source(ex: MinuteExchange) -> PerpSource:
    return PerpSource(exchange_id="binanceusdm", exchange=ex)


def test_a_multi_page_minute_download_lands_whole(tmp_path: Path) -> None:
    ex = MinuteExchange(minutes=4_320)
    end = START + timedelta(minutes=4_320)
    n = download_minutes(source(ex), tmp_path, "BTC/USDT:USDT", START, end, kind="mark")
    assert n == 4_320
    assert ex.calls >= 5  # 1,000 rows per page
    frame = read_minutes(minute_path(tmp_path, "BTC/USDT:USDT", "mark"))
    assert len(frame) == 4_320
    assert frame["ts"].is_monotonic_increasing


def test_an_interrupted_download_resumes_instead_of_starting_over(tmp_path: Path) -> None:
    """The point of the task. A restart must pick up where the file ends."""
    ex = MinuteExchange(minutes=4_320)
    end = START + timedelta(minutes=4_320)
    half = START + timedelta(minutes=2_000)
    download_minutes(source(ex), tmp_path, "BTC/USDT:USDT", START, half, kind="mark")
    assert len(read_minutes(minute_path(tmp_path, "BTC/USDT:USDT", "mark"))) == 2_000

    ex.calls = 0
    added = download_minutes(source(ex), tmp_path, "BTC/USDT:USDT", START, end, kind="mark")
    assert added == 2_320, "only the missing tail should be fetched"
    frame = read_minutes(minute_path(tmp_path, "BTC/USDT:USDT", "mark"))
    assert len(frame) == 4_320
    assert not frame["ts"].duplicated().any()

    # Resuming costs fewer requests than starting the same window over — which is the whole
    # point, since the real job is tens of thousands of requests long.
    fresh = MinuteExchange(minutes=4_320)
    download_minutes(source(fresh), tmp_path / "fresh", "BTC/USDT:USDT", START, end, kind="mark")
    assert ex.calls < fresh.calls


def test_an_interruption_mid_multi_page_fetch_keeps_a_resumable_checkpoint(
    tmp_path: Path,
) -> None:
    """If request 3 dies, pages 1-2 must already be durable instead of trapped in memory."""
    end = START + timedelta(minutes=4_320)
    failing = FailingMinuteExchange(minutes=4_320, fail_on_call=3)
    with pytest.raises(RuntimeError, match="dropped connection"):
        download_minutes(source(failing), tmp_path, "BTC/USDT:USDT", START, end, kind="mark")

    frame = read_minutes(minute_path(tmp_path, "BTC/USDT:USDT", "mark"))
    assert len(frame) == 2_000
    assert frame["ts"].is_monotonic_increasing

    resumed = MinuteExchange(minutes=4_320)
    added = download_minutes(source(resumed), tmp_path, "BTC/USDT:USDT", START, end, kind="mark")
    assert added == 2_320
    assert len(read_minutes(minute_path(tmp_path, "BTC/USDT:USDT", "mark"))) == 4_320
    assert resumed.calls == 3


def test_a_completed_download_re_run_fetches_nothing(tmp_path: Path) -> None:
    ex = MinuteExchange(minutes=1_440)
    end = START + timedelta(minutes=1_440)
    download_minutes(source(ex), tmp_path, "BTC/USDT:USDT", START, end, kind="mark")
    ex.calls = 0
    assert download_minutes(source(ex), tmp_path, "BTC/USDT:USDT", START, end, kind="mark") == 0
    assert ex.calls == 0


def test_a_resume_that_would_leave_a_hole_is_refused(tmp_path: Path) -> None:
    """Resuming from the file's end is only safe when the request starts at or before it.
    Asking for a later window would append a series with a gap in the middle — and a gap is
    exactly what the path summaries must never be built over (INV-94)."""
    ex = MinuteExchange(minutes=4_320)
    download_minutes(
        source(ex), tmp_path, "BTC/USDT:USDT", START, START + timedelta(minutes=600), kind="mark"
    )
    with pytest.raises(CoverageError, match="gap"):
        download_minutes(
            source(ex),
            tmp_path,
            "BTC/USDT:USDT",
            START + timedelta(minutes=2_000),
            START + timedelta(minutes=3_000),
            kind="mark",
        )


@pytest.mark.parametrize(
    ("mode", "message"),
    [
        ("duplicate", "duplicate"),
        ("nonmonotonic", "nonmonotonic"),
        ("missing", "missing minute"),
    ],
)
def test_bad_minute_pages_are_refused_instead_of_normalized(
    tmp_path: Path, mode: str, message: str
) -> None:
    """INV-94: the downloader must not sort, deduplicate, or fill a malformed minute page."""
    ex = BrokenMinuteExchange(mode)
    end = START + timedelta(minutes=1_440)
    with pytest.raises(CoverageError, match=message):
        download_minutes(source(ex), tmp_path, "BTC/USDT:USDT", START, end, kind="mark")


def test_summaries_rebuild_from_the_stored_minutes(tmp_path: Path) -> None:
    """The timeframe is a campaign knob, so summaries are a derived artifact. Keeping the
    minutes is what stops a download decision from jamming that knob."""
    ex = MinuteExchange(minutes=2_880)  # two days
    end = START + timedelta(minutes=2_880)
    download_minutes(source(ex), tmp_path, "BTC/USDT:USDT", START, end, kind="mark")

    daily = rebuild_summaries(tmp_path, "BTC/USDT:USDT", "1d", kind="mark")
    assert len(daily) == 2
    assert [len(bar.segments) for bar in daily] == [3, 3]  # funding at 00:00, 08:00, 16:00
    assert daily[0].starts == (0, 480, 960)

    four_hourly = rebuild_summaries(tmp_path, "BTC/USDT:USDT", "4h", kind="mark")
    assert len(four_hourly) == 12
    assert all(len(bar.segments) == 1 for bar in four_hourly)  # settlements land on bar edges


def test_summaries_can_cut_from_actual_funding_timestamps(tmp_path: Path) -> None:
    """P3-24 passes funding events through the materializer; those timestamps beat the default
    00:00/08:00/16:00 assumption."""
    ex = MinuteExchange(minutes=1_440)
    end = START + timedelta(minutes=1_440)
    download_minutes(source(ex), tmp_path, "BTC/USDT:USDT", START, end, kind="mark")

    funding_times: list[datetime | int] = [
        START + timedelta(hours=6),
        int((START + timedelta(hours=18)).timestamp() * 1000),
    ]
    daily = rebuild_summaries(
        tmp_path, "BTC/USDT:USDT", "1d", kind="mark", funding_times=funding_times
    )

    assert len(daily) == 1
    assert daily[0].starts == (0, 360, 1080)


def test_funding_iterator_is_applied_to_every_bar(tmp_path: Path) -> None:
    ex = MinuteExchange(minutes=2_880)
    end = START + timedelta(minutes=2_880)
    download_minutes(source(ex), tmp_path, "BTC/USDT:USDT", START, end, kind="mark")
    settlements = (at for at in (START + timedelta(hours=6), START + timedelta(days=1, hours=10)))
    daily = rebuild_summaries(
        tmp_path, "BTC/USDT:USDT", "1d", kind="mark", funding_times=settlements
    )
    assert [path.starts for path in daily] == [(0, 360), (0, 600)]


def test_missing_checkpoint_page_is_refused(tmp_path: Path) -> None:
    ex = MinuteExchange(minutes=3_000)
    end = START + timedelta(minutes=3_000)
    download_minutes(source(ex), tmp_path, "BTC/USDT:USDT", START, end, kind="mark")
    path = minute_path(tmp_path, "BTC/USDT:USDT", "mark")
    path.with_name(f"{path.stem}.part-000001.parquet").unlink()
    with pytest.raises(CoverageError, match="checkpoint"):
        read_minutes(path)


def test_a_missing_minute_stops_a_summary_rather_than_being_filled(tmp_path: Path) -> None:
    ex = MinuteExchange(minutes=1_440)
    end = START + timedelta(minutes=1_440)
    download_minutes(source(ex), tmp_path, "BTC/USDT:USDT", START, end, kind="mark")
    path = minute_path(tmp_path, "BTC/USDT:USDT", "mark")
    frame = read_minutes(path)
    frame.drop(index=frame.index[700]).to_parquet(path, index=False)  # punch one hole
    with pytest.raises(CoverageError, match="minute"):
        rebuild_summaries(tmp_path, "BTC/USDT:USDT", "1d", kind="mark")


def test_the_download_is_checksummed_and_a_changed_file_is_caught(tmp_path: Path) -> None:
    """The spot in-sample parquet has no integrity record at all. Mark and funding cannot be
    reconstructed from price, so a silently truncated window would change every result without
    changing any error message."""
    ex = MinuteExchange(minutes=1_440)
    end = START + timedelta(minutes=1_440)
    download_minutes(source(ex), tmp_path, "BTC/USDT:USDT", START, end, kind="mark")
    manifest = write_perp_manifest(tmp_path, "binanceusdm")
    assert manifest.source == "binanceusdm"
    assert manifest.coverage == {
        "BTC-USDT-USDT_1m.mark.parquet": "2021-01-01/2021-01-01",
        "BTC-USDT-USDT_1m.mark.part-000001.parquet": "2021-01-01/2021-01-01",
    }
    verify_manifest(tmp_path / "manifest.json", tmp_path)

    path = minute_path(tmp_path, "BTC/USDT:USDT", "mark")
    frame = read_minutes(path)
    frame.head(999).to_parquet(path, index=False)  # a truncated checkpoint page
    with pytest.raises(CoverageError, match="checksum"):
        verify_manifest(tmp_path / "manifest.json", tmp_path)
