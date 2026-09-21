"""Combinatorial Purged Cross-Validation for rule-based strategies (Architecture §3.2,
07-VALIDATION-LAYER §3, §4.1, ADR-0012).

A deterministic rule has nothing to *fit*, but its parameters are still *selected*. So each CPCV
fold selects, on its purged + embargoed training blocks, the configuration with the best Sharpe
from gate ④'s matrix (the loop's own selection rule), and scores it on the test blocks. The
folds are assembled into C(N−1, k−1) out-of-sample paths; their Sharpe distribution (median,
share of negative paths) is a **private** metric (§3.3.2) — it also feeds the IS→OOS degradation
curve, which never touches the holdout (§3.2).

Splits, purging, embargo and path reconstruction come from ``purgedcv`` (ADR-0007).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import pandas as pd
from purgedcv import CombinatorialPurgedCV, reconstruct_paths

N_GROUPS = 6
N_TEST_GROUPS = 2
EMBARGO_FRACTION = 0.01  # López de Prado's rule of thumb (AFML §7.4.2)

FloatArray = npt.NDArray[np.float64]
IntArray = npt.NDArray[np.int64]


@dataclass(frozen=True, slots=True)
class CpcvResult:
    path_sharpes: FloatArray  # annualized, one per OOS path
    n_folds: int
    n_paths: int
    selected: tuple[int, ...]  # the configuration chosen in each fold

    @property
    def median_sharpe(self) -> float:
        return float(np.median(self.path_sharpes))

    @property
    def negative_share(self) -> float:
        return float(np.mean(self.path_sharpes < 0))

    def private_metrics(self) -> dict[str, float]:
        return {
            "cpcv_oos_median_sharpe": self.median_sharpe,
            "cpcv_oos_negative_share": self.negative_share,
            "cpcv_n_paths": float(self.n_paths),
        }


def purged_splits(
    ts: Sequence[np.datetime64] | pd.DatetimeIndex,
    horizon_bars: int,
    embargo_bars: int,
    n_groups: int = N_GROUPS,
    n_test_groups: int = N_TEST_GROUPS,
) -> list[tuple[IntArray, IntArray]]:
    """(train, test) index pairs. Observation *i* is the return of bar *i*; its "label" spans
    ``horizon_bars`` bars from its time, so training rows whose span overlaps a test block are
    purged, and ``embargo_bars`` rows after each test block are dropped too."""
    times = pd.DatetimeIndex(pd.to_datetime(np.asarray(ts)))
    if len(times) < 2:
        raise ValueError("need at least 2 timestamps")
    bar = times[1] - times[0]
    cv = CombinatorialPurgedCV(
        n_splits=n_groups,
        n_test_groups=n_test_groups,
        prediction_times=pd.Series(times),
        evaluation_times=pd.Series(times + bar * max(horizon_bars, 1)),
        embargo_observations=embargo_bars,
    )
    placeholder = np.empty((len(times), 1))
    return [
        (np.asarray(tr, dtype=np.int64), np.asarray(te, dtype=np.int64))
        for tr, te in cv.split(placeholder)
    ]


def _sharpe(x: FloatArray) -> float:
    if len(x) < 2:
        return 0.0
    std = float(np.std(x, ddof=1))
    return float(np.mean(x)) / std if std > 0 else 0.0


def cpcv(
    matrix: npt.ArrayLike,
    ts: Sequence[np.datetime64] | pd.DatetimeIndex,
    periods_per_year: float,
    horizon_bars: int = 1,
    embargo_bars: int | None = None,
    n_groups: int = N_GROUPS,
    n_test_groups: int = N_TEST_GROUPS,
) -> CpcvResult:
    """CPCV OOS paths of "select the best configuration in-sample" over an (M × T) matrix."""
    m = np.asarray(matrix, dtype=np.float64)
    if m.ndim != 2 or m.shape[1] != len(ts):
        raise ValueError("matrix must be (n_configs, len(ts))")
    if not np.isfinite(m).all():
        raise ValueError("matrix contains NaN or infinite values")
    n_obs = m.shape[1]
    embargo = math.floor(n_obs * EMBARGO_FRACTION) if embargo_bars is None else embargo_bars
    splits = purged_splits(ts, horizon_bars, embargo, n_groups, n_test_groups)
    fold_returns: list[FloatArray] = []
    fold_tests: list[IntArray] = []
    selected: list[int] = []
    for train, test in splits:
        if len(train) < 2:
            raise ValueError("a fold has no training data left after purge/embargo")
        best = int(np.argmax([_sharpe(row[train]) for row in m]))
        selected.append(best)
        fold_returns.append(m[best, test])
        fold_tests.append(test)
    paths = reconstruct_paths(fold_returns, fold_tests, n_groups, n_test_groups, n_obs)
    sharpes = np.array([_sharpe(p) * math.sqrt(periods_per_year) for p in paths])
    return CpcvResult(sharpes, len(splits), len(paths), tuple(selected))


@dataclass(frozen=True, slots=True)
class DegradationPoint:
    label: str  # e.g. the candidate id of the record holder at that checkpoint
    is_sharpe: float
    oos_median_sharpe: float


def is_oos_diverging(points: Sequence[DegradationPoint], min_points: int = 3) -> bool:
    """The p-hacking signal of §3.2: the IS record line rises while the same strategies' CPCV-OOS
    median is flat or falling (OLS slopes over the checkpoints). Needs ``min_points``."""
    if len(points) < min_points:
        return False
    x = np.arange(len(points), dtype=np.float64)
    is_slope = float(np.polyfit(x, [p.is_sharpe for p in points], 1)[0])
    oos_slope = float(np.polyfit(x, [p.oos_median_sharpe for p in points], 1)[0])
    return is_slope > 0 and oos_slope <= 0
