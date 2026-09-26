"""CPCV with purging/embargo + the IS→OOS degradation curve (§3.2, 07 §3–4, INV-47, ADR-0012)."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from quantcrucible.ledger.db import Ledger
from quantcrucible.validation.cpcv import (
    DegradationPoint,
    cpcv,
    is_oos_diverging,
    purged_splits,
)
from quantcrucible.validation.pbo_gate import PboGate
from tests.validation.test_pbo_gate import GridRunner, cand, ctx

TS = pd.date_range("2020-01-01", periods=12, freq="D")


def _fold(splits: list[tuple[np.ndarray, np.ndarray]], test: list[int]) -> list[int]:
    return next(sorted(tr.tolist()) for tr, te in splits if sorted(te.tolist()) == test)


def test_split_layout_without_purge() -> None:
    """12 bars, 6 groups of 2, k = 2 ⇒ C(6,2) = 15 folds; a fold's train is its complement."""
    splits = purged_splits(TS, horizon_bars=1, embargo_bars=0)
    assert len(splits) == 15
    assert _fold(splits, [2, 3, 6, 7]) == [0, 1, 4, 5, 8, 9, 10, 11]


def test_purge_removes_rows_whose_label_overlaps_the_test_block() -> None:
    """A 3-bar label horizon: observation i spans [t_i, t_i+3). Test rows 2–3 span [t2, t6),
    rows 6–7 span [t6, t10). Every training row whose span overlaps them is purged — before the
    block (rows 0, 1, 4, 5 reach into it) and after it (rows 8, 9 start inside [t7, t10))."""
    splits = purged_splits(TS, horizon_bars=3, embargo_bars=0)
    assert _fold(splits, [2, 3, 6, 7]) == [10, 11]


def test_embargo_drops_rows_after_each_test_block() -> None:
    splits = purged_splits(TS, horizon_bars=1, embargo_bars=1)
    assert _fold(splits, [2, 3, 6, 7]) == [0, 1, 5, 9, 10, 11]  # rows 4 and 8 embargoed


def test_cpcv_paths_select_in_sample_and_score_out_of_sample() -> None:
    rng = np.random.default_rng(0)
    t = 1200
    ts = pd.date_range("2020-01-01", periods=t, freq="D")
    m = rng.normal(0, 0.01, (20, t))
    m[7] += 0.002  # the one real edge: selected in every fold
    res = cpcv(m, ts, periods_per_year=365)
    assert res.n_folds == 15 and res.n_paths == 5 and len(res.path_sharpes) == 5
    assert set(res.selected) == {7}
    # every fold picks config 7, so every path is config 7's own series
    row = m[7]
    expected = row.mean() / row.std(ddof=1) * math.sqrt(365)
    np.testing.assert_allclose(res.path_sharpes, expected, rtol=1e-12)
    assert res.negative_share == 0.0


def test_cpcv_on_noise_is_centered_on_zero() -> None:
    m = np.random.default_rng(1).normal(0, 0.01, (30, 1500))
    res = cpcv(m, pd.date_range("2020-01-01", periods=1500, freq="D"), 365)
    assert abs(res.median_sharpe) < 1.0


def test_oos_curve_is_private(tmp_path: Path) -> None:
    """INV-47: the CPCV-OOS numbers live in EvaluationReport.private and gate_results — never in
    public metrics or feedback — and come from the IS grid matrix, never from the holdout."""
    ledger = Ledger.open(tmp_path / "ledger.db")
    ledger.open_campaign("c1", "2030-01-01/2031-01-01", lock_hash="h")
    result = PboGate().check(cand(), ctx(ledger, tmp_path, GridRunner(edge=0.002)))
    assert result.report is not None
    assert "cpcv_oos_median_sharpe" in result.report.private
    assert not any(k.startswith("cpcv") for k in result.report.public)
    assert "cpcv" not in result.report.feedback
    assert result.detail is not None and len(result.detail["cpcv_path_sharpes"]) == 5


def test_degradation_curve_flags_is_up_oos_down() -> None:
    rising = [DegradationPoint(f"c{i}", 1.0 + 0.3 * i, 0.8 - 0.1 * i) for i in range(5)]
    assert is_oos_diverging(rising)
    healthy = [DegradationPoint(f"c{i}", 1.0 + 0.3 * i, 0.5 + 0.2 * i) for i in range(5)]
    assert not is_oos_diverging(healthy)
    assert not is_oos_diverging(rising[:2])  # too few checkpoints


def test_a_flat_curve_is_not_divergence() -> None:
    """Checkpoints that repeat the same record holder give slopes of ~1e-17, whose sign is
    arithmetic noise: without a tolerance the monitor stops an engine that did not move."""
    for n in (3, 4, 5, 7):
        assert not is_oos_diverging([DegradationPoint(f"c{i}", 1.0, 0.3) for i in range(n)])
    assert not is_oos_diverging([DegradationPoint(f"c{i}", 1.0, 0.3 + 0.2 * i) for i in range(3)])
    assert is_oos_diverging([DegradationPoint(f"c{i}", 1.0 + 0.4 * i, 0.3) for i in range(3)])


def test_cpcv_rejects_bad_input() -> None:
    with pytest.raises(ValueError, match="n_configs"):
        cpcv(np.zeros((3, 10)), TS, 365)
