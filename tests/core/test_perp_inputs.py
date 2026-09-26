"""The perpetual input bundle and its one slicing rule (P3-18, ADR-0034) — INV-99.

A perpetual backtest needs four series that a trade bar cannot supply: the mark price it is
liquidated on, the funding it pays, the intrabar path that decides *when* within a bar a level is
reached, and the leverage brackets that set margin. They are carried together, because the one
failure mode that matters here is **desynchronisation**: a mark series cut one bar differently
from the trade series silently marks every position at the wrong price, and nothing raises.

So the contract is deliberately narrow:

- one timestamp convention — nanoseconds, UTC, labelled by bar **close** time, as `Bars` is;
- half-open windows `[start, stop)`, as `Bars.slice` is;
- `bar_ix` is an index into *this* bundle and is renumbered on every slice, so a sliced bundle
  cannot be read with an unsliced one's indices;
- slicing moves all four series or raises. There is no way to cut one of them.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from quantcrucible.core.path_summary import SegmentedPath, segment_bar
from quantcrucible.core.perp_inputs import (
    FUNDING_COLUMNS,
    PerpBundle,
    read_bundle,
    write_bundle,
)
from quantcrucible.core.strategy.base import Bars

BRACKETS = ({"cap": 1_000_000.0, "max_leverage": 100, "mmr": 0.004, "amount": 0.0},)


def marks(n: int, symbol: str = "BTC/USDT:USDT") -> Bars:
    day = np.datetime64("2021-01-02", "D")
    ts = (day + np.arange(n, dtype="timedelta64[D]")).astype("datetime64[ns]")
    price = np.full(n, 100.0)
    return Bars(symbol, "1d", ts, price, price + 1, price - 1, price, np.zeros(n))


def path(minutes: int = 6, cuts: list[int] | None = None) -> SegmentedPath:
    prices = [100.0] * minutes
    return segment_bar(list(range(minutes)), prices, prices, cuts=cuts or [])


def funding(rows: list[tuple[int, int, float, float]]) -> np.ndarray:
    return np.array(rows, dtype=np.float64)


def bundle(
    n: int = 4, funding_rows: list[tuple[int, int, float, float]] | None = None
) -> PerpBundle:
    m = marks(n)
    if funding_rows is None:
        funding_rows = [(k, int(m.ts[k].astype("int64")), 0.0001, 100.0) for k in range(n)]
    return PerpBundle(
        symbol="BTC/USDT:USDT",
        timeframe="1d",
        marks=m,
        funding=funding(funding_rows),
        paths=tuple(path() for _ in range(n)),
        brackets=BRACKETS,
    )


def test_the_funding_table_names_its_columns() -> None:
    """The order is part of the contract: it crosses the sandbox boundary as a bare array."""
    assert FUNDING_COLUMNS == ("bar_ix", "ts", "rate", "mark")


def test_a_bundle_holds_one_path_per_bar() -> None:
    b = bundle(4)
    assert len(b) == 4
    assert len(b.paths) == len(b.marks) == 4


def test_a_path_missing_for_one_bar_is_refused() -> None:
    """A short path list would silently shift every later bar's intrabar answer by one."""
    with pytest.raises(ValueError, match="one path per bar"):
        PerpBundle(
            symbol="BTC/USDT:USDT",
            timeframe="1d",
            marks=marks(4),
            funding=funding([(0, 0, 0.0, 100.0)]),
            paths=tuple(path() for _ in range(3)),
            brackets=BRACKETS,
        )


def test_an_empty_bracket_table_is_refused() -> None:
    with pytest.raises(ValueError, match="bracket"):
        PerpBundle(
            symbol="BTC/USDT:USDT",
            timeframe="1d",
            marks=marks(2),
            funding=funding([(0, 0, 0.0, 100.0)]),
            paths=(path(), path()),
            brackets=(),
        )


def test_a_funding_row_outside_the_bundle_is_refused() -> None:
    """`bar_ix` indexes this bundle. A row pointing past its end is a desynchronised slice."""
    with pytest.raises(ValueError, match="bar_ix"):
        bundle(2, funding_rows=[(5, 0, 0.0001, 100.0)])


