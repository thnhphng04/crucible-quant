"""Perpetual minute series on disk, downloaded resumably (Architecture §6.1, ADR-0032, P3-17).

Five years of one-minute marks for five contracts is tens of thousands of paged requests. A run
that long **will** be interrupted, so :func:`download_minutes` resumes from whatever is already
on disk instead of starting over — and refuses a resume that would leave a hole, because a gap
is precisely what a path summary must never be built across (INV-94).

**Why the minutes are kept rather than discarded once summaries exist.** The bar timeframe is a
campaign knob (P3-14), and the summaries are cut per bar, so switching ``1d`` to ``4h``
invalidates every stored summary. Keeping the minutes makes the summaries a *derived* artifact
that :func:`rebuild_summaries` can regenerate; discarding them would let a download decision jam
a configuration knob, and the only fix would be re-fetching five years.

Summaries are cut at Binance's funding settlements — 00:00, 08:00 and 16:00 UTC — because
funding leaves the isolated wallet at those instants and so moves the liquidation price *inside*
a daily bar (ADR-0032 decision 6b).
"""

from __future__ import annotations

import itertools
import json
import os
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from quantcrucible.core.path_summary import SegmentedPath, segment_bar
from quantcrucible.data.manifest import Manifest, write_manifest
from quantcrucible.data.perp_source import CoverageError, PerpSource
from quantcrucible.data.source import timeframe_delta

MINUTE_MS = 60_000
FUNDING_HOURS = (0, 8, 16)  # UTC settlements; see ADR-0032 decision 6b
COLUMNS = ("ts", "open", "high", "low", "close", "volume")
# ADR-0042: the venue's mark history has short holes (24 minutes on 2020-12-17 on every
# contract). A run up to this long is filled from the same minutes' trade bars and recorded;
# a longer one, a trade gap, or a hole with no trade bar to fill it is still refused.
MAX_MARK_FILL_MINUTES = 60


def minute_path(root: Path, symbol: str, kind: str) -> Path:
    """Where one contract's minute series lives. ``kind`` is ``trade`` or ``mark``.

    The colon of ``BTC/USDT:USDT`` is replaced for the same reason as in
    :func:`~quantcrucible.data.store.file_name`: on NTFS it opens an alternate data stream
    instead of failing.
    """
    safe = symbol.replace("/", "-").replace(":", "-")
    return root / f"{safe}_1m.{kind}.parquet"


def read_minutes(path: Path) -> pd.DataFrame:
    parts = sorted(path.parent.glob(f"{path.stem}.part-*.parquet"))
    if not path.is_file() and not parts:
        dtypes = {c: "int64" if c == "ts" else "float64" for c in COLUMNS}
        return pd.DataFrame({c: pd.Series(dtype=d) for c, d in dtypes.items()})
    if not path.is_file():
        raise CoverageError(f"{path.name}: minute checkpoint is missing its first page")
    for number, part in enumerate(parts, 1):
        if part.name != f"{path.stem}.part-{number:06d}.parquet":
            raise CoverageError(f"{path.name}: missing or unexpected minute checkpoint page")
    frames = [pd.read_parquet(file) for file in (path, *parts)]
    return pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]


