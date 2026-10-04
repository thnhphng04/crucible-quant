"""MAP-Elites feature map with bounds fixed per campaign (arch §3.1.3, P2-07).

Six behavioural dimensions: the strategy category (a bit set over the grammar's categories)
plus five continuous ones — trades per year, max drawdown, IS Sharpe, IS Sortino and total return
— each cut into ``bins`` equal bins between bounds read **only** from the campaign lock
(``derived.feature_map``, INV-63). Bounds never stretch with observed values (MadEvolve X4);
out-of-range values land in the edge bin, a missing or non-finite value in bin 0.

A v2 map (ADR-0046) names its dimensions and its log ones: trades per year are cut on a log
scale, so one map holds a strategy trading twice a year and one trading every few hours, and the
annual return replaces the total return, which grows with the length of the IS period.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

DIMENSIONS = ("trades_per_year", "max_drawdown", "sharpe_is", "sortino_is", "total_return")
DIMENSIONS_V2 = ("trades_per_year", "max_drawdown", "sharpe_is", "sortino_is", "annual_return")

Cell = tuple[int, ...]  # (category bits, bin per continuous dimension)


class FeatureMapError(ValueError):
    """The campaign lock does not define the feature map."""


@dataclass(frozen=True, slots=True)
class FeatureMap:
    bins: int
    bounds: Mapping[str, tuple[float, float]]
    categories: tuple[str, ...]
    dimensions: tuple[str, ...] = DIMENSIONS
    log_dims: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def from_lock(cls, lock: Mapping[str, Any]) -> FeatureMap:
        spec = lock.get("derived", {}).get("feature_map")
        if not spec:
            raise FeatureMapError("the campaign lock has no derived.feature_map: open a new one")
        dims = tuple(spec.get("dimensions", DIMENSIONS))  # a v1 map names none
        log_dims = frozenset(spec.get("log", ()))
        bounds = {d: (float(spec["bounds"][d][0]), float(spec["bounds"][d][1])) for d in dims}
        for d, (lo, hi) in bounds.items():
            if not lo < hi:
                raise FeatureMapError(f"feature_map bounds for {d} must satisfy low < high")
            if d in log_dims and lo <= 0:
                raise FeatureMapError(f"feature_map log dimension {d} needs a lower bound > 0")
        return cls(int(spec["bins"]), bounds, tuple(spec["categories"]), dims, log_dims)

    def bin(self, dim: str, value: float | None) -> int:
        if value is None or not math.isfinite(value):
            return 0
        lo, hi = self.bounds[dim]
        if dim in self.log_dims:
            if value <= lo:  # zero trades included: the edge bin, not a log error
                return 0
            i = math.floor(math.log(value / lo) / math.log(hi / lo) * self.bins)
        else:
            i = math.floor((value - lo) / (hi - lo) * self.bins)
        return min(max(i, 0), self.bins - 1)

    def category_bits(self, categories: Iterable[str]) -> int:
        found = set(categories)
        unknown = found - set(self.categories)
        if unknown:
            raise FeatureMapError(f"categories {sorted(unknown)} are not in the locked map")
        return sum(1 << i for i, c in enumerate(self.categories) if c in found)

    def cell(self, descriptors: Mapping[str, float | None], categories: Iterable[str]) -> Cell:
        return (
            self.category_bits(categories),
            *(self.bin(d, descriptors.get(d)) for d in self.dimensions),
        )


def cell_id(cell: Cell) -> str:
    return "c" + "-".join(str(v) for v in cell)


def descriptors(public: Mapping[str, float], years: float) -> dict[str, float | None]:
    """The continuous descriptors from a trial's gate-③ ``public`` metrics, for either map."""
    trades = public.get("n_trades")
    total = public.get("total_return")
    return {
        "trades_per_year": trades / years if trades is not None and years > 0 else None,
        "max_drawdown": public.get("max_drawdown"),
        "sharpe_is": public.get("sharpe_is"),
        "sortino_is": public.get("sortino_is"),
        "total_return": total,
        "annual_return": _annual(total, years),
    }


def _annual(total: float | None, years: float) -> float | None:
    """The compound annual rate of a total return earned over ``years``."""
    if total is None or not math.isfinite(total) or years <= 0:
        return None
    if total <= -1:
        return -1.0
    return float((1 + total) ** (1 / years) - 1)
