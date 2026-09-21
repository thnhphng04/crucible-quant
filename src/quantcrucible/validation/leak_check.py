"""Gate ①b's measurement — the dynamic look-ahead check (Architecture §3.2, ADR-0005).

Runs inside the sandbox (it executes candidate code). For seeded cut points *t* it compares the
signals at bars ``s ≤ t`` computed three ways:

* **full** — indicators on the whole series;
* **truncation** — indicators on ``bars[:t+1]`` (the future does not exist yet);
* **perturbation** — indicators on a copy whose bars after *t* are a fresh random walk.

A causal strategy gives the same signal all three ways. Any difference means a signal at ``s``
depended on bars after ``t ≥ s``: look-ahead, a repainting indicator, a forming candle, or
information joined before it was published.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from quantcrucible.core.strategy.base import Bars, Features, FeatureView, Signal, Strategy

TOLERANCE = 1e-9
MAX_REPORTED = 10


def perturb_after(bars: Bars, t: int, rng: np.random.Generator) -> Bars:
    """``bars`` with every bar after ``t`` replaced by a random walk from ``close[t]``."""
    k = len(bars) - t - 1
    if k <= 0:
        return bars
    log_ret = np.diff(np.log(bars.close[: t + 1]))
    sigma = float(np.std(log_ret)) if len(log_ret) > 1 else 0.0
    sigma = sigma if math.isfinite(sigma) and sigma > 0 else 0.01
    close = bars.close[t] * np.exp(np.cumsum(rng.normal(0.0, sigma, k)))
    open_ = np.concatenate(([bars.close[t]], close[:-1]))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0.0, sigma / 2, k)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0.0, sigma / 2, k)))
    volume = rng.choice(bars.volume[: t + 1], k)
    head = bars.slice(0, t + 1)
    return Bars(
        bars.symbol,
        bars.timeframe,
        bars.ts,
        np.concatenate((head.open, open_)),
        np.concatenate((head.high, high)),
        np.concatenate((head.low, low)),
        np.concatenate((head.close, close)),
        np.concatenate((head.volume, volume)),
    )


def _features(strategy: Strategy, bars: Bars) -> Features:
    features = strategy.indicators(bars)
    for name, arr in features.items():
        if np.shape(arr) != (len(bars),):
            raise ValueError(f"feature {name!r} must have one value per bar")
    return features


def _close(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return a is b
    return abs(a - b) <= TOLERANCE * (1.0 + abs(a))


def same_signal(a: Signal, b: Signal) -> bool:
    return (
        a.direction == b.direction
        and _close(a.strength, b.strength)
        and _close(a.stop_distance, b.stop_distance)
        and _close(a.take_profit, b.take_profit)
    )


def _describe(s: Signal) -> list[Any]:
    return [s.direction, s.strength, s.stop_distance, s.take_profit]


def cut_points(n: int, count: int, rng: np.random.Generator) -> list[int]:
    """Up to ``count`` distinct cut points in ``[n // 10, n - 2]`` (the last bar has no future)."""
    lo, hi = max(1, n // 10), n - 1
    if hi <= lo:
        return []
    pool = np.arange(lo, hi)
    return sorted(int(t) for t in rng.choice(pool, size=min(count, len(pool)), replace=False))


def leak_check(
    strategy: Strategy, bars: Bars, count: int = 20, seed: int = 0, window: int = 20
) -> dict[str, Any]:
    """Compare full / truncated / perturbed signals for ``window`` bars up to each cut point."""
    rng = np.random.default_rng(seed)
    full = _features(strategy, bars)
    points = cut_points(len(bars), count, rng)
    leaks: list[dict[str, Any]] = []
    n_leaks = checked = 0
    for t in points:
        variants = {
            "truncation": _features(strategy, bars.slice(0, t + 1)),
            "perturbation": _features(strategy, perturb_after(bars, t, rng)),
        }
        for s in range(max(0, t - window + 1), t + 1):
            ref = strategy.signal(FeatureView(full, s))
            for kind, features in variants.items():
                other = strategy.signal(FeatureView(features, s))
                checked += 1
                if not same_signal(ref, other):
                    n_leaks += 1
                    if len(leaks) < MAX_REPORTED:
                        leaks.append(
                            {
                                "symbol": bars.symbol, "cut": t, "bar": s, "kind": kind,
                                "full": _describe(ref), "other": _describe(other),
                            }
                        )  # fmt: skip
    return {"cut_points": points, "checked": checked, "n_leaks": n_leaks, "leaks": leaks}