def test_slicing_moves_every_series_together() -> None:
    b = bundle(6)
    cut = b.slice(2, 5)
    assert len(cut) == 3
    assert len(cut.marks) == 3 and len(cut.paths) == 3
    assert cut.marks.ts[0] == b.marks.ts[2]
    assert cut.timeframe == b.timeframe and cut.symbol == b.symbol
    assert cut.brackets == b.brackets  # brackets are a campaign constant, not a window


def test_slicing_renumbers_bar_ix_from_zero() -> None:
    """The whole point of the rule. A sliced bundle read with unsliced indices would charge
    funding to the wrong bar — off by exactly the slice offset, and invisible."""
    b = bundle(6)
    cut = b.slice(2, 5)
    assert [int(r[0]) for r in cut.funding] == [0, 1, 2]
    # and the timestamps are untouched, so the renumbering is checkable against them
    assert [int(r[1]) for r in cut.funding] == [int(r[1]) for r in b.funding[2:5]]


def test_slicing_drops_funding_outside_the_window() -> None:
    b = bundle(6)
    assert len(b.slice(0, 2).funding) == 2
    assert len(b.slice(4, 6).funding) == 2


def test_several_funding_events_in_one_bar_stay_separate() -> None:
    """Binance settles three times a day. Summing them into one rate per bar would hide the
    intrabar liquidation move each one causes (ADR-0032 decision 6b)."""
    m = marks(2)
    day = int(m.ts[0].astype("int64"))
    rows = [
        (0, day - 16 * 3_600_000_000_000, 0.0001, 100.0),
        (0, day - 8 * 3_600_000_000_000, -0.0002, 101.0),
        (0, day, 0.0003, 99.0),
        (1, int(m.ts[1].astype("int64")), 0.0001, 100.0),
    ]
    b = PerpBundle(
        symbol="BTC/USDT:USDT",
        timeframe="1d",
        marks=m,
        funding=funding(rows),
        paths=(path(minutes=1440, cuts=[0, 480, 960]), path(minutes=1440, cuts=[0, 480, 960])),
        brackets=BRACKETS,
    )
    assert b.funding_at(0).shape == (3, 4)
    assert [float(r[2]) for r in b.funding_at(0)] == [0.0001, -0.0002, 0.0003]
    assert len(b.paths[0].segments) == 3  # one segment per settlement


def test_a_bundle_whose_marks_disagree_with_a_bar_count_is_refused() -> None:
    b = bundle(4)
    with pytest.raises(ValueError, match="does not line up"):
        b.aligned_with(marks(5, symbol="BTC/USDT:USDT"))


def test_a_bundle_aligned_with_matching_trade_bars_passes() -> None:
    b = bundle(4)
    b.aligned_with(marks(4))  # same close timestamps


def test_a_bundle_whose_marks_are_a_different_instrument_is_refused() -> None:
    b = bundle(4)
    with pytest.raises(ValueError, match="does not line up"):
        b.aligned_with(marks(4, symbol="ETH/USDT:USDT"))


# ── crossing the sandbox boundary (P3-18) ─────────────────────────────────────────────


def test_a_bundle_round_trips_through_the_sidecar_files(tmp_path: Path) -> None:
    """The container reads files, not objects. Everything the account arithmetic depends on has
    to survive that, exactly — a lossy path summary would change which bar liquidates."""
    b = bundle(4)
    names = write_bundle(tmp_path, b)
    restored = read_bundle(tmp_path, b.symbol, b.timeframe, names)

    assert restored.symbol == b.symbol and restored.timeframe == b.timeframe
    assert np.array_equal(restored.marks.ts, b.marks.ts)
    assert np.array_equal(restored.marks.close, b.marks.close)
    np.testing.assert_array_equal(restored.funding, b.funding)
    assert [p.starts for p in restored.paths] == [p.starts for p in b.paths]
    assert [[s.points for s in p.segments] for p in restored.paths] == [
        [s.points for s in p.segments] for p in b.paths
    ]
    assert restored.brackets == b.brackets


