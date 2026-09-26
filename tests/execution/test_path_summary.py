"""Intrabar path summary (P3-07, ADR-0032) — first touch without shipping minute bars.

Gate ④ runs up to 200 configurations per candidate, each a full backtest inside a sandbox whose
only input is a serialized payload. Going from ~1,800 daily bars to ~3.5M one-minute bars per
contract is a factor of ~1,900, and ~200x again per candidate. So the minute series never enters
the sandbox: each daily bar carries a *monotone path summary* instead.

The summary is the time-ordered sequence of running-minimum and running-maximum breakpoints. It
is a sufficient statistic for first touch of any price level in either direction — a level below
the open is first touched exactly when the running minimum first drops below it — and it
compresses a day's 1,440 minutes to a few dozen points.

The reference here is a brute-force scan over the minute series, never a value the
implementation produced.
"""

from __future__ import annotations

import numpy as np
import pytest

from quantcrucible.execution.path_summary import PathSummary, summarize_path


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
