"""Volatility estimate, IDM and portfolio-level scaling (Architecture §3.4, D7).

* :func:`ewma_vol` — the per-instrument ``vol_estimate`` fed to the sizer.
* :func:`instrument_diversification_multiplier` — lifts each instrument's vol budget so that the
  diversified portfolio still reaches ``target_vol`` (Carver's IDM), re-estimated on rebalance.
* :func:`portfolio_scale` — the uniform factor that brings the estimated portfolio volatility
  back to ``target_vol``, capped so gross exposure never exceeds ``max_leverage``.
"""

from __future__ import annotations

import math

import numpy as np
import numpy.typing as npt

VOL_SPAN = 25  # EWMA span in bars (ADR-0010)
MIN_VOL_OBS = 10  # fewer returns than this ⇒ no estimate ⇒ no position
IDM_CAP = 2.5


def ewma_vol(
    close: npt.ArrayLike, span: int, periods_per_year: float, min_obs: int = MIN_VOL_OBS
) -> float | None:
    """Annualized EWMA volatility of simple returns (zero mean), or None if too short."""
    c = np.asarray(close, dtype=np.float64)
    if len(c) < min_obs + 1 or span < 1:
        return None
    r = c[1:] / c[:-1] - 1.0
    r = r[np.isfinite(r)]
    if len(r) < min_obs:
        return None
    alpha = 2.0 / (span + 1.0)
    weights = (1.0 - alpha) ** np.arange(len(r) - 1, -1, -1)
    var = float(np.sum(weights * r**2) / np.sum(weights))
    return math.sqrt(var * periods_per_year)


def instrument_diversification_multiplier(
    corr: npt.ArrayLike, weights: npt.ArrayLike | None = None, cap: float = IDM_CAP
) -> float:
    """IDM = 1 / √(wᵀ C w), negative correlations floored at 0, capped at ``cap``."""
    c = np.clip(np.nan_to_num(np.asarray(corr, dtype=np.float64), nan=0.0), 0.0, 1.0)
    n = len(c)
    if n == 0:
        return 1.0
    np.fill_diagonal(c, 1.0)
    w = np.full(n, 1.0 / n) if weights is None else np.asarray(weights, dtype=np.float64)
    quad = float(w @ c @ w)
    if quad <= 0:
        return cap
    return min(1.0 / math.sqrt(quad), cap)


def portfolio_scale(
    notionals: npt.ArrayLike,
    cov_annual: npt.ArrayLike,
    equity: float,
    target_vol: float,
    max_leverage: float,
) -> float:
    """Uniform factor for every position: estimated portfolio vol → ``target_vol``, then capped
    so that gross notional / equity ≤ ``max_leverage``. Nothing held (or no vol) ⇒ 1."""
    n = np.asarray(notionals, dtype=np.float64)
    cov = np.asarray(cov_annual, dtype=np.float64)
    if equity <= 0 or not np.any(n):
        return 1.0
    var = float(n @ cov @ n)
    scale = target_vol / (math.sqrt(var) / equity) if var > 0 else 1.0
    gross = float(np.sum(np.abs(n))) * scale / equity
    if gross > max_leverage:
        scale *= max_leverage / gross
    return scale
