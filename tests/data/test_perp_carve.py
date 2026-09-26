"""The perpetual holdout: a second carve, beside the first (P3-22, ADR-0032) — P6, INV-94.

The spot carve splits one series. A perpetual one has to split four — trade bars, mark bars,
funding and the intrabar paths — and split them at **exactly** the same bar. A mark series cut one
bar differently from the trade series marks every OOS position at its neighbour's price, and
nothing raises; that is the failure `PerpBundle` exists to make impossible, so the carve slices
the bundle rather than the files.

The carve writes into `<holdout>/perp/` with its own lock, nested inside the existing holdout
directory. That is deliberate: the guard hook matches anything under it, so the second holdout is
protected with no change to the rail (`tests/tooling/test_guard_paths.py`).

Nothing here touches the real holdout — every path is under `tmp_path`.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pytest

from quantcrucible.core.path_summary import segment_bar
from quantcrucible.core.perp_inputs import PerpBundle, read_bundle
from quantcrucible.core.strategy.base import Bars
from quantcrucible.data.perp_carve import carve_perp
from quantcrucible.data.perp_source import CoverageError

SYMBOL = "BTC/USDT:USDT"
BRACKETS = ({"cap": 1e9, "max_leverage": 100, "mmr": 0.004, "amount": 0.0},)


def series(n: int, start: str = "2024-01-01") -> Bars:
    day = np.datetime64(start, "D")
    ts = (day + np.arange(n, dtype="timedelta64[D]")).astype("datetime64[ns]")
    price = 100.0 + np.arange(n, dtype=np.float64)
    return Bars(SYMBOL, "1d", ts, price, price + 1, price - 1, price, np.full(n, 1e9))


def bundle(trades: Bars) -> PerpBundle:
    return PerpBundle(
        symbol=trades.symbol,
        timeframe=trades.timeframe,
        marks=trades,
        funding=np.array(
            [
                [k, float(trades.ts[k].astype("int64")), 0.0001, float(trades.close[k])]
                for k in range(len(trades))
            ],
            dtype=np.float64,
        ),
        paths=tuple(
            segment_bar([0, 1], [float(trades.low[k])] * 2, [float(trades.high[k])] * 2, cuts=[])
            for k in range(len(trades))
        ),
        brackets=BRACKETS,
    )


def layout(tmp_path: Path) -> dict[str, Path]:
    return {
        "in_sample_dir": tmp_path / "data" / "perp",
        "holdout_dir": tmp_path / "holdout" / "perp",
        "lock_path": tmp_path / "holdout" / "perp.lock",
    }


def test_all_four_series_split_at_the_same_bar(tmp_path: Path) -> None:
    """The invariant that matters. 100 bars, the last 30 held out ⇒ 70 in sample, in every series
    at once — and the bundle's own alignment check is what proves it."""
    trades = series(100)
    summary = carve_perp(
        {SYMBOL: (trades, bundle(trades))},
        holdout_start=date(2024, 3, 11),  # bar 70 closes on this day
        holdout_end=date(2024, 4, 10),
        harden=False,
        **layout(tmp_path),
    )
    assert summary.in_sample_rows == {SYMBOL: 70}

    names = json.loads((tmp_path / "holdout" / "perp.lock").read_text(encoding="utf-8"))
    assert set(names["files"]) == {
        "BTC-USDT-USDT_1d.parquet",
        "BTC-USDT-USDT_1d.mark.parquet",
        "BTC-USDT-USDT.funding.parquet",
        "BTC-USDT-USDT_1d.paths.parquet",
        "BTC-USDT-USDT.brackets.json",
    }
    inside = read_bundle(
        tmp_path / "data" / "perp",
        SYMBOL,
        "1d",
        {
            "mark": "BTC-USDT-USDT_1d.mark.parquet",
            "funding": "BTC-USDT-USDT.funding.parquet",
            "paths": "BTC-USDT-USDT_1d.paths.parquet",
            "brackets": "BTC-USDT-USDT.brackets.json",
        },
    )
    assert len(inside) == 70 and len(inside.paths) == 70
    assert len(inside.funding) == 70
    inside.aligned_with(trades.slice(0, 70))  # raises if any series was cut differently


def test_the_holdout_bundle_renumbers_its_own_bar_index(tmp_path: Path) -> None:
    """`bar_ix` indexes the bundle it belongs to (ADR-0034). A holdout bundle still carrying the
    in-sample offsets would charge every OOS funding event to the wrong bar."""
    trades = series(100)
    carve_perp(
        {SYMBOL: (trades, bundle(trades))},
        holdout_start=date(2024, 3, 11),
        holdout_end=date(2024, 4, 10),
        harden=False,
        **layout(tmp_path),
    )
    outside = read_bundle(
        tmp_path / "holdout" / "perp",
        SYMBOL,
        "1d",
        {
            "mark": "BTC-USDT-USDT_1d.mark.parquet",
            "funding": "BTC-USDT-USDT.funding.parquet",
            "paths": "BTC-USDT-USDT_1d.paths.parquet",
            "brackets": "BTC-USDT-USDT.brackets.json",
        },
    )
    assert len(outside) == 30
    assert int(outside.funding[:, 0].min()) == 0
    assert int(outside.funding[:, 0].max()) == 29


def test_the_lock_is_write_once(tmp_path: Path) -> None:
    """P6 layer 1: a second carve must not be able to replace the first."""
    trades = series(60)
    args = dict(
        holdout_start=date(2024, 2, 20), holdout_end=date(2024, 3, 1), harden=False,
        **layout(tmp_path),
    )  # fmt: skip
    carve_perp({SYMBOL: (trades, bundle(trades))}, **args)  # type: ignore[arg-type]
    with pytest.raises(Exception, match="exists"):
        carve_perp({SYMBOL: (trades, bundle(trades))}, **args)  # type: ignore[arg-type]


def test_a_bundle_that_does_not_match_its_trade_bars_is_refused(tmp_path: Path) -> None:
    trades = series(60)
    with pytest.raises(ValueError, match="does not line up"):
        carve_perp(
            {SYMBOL: (trades, bundle(series(59)))},
            holdout_start=date(2024, 2, 20),
            holdout_end=date(2024, 3, 1),
            harden=False,
            **layout(tmp_path),
        )


def test_a_holdout_window_outside_the_data_is_refused(tmp_path: Path) -> None:
    """A carve that leaves nothing in sample, or nothing held out, is a mistake rather than an
    empty result — and it would be discovered only after a campaign had been opened on it."""
    trades = series(60)
    with pytest.raises(CoverageError, match="holdout"):
        carve_perp(
            {SYMBOL: (trades, bundle(trades))},
            holdout_start=date(2030, 1, 1),
            holdout_end=date(2030, 2, 1),
            harden=False,
            **layout(tmp_path),
        )


def test_the_recorded_range_is_the_window_that_was_asked_for(tmp_path: Path) -> None:
    trades = series(100)
    summary = carve_perp(
        {SYMBOL: (trades, bundle(trades))},
        holdout_start=date(2024, 3, 11),
        holdout_end=date(2024, 4, 10),
        harden=False,
        **layout(tmp_path),
    )
    assert summary.holdout_range == "2024-03-11/2024-04-10"
    manifest = json.loads((tmp_path / "holdout" / "perp.lock").read_text(encoding="utf-8"))
    assert manifest["range"] == summary.holdout_range
    assert datetime.fromisoformat(manifest["created_at"]).tzinfo is not None
    assert manifest["created_at"] <= datetime.now(UTC).isoformat()
