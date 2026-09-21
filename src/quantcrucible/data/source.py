"""DataSource protocol (Architecture §6.1). Free sources only (A7)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal, Protocol

from quantcrucible.core.strategy.base import Bars

_UNITS = {"m": timedelta(minutes=1), "h": timedelta(hours=1), "d": timedelta(days=1)}


def timeframe_delta(timeframe: str) -> timedelta:
    """'15m' → 15 minutes, '4h' → 4 hours, '1d' → 1 day."""
    try:
        return int(timeframe[:-1]) * _UNITS[timeframe[-1]]
    except (KeyError, ValueError):
        raise ValueError(f"unsupported timeframe {timeframe!r}") from None


class DataSource(Protocol):
    def bars(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> Bars:
        """Closed bars whose CLOSE time is in [start, end]."""
        ...

    def is_continuous(self) -> bool:
        """Futures already rolled into a continuous series?"""
        ...

    def adjustment(self) -> Literal["none", "ratio", "difference"]: ...
