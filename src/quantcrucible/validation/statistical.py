"""Selection-bias statistics (Architecture §3.1.6, §3.2, §4.1, 07-VALIDATION-LAYER §4).

MinBTL (gate ②), PSR and DSR. DSR/PSR are implemented here from moments rather than wrapped
(ADR-0007): the published example is stated in moments, and gate ⑤ must report DSR at both
``N_raw`` and ``N_eff``. Sharpe ratios in this module are **per observation** unless a name says
``annual``; ``trials.sharpe_is`` is annualized, so :func:`portfolio_dsr` converts ``V[SR]``.

There is deliberately no per-strategy or per-cell DSR entry point: DSR is computed on the
consolidated portfolio only (§3.1.6, INV-42) — see :func:`portfolio_dsr`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import NormalDist

import numpy as np
import numpy.typing as npt
from scipy import stats

from quantcrucible.ledger.records import TrialStats

EULER_GAMMA = 0.5772156649015329
_Z = NormalDist().inv_cdf
_PHI = NormalDist().cdf


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


def max_trials_within(years: float, target_sharpe: float) -> int:
    """The largest N whose MinBTL at ``target_sharpe`` fits in ``years`` of data (0 if none)."""
    if min_btl_years(1, target_sharpe) > years:
        return 0
    lo, hi = 1, 2
    while min_btl_years(hi, target_sharpe) <= years:
        lo, hi = hi, hi * 2
        if hi > 10**15:  # expected_max_sharpe grows like sqrt(2 ln N): effectively unbounded
            return hi
    while hi - lo > 1:
        mid = (lo + hi) // 2
        lo, hi = (mid, hi) if min_btl_years(mid, target_sharpe) <= years else (lo, mid)
    return lo


@dataclass(frozen=True, slots=True)
class SharpeMoments:
    """Per-observation Sharpe ratio plus the moments PSR corrects for.

    ``kurtosis`` is the plain (non-excess) kurtosis: 3 for a normal distribution.
    """

    sr: float
    skew: float
    kurtosis: float
    n_obs: int


def sharpe_moments(returns: npt.ArrayLike) -> SharpeMoments:
    """Moments of a return series, with ``purgedcv``'s conventions (ADR-0007): SR with the
    population standard deviation, bias-corrected skew and kurtosis."""
    r = np.asarray(returns, dtype=np.float64).ravel()
    if r.size < 4:
        raise ValueError(f"need at least 4 returns, got {r.size}")
    if not np.isfinite(r).all():
        raise ValueError("returns contain NaN or infinite values")
    if float(np.ptp(r)) == 0.0:
        raise ValueError("returns have zero variance: the Sharpe ratio is undefined")
    return SharpeMoments(
        sr=float(r.mean() / r.std(ddof=0)),
        skew=float(stats.skew(r, bias=False)),
        kurtosis=float(stats.kurtosis(r, bias=False, fisher=False)),
        n_obs=int(r.size),
    )


def probabilistic_sharpe_ratio(m: SharpeMoments, sr_benchmark: float) -> float:
    """P(true SR > ``sr_benchmark``) — Bailey & López de Prado (2012), eq. 7:
    Φ[(SR − SR*)·√(T − 1) / √(1 − γ₃·SR + (γ₄ − 1)/4·SR²)]."""
    if m.n_obs < 2:
        raise ValueError("PSR needs at least 2 observations")
    denominator_sq = 1 - m.skew * m.sr + (m.kurtosis - 1) / 4 * m.sr**2
    if not math.isfinite(denominator_sq) or denominator_sq <= 0:
        raise ValueError(
            f"PSR denominator is {denominator_sq:.4g}: the moments are too extreme for the "
            "normal approximation"
        )
    return _PHI((m.sr - sr_benchmark) * math.sqrt(m.n_obs - 1) / math.sqrt(denominator_sq))


def deflated_benchmark(n_trials: int, var_sr: float) -> float:
    """SR₀ = √V[SR] · E[max_N]: the Sharpe the best of ``n_trials`` skill-less trials reaches
    by chance (Bailey & López de Prado 2014). Same units as ``var_sr``."""
    if n_trials < 1:
        raise ValueError(f"n_trials must be >= 1, got {n_trials}")
    if not math.isfinite(var_sr) or var_sr < 0:
        raise ValueError(f"var_sr must be finite and >= 0, got {var_sr}")
    return math.sqrt(var_sr) * expected_max_sharpe(n_trials)


def deflated_sharpe_ratio(m: SharpeMoments, n_trials: int, var_sr: float) -> float:
    """DSR = PSR against the deflated benchmark SR₀(N, V[SR]); ``var_sr`` per observation."""
    return probabilistic_sharpe_ratio(m, deflated_benchmark(n_trials, var_sr))


@dataclass(frozen=True, slots=True)
class DsrReport:
    """Gate ⑤'s statistics for one consolidated portfolio, at both trial counts (§4.1)."""

    dsr_n_eff: float
    dsr_n_raw: float
    n_eff: int  # N_eff + portfolio variants
    n_raw: int  # N_raw + portfolio variants
    var_sr: float  # per observation
    sr_benchmark_n_eff: float  # per observation
    sr_benchmark_n_raw: float
    moments: SharpeMoments


def portfolio_dsr(
    returns: npt.ArrayLike,
    trial_stats: TrialStats,
    n_variants: int,
    periods_per_year: float,
) -> DsrReport:
    """DSR of the consolidated portfolio's returns (§3.2 row ⑤).

    ``N`` = every statistical trial (``trial_stats``, all engines, all verdicts) plus every
    evaluated portfolio variant; ``V[SR]`` is the variance of the trials' annualized IS Sharpe
    ratios, converted to per-observation units with ``periods_per_year``.
    """
    if trial_stats.var_sr is None or trial_stats.n_raw < 1:
        raise ValueError("no trials in the ledger: DSR has no N or V[SR] to deflate with")
    if n_variants < 0 or periods_per_year <= 0:
        raise ValueError("n_variants must be >= 0 and periods_per_year > 0")
    m = sharpe_moments(returns)
    var_sr = max(trial_stats.var_sr, 0.0) / periods_per_year  # SQL float noise can dip below 0
    n_eff = trial_stats.n_eff + n_variants
    n_raw = trial_stats.n_raw + n_variants
    return DsrReport(
        dsr_n_eff=deflated_sharpe_ratio(m, n_eff, var_sr),
        dsr_n_raw=deflated_sharpe_ratio(m, n_raw, var_sr),
        n_eff=n_eff,
        n_raw=n_raw,
        var_sr=var_sr,
        sr_benchmark_n_eff=deflated_benchmark(n_eff, var_sr),
        sr_benchmark_n_raw=deflated_benchmark(n_raw, var_sr),
        moments=m,
    )