def _at(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


def _check_minute_grid(frame: pd.DataFrame, symbol: str, kind: str, *, context: str) -> None:
    if not len(frame):
        return
    ts = frame["ts"].to_numpy(dtype=np.int64)
    diffs = np.diff(ts)
    if (diffs == 0).any():
        raise CoverageError(f"{symbol} {kind}: duplicate minute timestamps in {context}")
    if (diffs < 0).any():
        raise CoverageError(f"{symbol} {kind}: nonmonotonic minute timestamps in {context}")
    if (diffs != MINUTE_MS).any():
        raise CoverageError(f"{symbol} {kind}: missing minute in {context}")


def _fresh_minutes(
    rows: list[list[float]], symbol: str, kind: str, *, expected_first: int
) -> pd.DataFrame:
    fresh = pd.DataFrame(rows, columns=list(COLUMNS)).astype({"ts": "int64"})
    _check_minute_grid(fresh, symbol, kind, context="download page")
    if len(fresh) and int(fresh["ts"].iloc[0]) != expected_first:
        raise CoverageError(
            f"{symbol} {kind}: expected next minute {_at(expected_first):%Y-%m-%d %H:%M} "
            f"but the venue returned {_at(int(fresh['ts'].iloc[0])):%Y-%m-%d %H:%M}"
        )
    return fresh


def fills_path(root: Path, symbol: str) -> Path:
    """The record of every mark minute filled from a trade bar (ADR-0042)."""
    path = minute_path(root, symbol, "mark")
    return path.with_name(f"{path.stem}.fills.json")


def read_mark_fills(root: Path, symbol: str) -> tuple[int, ...]:
    """Open times (ms) of the mark minutes taken from the trade series, ascending."""
    path = fills_path(root, symbol)
    if not path.is_file():
        return ()
    return tuple(sorted(int(t) for t in json.loads(path.read_text(encoding="utf-8"))))


def _record_fills(root: Path, symbol: str, minutes: Iterable[int]) -> None:
    """Written before the page that holds the fills, so a crash between the two leaves a record
    of a fill that a resume repeats — never a filled minute with no record."""
    merged = sorted(set(read_mark_fills(root, symbol)) | set(minutes))
    path = fills_path(root, symbol)
    temporary = path.with_suffix(".partial")
    temporary.write_text(json.dumps(merged), encoding="utf-8")
    os.replace(temporary, path)


def _fill_mark_gaps(
    rows: list[list[float]], symbol: str, expected_first: int, trade: pd.DataFrame
) -> tuple[list[list[float]], list[int]]:
    """Fill each short hole in one mark page from the trade bars of the same minutes.

    Only holes *on the minute grid* are filled: a duplicate, a step back or an off-grid stamp is
    left for :func:`_fresh_minutes` to refuse, because those are a broken page, not a missing
    minute. The filled bar is the trade bar's OHLC with zero volume.
    """
    ts = [int(r[0]) for r in rows]
    if (
        not ts
        or ts[0] < expected_first
        or any(b <= a for a, b in itertools.pairwise(ts))
        or any((t - expected_first) % MINUTE_MS for t in ts)
    ):
        return rows, []
    present = set(ts)
    missing = [t for t in range(expected_first, ts[-1], MINUTE_MS) if t not in present]
    if not missing:
        return rows, []
    run_start, run_length, previous = missing[0], 0, missing[0] - MINUTE_MS
    for t in missing:
        if t == previous + MINUTE_MS:
            run_length += 1
        else:
            run_start, run_length = t, 1
        previous = t
        if run_length > MAX_MARK_FILL_MINUTES:
            raise CoverageError(
                f"{symbol} mark: missing minute run from {_at(run_start):%Y-%m-%d %H:%M} exceeds "
                f"the {MAX_MARK_FILL_MINUTES}-minute fill cap (ADR-0042)"
            )
    trade_ts = trade["ts"].to_numpy(dtype=np.int64)
    at = np.searchsorted(trade_ts, missing)
    filled: list[list[float]] = []
    for t, i in zip(missing, at.tolist(), strict=True):
        if i >= len(trade_ts) or int(trade_ts[i]) != t:
            raise CoverageError(
                f"{symbol} mark: missing minute {_at(t):%Y-%m-%d %H:%M} and no trade minute to "
                "fill it from (ADR-0042) — download the trade series first"
            )
        row = trade.iloc[i]
        filled.append(
            [float(t), float(row["open"]), float(row["high"]), float(row["low"]),
             float(row["close"]), 0.0]
        )  # fmt: skip
    return sorted([*rows, *filled], key=lambda r: int(r[0])), missing


def download_minutes(
    source: PerpSource,
    root: Path,
    symbol: str,
    start: datetime,
    end: datetime,
    *,
    kind: str,
) -> int:
    """Fetch ``[start, end)`` of one-minute bars, appending only what is missing.

    Returns the number of rows added. Re-running a finished download fetches nothing and makes
    no request at all, which is what makes a job of this length safe to restart by hand.
    """
    path = minute_path(root, symbol, kind)
    existing = read_minutes(path)
    _check_minute_grid(existing, symbol, kind, context=path.name)
    since = start
    if len(existing):
        last = int(existing["ts"].iloc[-1])
        if int(start.timestamp() * 1000) > last + MINUTE_MS:
            raise CoverageError(
                f"{symbol} {kind}: resuming at {start:%Y-%m-%d %H:%M} would leave a gap after "
                f"{_at(last):%Y-%m-%d %H:%M}. A summary built across a gap gives an "
                "exact-looking answer the data cannot support"
            )
        since = max(start, _at(last + MINUTE_MS))
    if since >= end:
        return 0

    path.parent.mkdir(parents=True, exist_ok=True)
    expected = int(since.timestamp() * 1000)
    until = int(end.timestamp() * 1000)
    parts = sorted(path.parent.glob(f"{path.stem}.part-*.parquet"))
    next_part = len(parts) + 1
    trade: pd.DataFrame | None = None
    for rows in source.iter_raw_pages(symbol, "1m", since, end, kind=kind):
        if kind == "mark":
            if trade is None:  # read once, and only for marks: trade gaps are never filled
                trade = read_minutes(minute_path(root, symbol, "trade"))
            rows, filled = _fill_mark_gaps(rows, symbol, expected, trade)
            if filled:
                _record_fills(root, symbol, filled)
        fresh = _fresh_minutes(rows, symbol, kind, expected_first=expected)
        if not len(fresh):
            continue
        target = (
            path
            if not path.exists()
            else path.with_name(f"{path.stem}.part-{next_part:06d}.parquet")
        )
        temporary = target.with_suffix(".partial")
        fresh.to_parquet(temporary, index=False)
        os.replace(temporary, target)
        if target != path:
            next_part += 1
        expected = int(fresh["ts"].iloc[-1]) + MINUTE_MS
        if expected >= until:
            break
    if expected < until:
        raise CoverageError(
            f"{symbol} {kind}: download stopped at {_at(expected - MINUTE_MS):%Y-%m-%d %H:%M} "
            f"before requested end {end:%Y-%m-%d %H:%M}"
        )
    return (expected - int(since.timestamp() * 1000)) // MINUTE_MS


def write_perp_manifest(root: Path, source_id: str) -> Manifest:
    """Checksum and date-stamp everything downloaded into ``root``.

    The spot in-sample parquet has no manifest and no checksum; the only integrity record in the
    project is the holdout lock. Perpetual data must not repeat that, because it carries two
    series a backtest cannot reconstruct from price — mark and funding — and a silently truncated
    window in either changes every result without changing any error message.

    Coverage is recorded per file rather than per instrument, so a partial download is visible
    as a short range instead of being mistaken for a complete one.
    """
    files = sorted(p for p in root.glob("*.parquet"))
    coverage: dict[str, str] = {}
    for path in files:
        frame = read_minutes(path)
        if len(frame):
            first, last = int(frame["ts"].min()), int(frame["ts"].max())
            coverage[path.name] = f"{_at(first):%Y-%m-%d}/{_at(last):%Y-%m-%d}"
    return write_manifest(root / "manifest.json", files, source_id, coverage)


def _timestamp_ms(at: datetime | int) -> int:
    if isinstance(at, datetime):
        aware = at if at.tzinfo is not None else at.replace(tzinfo=UTC)
        return int(aware.timestamp() * 1000)
    return int(at)


def _cuts(
    bar_open: datetime, minutes: int, funding_times: Iterable[datetime | int] | None = None
) -> list[int]:
    """Minute offsets inside one bar at which a funding settlement falls.

    A settlement is placed at its *nearest* minute, the rule the replay and the kernel use to find
    its segment: Binance stamps settlements a few milliseconds off the boundary (08:00:00.005),
    and a cut that required an exact minute dropped every one of them.
    """
    if funding_times is not None:
        start = int(bar_open.timestamp() * 1000)
        offsets = set()
        for at in funding_times:
            offset = round((_timestamp_ms(at) - start) / MINUTE_MS)
            if 0 <= offset < minutes:
                offsets.add(offset)
        return sorted(offsets)
    return [
        offset
        for offset in range(minutes)
        if (at := bar_open + timedelta(minutes=offset)).hour in FUNDING_HOURS and at.minute == 0
    ]


def rebuild_summaries(
    root: Path,
    symbol: str,
    timeframe: str,
    *,
    kind: str,
    funding_times: Iterable[datetime | int] | None = None,
) -> list[SegmentedPath]:
    """Rebuild one contract's per-bar segmented paths from the stored minutes.

    Derived, never the source of truth: changing ``timeframe`` re-cuts every bar, and the stored
    minutes are what make that possible without re-fetching.
    """
    frame = read_minutes(minute_path(root, symbol, kind))
    if not len(frame):
        raise CoverageError(f"{symbol} {kind}: no minute data to summarize")
    ts = frame["ts"].to_numpy(dtype=np.int64)
    lows = frame["low"].to_numpy(dtype=np.float64)
    highs = frame["high"].to_numpy(dtype=np.float64)

    span = int(timeframe_delta(timeframe).total_seconds() * 1000)
    per_bar = span // MINUTE_MS
    first = int(ts[0]) - int(ts[0]) % span  # align to the timeframe grid
    funding_stamps = tuple(funding_times) if funding_times is not None else None
    bars: list[SegmentedPath] = []
    for bar_start in range(first, int(ts[-1]) + 1, span):
        lo = int(np.searchsorted(ts, bar_start, side="left"))
        hi = int(np.searchsorted(ts, bar_start + span, side="left"))
        offsets = ((ts[lo:hi] - bar_start) // MINUTE_MS).astype(int)
        if len(offsets) != per_bar:
            raise CoverageError(
                f"{symbol} {kind}: the bar opening {_at(bar_start):%Y-%m-%d %H:%M} has "
                f"{len(offsets)} of {per_bar} minutes — refusing rather than filling the gap"
            )
        bars.append(
            segment_bar(
                offsets.tolist(),
                lows[lo:hi].tolist(),
                highs[lo:hi].tolist(),
                cuts=_cuts(_at(bar_start), per_bar, funding_stamps),
            )
        )
    return bars
