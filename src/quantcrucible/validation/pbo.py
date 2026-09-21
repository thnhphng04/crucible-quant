"""Probability of Backtest Overfitting via CSCV (Architecture §3.2 "What PBO actually tests",
07-VALIDATION-LAYER §4.2, ADR-0011).

The performance matrix is ``(n_configs, n_obs)`` — one row per configuration of the pre-registered
set, on a shared time axis. The time axis is cut into ``n_splits`` contiguous blocks; for every
way of choosing half of them as out-of-sample, the configuration with the highest IS Sharpe is
selected (the evolution loop's own rule) and its relative OOS rank ω = rank / (M + 1) turned into
a logit. PBO = the share of combinations with logit < 0.

Same definitions as ``purgedcv.probability_of_backtest_overfitting`` 0.1.6 (sample Sharpe with
``ddof=1``, a degenerate slice scores 0, first argmax, average ranks for ties) — a test pins the
two equal — but vectorized over all C(S, S/2) combinations from per-block sums, so S = 16 costs
about a second instead of minutes.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

import numpy as np
import numpy.typing as npt
from scipy import stats

DEFAULT_SPLITS = 16

FloatArray = npt.NDArray[np.float64]


@dataclass(frozen=True, slots=True)
class PboResult:
    pbo: float
    logits: FloatArray  # one per combination; the mass below 0 is `pbo`
    slope: float  # OLS slope of the IS-best's OOS Sharpe on its IS Sharpe
    is_best_is_sharpe: FloatArray
    is_best_oos_sharpe: FloatArray
    n_combos: int
    n_configs: int


def _block_edges(n_obs: int, n_splits: int) -> list[tuple[int, int]]:
    size, remainder = divmod(n_obs, n_splits)
    edges: list[tuple[int, int]] = []
    cursor = 0
    for k in range(n_splits):
        width = size + (1 if k < remainder else 0)
        edges.append((cursor, cursor + width))
        cursor += width
    return edges


def _sharpe(n: FloatArray, s: FloatArray, q: FloatArray) -> FloatArray:
    """Per-period Sharpe (ddof = 1) from counts, sums and sums of squares; degenerate ⇒ 0."""
    mean = s / n
    var = np.maximum(q - n * mean**2, 0.0) / (n - 1)
    std = np.sqrt(var)
    scale = np.maximum(np.abs(mean), np.sqrt(q / n))  # a relative floor for rounding noise
    ok = (n >= 2) & (std > 1e-7 * np.maximum(scale, 1e-300))
    return np.where(ok, mean / np.where(ok, std, 1.0), 0.0)


def pbo(returns: npt.ArrayLike, n_splits: int = DEFAULT_SPLITS) -> PboResult:
    matrix = np.asarray(returns, dtype=np.float64)
    if matrix.ndim != 2:
        raise ValueError("returns must be a 2-D (n_configs, n_obs) matrix")
    n_configs, n_obs = matrix.shape
    if n_configs < 2:
        raise ValueError(f"PBO needs at least 2 configurations, got {n_configs}")
    if n_splits < 2 or n_splits % 2:
        raise ValueError(f"n_splits must be even and >= 2, got {n_splits}")
    if n_obs < 2 * n_splits:
        raise ValueError(f"{n_obs} observations are too few for {n_splits} blocks")
    if not np.isfinite(matrix).all():
        raise ValueError("returns contain NaN or infinite values")

    edges = _block_edges(n_obs, n_splits)
    count = np.array([hi - lo for lo, hi in edges], dtype=np.float64)  # (S,)
    sums = np.stack([matrix[:, lo:hi].sum(axis=1) for lo, hi in edges], axis=1)  # (M, S)
    squares = np.stack([(matrix[:, lo:hi] ** 2).sum(axis=1) for lo, hi in edges], axis=1)

    combos = list(combinations(range(n_splits), n_splits // 2))
    oos = np.zeros((len(combos), n_splits))
    for i, combo in enumerate(combos):
        oos[i, list(combo)] = 1.0
    ins = 1.0 - oos

    def sharpe(mask: FloatArray) -> FloatArray:  # (K, M)
        n = (mask @ count)[:, None]
        return _sharpe(n, mask @ sums.T, mask @ squares.T)

    is_sr, oos_sr = sharpe(ins), sharpe(oos)
    best = np.argmax(is_sr, axis=1)
    rows = np.arange(len(combos))
    ranks = stats.rankdata(oos_sr, axis=1)[rows, best]
    omega = ranks / (n_configs + 1)
    logits = np.log(omega / (1.0 - omega))
    is_best, oos_best = is_sr[rows, best], oos_sr[rows, best]
    var_is = float(np.var(is_best))
    slope = float(np.cov(is_best, oos_best, ddof=0)[0, 1] / var_is) if var_is > 0 else float("nan")
    return PboResult(
        pbo=float(np.mean(logits < 0)),
        logits=logits,
        slope=slope,
        is_best_is_sharpe=is_best,
        is_best_oos_sharpe=oos_best,
        n_combos=len(combos),
        n_configs=n_configs,
    )
