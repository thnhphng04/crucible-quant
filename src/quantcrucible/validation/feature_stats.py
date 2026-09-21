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
        rho = abs(float(np.corrcoef(xs, ys)[0, 1]))
        if math.isfinite(rho) and rho > best:
            best, pair = rho, (a, b)
    return best, pair
