"""Feature map v2: timeframe-free dimensions (P3-59, INV-120, ADR-0046).

Bins are computed by hand from the locked bounds: a log dimension cuts ln(value) evenly between
ln(low) and ln(high); annual_return is the compound annual rate of the IS total return.
"""

from __future__ import annotations

import math

import pytest

from quantcrucible.agent.evolution.feature_map import (
    DIMENSIONS,
    FeatureMap,
    FeatureMapError,
    descriptors,
)
from quantcrucible.validation.run import FEATURE_MAP, FEATURE_MAP_V2, derived_settings

V2 = FeatureMap.from_lock({"derived": {"feature_map": FEATURE_MAP_V2}})


def test_v2_dimensions_and_bounds() -> None:
    assert V2.dimensions == (
        "trades_per_year", "max_drawdown", "sharpe_is", "sortino_is", "annual_return",
    )  # fmt: skip
    assert V2.bounds["trades_per_year"] == (1.0, 10_000.0)
    assert V2.bounds["annual_return"] == (-0.1, 0.3)
    assert V2.bounds["max_drawdown"] == (0.0, 0.5)
    assert V2.log_dims == frozenset({"trades_per_year"})
    assert derived_settings("joint")["feature_map"] == FEATURE_MAP_V2  # new locks


def test_trades_per_year_bins_on_a_log_scale() -> None:
    """16 bins over ln(1)..ln(10 000): each bin spans a factor 10^(4/16) ≈ 1.78."""
    width = math.log(10_000) / 16
    for trades, want in ((1.0, 0), (8.0, math.floor(math.log(8) / width)),
                         (110.0, 8), (2_000.0, math.floor(math.log(2_000) / width)),
                         (9_999.0, 15), (50_000.0, 15)):  # fmt: skip
        assert V2.bin("trades_per_year", trades) == want, trades
    assert V2.bin("trades_per_year", 0.0) == 0  # never traded: the edge, not a log error
    assert V2.bin("trades_per_year", 0.5) == 0


def test_annual_return_is_the_compound_rate() -> None:
    d = descriptors({"n_trades": 40.0, "total_return": 0.21}, years=2.0)
    assert d["annual_return"] == pytest.approx(0.1)  # 1.1² = 1.21
    assert d["trades_per_year"] == pytest.approx(20.0)
    assert descriptors({"total_return": -1.0}, years=2.0)["annual_return"] == -1.0
    assert descriptors({"total_return": 0.5}, years=0.0)["annual_return"] is None
    assert V2.bin("annual_return", 0.11) == 8  # (0.11 + 0.1) / 0.4 · 16 = 8.4


def test_a_v1_map_is_read_as_before() -> None:
    """INV-63 still holds for the locks already written: same dimensions, linear bins."""
    v1 = FeatureMap.from_lock({"derived": {"feature_map": FEATURE_MAP}})
    assert v1.dimensions == DIMENSIONS and v1.log_dims == frozenset()
    assert v1.bin("trades_per_year", 75.0) == 8
    cell = v1.cell(descriptors({"n_trades": 30.0, "total_return": 0.5}, years=1.0), ["trend"])
    assert cell[1] == math.floor(30 / 150 * 16) and cell[5] == math.floor(1.5 / 5 * 16)


def test_a_log_dimension_needs_a_positive_lower_bound() -> None:
    bad = {**FEATURE_MAP_V2, "bounds": {**FEATURE_MAP_V2["bounds"], "trades_per_year": [0.0, 10.0]}}
    with pytest.raises(FeatureMapError, match="log"):
        FeatureMap.from_lock({"derived": {"feature_map": bad}})
