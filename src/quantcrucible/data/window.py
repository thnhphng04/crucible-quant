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

from datetime import date, timedelta
from typing import Any

from quantcrucible.config.schema import MIN_HOLDOUT_DAYS

# The lock tag of a campaign opened under ADR-0051: its IS is exactly [data.start, cut), checked
# against the data when it opened. A lock without it may have read a legacy store whole.
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
