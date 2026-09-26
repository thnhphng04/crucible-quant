"""Monotone intrabar path summary (Architecture §3.5, ADR-0032, P3-07, P3-16).

A perpetual backtest resolves stops and liquidations inside the signal bar, which needs the
intrabar price path. Shipping that path is not an option: gate ④ runs up to 200 configurations
per candidate, each a full backtest inside a sandbox whose only input is a serialized payload,
and a day of one-minute marks is ~1,440 rows against one daily bar.

So each bar carries a summary instead: the time-ordered sequence of **running-minimum and
running-maximum breakpoints**. That sequence is a sufficient statistic for first touch of any
price level in either direction — a level below the open is first reached exactly when the
running minimum first drops below it — and it is ~87 breakpoints for a day, measured on
simulated minute paths (p95 132, and a strongly trending day pushes one side to 200–400).

It also makes the ambiguity rule exact rather than heuristic. When a stop and a liquidation are
first reached in the same minute, their order inside that minute is genuinely unknown, and the
summary says so instead of handing the caller a guess.

**One summary per bar is not enough** (P3-16). Funding leaves the isolated wallet at fixed
settlement times, so the liquidation price moves *inside* the bar, and a monotone sequence
cannot answer a level that changes part-way through: two paths with identical summaries can
differ in whether they reach a threshold that only comes into force late. So a bar is cut into
segments at its settlements — :class:`SegmentedPath` — and each segment carries its own summary.

This module lives in ``core`` rather than ``execution`` because the summaries are built at fetch
time, and ``data`` may not import ``execution`` (import-linter). Nothing here reaches upward.
"""

from __future__ import annotations

import bisect
from collections.abc import Sequence
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


def summarize_minutes(lows: Sequence[float], highs: Sequence[float]) -> PathSummary:
    """Summarize a run of minutes, taking each minute's own low and high.

    A minute is a bar, not a point. Summarizing its close alone hides the wick, which is exactly
    what reaches a stop or a liquidation first — so ``lows`` drives the running minimum and
    ``highs`` the running maximum. That is conservative for touch detection in both directions,
    and the monotone invariant still holds because each series is scanned on its own.
    """
    if not lows or len(lows) != len(highs):
        raise ValueError("a path summary needs at least one minute, with a low for every high")
    running_low: list[Breakpoint] = [(0, lows[0])]
    running_high: list[Breakpoint] = [(0, highs[0])]
    for i in range(1, len(lows)):
        if lows[i] < running_low[-1][1]:
            running_low.append((i, lows[i]))
        if highs[i] > running_high[-1][1]:
            running_high.append((i, highs[i]))
    return PathSummary(lows=tuple(running_low), highs=tuple(running_high))


@dataclass(frozen=True, slots=True)
class SegmentedPath:
    """One bar's intrabar path, cut at the funding settlements inside it.

    ``starts[k]`` is the minute offset within the bar at which segment ``k`` begins, and
    ``segments[k]`` summarizes the minutes from there to the next start. Indices returned by the
    lookups below are absolute within the bar, not within a segment.
    """

    starts: tuple[int, ...]
    segments: tuple[PathSummary, ...]

    def first_touch(self, level: float, *, above: bool) -> int | None:
        """First minute at which the path reaches a level that holds for the whole bar."""
        for start, segment in zip(self.starts, self.segments, strict=True):
            hit = segment.first_touch(level, above=above)
            if hit is not None:
                return start + hit
        return None

    def first_touch_stepwise(self, levels: Sequence[float], *, above: bool) -> int | None:
        """First minute at which the path reaches the level **in force at that minute**.

        One level per segment, in order. Reusing the last level for a missing one would make a
        missed settlement invisible, so a length mismatch is refused.
        """
        if len(levels) != len(self.segments):
            raise ValueError(
                f"first_touch_stepwise needs one level per segment: got {len(levels)} for "
                f"{len(self.segments)} segments"
            )
        for start, segment, level in zip(self.starts, self.segments, levels, strict=True):
            hit = segment.first_touch(level, above=above)
            if hit is not None:
                return start + hit
        return None

    def as_dict(self) -> dict[str, Any]:
        return {
            "starts": list(self.starts),
            "segments": [s.as_dict() for s in self.segments],
        }

    @classmethod
    def from_dict(cls, d: Any) -> SegmentedPath:
        return cls(
            starts=tuple(int(i) for i in d["starts"]),
            segments=tuple(PathSummary.from_dict(s) for s in d["segments"]),
        )


def segment_bar(
    minutes: Sequence[int],
    lows: Sequence[float],
    highs: Sequence[float],
    cuts: Sequence[int],
) -> SegmentedPath:
    """Build one bar's segmented path from its minute series.

    ``minutes`` are offsets within the bar and must be contiguous from 0: a gap means the
    first-touch answer is unknown, and filling it would make an inexact answer look exact
    (INV-94). ``cuts`` are the offsets at which a new segment begins — the bar's funding
    settlements — and an empty ``cuts`` gives one segment, which behaves exactly like a single
    whole-bar summary.
    """
    if not minutes:
        raise ValueError("a bar needs at least one minute")
    if not (len(minutes) == len(lows) == len(highs)):
        raise ValueError("minutes, lows and highs must be the same length")
    for expected, got in enumerate(minutes):
        if got != expected:
            raise ValueError(
                f"minute {expected} is missing from the bar's path (found {got}): refusing "
                "rather than filling the gap, because a filled path gives an exact-looking "
                "answer to a question the data cannot answer"
            )
    bounds = sorted({0, *cuts})
    if bounds[-1] >= len(minutes) or bounds[0] < 0:
        raise ValueError(f"cut {bounds[-1]} is outside a bar of {len(minutes)} minutes")
    ends = [*bounds[1:], len(minutes)]
    return SegmentedPath(
        starts=tuple(bounds),
        segments=tuple(
            summarize_minutes(lows[a:b], highs[a:b]) for a, b in zip(bounds, ends, strict=True)
        ),
    )
