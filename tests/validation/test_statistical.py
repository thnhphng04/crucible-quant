"""MinBTL, PSR and DSR against the published examples (Bailey & López de Prado 2012, 2014;
Bailey, Borwein, López de Prado & Zhu 2014), cross-checked against the pinned `purgedcv`."""

import math
from itertools import pairwise

import numpy as np
import purgedcv
import pytest

from quantcrucible.ledger.records import TrialStats
from quantcrucible.validation.statistical import (
    SharpeMoments,
    deflated_benchmark,
    deflated_sharpe_ratio,
    expected_max_sharpe,
    min_btl_years,
    portfolio_dsr,
    probabilistic_sharpe_ratio,
    sharpe_moments,
)


@pytest.mark.parametrize(
    ("n_trials", "years"),
    [
        (7, 2.0),  # "no more than 7 independent configurations with 2 years of data"
        (45, 5.0),  # "… 45 independent configurations with 5 years of data"
    ],
)
def test_min_btl_published_examples(n_trials: int, years: float) -> None:
    assert min_btl_years(n_trials, 1.0) == pytest.approx(years, rel=0.05)


def test_min_btl_edges_and_monotonicity() -> None:
    assert min_btl_years(0, 1.0) == 0.0 and min_btl_years(1, 1.0) == 0.0
    values = [min_btl_years(n, 1.5) for n in (2, 5, 10, 100, 1000)]
    assert values == sorted(values)
    assert min_btl_years(45, 1.0) > min_btl_years(45, 1.5)  # a lower target is stricter
    with pytest.raises(ValueError):
        min_btl_years(10, 0.0)


@pytest.mark.parametrize("n_trials", [1, 2, 7, 45, 100, 10_000])
def test_min_btl_matches_purgedcv(n_trials: int) -> None:
    """Cross-check against the pinned library (ADR-0007): a silent formula change fails here."""
    assert min_btl_years(n_trials, 1.0) == pytest.approx(
        purgedcv.minimum_backtest_length(n_trials, 1.0), abs=1e-12
    )


def test_expected_max_sharpe_grows_like_sqrt_2_log_n() -> None:
    assert expected_max_sharpe(1_000_000) == pytest.approx(5.0, rel=0.1)


# ── PSR / DSR ────────────────────────────────────────────────────────────────────────

DAYS = 250  # the paper's convention for this example


def _paper_moments(skew: float = -3.0, kurtosis: float = 10.0) -> SharpeMoments:
    """Bailey & López de Prado (2014) numerical example: annualized SR 2.5 over 5 years of
    daily observations (T = 1250), skew −3, kurtosis 10."""
    return SharpeMoments(sr=2.5 / math.sqrt(DAYS), skew=skew, kurtosis=kurtosis, n_obs=1250)


def test_dsr_reference_example() -> None:
    """N = 100 trials with annualized V[SR] = 1/2 ⇒ SR₀ ≈ 1.7894 (annualized), DSR ≈ 0.9004."""
    var_sr = 0.5 / DAYS  # per-observation units
    assert deflated_benchmark(100, var_sr) * math.sqrt(DAYS) == pytest.approx(1.7894, abs=1e-4)
    assert deflated_sharpe_ratio(_paper_moments(), 100, var_sr) == pytest.approx(0.9004, abs=1e-4)


def test_psr_and_dsr_match_purgedcv_on_a_series() -> None:
    returns = np.random.default_rng(7).normal(0.0008, 0.01, 1500)
    m = sharpe_moments(returns)
    assert probabilistic_sharpe_ratio(m, 0.01) == pytest.approx(
        purgedcv.probabilistic_sharpe_ratio(returns, benchmark_skill=0.01), abs=1e-12
    )
    assert deflated_sharpe_ratio(m, 50, 0.3**2 / 365) == pytest.approx(
        purgedcv.deflated_sharpe_ratio(returns, 50, 0.3**2, bars_per_year=365), abs=1e-12
    )


def test_single_trial_dsr_is_psr_against_zero() -> None:
    m = _paper_moments()
    assert deflated_sharpe_ratio(m, 1, 0.5 / DAYS) == probabilistic_sharpe_ratio(m, 0.0)


def test_dsr_falls_with_more_trials_and_more_dispersion() -> None:
    m = _paper_moments()
    by_n = [deflated_sharpe_ratio(m, n, 0.5 / DAYS) for n in (1, 2, 10, 100, 1000, 10_000)]
    assert all(a > b for a, b in pairwise(by_n))
    by_var = [deflated_sharpe_ratio(m, 100, v / DAYS) for v in (0.1, 0.25, 0.5, 1.0, 2.0)]
    assert all(a > b for a, b in pairwise(by_var))


def test_negative_skew_and_fat_tails_are_penalized() -> None:
    var_sr = 0.5 / DAYS
    normal = deflated_sharpe_ratio(_paper_moments(0.0, 3.0), 100, var_sr)
    assert deflated_sharpe_ratio(_paper_moments(-3.0, 3.0), 100, var_sr) < normal
    assert deflated_sharpe_ratio(_paper_moments(0.0, 10.0), 100, var_sr) < normal


def test_invalid_inputs_raise() -> None:
    m = _paper_moments()
    with pytest.raises(ValueError):
        deflated_sharpe_ratio(m, 0, 0.1)
    with pytest.raises(ValueError):
        deflated_sharpe_ratio(m, 10, -0.1)
    with pytest.raises(ValueError):
        sharpe_moments(np.full(100, 0.01))  # zero variance: Sharpe undefined
    with pytest.raises(ValueError):
        sharpe_moments(np.array([0.01, np.nan, 0.02]))
    with pytest.raises(ValueError):  # moments too extreme for the normal approximation
        probabilistic_sharpe_ratio(SharpeMoments(sr=1.0, skew=5.0, kurtosis=3.0, n_obs=100), 0.0)


# ── the portfolio-level report (inputs of gate ⑤) ──────────────────────────────────────


def _portfolio_returns() -> np.ndarray:
    return np.random.default_rng(11).normal(0.0012, 0.01, 2000)


def test_portfolio_dsr_reports_both_n_raw_and_n_eff() -> None:
    returns = _portfolio_returns()
    stats = TrialStats(n_raw=400, n_eff=40, var_sr=0.25)  # annualized, as trials.sharpe_is
    report = portfolio_dsr(returns, stats, n_variants=2, periods_per_year=365)
    assert (report.n_raw, report.n_eff) == (402, 42)  # each portfolio variant is a trial too
    m = sharpe_moments(returns)
    assert report.dsr_n_raw == deflated_sharpe_ratio(m, 402, 0.25 / 365)
    assert report.dsr_n_eff == deflated_sharpe_ratio(m, 42, 0.25 / 365)
    assert report.dsr_n_eff > report.dsr_n_raw  # N_eff only lowers the bar
    assert report.var_sr == pytest.approx(0.25 / 365)


def test_portfolio_dsr_more_variants_lower_it() -> None:
    returns = _portfolio_returns()
    stats = TrialStats(n_raw=50, n_eff=20, var_sr=0.25)
    few = portfolio_dsr(returns, stats, n_variants=1, periods_per_year=365)
    many = portfolio_dsr(returns, stats, n_variants=30, periods_per_year=365)
    assert many.dsr_n_eff < few.dsr_n_eff and many.dsr_n_raw < few.dsr_n_raw


def test_portfolio_dsr_needs_trials() -> None:
    with pytest.raises(ValueError, match="no trials"):
        portfolio_dsr(_portfolio_returns(), TrialStats(0, 0, None), 1, 365)
