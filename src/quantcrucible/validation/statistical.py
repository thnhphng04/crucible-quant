"""Selection-bias statistics (Architecture §3.2, 07-VALIDATION-LAYER §4.4).

Phase 0 needs only the Minimum Backtest Length; DSR / PBO / CPCV arrive in phase 1.
"""

from __future__ import annotations

import math
from statistics import NormalDist

EULER_GAMMA = 0.5772156649015329
_Z = NormalDist().inv_cdf


def expected_max_sharpe(n_trials: int) -> float:
    """E[max] of ``n_trials`` independent standard-normal Sharpe estimates whose true Sharpe is 0.

    Bailey, Borwein, López de Prado & Zhu (2014), "Pseudo-mathematics and financial
    charlatanism", eq. for E[max_N]: (1 − γ) Z⁻¹[1 − 1/N] + γ Z⁻¹[1 − 1/(N e)].
    """
    if n_trials <= 1:
        return 0.0
    n = float(n_trials)
    return (1 - EULER_GAMMA) * _Z(1 - 1 / n) + EULER_GAMMA * _Z(1 - 1 / (n * math.e))


def min_btl_years(n_trials: int, target_sharpe: float) -> float:
    """Years of data below which the best of ``n_trials`` skill-less strategies is expected to
    show an annualized IS Sharpe of ``target_sharpe`` by luck alone (Bailey et al. 2014)."""
    if target_sharpe <= 0:
        raise ValueError("target_sharpe must be > 0")
    return (expected_max_sharpe(n_trials) / target_sharpe) ** 2
