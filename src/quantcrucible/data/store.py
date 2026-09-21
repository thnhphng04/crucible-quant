"""Parquet storage for bars, and the research-side reader (Architecture §4.2, P6).

:class:`ResearchStore` reads only the in-sample folder and refuses any range that overlaps a
holdout range — defence in depth on top of the physical split done by ``holdout_split``.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime, time
from pathlib import Path

import numpy as np
import pandas as pd

from quantcrucible.core.strategy.base import Bars


class HoldoutAccessError(PermissionError):
    """Research code asked for data inside a holdout range."""


DateRange = tuple[date, date]  # inclusive start, exclusive end


def parse_range(text: str) -> DateRange:
    """'2025-09-21/2026-09-21' → (date, date)."""
    start, end = text.split("/")
    return date.fromisoformat(start), date.fromisoformat(end)


def file_name(symbol: str, timeframe: str) -> str:
    return f"{symbol.replace('/', '-')}_{timeframe}.parquet"


def write_bars(path: Path, bars: Bars) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(
        {
            "ts": bars.ts,
            "open": bars.open,
            "high": bars.high,
            "low": bars.low,
            "close": bars.close,
            "volume": bars.volume,
        }
    )
    frame.to_parquet(path, index=False)


def read_bars(path: Path, symbol: str, timeframe: str) -> Bars:
    frame = pd.read_parquet(path)
    ts = frame["ts"].to_numpy(dtype="datetime64[ns]")
    cols = {
        c: frame[c].to_numpy(dtype=np.float64) for c in ("open", "high", "low", "close", "volume")
    }
    return Bars(symbol, timeframe, ts, **cols)


def _day_start(d: date) -> np.datetime64:
    return np.datetime64(datetime.combine(d, time(), tzinfo=UTC).replace(tzinfo=None), "ns")


class ResearchStore:
    """In-sample bars for research. Never returns a bar inside a holdout range."""

    def __init__(self, root: Path, holdout_ranges: Iterable[DateRange]) -> None:
        self.root = root
        self.holdout_ranges = tuple(holdout_ranges)

    def _check(self, lo: np.datetime64, hi: np.datetime64) -> None:
        for start, end in self.holdout_ranges:
            if lo < _day_start(end) and hi >= _day_start(start):
                raise HoldoutAccessError(
                    f"requested bars overlap the holdout range {start}/{end} (P6)"
                )

    def bars(
        self,
        symbol: str,
        timeframe: str,
        start: date | None = None,
        end: date | None = None,
    ) -> Bars:
        """Bars with close time in [start, end); an omitted bound means the file's own extent.

        Refused if the requested range, or the file itself, touches a holdout range.
        """
        if start and end and not start < end:
            raise ValueError("start must be before end")
        if start or end:
            lo_req = _day_start(start) if start else np.datetime64("1970-01-01", "ns")
            hi_req = _day_start(end) - np.timedelta64(1, "ns") if end else None
            if hi_req is not None:
                self._check(lo_req, hi_req)
        bars = read_bars(self.root / file_name(symbol, timeframe), symbol, timeframe)
        if not len(bars):
            return bars
        self._check(bars.ts[0], bars.ts[-1])  # the file itself must be clean
        lo = _day_start(start) if start else bars.ts[0]
        hi = _day_start(end) - np.timedelta64(1, "ns") if end else bars.ts[-1]
        mask = (bars.ts >= lo) & (bars.ts <= hi)
        idx = np.flatnonzero(mask)
        return bars.slice(int(idx[0]), int(idx[-1]) + 1) if len(idx) else bars.slice(0, 0)
