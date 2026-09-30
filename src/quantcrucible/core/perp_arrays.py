"""A perpetual bundle as flat arrays, for the kernel replay (P3-49, ADR-0038).

:class:`~quantcrucible.core.perp_inputs.PerpBundle` holds tuples of per-bar path objects; a
compiled replay reads arrays. This module packs one bundle — and nothing is computed here that
the Python replay (``joint_account.replay_signals``) does not compute the same way — into
compressed sparse rows:

- a bar's segments are ``seg[bar_ptr[b]:bar_ptr[b+1]]``, each starting at minute ``seg_start``;
- a segment's running lows are ``low_ix/low_px[low_ptr[s]:low_ptr[s+1]]``, its highs likewise;
- a bar's funding settlements are ``fund_*[fund_ptr[b]:fund_ptr[b+1]]``, in time order, each at
  the minute offset the replay derives from its timestamp.

The replay raises on two inconsistencies, but only when it reaches the bar with a position
open: trade and mark paths cut at different minutes, or a settlement at a minute where no
segment starts. Packing therefore flags such a bar (``bad``) instead of refusing the bundle, so
the kernel can fail exactly where the replay would.

``core`` may not import numba (INV-111); the arrays are plain numpy.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from quantcrucible.core.path_summary import SegmentedPath
from quantcrucible.core.perp_inputs import PerpBundle
from quantcrucible.core.strategy.base import Bars

IntArray = npt.NDArray[np.int64]
FloatArray = npt.NDArray[np.float64]
MINUTE_NS = 60_000_000_000
_UNIT_MINUTES = {"m": 1, "h": 60, "d": 1440}


@dataclass(frozen=True, slots=True)
class PathArrays:
    """Every bar's :class:`SegmentedPath`, in CSR form."""

    bar_ptr: IntArray  # [bars + 1]
    seg_start: IntArray  # [segments]
    low_ptr: IntArray  # [segments + 1]
    low_ix: IntArray
    low_px: FloatArray
    high_ptr: IntArray  # [segments + 1]
    high_ix: IntArray
    high_px: FloatArray

    def segment(self, s: int, *, above: bool) -> tuple[IntArray, FloatArray]:
        """One segment's running highs (``above``) or lows, as (minute, price) arrays."""
        if above:
            a, b = self.high_ptr[s], self.high_ptr[s + 1]
            return self.high_ix[a:b], self.high_px[a:b]
        a, b = self.low_ptr[s], self.low_ptr[s + 1]
        return self.low_ix[a:b], self.low_px[a:b]


def pack_paths(paths: Sequence[SegmentedPath]) -> PathArrays:
    bar_ptr, seg_start = [0], []
    low_ptr, low_ix, low_px = [0], [], []
    high_ptr, high_ix, high_px = [0], [], []
    for path in paths:
        for start, summary in zip(path.starts, path.segments, strict=True):
            seg_start.append(start)
            for i, p in summary.lows:
                low_ix.append(i)
                low_px.append(p)
            for i, p in summary.highs:
                high_ix.append(i)
                high_px.append(p)
            low_ptr.append(len(low_ix))
            high_ptr.append(len(high_ix))
        bar_ptr.append(len(seg_start))
    return PathArrays(
        np.asarray(bar_ptr, dtype=np.int64), np.asarray(seg_start, dtype=np.int64),
        np.asarray(low_ptr, dtype=np.int64), np.asarray(low_ix, dtype=np.int64),
        np.asarray(low_px, dtype=np.float64), np.asarray(high_ptr, dtype=np.int64),
        np.asarray(high_ix, dtype=np.int64), np.asarray(high_px, dtype=np.float64),
    )  # fmt: skip


def first_touch(arrays: PathArrays, s: int, level: float, *, above: bool) -> int | None:
    """``PathSummary.first_touch`` of segment ``s`` (minute within the segment), on the arrays:
    the same ``bisect_left`` — on the negated lows for a touch from above."""
    ix, px = arrays.segment(s, above=above)
    lo, hi = 0, len(px)
    while lo < hi:
        mid = (lo + hi) // 2
        if (px[mid] < level) if above else (-px[mid] < -level):
            lo = mid + 1
        else:
            hi = mid
    return None if lo >= len(px) else int(ix[lo])


@dataclass(frozen=True, slots=True)
class BracketArrays:
    """A leverage-tier table, ascending by cap — ``BracketTable.from_rows``'s order."""

    cap: FloatArray
    max_leverage: IntArray
    mmr: FloatArray
    amount: FloatArray


def pack_brackets(bundle: PerpBundle) -> BracketArrays:
    rows = sorted(bundle.brackets, key=lambda r: float(r["cap"]))
    return BracketArrays(
        np.asarray([float(r["cap"]) for r in rows], dtype=np.float64),
        np.asarray([int(r["max_leverage"]) for r in rows], dtype=np.int64),
        np.asarray([float(r["mmr"]) for r in rows], dtype=np.float64),
        np.asarray([float(r["amount"]) for r in rows], dtype=np.float64),
    )


@dataclass(frozen=True, slots=True)
class PerpArrays:
    mark_close: FloatArray
    mark: PathArrays
    trade: PathArrays
    fund_ptr: IntArray  # [bars + 1]
    fund_minute: IntArray
    fund_rate: FloatArray
    fund_mark: FloatArray
    bad: npt.NDArray[np.int8]  # 1 where the replay raises if a position is open at that bar
    brackets: BracketArrays


def period_minutes(timeframe: str) -> int:
    return int(timeframe[:-1]) * _UNIT_MINUTES[timeframe[-1]]


def pack_bundle(bundle: PerpBundle, bars: Bars) -> PerpArrays:
    """The bracket-mode replay's inputs for one contract. ``bars`` are the trade bars."""
    if bundle.trade_paths is None:
        raise ValueError("bracket replay requires trade-minute paths")
    if len(bundle) != len(bars):
        raise ValueError(f"{bars.symbol}: bundle and bars differ in length")
    n = len(bars)
    step = period_minutes(bars.timeframe)
    fund_ptr = [0]
    minutes: list[int] = []
    rates: list[float] = []
    at_marks: list[float] = []
    bad = np.zeros(n, dtype=np.int8)
    for b in range(n):
        mark_path, trade_path = bundle.paths[b], bundle.trade_paths[b]
        close_ns = int(bars.ts[b].astype("datetime64[ns]").astype("int64"))
        open_ns = close_ns - step * MINUTE_NS
        seen: set[int] = set()
        for row in bundle.funding_at(b):  # time order, as the replay reads it
            minute = round((float(row[1]) - open_ns) / MINUTE_NS)
            seen.add(minute)
            minutes.append(minute)
            rates.append(float(row[2]))
            at_marks.append(float(row[3]))
        if mark_path.starts != trade_path.starts or seen - set(mark_path.starts):
            bad[b] = 1
        fund_ptr.append(len(minutes))
    return PerpArrays(
        mark_close=np.ascontiguousarray(bundle.marks.close, dtype=np.float64),
        mark=pack_paths(bundle.paths),
        trade=pack_paths(bundle.trade_paths),
        fund_ptr=np.asarray(fund_ptr, dtype=np.int64),
        fund_minute=np.asarray(minutes, dtype=np.int64),
        fund_rate=np.asarray(rates, dtype=np.float64),
        fund_mark=np.asarray(at_marks, dtype=np.float64),
        bad=bad,
        brackets=pack_brackets(bundle),
    )
