"""The data window and where it is cut into IS and holdout (P3-64, ADR-0051, D18, D28).

A campaign's data is the window ``[research.data.start, end]``. Its holdout starts at
``research.data.holdout_start`` when that is set, else ``holdout_months`` before the end; the IS is
everything before. Every fetch and carve asks :func:`holdout_window`, so the legacy fetch, the
dataset registry and a campaign's admission cannot disagree about the cut.

A holdout must also be **clean**: no day of it may lie inside a period some recorded trial was
already searched on (:func:`unclean_holdout`). Spot and perpetual prices of one coin move
together, so the check ignores symbol and market.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, time, timedelta
from typing import Any

import numpy as np

from quantcrucible.config.schema import MIN_HOLDOUT_DAYS
from quantcrucible.core.perp_inputs import PerpBundle
from quantcrucible.core.strategy.base import Bars

# The lock tag of a campaign opened under ADR-0051: its IS is exactly [data.start, cut). A legacy
# store that starts earlier is cut at data.start when read (:func:`is_from`); a lock without the
# tag read the store whole, as before.
DATA_WINDOW_V1 = "v1"


def add_months(value: date, months: int) -> date:
    month_index = value.year * 12 + (value.month - 1) + months
    year, month = divmod(month_index, 12)
    month += 1
    for day in (value.day, 30, 29, 28):
        try:
            return date(year, month, day)
        except ValueError:
            continue
    raise AssertionError("unreachable")


def holdout_window(data: Any, end_day: date) -> tuple[date, date]:
    """``(holdout_start, holdout_end)`` of ``research.data`` ending on ``end_day``; the end is
    exclusive (``end_day + 1``), as the carve and the store read it."""
    cut: date | None = getattr(data, "holdout_start", None)
    start = cut if cut is not None else add_months(end_day, -int(data.holdout_months))
    if cut is not None and cut <= data.start:
        raise ValueError("research.data.holdout_start must be after research.data.start")
    if (end_day - start).days + 1 < MIN_HOLDOUT_DAYS:
        raise ValueError(
            f"the holdout {start}..{end_day} must last at least one month: move "
            "research.data.holdout_start earlier or research.data.end later"
        )
    return start, end_day + timedelta(days=1)


def is_from(
    start: date, bars: Mapping[str, Bars], bundles: Mapping[str, PerpBundle] | None = None
) -> tuple[dict[str, Bars], dict[str, PerpBundle]]:
    """The IS from ``start`` on: every bar whose close is at or after ``start`` 00:00 UTC (the
    store's convention), and each perpetual bundle cut at the same bar, so trade, mark, funding
    and paths stay aligned (:meth:`PerpBundle.slice`). A store starting later is left whole."""
    lo = np.datetime64(datetime.combine(start, time()), "ns")
    out_bars: dict[str, Bars] = {}
    out_bundles: dict[str, PerpBundle] = {}
    for symbol, series in bars.items():
        k = int(np.searchsorted(series.ts, lo, side="left"))
        out_bars[symbol] = series.slice(k, len(series))
        if bundles is not None and symbol in bundles:
            out_bundles[symbol] = bundles[symbol].slice(k, len(series))
    return out_bars, out_bundles


def unclean_holdout(latest_is_end: date | None, holdout_start: date) -> str | None:
    """Why a holdout starting on ``holdout_start`` is not clean, or ``None``.

    ``latest_is_end`` is the last day of any recorded trial's IS range (``trials.timerange``):
    research has already looked at that day, so a holdout may start the day after at the earliest.
    """
    if latest_is_end is None or holdout_start > latest_is_end:
        return None
    earliest = latest_is_end + timedelta(days=1)
    return (
        f"the holdout would start on {holdout_start}, inside data that recorded trials were "
        f"already searched on (up to {latest_is_end}): a holdout must start on {earliest} or "
        "later (D28)"
    )
