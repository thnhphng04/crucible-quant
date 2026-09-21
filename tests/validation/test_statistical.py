"""MinBTL against the published examples (Bailey, Borwein, López de Prado & Zhu 2014)."""

import purgedcv
import pytest

from quantcrucible.validation.statistical import expected_max_sharpe, min_btl_years


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
