"""Intrabar path summary (P3-07, ADR-0032) — first touch without shipping minute bars.

Gate ④ runs up to 200 configurations per candidate, each a full backtest inside a sandbox whose
only input is a serialized payload. So the minute series never enters the sandbox: each bar
carries a *monotone path summary* instead.

The summary is the time-ordered sequence of running-minimum and running-maximum breakpoints. It
is a sufficient statistic for first touch of any price level in either direction — a level below
the open is first touched exactly when the running minimum first drops below it — and it
compresses a day's 1,440 minutes to ~87 breakpoints, measured.

**A whole-bar summary is not enough on its own** (P3-16). Funding leaves the isolated wallet at
fixed settlement times, which moves the liquidation price *inside* the bar. A level that changes
part-way through cannot be answered by one monotone sequence, so a bar is cut into segments at
its settlements and each segment carries its own summary.

The reference here is a brute-force scan over the minute series, never a value the
implementation produced.
"""

from __future__ import annotations

import numpy as np
import pytest

from quantcrucible.core.path_summary import PathSummary, segment_bar, summarize_path


def brute_force_first_touch(prices: list[float], level: float, above: bool) -> int | None:
    """The definition: the first index at or beyond ``level``. Slow, obviously correct."""
    for i, p in enumerate(prices):
        if (above and p >= level) or (not above and p <= level):
            return i
    return None


def _walk(n: int, seed: int) -> list[float]:
    rng = np.random.default_rng(seed)
    return [float(x) for x in 100 * np.cumprod(1 + rng.normal(0, 0.001, n))]


def test_first_touch_matches_a_brute_force_minute_scan() -> None:
    """Property test over random paths and levels, against the definition."""
    for seed in range(12):
        prices = _walk(240, seed)
        summary = summarize_path(prices)
        lo, hi = min(prices), max(prices)
        for level in np.linspace(lo * 0.999, hi * 1.001, 25):
            for above in (True, False):
                expected = brute_force_first_touch(prices, float(level), above)
                got = summary.first_touch(float(level), above=above)
                assert got == expected, (seed, level, above, got, expected)


def test_a_level_never_reached_returns_none() -> None:
    summary = summarize_path([100.0, 101.0, 100.5, 99.5])
    assert summary.first_touch(200.0, above=True) is None
    assert summary.first_touch(1.0, above=False) is None


def test_the_open_itself_counts_as_touched() -> None:
    summary = summarize_path([100.0, 101.0])
    assert summary.first_touch(100.0, above=True) == 0
    assert summary.first_touch(100.0, above=False) == 0


def test_the_summary_is_much_smaller_than_the_minute_series() -> None:
    """The whole reason it exists: a day of minutes must fit in a sandbox payload."""
    prices = _walk(1_440, seed=99)
    summary = summarize_path(prices)
    assert len(summary.points) < len(prices) // 10


def test_a_monotone_path_keeps_only_its_endpoints_on_one_side() -> None:
    rising = [100.0 + i for i in range(50)]
    summary = summarize_path(rising)
    # every bar is a new running maximum, but the running minimum never moves after the open
    assert [i for i, _ in summary.lows] == [0]
    assert len(summary.highs) == 50


def test_which_of_two_levels_is_touched_first() -> None:
    """The stop-or-liquidation question, which is the reason for all of this."""
    prices = [100.0, 99.0, 98.0, 101.0, 97.0]
    summary = summarize_path(prices)
    stop, liquidation = 98.5, 97.5
    assert summary.first_touch(stop, above=False) == 2
    assert summary.first_touch(liquidation, above=False) == 4


def test_a_simultaneous_touch_is_reported_as_ambiguous() -> None:
    """Both levels first reached in the same minute: the order inside it is unknown, so the
    caller must be told rather than handed a guess."""
    prices = [100.0, 96.0]  # one step through both 98.5 and 97.5
    summary = summarize_path(prices)
    assert summary.ambiguous(98.5, 97.5, above=False)
    assert not summarize_path([100.0, 98.0, 96.0]).ambiguous(98.5, 97.5, above=False)


def test_an_empty_path_is_refused() -> None:
    with pytest.raises(ValueError, match="at least one price"):
        summarize_path([])


