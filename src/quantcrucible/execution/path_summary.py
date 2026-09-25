"""Monotone intrabar path summary (Architecture §3.5, ADR-0032, P3-07).

A perpetual backtest resolves stops and liquidations inside the signal bar, which needs the
intrabar price path. Shipping that path is not an option: gate ④ runs up to 200 configurations
per candidate, each a full backtest inside a sandbox whose only input is a serialized payload,
and a day of one-minute marks is ~1,440 rows against one daily bar.

So each bar carries a summary instead: the time-ordered sequence of **running-minimum and
running-maximum breakpoints**. That sequence is a sufficient statistic for first touch of any
price level in either direction — a level below the open is first reached exactly when the
running minimum first drops below it — and it is typically a few dozen points for a day.

It also makes the ambiguity rule exact rather than heuristic. When a stop and a liquidation are
first reached in the same minute, their order inside that minute is genuinely unknown, and the
summary says so instead of handing the caller a guess.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from typing import Any

# (index into the original path, price at that index)
Breakpoint = tuple[int, float]


@dataclass(frozen=True, slots=True)
class PathSummary:
    """Running extremes of one bar's intrabar path.

    ``lows`` is non-increasing in price and ``highs`` non-decreasing, both strictly increasing in
    index. The first entry of each is the path's opening price.
    """

    lows: tuple[Breakpoint, ...]
    highs: tuple[Breakpoint, ...]

    @property
    def points(self) -> tuple[Breakpoint, ...]:
        """Every breakpoint, for size accounting. Both series start at the open."""
        return self.lows + self.highs

    def first_touch(self, level: float, *, above: bool) -> int | None:
        """Index at which the path first reaches ``level``, or ``None`` if it never does.

        ``above`` asks for the first index at or above ``level``; otherwise at or below it.
        """
        series = self.highs if above else self.lows
        prices = [p for _, p in series]
        if above:
            # highs ascend: find the first entry >= level
            pos = bisect.bisect_left(prices, level)
        else:
            # lows descend, so search the negated series, which ascends
            pos = bisect.bisect_left([-p for p in prices], -level)
        if pos >= len(series):
            return None
        return series[pos][0]

    def ambiguous(self, first: float, second: float, *, above: bool) -> bool:
        """Whether both levels are first reached at the same index.

        Inside one minute the order is unknown, so a caller resolving a stop against a
        liquidation must take the worse outcome and flag it rather than pick.
        """
        a = self.first_touch(first, above=above)
        b = self.first_touch(second, above=above)
        return a is not None and a == b

    def as_dict(self) -> dict[str, Any]:
        return {
            "lows": [[i, p] for i, p in self.lows],
            "highs": [[i, p] for i, p in self.highs],
        }

    @classmethod
    def from_dict(cls, d: Any) -> PathSummary:
        return cls(
            lows=tuple((int(i), float(p)) for i, p in d["lows"]),
            highs=tuple((int(i), float(p)) for i, p in d["highs"]),
        )


def summarize_path(prices: list[float]) -> PathSummary:
    """Compress an intrabar price path to its running extremes.

    ``prices`` is in time order — typically one bar's one-minute marks, but the summary does not
    care what the sampling interval is.
    """
    if not prices:
        raise ValueError("a path summary needs at least one price")
    lows: list[Breakpoint] = [(0, prices[0])]
    highs: list[Breakpoint] = [(0, prices[0])]
    for i, price in enumerate(prices[1:], start=1):
        if price < lows[-1][1]:
            lows.append((i, price))
        if price > highs[-1][1]:
            highs.append((i, price))
    return PathSummary(lows=tuple(lows), highs=tuple(highs))
