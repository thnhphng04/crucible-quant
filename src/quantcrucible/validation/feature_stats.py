"""Indicator-correlation constraint for gate ③ (Architecture §3.3.1 rule 4, ADR-0006).

Runs inside the sandbox (it needs the candidate's ``indicators``). Correlation is measured on
bar-to-bar CHANGES: the levels of any two price-following indicators (two EMAs, say) correlate
near 1 however different they are, which says nothing about redundancy.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from itertools import combinations

import numpy as np
import numpy.typing as npt

MIN_OBSERVATIONS = 30


def max_abs_change_corr(
    features: Mapping[str, npt.ArrayLike],
) -> tuple[float, tuple[str, str] | None]:
    """Largest |ρ| between the bar-to-bar changes of any two features, and the pair."""
    changes = {k: np.diff(np.asarray(v, dtype=np.float64)) for k, v in features.items()}
    best, pair = 0.0, None
    for a, b in combinations(sorted(changes), 2):
        x, y = changes[a], changes[b]
        ok = np.isfinite(x) & np.isfinite(y)
        if int(ok.sum()) < MIN_OBSERVATIONS:
            continue
        xs, ys = x[ok], y[ok]
        if float(np.std(xs)) == 0.0 or float(np.std(ys)) == 0.0:
            continue
        rho = abs(_pearson(xs, ys))
        if math.isfinite(rho) and rho > best:
            best, pair = rho, (a, b)
    return best, pair


def _pearson(x: npt.NDArray[np.float64], y: npt.NDArray[np.float64]) -> float:
    """Pearson's r from sums, a square root and a division, in a fixed order.

    Not ``np.corrcoef``: it goes through a BLAS matrix product whose last bit differs between the
    Windows and Linux builds of the same numpy, and the kernel engine (host) and the sandbox
    (Linux) must report the same bits for the L2 audit (ADR-0038)."""
    dx = x - np.sum(x) / x.size
    dy = y - np.sum(y) / y.size
    sxx, syy = float(np.sum(dx * dx)), float(np.sum(dy * dy))
    if sxx == 0.0 or syy == 0.0:
        return math.nan
    return max(-1.0, min(1.0, float(np.sum(dx * dy)) / math.sqrt(sxx * syy)))
