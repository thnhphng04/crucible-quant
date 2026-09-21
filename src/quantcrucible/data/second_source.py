"""In-sample bars from a second free source, for gate ⑥′ (Architecture §6.1 "two-tier data",
§3.2 row ⑥′, ADR-0015).

Only the research period is ever downloaded and stored: requests end before the holdout starts,
and any bar closing inside a holdout range is dropped before writing (a second vendor's
holdout-period prices are holdout information too, P6). The files are read back through
:class:`~quantcrucible.data.store.ResearchStore`, which refuses the holdout again.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import numpy as np

from quantcrucible.data.source import DataSource
from quantcrucible.data.store import DateRange, _day_start, file_name, write_bars


def download_in_sample(
    source: DataSource,
    symbols: Sequence[str],
    timeframe: str,
    start: date,
    holdout_ranges: Iterable[DateRange],
    out_dir: Path,
) -> dict[str, int]:
    """Fetch ``symbols`` from ``start`` up to the first holdout start; write them to ``out_dir``.
    Returns the number of bars written per symbol."""
    ranges = sorted(holdout_ranges)
    if not ranges:
        raise ValueError("a holdout range is required: the research period ends where it starts")
    first_holdout = ranges[0][0]
    begin = datetime.combine(start, time(), tzinfo=UTC)
    stop = datetime.combine(first_holdout, time(), tzinfo=UTC) - timedelta(microseconds=1)
    written: dict[str, int] = {}
    for symbol in symbols:
        bars = source.bars(symbol, timeframe, begin, stop)
        keep = np.ones(len(bars), dtype=bool)
        for lo, hi in ranges:  # belt and braces: nothing inside any holdout range
            keep &= ~((bars.ts >= _day_start(lo)) & (bars.ts < _day_start(hi)))
        idx = np.flatnonzero(keep)
        clean = bars.slice(int(idx[0]), int(idx[-1]) + 1) if len(idx) else bars.slice(0, 0)
        if len(clean) != int(keep.sum()):
            raise ValueError(f"{symbol}: a holdout range falls inside the research period")
        write_bars(out_dir / file_name(symbol, timeframe), clean)
        written[symbol] = len(clean)
    return written
