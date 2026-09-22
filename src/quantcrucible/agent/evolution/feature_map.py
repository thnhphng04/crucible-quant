"""MAP-Elites feature map with bounds fixed per campaign (arch §3.1.3, P2-07).

Six behavioural dimensions: the strategy category (a bit set over the grammar's categories)
plus five continuous ones — trades per year, max drawdown, IS Sharpe, IS Sortino and total return
— each cut into ``bins`` equal bins between bounds read **only** from the campaign lock
(``derived.feature_map``, INV-63). Bounds never stretch with observed values (MadEvolve X4);
out-of-range values land in the edge bin, a missing or non-finite value in bin 0.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

DIMENSIONS = ("trades_per_year", "max_drawdown", "sharpe_is", "sortino_is", "total_return")

Cell = tuple[int, ...]  # (category bits, bin per continuous dimension)


class FeatureMapError(ValueError):
    """The campaign lock does not define the feature map."""


@dataclass(frozen=True, slots=True)
class FeatureMap:
    bins: int
    bounds: Mapping[str, tuple[float, float]]
    categories: tuple[str, ...]

    @classmethod
    def from_lock(cls, lock: Mapping[str, Any]) -> FeatureMap:
        spec = lock.get("derived", {}).get("feature_map")
        if not spec:
            raise FeatureMapError("the campaign lock has no derived.feature_map: open a new one")
        bounds = {d: (float(spec["bounds"][d][0]), float(spec["bounds"][d][1])) for d in DIMENSIONS}
        for d, (lo, hi) in bounds.items():
            if not lo < hi:
                raise FeatureMapError(f"feature_map bounds for {d} must satisfy low < high")
        return cls(int(spec["bins"]), bounds, tuple(spec["categories"]))

    def bin(self, dim: str, value: float | None) -> int:
        if value is None or not math.isfinite(value):
            return 0
        lo, hi = self.bounds[dim]
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
            *(self.bin(d, descriptors.get(d)) for d in DIMENSIONS),
        )


def cell_id(cell: Cell) -> str:
    return "c" + "-".join(str(v) for v in cell)


def descriptors(public: Mapping[str, float], years: float) -> dict[str, float | None]:
    """The five continuous descriptors from a trial's gate-③ ``public`` metrics."""
    trades = public.get("n_trades")
    return {
        "trades_per_year": trades / years if trades is not None and years > 0 else None,
        "max_drawdown": public.get("max_drawdown"),
        "sharpe_is": public.get("sharpe_is"),
        "sortino_is": public.get("sortino_is"),
        "total_return": public.get("total_return"),
    }
