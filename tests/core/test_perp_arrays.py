"""A perpetual bundle packed as flat arrays reads exactly like the bundle (P3-49, ADR-0038)."""

from __future__ import annotations

import dataclasses

import numpy as np
import pytest

from quantcrucible.core.path_summary import segment_bar
from quantcrucible.core.perp_arrays import first_touch, pack_bundle, pack_paths
from quantcrucible.core.perp_inputs import PerpBundle
from tests.perp_fixtures import BRACKETS, MINUTE_NS, MINUTES, perp_market


def test_first_touch_on_the_arrays_equals_the_segmented_path() -> None:
    """Every segment, both directions, at random levels and at every breakpoint exactly."""
    _, bundle = perp_market(60, seed=3)
    arrays = pack_paths(bundle.paths)
    rng = np.random.default_rng(0)
    checked = 0
    for b, path in enumerate(bundle.paths):
        segs = range(arrays.bar_ptr[b], arrays.bar_ptr[b + 1])
        assert [int(arrays.seg_start[s]) for s in segs] == list(path.starts)
        for s, summary in zip(segs, path.segments, strict=True):
            pts = [p for _, p in summary.points]
            levels = [*pts, *rng.uniform(min(pts) * 0.99, max(pts) * 1.01, 20)]
            for level in levels:
                for above in (True, False):
                    want = summary.first_touch(level, above=above)
                    assert first_touch(arrays, s, level, above=above) == want
                    checked += 1
    assert checked > 5_000


def test_the_bundle_packs_funding_in_replay_order_with_its_minutes() -> None:
    bars, bundle = perp_market(30, seed=5)
    packed = pack_bundle(bundle, bars)
    assert not packed.bad.any()
    for b in range(len(bars)):
        rows = bundle.funding_at(b)
        a, z = packed.fund_ptr[b], packed.fund_ptr[b + 1]
        assert list(packed.fund_rate[a:z]) == [float(r[2]) for r in rows]
        assert list(packed.fund_mark[a:z]) == [float(r[3]) for r in rows]
        open_ns = int(bars.ts[b].astype("int64")) - MINUTES * MINUTE_NS
        assert list(packed.fund_minute[a:z]) == [round((r[1] - open_ns) / MINUTE_NS) for r in rows]
    assert packed.fund_ptr[-1] == len(bundle.funding) > 0
    assert list(packed.brackets.cap) == sorted(r["cap"] for r in BRACKETS)
    assert np.array_equal(packed.mark_close, bundle.marks.close)


def test_bars_the_replay_would_refuse_are_flagged_not_refused() -> None:
    """A settlement off every segment start, or trade and mark paths cut differently: the
    replay raises only when a position is open there, so packing marks the bar."""
    bars, bundle = perp_market(8, seed=7)
    minutes = list(range(MINUTES))
    flat = [20_000.0] * MINUTES
    uncut = segment_bar(minutes, flat, flat, [])
    assert bundle.trade_paths is not None
    trade = (*bundle.trade_paths[:3], uncut, *bundle.trade_paths[4:])
    skewed = dataclasses.replace(bundle, trade_paths=trade)
    packed = pack_bundle(skewed, bars)
    assert list(np.flatnonzero(packed.bad)) == [3]  # bar 3 has a settlement at minute 120
    with pytest.raises(ValueError, match="trade-minute paths"):
        pack_bundle(dataclasses.replace(bundle, trade_paths=None), bars)


def test_a_bundle_of_another_length_is_refused() -> None:
    bars, bundle = perp_market(8, seed=1)
    short = PerpBundle(
        bundle.symbol, bundle.timeframe, bundle.marks.slice(0, 7), bundle.funding[:0],
        bundle.paths[:7], bundle.brackets, trade_paths=bundle.trade_paths[:7],  # type: ignore[index]
    )  # fmt: skip
    with pytest.raises(ValueError, match="differ in length"):
        pack_bundle(short, bars)