def test_the_summary_round_trips_through_json() -> None:
    """It crosses the sandbox boundary as data, so it must serialize losslessly."""
    prices = _walk(200, seed=5)
    summary = summarize_path(prices)
    restored = PathSummary.from_dict(summary.as_dict())
    assert restored.points == summary.points
    for level in (min(prices), max(prices), sum(prices) / len(prices)):
        for above in (True, False):
            assert restored.first_touch(level, above=above) == summary.first_touch(
                level, above=above
            )


# ── segments cut at funding settlements (P3-16) ───────────────────────────────────────


def flat(path: list[float]) -> tuple[list[int], list[float], list[float]]:
    """A degenerate minute series: each minute's low, high and close are the same number."""
    return list(range(len(path))), list(path), list(path)


def test_a_funding_cut_distinguishes_paths_a_whole_bar_summary_cannot() -> None:
    """The reason segments exist.

    These two paths have byte-identical whole-bar summaries. If the liquidation price only
    becomes 95 after minute 3 — because funding just left the wallet — then the first reaches it
    and the second does not. One monotone sequence over the whole bar cannot tell them apart, and
    worse, it answers minute 1 for both: the minute where 80 printed, when 95 was not yet in
    force and the level actually in force (50) was never reached at all.
    """
    a, b = [100.0, 80.0, 110.0, 100.0, 90.0], [100.0, 80.0, 110.0, 100.0, 100.0]
    whole_a, whole_b = summarize_path(a), summarize_path(b)
    assert whole_a.points == whole_b.points  # indistinguishable, and both answer minute 1
    assert whole_a.first_touch(95.0, above=False) == whole_b.first_touch(95.0, above=False) == 1

    levels = [50.0, 95.0]  # liquidation before and after the settlement at minute 3
    seg_a = segment_bar(*flat(a), cuts=[3])
    seg_b = segment_bar(*flat(b), cuts=[3])
    assert seg_a.first_touch_stepwise(levels, above=False) == 4
    assert seg_b.first_touch_stepwise(levels, above=False) is None


def test_a_segment_boundary_does_not_shift_a_constant_level_answer() -> None:
    """Cutting a bar must not change what it says about a level that never moves."""
    path = [100.0, 99.0, 98.0, 101.0, 97.0]
    for cuts in ([], [2], [1, 3], [1, 2, 3, 4]):
        segmented = segment_bar(*flat(path), cuts=cuts)
        for level in (98.5, 97.5, 200.0, 1.0):
            for above in (True, False):
                assert segmented.first_touch(level, above=above) == summarize_path(
                    path
                ).first_touch(level, above=above), (cuts, level, above)


def test_lows_come_from_minute_lows_and_highs_from_minute_highs() -> None:
    """A minute is a bar, not a point. Summarising its close alone hides the wick that is
    exactly what reaches a stop or a liquidation first."""
    minutes = [0, 1, 2]
    lows = [100.0, 90.0, 99.0]  # minute 1 wicks to 90 ...
    highs = [100.0, 108.0, 99.0]  # ... and to 108, while its close is somewhere between
    bar = segment_bar(minutes, lows, highs, cuts=[])
    assert bar.first_touch(92.0, above=False) == 1
    assert bar.first_touch(105.0, above=True) == 1


def test_a_missing_minute_is_refused_rather_than_filled() -> None:
    """INV-94 at the path level: a gap means the first-touch answer is unknown, not zero.
    Filling it and then calling the result exact is the failure this refuses."""
    with pytest.raises(ValueError, match="minute 2 is missing"):
        segment_bar([0, 1, 3], [100.0, 99.0, 98.0], [100.0, 99.0, 98.0], cuts=[])


def test_a_cut_outside_the_bar_is_refused() -> None:
    with pytest.raises(ValueError, match="cut"):
        segment_bar(*flat([100.0, 99.0]), cuts=[5])


def test_stepwise_needs_one_level_per_segment() -> None:
    """Silently reusing the last level would make a missing settlement invisible."""
    bar = segment_bar(*flat([100.0, 99.0, 98.0, 97.0]), cuts=[2])
    with pytest.raises(ValueError, match="2 segments"):
        bar.first_touch_stepwise([95.0], above=False)


def test_a_segmented_bar_round_trips_through_json() -> None:
    bar = segment_bar(*flat(_walk(200, seed=11)), cuts=[60, 120])
    restored = type(bar).from_dict(bar.as_dict())
    assert restored.starts == bar.starts
    assert [s.points for s in restored.segments] == [s.points for s in bar.segments]
