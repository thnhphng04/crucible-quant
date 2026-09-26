"""Assemble fail-closed USDT-M perpetual inputs for a campaign.

This module is the bridge between the venue-facing downloader and the research side. It keeps
the large one-minute trade/mark series in :mod:`quantcrucible.data.perp_store`, fetches the
requested research timeframe through :class:`~quantcrucible.data.perp_source.PerpSource`, and
returns the exact in-memory shape that :func:`quantcrucible.data.perp_carve.carve_perp` accepts:
``symbol -> (trade Bars, PerpBundle)``.

Funding events, not hardcoded settlement hours, define path segment cuts. That matters for
replays across exchanges or historical venue quirks: a settlement that lands at 07:59 must move
the liquidation path at 07:59, not at the expected 08:00 slot.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import numpy.typing as npt
import pandas as pd

from quantcrucible.core.path_summary import SegmentedPath
from quantcrucible.core.perp_inputs import Bracket, PerpBundle, brackets_of
from quantcrucible.core.strategy.base import Bars
from quantcrucible.data.perp_source import CoverageError, PerpData, PerpSource
from quantcrucible.data.perp_store import (
    MINUTE_MS,
    download_minutes,
    minute_path,
    read_minutes,
    rebuild_summaries,
)
from quantcrucible.data.source import timeframe_delta

SUPPORTED_TIMEFRAMES = frozenset({"1d", "8h", "4h"})


@dataclass(frozen=True, slots=True)
class PerpCoverage:
    """Preflight evidence for one prepared contract."""

    symbol: str
    timeframe: str
    start: datetime
    end: datetime
    bars: int
    minute_rows: int
    funding_rows: int
    path_rows: int


@dataclass(frozen=True, slots=True)
class PerpetualPreparation:
    """Prepared perpetual data, ready for ``carve_perp`` and later sandbox payloads."""

    data: Mapping[str, tuple[Bars, PerpBundle]]
    coverage: Mapping[str, PerpCoverage]
    brackets: Mapping[str, tuple[Bracket, ...]]


def prepare_perpetual(
    source: PerpSource,
    root: Path,
    symbols: Sequence[str],
    timeframe: str,
    start: datetime,
    end: datetime,
    *,
    funding_interval_hours: int = 8,
) -> PerpetualPreparation:
    """Download, validate, and assemble aligned perpetual inputs.

    ``root`` is the minute-cache directory used by :mod:`quantcrucible.data.perp_store`.
    The returned ``data`` mapping is the direct input expected by ``carve_perp``.
    """

    start_utc, end_utc = normalize_window(timeframe, start, end)
    if not 0 < funding_interval_hours <= 24:
        raise ValueError("funding_interval_hours must be in 1..24")
    if not symbols:
        raise CoverageError("no perpetual symbols to prepare")

    data: dict[str, tuple[Bars, PerpBundle]] = {}
    coverage: dict[str, PerpCoverage] = {}
    brackets: dict[str, tuple[Bracket, ...]] = {}
    common_ts: npt.NDArray[np.datetime64] | None = None

    for symbol in symbols:
        raw = _fetch_research_window(
            source,
            symbol,
            timeframe,
            start_utc,
            end_utc,
            timedelta(hours=funding_interval_hours),
        )
        trades, marks = _slice_to_window(raw, start_utc, end_utc)
        _check_bar_alignment(symbol, trades, marks, common_ts)
        if common_ts is None:
            common_ts = trades.ts

        # The signed bracket endpoint is cheap and may require credentials. Refuse before a
        # multi-year minute download rather than discovering that blocker at its end.
        rows = brackets_of(source.fetch_brackets(symbol))
        _download_required_minutes(source, root, symbol, start_utc, end_utc)
        mark_minutes = _window_minutes(root, symbol, "mark", start_utc, end_utc)
        _ = _window_minutes(root, symbol, "trade", start_utc, end_utc)

        funding = _funding_rows(symbol, trades, raw.funding, mark_minutes)
        paths = _paths_from_store(root, symbol, timeframe, funding, start_utc, end_utc)
        bundle = PerpBundle(
            symbol=symbol,
            timeframe=timeframe,
            marks=marks,
            funding=funding,
            paths=tuple(paths),
            brackets=rows,
        )
        bundle.aligned_with(trades)

        data[symbol] = (trades, bundle)
        brackets[symbol] = rows
        coverage[symbol] = PerpCoverage(
            symbol=symbol,
            timeframe=timeframe,
            start=start_utc,
            end=end_utc,
            bars=len(trades),
            minute_rows=len(mark_minutes),
            funding_rows=len(funding),
            path_rows=len(paths),
        )

    return PerpetualPreparation(data=data, coverage=coverage, brackets=brackets)


def normalize_window(
    timeframe: str,
    start: datetime,
    end: datetime,
) -> tuple[datetime, datetime]:
    """Return UTC bounds, refusing windows that do not sit on the supported bar grid."""

    if timeframe not in SUPPORTED_TIMEFRAMES:
        raise ValueError(
            f"unsupported perpetual timeframe {timeframe!r}; expected one of "
            f"{sorted(SUPPORTED_TIMEFRAMES)}"
        )
    start_utc = _aware_utc(start, "start")
    end_utc = _aware_utc(end, "end")
    if start_utc >= end_utc:
        raise ValueError("start must be before end")
    step = timeframe_delta(timeframe)
    if (end_utc - start_utc) % step != timedelta(0):
        raise ValueError(f"{timeframe} window length must be an integer number of bars")
    for name, value in (("start", start_utc), ("end", end_utc)):
        if value.minute or value.second or value.microsecond:
            raise ValueError(f"{name} must be on an hour boundary")
        if timeframe == "1d" and value.hour != 0:
            raise ValueError(f"{name} must be midnight UTC for 1d data")
        if timeframe.endswith("h") and value.hour % int(timeframe[:-1]) != 0:
            raise ValueError(f"{name} must align to the {timeframe} UTC grid")
    return start_utc, end_utc


def _aware_utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware UTC")
    return value.astimezone(UTC)


def _fetch_research_window(
    source: PerpSource,
    symbol: str,
    timeframe: str,
    start: datetime,
    end: datetime,
    interval: timedelta,
) -> PerpData:
    step = timeframe_delta(timeframe)
    fetch_start = start
    fetch_end = end
    if step < interval:
        interval_ms = int(interval.total_seconds() * 1000)
        start_ms = _ms(start)
        start_offset = start_ms % interval_ms
        if start_offset:
            fetch_start = start - timedelta(milliseconds=start_offset)
        end_ms = _ms(end)
        end_offset = end_ms % interval_ms
        next_funding_ms = end_ms if end_offset == 0 else end_ms + (interval_ms - end_offset)
        fetch_end = _dt_from_ms(next_funding_ms + int(step.total_seconds() * 1000))
    return source.fetch(symbol, timeframe, fetch_start, fetch_end)


def _slice_to_window(raw: PerpData, start: datetime, end: datetime) -> tuple[Bars, Bars]:
    lo = _ns(start)
    hi = _ns(end)
    trades = _slice_bars(raw.trades, lo, hi)
    marks = _slice_bars(raw.marks, lo, hi)
    if not len(trades):
        raise CoverageError(f"{raw.symbol}: no trade bars in the requested window")
    return trades, marks


def _slice_bars(bars: Bars, lo_ns: int, hi_ns: int) -> Bars:
    ts = bars.ts.astype("datetime64[ns]").astype(np.int64)
    keep = np.flatnonzero((ts > lo_ns) & (ts <= hi_ns))
    if not len(keep):
        return bars.slice(0, 0)
    return bars.slice(int(keep[0]), int(keep[-1]) + 1)


def _check_bar_alignment(
    symbol: str,
    trades: Bars,
    marks: Bars,
    common_ts: npt.NDArray[np.datetime64] | None,
) -> None:
    if len(trades) != len(marks) or not np.array_equal(trades.ts, marks.ts):
        raise CoverageError(f"{symbol}: trade and mark bars do not share the same close times")
    if common_ts is not None and not np.array_equal(trades.ts, common_ts):
        raise CoverageError(f"{symbol}: bar coverage differs from the common campaign window")


def _download_required_minutes(
    source: PerpSource,
    root: Path,
    symbol: str,
    start: datetime,
    end: datetime,
) -> None:
    download_minutes(source, root, symbol, start, end, kind="trade")
    download_minutes(source, root, symbol, start, end, kind="mark")


def _window_minutes(
    root: Path,
    symbol: str,
    kind: str,
    start: datetime,
    end: datetime,
) -> pd.DataFrame:
    frame = read_minutes(minute_path(root, symbol, kind))
    start_ms = _ms(start)
    end_ms = _ms(end)
    window = frame[(frame["ts"] >= start_ms) & (frame["ts"] < end_ms)].copy()
    expected = (end_ms - start_ms) // MINUTE_MS
    if len(window) != expected:
        raise CoverageError(
            f"{symbol} {kind}: minute coverage is {len(window)} rows, expected {expected}"
        )
    ts = window["ts"].to_numpy(dtype=np.int64)
    if len(ts):
        want = np.arange(start_ms, end_ms, MINUTE_MS, dtype=np.int64)
        if not np.array_equal(ts, want):
            raise CoverageError(f"{symbol} {kind}: minute data has a gap in the requested window")
    return window


def _funding_rows(
    symbol: str,
    trades: Bars,
    funding: npt.NDArray[np.float64],
    mark_minutes: pd.DataFrame,
) -> npt.NDArray[np.float64]:
    if funding.ndim != 2 or funding.shape[1] != 2:
        raise CoverageError(f"{symbol}: funding must be (timestamp_ns, rate) rows")
    close_ns = trades.ts.astype("datetime64[ns]").astype(np.int64)
    minute_mark = {
        int(ts) * 1_000_000: float(mark)
        for ts, mark in zip(mark_minutes["ts"], mark_minutes["close"], strict=True)
    }
    rows: list[tuple[float, float, float, float]] = []
    first_mark_ns = int(mark_minutes["ts"].iloc[0]) * 1_000_000
    minute_ns = MINUTE_MS * 1_000_000
    for ts_ns_float, rate in funding:
        ts_ns = int(ts_ns_float)
        # OHLCV timestamps name the minute OPEN. At settlement the 08:00 minute has not
        # closed yet, so its close would look one minute into the future. The last known
        # mark is the close of 07:59; the start-boundary event has no such minute in this
        # window and cannot affect a position opened after the boundary.
        if ts_ns <= first_mark_ns:
            continue
        bar_ix = int(np.searchsorted(close_ns, ts_ns, side="right"))
        if bar_ix >= len(trades):
            continue
        mark_at = ts_ns - minute_ns
        if mark_at not in minute_mark:
            raise CoverageError(
                f"{symbol}: no completed one-minute mark before funding settlement "
                f"{_dt_from_ns(ts_ns):%Y-%m-%d %H:%M}"
            )
        rows.append((float(bar_ix), float(ts_ns), float(rate), minute_mark[mark_at]))
    if not rows:
        raise CoverageError(f"{symbol}: no funding rows fall inside the requested window")
    return np.array(rows, dtype=np.float64)


def _paths_from_store(
    root: Path,
    symbol: str,
    timeframe: str,
    funding: npt.NDArray[np.float64],
    start: datetime,
    end: datetime,
) -> tuple[SegmentedPath, ...]:
    step_ms = int(timeframe_delta(timeframe).total_seconds() * 1000)
    start_ms = _ms(start)
    end_ms = _ms(end)
    funding_times = [int(ts // 1_000_000) for ts in funding[:, 1]]
    all_paths = rebuild_summaries(
        root,
        symbol,
        timeframe,
        kind="mark",
        funding_times=funding_times,
    )
    minute_ts = read_minutes(minute_path(root, symbol, "mark"))["ts"].to_numpy(dtype=np.int64)
    first = int(minute_ts[0]) - int(minute_ts[0]) % step_ms
    starts = list(range(first, int(minute_ts[-1]) + 1, step_ms))
    selected = tuple(
        path
        for bar_start, path in zip(starts, all_paths, strict=True)
        if start_ms <= bar_start < end_ms
    )
    expected = (end_ms - start_ms) // step_ms
    if len(selected) != expected:
        raise CoverageError(f"{symbol}: path coverage is {len(selected)} bars, expected {expected}")
    return selected


def _ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def _ns(value: datetime) -> int:
    return _ms(value) * 1_000_000


def _dt_from_ms(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1000, tz=UTC)


def _dt_from_ns(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1_000_000_000, tz=UTC)
