"""A campaign's backtest numerics, read from its lock (D23, ADR-0039).

``research.backtest.precision`` is Group B; ``derived.backtest_numerics`` names the arithmetic it
implies, so a later change of meaning gets a new tag instead of silently re-reading old locks. A
lock written before the key existed means float64 and carries no tag.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from quantcrucible.config.schema import Precision

NUMERICS_TAGS: dict[str, str] = {
    "float64": "fp64_oracle_v1",  # bit-exact with the Python replay (ADR-0038)
    "float32": "fp32_signals_v1",  # float32 features and signals, float64 account
}


def numerics_tag(precision: str) -> str:
    try:
        return NUMERICS_TAGS[precision]
    except KeyError:
        raise ValueError(f"unknown backtest precision {precision!r}") from None


@dataclass(frozen=True, slots=True)
class Numerics:
    precision: Precision
    tag: str

    @classmethod
    def from_lock(cls, lock: Mapping[str, Any]) -> Numerics:
        precision = str(lock.get("research", {}).get("backtest", {}).get("precision", "float64"))
        expected = numerics_tag(precision)
        tag = lock.get("derived", {}).get("backtest_numerics")
        if tag is None and precision != "float64":
            raise ValueError(f"a {precision} lock must name its backtest numerics")
        if tag is not None and tag != expected:
            raise ValueError(f"locked backtest numerics {tag!r} contradict precision {precision}")
        return cls("float32" if precision == "float32" else "float64", expected)
