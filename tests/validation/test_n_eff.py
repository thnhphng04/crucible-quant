"""N_eff via ONC clustering (Architecture §4.1 "Estimating N_eff", P1-05, ADR-0009)."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from quantcrucible.ledger.db import Ledger
from quantcrucible.ledger.records import TrialRecord
from quantcrucible.validation.n_eff import (
    ONC_METHOD,
    correlation_matrix,
    onc_clusters,
    update_n_eff,
)


def _sources(k: int, per_source: int, n_obs: int, noise: float, seed: int) -> np.ndarray:
    """``k`` independent return sources, each copied ``per_source`` times with its own noise."""
    rng = np.random.default_rng(seed)
    base = rng.normal(0, 0.01, (k, n_obs))
    series = np.repeat(base, per_source, axis=0)
    return series + rng.normal(0, 0.01 * noise, series.shape)


@pytest.mark.parametrize(("k", "seed"), [(2, 0), (4, 1), (7, 2)])
def test_onc_recovers_the_number_of_independent_sources(k: int, seed: int) -> None:
    series = _sources(k, per_source=8, n_obs=750, noise=0.6, seed=seed)
    labels = onc_clusters(np.corrcoef(series), 750, seed=0)
    assert len(set(labels)) == k
    # each source's copies land together
    for j in range(k):
        assert len(set(labels[j * 8 : (j + 1) * 8])) == 1


def test_onc_on_independent_series_keeps_them_apart() -> None:
    rng = np.random.default_rng(3)
    labels = onc_clusters(np.corrcoef(rng.normal(size=(12, 1000))), 1000, seed=0)
    assert len(set(labels)) == 12  # pure noise: every trial its own cluster


def test_onc_small_inputs() -> None:
    assert list(onc_clusters(np.ones((1, 1)), 500)) == [0]
    assert len(set(onc_clusters(np.array([[1.0, 0.0], [0.0, 1.0]]), 500))) == 2
    # ONC needs >= 3 trials; below that N_eff = N_raw even for near-duplicates (ADR-0009)
    assert len(set(onc_clusters(np.array([[1.0, 0.99], [0.99, 1.0]]), 500))) == 2
    assert len(set(onc_clusters(np.ones((4, 4)), 500))) == 1  # identical series


def test_correlation_matrix_aligns_on_time_and_tolerates_flat_series() -> None:
    ts = pd.date_range("2020-01-01", periods=300, freq="D")
    rng = np.random.default_rng(4)
    a = pd.Series(rng.normal(size=300), index=ts)
    b = a.iloc[100:] * 2 + 0.001  # shorter, same shape where it overlaps
    flat = pd.Series(0.0, index=ts)  # never traded
    corr, n_obs = correlation_matrix([a, b, flat])
    assert n_obs[0, 1] == 200 and n_obs[0, 0] == 300
    assert corr[0, 1] == pytest.approx(1.0)
    assert corr[0, 2] == 0.0 and corr[2, 2] == 1.0
    assert np.allclose(corr, corr.T)


def _record(ledger: Ledger, tmp_path: Path, i: int, rets: np.ndarray) -> int:
    path = tmp_path / f"t{i}.parquet"
    ts = pd.date_range("2020-01-01", periods=len(rets), freq="D")
    pd.DataFrame({"ts": ts, "ret": rets}).to_parquet(path, index=False)
    return ledger.record_trial(
        TrialRecord(
            run_id="r", campaign_id="c1", candidate_id=f"cand{i}", engine="manual", seed=0,
            strategy_hash=f"h{i}", params={}, universe="X", timeframe="1d",
            timerange="2020/2022", source="manual", sharpe_is=float(i) / 10,
            returns_path=str(path), verdict="PASS",
        )
    )  # fmt: skip


def test_update_n_eff_appends_a_clustering_run(tmp_path: Path) -> None:
    ledger = Ledger.open(tmp_path / "ledger.db")
    ledger.open_campaign("c1", "2025/2026", lock_hash="x")
    series = _sources(3, per_source=5, n_obs=500, noise=0.5, seed=5)
    for i, rets in enumerate(series):
        _record(ledger, tmp_path, i, rets)
    assert ledger.trial_stats().n_eff == 15  # before clustering: N_raw

    result = update_n_eff(ledger, seed=0)
    assert result.n_trials == 15 and result.n_clusters == 3
    stats = ledger.trial_stats()
    assert (stats.n_raw, stats.n_eff) == (15, 3)

    # a new, unrelated trial counts as its own cluster until the next run
    _record(ledger, tmp_path, 15, np.random.default_rng(9).normal(0, 0.01, 500))
    assert ledger.trial_stats().n_eff == 4
    second = update_n_eff(ledger, seed=0)
    assert second.run_id == result.run_id + 1 and ledger.trial_stats().n_eff == 4
    assert result.method == ONC_METHOD