def test_a_multi_segment_path_survives_the_round_trip(tmp_path: Path) -> None:
    """The segment boundaries are the part that matters: lose them and a level that moves
    mid-bar becomes a level that does not."""
    m = marks(2)
    b = PerpBundle(
        symbol="BTC/USDT:USDT",
        timeframe="1d",
        marks=m,
        funding=funding([(0, int(m.ts[0].astype("int64")), 0.0001, 100.0)]),
        paths=tuple(path(minutes=1440, cuts=[0, 480, 960]) for _ in range(2)),
        brackets=BRACKETS,
    )
    names = write_bundle(tmp_path, b)
    restored = read_bundle(tmp_path, b.symbol, b.timeframe, names)
    assert [p.starts for p in restored.paths] == [(0, 480, 960), (0, 480, 960)]


def test_an_empty_funding_table_round_trips_as_empty(tmp_path: Path) -> None:
    """A contract with no settlement in the window is different from a missing series; the
    first is data, the second is refused upstream (INV-94)."""
    m = marks(2)
    b = PerpBundle(
        symbol="BTC/USDT:USDT",
        timeframe="1d",
        marks=m,
        funding=np.zeros((0, len(FUNDING_COLUMNS)), dtype=np.float64),
        paths=(path(), path()),
        brackets=BRACKETS,
    )
    names = write_bundle(tmp_path, b)
    restored = read_bundle(tmp_path, b.symbol, b.timeframe, names)
    assert restored.funding.shape == (0, len(FUNDING_COLUMNS))


def test_no_sidecar_carries_a_raw_minute_series(tmp_path: Path) -> None:
    """INV-99. The minutes are the thing that must not cross: gate ④ stages one payload per
    candidate, and the summary exists so a container never parses 1,440 rows per bar."""
    b = PerpBundle(
        symbol="BTC/USDT:USDT",
        timeframe="1d",
        marks=marks(3),
        funding=np.zeros((0, len(FUNDING_COLUMNS)), dtype=np.float64),
        paths=tuple(path(minutes=1440, cuts=[0, 480, 960]) for _ in range(3)),
        brackets=BRACKETS,
    )
    write_bundle(tmp_path, b)
    for file in tmp_path.iterdir():
        assert "_1m." not in file.name, file.name
        rows = len(pd.read_parquet(file)) if file.suffix == ".parquet" else 0
        assert rows < 3 * 1_440, f"{file.name} carries {rows} rows — that is a minute series"


# ── joining a warm-up window to a holdout window (P3-22) ───────────────────────────────


def test_two_bundles_join_into_one_continuous_window() -> None:
    """The evaluator reads a warm-up bundle from in-sample storage and the out-of-sample bundle
    from the holdout, and has to hand the backtest one continuous object."""
    warmup, held = bundle(3), bundle(2)
    joined = warmup.followed_by(_shifted(held, 3))
    assert len(joined) == 5
    assert len(joined.paths) == 5
    # the second half's bar_ix is re-based onto the joined axis, not left at its own offsets
    assert [int(r[0]) for r in joined.funding] == [0, 1, 2, 3, 4]


def test_joining_bundles_of_different_instruments_is_refused() -> None:
    a = bundle(2)
    b = PerpBundle(
        symbol="ETH/USDT:USDT",
        timeframe="1d",
        marks=marks(2, symbol="ETH/USDT:USDT"),
        funding=funding([(0, 0, 0.0001, 100.0)]),
        paths=(path(), path()),
        brackets=BRACKETS,
    )
    with pytest.raises(ValueError, match="same contract"):
        a.followed_by(b)


def test_joining_a_bundle_that_does_not_come_after_is_refused() -> None:
    """Out of order would put a later bar before an earlier one and the marks would go backwards,
    which `Bars` forbids — but the message should name the real problem."""
    later = _shifted(bundle(2), 10)
    with pytest.raises(ValueError, match="after"):
        later.followed_by(bundle(2))


def _shifted(b: PerpBundle, days: int) -> PerpBundle:
    """The same bundle with its timestamps moved forward, so two can be joined in a test."""
    delta = np.timedelta64(days, "D").astype("timedelta64[ns]")
    moved = Bars(
        b.symbol, b.timeframe, b.marks.ts + delta,
        b.marks.open, b.marks.high, b.marks.low, b.marks.close, b.marks.volume,
    )  # fmt: skip
    rows = b.funding.copy()
    if len(rows):
        rows[:, 1] += float(delta.astype("int64"))
    return PerpBundle(b.symbol, b.timeframe, moved, rows, b.paths, b.brackets)
